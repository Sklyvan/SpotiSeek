"""Async Soulseek client built on top of aioslsk.

Wraps connection/login, searching and downloading behind a small, typed
surface. Used as an async context manager::

    async with SoulseekClient(user, pw, incoming_dir) as client:
        candidates = await client.search("daft punk one more time", timeout=15)
        path = await client.download(candidates[0], download_timeout=180)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Callable

from aioslsk.client import SoulSeekClient
from aioslsk.protocol.primitives import AttributeKey, FileData
from aioslsk.search.model import SearchResult
from aioslsk.settings import CredentialsSettings, Settings
from aioslsk.transfer.model import Transfer, TransferState

from ..errors import DownloadError, SoulseekError
from ..models import Candidate

logger = logging.getLogger(__name__)

_TERMINAL_OK = {TransferState.State.COMPLETE}
_TERMINAL_FAIL = {
    TransferState.State.FAILED,
    TransferState.State.ABORTED,
    TransferState.State.INCOMPLETE,
}


def _attributes_to_dict(attributes: list) -> dict[int, int]:
    return {a.key: a.value for a in attributes or []}


def _file_to_candidate(result: SearchResult, file: FileData) -> Candidate:
    attrs = _attributes_to_dict(file.attributes)
    extension = (file.extension or "").lstrip(".").lower()
    if not extension and "." in file.filename:
        extension = file.filename.rsplit(".", 1)[-1].lower()
    return Candidate(
        username=result.username,
        filename=file.filename,
        filesize=file.filesize,
        extension=extension,
        bitrate=attrs.get(AttributeKey.BITRATE.value),
        duration=attrs.get(AttributeKey.DURATION.value),
        sample_rate=attrs.get(AttributeKey.SAMPLE_RATE.value),
        bit_depth=attrs.get(AttributeKey.BIT_DEPTH.value),
        vbr=bool(attrs[AttributeKey.VBR.value])
        if AttributeKey.VBR.value in attrs
        else None,
        has_free_slots=result.has_free_slots,
        avg_speed=result.avg_speed,
        queue_size=result.queue_size,
    )


class SoulseekClient:
    """Thin async wrapper around :class:`aioslsk.client.SoulSeekClient`."""

    def __init__(
        self,
        username: str,
        password: str,
        incoming_dir: str | os.PathLike[str],
    ) -> None:
        self._username = username
        self._incoming_dir = os.fspath(incoming_dir)
        os.makedirs(self._incoming_dir, exist_ok=True)

        settings = Settings(
            credentials=CredentialsSettings(username=username, password=password)
        )
        # We are a download-only client: don't scan or share anything, and land
        # completed files in a dedicated incoming directory.
        settings.shares.scan_on_start = False
        settings.shares.directories = []
        settings.shares.download = self._incoming_dir
        settings.network.server.reconnect.auto = True

        self._settings = settings
        self._client = SoulSeekClient(settings)
        self._started = False

    async def __aenter__(self) -> "SoulseekClient":
        await self.start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.stop()

    async def start(self) -> None:
        logger.info("Connecting to Soulseek as %r...", self._username)
        try:
            await self._client.start()
            await self._client.login()
        except Exception as exc:  # aioslsk raises various connection errors
            raise SoulseekError(f"Failed to connect/login to Soulseek: {exc}") from exc
        self._started = True
        logger.info("Logged in to Soulseek.")

    async def stop(self) -> None:
        if self._started:
            try:
                await self._client.stop()
            except Exception as exc:  # pragma: no cover - best effort cleanup
                logger.debug("Error while stopping Soulseek client: %s", exc)
            self._started = False

    async def search(
        self,
        query: str,
        timeout: float,
        stop_when: "Callable[[list[Candidate]], bool] | None" = None,
        min_wait: float = 4.0,
        poll_interval: float = 1.0,
    ) -> list[Candidate]:
        """Run a search and collect candidates.

        Waits up to ``timeout`` seconds while peers reply. If ``stop_when`` is
        given, results are polled every ``poll_interval`` seconds (after an
        initial ``min_wait`` grace period so good peers can respond), and the
        search returns early once the predicate is satisfied — this shortens the
        common case where a great match arrives quickly.
        """
        logger.debug("Searching Soulseek: %r (timeout %.0fs)", query, timeout)
        request = await self._client.searches.search(query)

        loop = asyncio.get_running_loop()
        start = loop.time()
        deadline = start + timeout
        candidates: list[Candidate] | None = None
        if stop_when is None:
            await asyncio.sleep(timeout)
        else:
            while loop.time() < deadline:
                await asyncio.sleep(min(poll_interval, max(0.0, deadline - loop.time())))
                if loop.time() - start < min_wait:
                    continue
                polled = self._collect(request)
                if self._stop_satisfied(stop_when, polled):
                    logger.debug("Search %r satisfied early.", query)
                    candidates = polled  # reuse; avoid re-collecting below
                    break

        if candidates is None:
            candidates = self._collect(request)
        logger.debug("Search %r returned %d file(s).", query, len(candidates))
        return candidates

    @staticmethod
    def _stop_satisfied(
        stop_when: "Callable[[list[Candidate]], bool]", candidates: list[Candidate]
    ) -> bool:
        """Evaluate the early-stop predicate, never letting it abort the search."""
        try:
            return bool(stop_when(candidates))
        except Exception as exc:  # a bad predicate must not kill the run
            logger.debug("Early-stop predicate raised (ignored): %s", exc)
            return False

    @staticmethod
    def _collect(request) -> list[Candidate]:
        candidates: list[Candidate] = []
        for result in request.results:
            for file in result.shared_items:
                candidates.append(_file_to_candidate(result, file))
        return candidates

    async def download(
        self,
        candidate: Candidate,
        download_timeout: float,
        queue_timeout: float = 60.0,
        stall_timeout: float = 60.0,
    ) -> str:
        """Download a candidate and return the absolute local path on success.

        Three independent guards decide when to give up, so a single bad peer
        never pins a worker for the full ``download_timeout``:

        * ``queue_timeout`` — the most it may sit in a pre-transfer state
          (queued / initializing) with **no** bytes flowing. Peers routinely
          accept the request but never free an upload slot; without this guard
          such a transfer idles for the entire ``download_timeout`` (the common
          "stuck" case). ``0``/negative disables it.
        * ``stall_timeout`` — once bytes are flowing, the most it may go with no
          further progress before the transfer is treated as stalled.
          ``0``/negative disables it.
        * ``download_timeout`` — an absolute cap on the whole transfer.

        Raises :class:`DownloadError` on failure, abort, stall or timeout.
        """
        logger.debug(
            "Requesting download of %r from %s", candidate.basename, candidate.username
        )
        transfer: Transfer = await self._client.transfers.download(
            candidate.username, candidate.filename
        )

        loop = asyncio.get_running_loop()
        start = loop.time()
        deadline = start + download_timeout
        last_bytes = 0
        last_progress = start
        started = False  # flips True the moment the transfer actually moves bytes
        while True:
            state = transfer.state.VALUE
            if state in _TERMINAL_OK:
                if not transfer.local_path or not os.path.exists(transfer.local_path):
                    raise DownloadError("Transfer completed but file is missing.")
                logger.debug("Download complete: %s", transfer.local_path)
                return transfer.local_path
            if state in _TERMINAL_FAIL:
                reason = (
                    transfer.fail_reason
                    or transfer.abort_reason
                    or state.name.lower()
                )
                raise DownloadError(f"Transfer {state.name.lower()}: {reason}")

            now = loop.time()
            transferred = transfer.bytes_transfered or 0
            if transferred > last_bytes:
                last_bytes = transferred
                last_progress = now
                started = True

            if now > deadline:
                await self._safe_abort(transfer)
                raise DownloadError(
                    f"Download timed out after {download_timeout:.0f}s "
                    f"(state={state.name.lower()})."
                )
            if not started:
                if queue_timeout > 0 and now - start > queue_timeout:
                    await self._safe_abort(transfer)
                    raise DownloadError(
                        f"No upload slot after {queue_timeout:.0f}s "
                        f"(state={state.name.lower()}); peer never started sending."
                    )
            elif stall_timeout > 0 and now - last_progress > stall_timeout:
                await self._safe_abort(transfer)
                raise DownloadError(
                    f"Transfer stalled: no progress for {stall_timeout:.0f}s "
                    f"(state={state.name.lower()}, {last_bytes} byte(s) received)."
                )
            await asyncio.sleep(0.5)

    async def _safe_abort(self, transfer: Transfer) -> None:
        try:
            await self._client.transfers.abort(transfer)
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("Failed to abort transfer: %s", exc)
