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

    async def search(self, query: str, timeout: float) -> list[Candidate]:
        """Run a search and collect candidates for ``timeout`` seconds."""
        logger.debug("Searching Soulseek: %r (timeout %.0fs)", query, timeout)
        request = await self._client.searches.search(query)
        await asyncio.sleep(timeout)

        candidates: list[Candidate] = []
        for result in request.results:
            for file in result.shared_items:
                candidates.append(_file_to_candidate(result, file))
        logger.debug("Search %r returned %d file(s).", query, len(candidates))
        return candidates

    async def download(self, candidate: Candidate, download_timeout: float) -> str:
        """Download a candidate and return the absolute local path on success.

        Raises :class:`DownloadError` on failure, abort or timeout.
        """
        logger.debug(
            "Requesting download of %r from %s", candidate.basename, candidate.username
        )
        transfer: Transfer = await self._client.transfers.download(
            candidate.username, candidate.filename
        )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + download_timeout
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
            if loop.time() > deadline:
                await self._safe_abort(transfer)
                raise DownloadError(
                    f"Download timed out after {download_timeout:.0f}s "
                    f"(state={state.name.lower()})."
                )
            await asyncio.sleep(0.5)

    async def _safe_abort(self, transfer: Transfer) -> None:
        try:
            await self._client.transfers.abort(transfer)
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("Failed to abort transfer: %s", exc)
