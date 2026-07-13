"""Unit tests for SoulseekClient search collection + early-exit (no network)."""

from __future__ import annotations

from aioslsk.protocol.primitives import Attribute, FileData
from aioslsk.search.model import SearchResult

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
