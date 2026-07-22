"""Unit tests for SoulseekClient search collection + early-exit (no network)."""

from __future__ import annotations

import pytest
from aioslsk.protocol.primitives import Attribute, FileData
from aioslsk.search.model import SearchResult
from aioslsk.transfer.model import TransferState

from spotiseek.errors import DownloadError
from spotiseek.models import Candidate
from spotiseek.soulseek.client import SoulseekClient


class _FakeReq:
    def __init__(self, results):
        self.results = results


class _FakeSearches:
    def __init__(self, request):
        self._request = request

    async def search(self, query):
        return self._request


class _FakeInner:
    def __init__(self, request):
        self.searches = _FakeSearches(request)


def _client(tmp_path, results):
    client = SoulseekClient("u", "p", tmp_path / "incoming")
    client._client = _FakeInner(_FakeReq(results))
    return client


def _result():
    fd = FileData(
        unknown=0,
        filename=r"Music\Artist\Song.flac",
        filesize=1000,
        extension="flac",
        attributes=[Attribute(key=1, value=200)],  # duration
    )
    return SearchResult(
        ticket=1, username="peer", has_free_slots=True, avg_speed=1000,
        queue_size=0, shared_items=[fd], locked_results=[],
    )


async def test_search_collects_candidates(tmp_path) -> None:
    client = _client(tmp_path, [_result()])
    candidates = await client.search("q", timeout=0.05)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.username == "peer"
    assert c.extension == "flac"
    assert c.duration == 200
    assert c.basename == "Song.flac"


async def test_search_early_exit(tmp_path) -> None:
    client = _client(tmp_path, [_result()])
    # A long timeout would take ages, but stop_when should end it immediately
    # once the grace period passes.
    candidates = await client.search(
        "q", timeout=100.0, stop_when=lambda c: True, min_wait=0.0, poll_interval=0.01
    )
    assert len(candidates) == 1


async def test_search_waits_when_predicate_unsatisfied(tmp_path) -> None:
    client = _client(tmp_path, [_result()])
    # Predicate never satisfied -> falls through to the (small) timeout.
    candidates = await client.search(
        "q", timeout=0.1, stop_when=lambda c: False, min_wait=0.0, poll_interval=0.02
    )
    assert len(candidates) == 1


# --- download guards (queue / stall / success) -----------------------------


class _FakeState:
    def __init__(self, value) -> None:
        self.VALUE = value


State = TransferState.State


class _FakeTransfer:
    """Walks a scripted list of (state, bytes_transfered) on each state read."""

    def __init__(self, script, local_path=None) -> None:
        self._script = script
        self._i = 0
        self.local_path = local_path
        self.bytes_transfered = script[0][1]
        self.fail_reason = None
        self.abort_reason = None

    @property
    def state(self):
        st, by = self._script[min(self._i, len(self._script) - 1)]
        self.bytes_transfered = by
        if self._i < len(self._script) - 1:
            self._i += 1
        return _FakeState(st)


class _FakeTransfers:
    def __init__(self, transfer) -> None:
        self._transfer = transfer
        self.aborted = False

    async def download(self, username, filename):
        return self._transfer

    async def abort(self, transfer):
        self.aborted = True


class _FakeInnerTransfers:
    def __init__(self, transfer) -> None:
        self.transfers = _FakeTransfers(transfer)


def _dl_client(tmp_path, transfer):
    client = SoulseekClient("u", "p", tmp_path / "incoming")
    client._client = _FakeInnerTransfers(transfer)
    return client


_CAND = Candidate(username="peer", filename=r"Music\Song.flac", extension="flac")


async def test_download_success(tmp_path) -> None:
    path = tmp_path / "Song.flac"
    path.write_bytes(b"audio")
    transfer = _FakeTransfer(
        [(State.QUEUED, 0), (State.DOWNLOADING, 100), (State.COMPLETE, 200)],
        local_path=str(path),
    )
    client = _dl_client(tmp_path, transfer)
    result = await client.download(_CAND, download_timeout=100)
    assert result == str(path)


async def test_download_queue_timeout_aborts_without_waiting_full_timeout(
    tmp_path,
) -> None:
    # Peer parks the transfer in its queue and never grants a slot: the queue
    # guard must fire long before the (large) absolute download timeout.
    transfer = _FakeTransfer([(State.QUEUED, 0)])
    client = _dl_client(tmp_path, transfer)
    with pytest.raises(DownloadError, match="No upload slot"):
        await client.download(
            _CAND, download_timeout=100, queue_timeout=0.05, stall_timeout=100
        )
    assert client._client.transfers.aborted


async def test_download_stall_timeout_aborts(tmp_path) -> None:
    # Bytes start flowing, then progress freezes: the stall guard must fire.
    transfer = _FakeTransfer([(State.DOWNLOADING, 100)])
    client = _dl_client(tmp_path, transfer)
    with pytest.raises(DownloadError, match="stalled"):
        await client.download(
            _CAND, download_timeout=100, queue_timeout=100, stall_timeout=0.05
        )
    assert client._client.transfers.aborted
