"""Unit tests for the download orchestrator (fully mocked, no network)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from spotiseek.config import Config
from spotiseek.errors import DownloadError
from spotiseek.models import DownloadStatus, MetadataSource, Track
from spotiseek import downloader as dl

from ..conftest import make_candidate

TRACK_URL = "https://open.spotify.com/track/0DiWol3AO6WpXZgp0goxAV"


class FakeClient:
    """Stand-in for SoulseekClient. Configure via class attributes per test."""

    results: list = []
    ext_results: list | None = None  # returned for "extended mix" queries when set
    fail_users: set = set()
    search_calls: int = 0
    download_calls: int = 0
    queries: list = []

    def __init__(self, username, password, incoming_dir):
        self.incoming_dir = incoming_dir
        os.makedirs(incoming_dir, exist_ok=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def search(self, query, timeout, stop_when=None, **kwargs):
        type(self).search_calls += 1
        type(self).queries.append(query)
        if "extended mix" in query and type(self).ext_results is not None:
            return list(type(self).ext_results)
        return list(type(self).results)

    async def download(self, candidate, download_timeout):
        type(self).download_calls += 1
        if candidate.username in type(self).fail_users:
            raise DownloadError(f"simulated failure for {candidate.username}")
        path = os.path.join(self.incoming_dir, candidate.basename)
        with open(path, "wb") as fh:
            fh.write(b"FAKEAUDIO")
        return path


@pytest.fixture
def patched(monkeypatch, sample_track):
    """Patch downloader dependencies; return a controller object."""
    FakeClient.results = []
    FakeClient.ext_results = None
    FakeClient.fail_users = set()
    FakeClient.search_calls = 0
    FakeClient.download_calls = 0
    FakeClient.queries = []

    tag_calls: list = []

    state = {"tracks": [sample_track]}

    def fake_fetch(config, kind, spotify_id):
        return state["tracks"], MetadataSource.EMBED

    monkeypatch.setattr(dl, "SoulseekClient", FakeClient)
    monkeypatch.setattr(dl, "fetch_tracks", fake_fetch)
    monkeypatch.setattr(dl.tagging, "tag_file", lambda *a, **k: tag_calls.append(a))

    return type("Ctl", (), {"FakeClient": FakeClient, "tag_calls": tag_calls, "state": state})


def _config(tmp_path: Path, **kw) -> Config:
    return Config(
        soulseek_username="u",
        soulseek_password="p",
        output_dir=tmp_path / "out",
        **kw,
    )


async def test_download_success(patched, sample_track, tmp_path) -> None:
    patched.FakeClient.results = [
        make_candidate(username="good", filename="Daft Punk - One More Time.flac")
    ]
    results = await dl.run_download(_config(tmp_path), TRACK_URL)

    assert len(results) == 1
    assert results[0].status is DownloadStatus.DOWNLOADED
    saved = Path(results[0].path)
    assert saved.exists()
    assert saved.name == "Daft Punk - One More Time.flac"
    assert saved.parent.name == "out"
    assert len(patched.tag_calls) == 1  # tagging attempted once
    # Incoming scratch dir cleaned up.
    assert not (tmp_path / "out" / ".incoming").exists()


async def test_no_results(patched, tmp_path) -> None:
    patched.FakeClient.results = []
    results = await dl.run_download(_config(tmp_path), TRACK_URL)
    assert results[0].status is DownloadStatus.SKIPPED_NO_RESULTS
    assert patched.FakeClient.download_calls == 0


async def test_results_but_no_match(patched, tmp_path) -> None:
    patched.FakeClient.results = [
        make_candidate(username="x", filename="Unrelated Artist - Other Song.mp3",
                       extension="mp3", bitrate=320, duration=100)
    ]
    results = await dl.run_download(_config(tmp_path), TRACK_URL)
    assert results[0].status is DownloadStatus.SKIPPED_NO_MATCH
    assert patched.FakeClient.download_calls == 0


async def test_fallback_to_next_candidate(patched, tmp_path) -> None:
    # 'bad' scores higher (free slot) but fails; 'good' succeeds.
    patched.FakeClient.results = [
        make_candidate(username="bad", filename="Daft Punk - One More Time.flac",
                       has_free_slots=True),
        make_candidate(username="good", filename="Daft Punk - One More Time.flac",
                       has_free_slots=False, queue_size=5),
    ]
    patched.FakeClient.fail_users = {"bad"}
    results = await dl.run_download(_config(tmp_path), TRACK_URL)
    assert results[0].status is DownloadStatus.DOWNLOADED
    assert results[0].candidate.username == "good"
    assert patched.FakeClient.download_calls == 2


async def test_all_attempts_fail(patched, tmp_path) -> None:
    patched.FakeClient.results = [
        make_candidate(username="bad1", filename="Daft Punk - One More Time.flac"),
        make_candidate(username="bad2", filename="Daft Punk - One More Time.flac"),
    ]
    patched.FakeClient.fail_users = {"bad1", "bad2"}
    results = await dl.run_download(_config(tmp_path), TRACK_URL)
    assert results[0].status is DownloadStatus.FAILED
    assert results[0].error


async def test_dry_run_does_not_download(patched, tmp_path) -> None:
    patched.FakeClient.results = [
        make_candidate(username="good", filename="Daft Punk - One More Time.flac")
    ]
    results = await dl.run_download(_config(tmp_path, dry_run=True), TRACK_URL)
    assert results[0].status is DownloadStatus.DRY_RUN
    assert patched.FakeClient.download_calls == 0
    assert not any((tmp_path / "out").glob("*.flac"))


async def test_no_tag_skips_tagging(patched, tmp_path) -> None:
    patched.FakeClient.results = [
        make_candidate(username="good", filename="Daft Punk - One More Time.flac")
    ]
    await dl.run_download(_config(tmp_path, tag=False), TRACK_URL)
    assert len(patched.tag_calls) == 0


async def test_skip_if_already_present(patched, sample_track, tmp_path) -> None:
    out = tmp_path / "out"
    out.mkdir(parents=True)
    (out / "Daft Punk - One More Time.mp3").write_bytes(b"already here")
    patched.FakeClient.results = [
        make_candidate(username="good", filename="Daft Punk - One More Time.flac")
    ]
    results = await dl.run_download(_config(tmp_path), TRACK_URL)
    assert results[0].status is DownloadStatus.DOWNLOADED
    assert patched.FakeClient.search_calls == 0  # never searched
    assert patched.FakeClient.download_calls == 0


async def test_extended_mix_found(patched, tmp_path) -> None:
    patched.FakeClient.ext_results = [
        make_candidate(
            username="ext",
            filename="Daft Punk - One More Time (Extended Mix).flac",
            duration=480,  # longer than Spotify duration; must not be rejected
        )
    ]
    patched.FakeClient.results = [
        make_candidate(username="std", filename="Daft Punk - One More Time.flac")
    ]
    results = await dl.run_download(_config(tmp_path, extended_mix=True), TRACK_URL)

    r = results[0]
    assert r.status is DownloadStatus.DOWNLOADED
    assert r.extended is True
    assert Path(r.path).name == "Daft Punk - One More Time (Extended Mix).flac"
    # Only the extended search was needed (no fallback search).
    assert any("extended mix" in q for q in patched.FakeClient.queries)


async def test_extended_mix_falls_back_to_standard(patched, tmp_path) -> None:
    patched.FakeClient.ext_results = []  # no extended mix available
    patched.FakeClient.results = [
        make_candidate(username="std", filename="Daft Punk - One More Time.flac")
    ]
    results = await dl.run_download(_config(tmp_path, extended_mix=True), TRACK_URL)

    r = results[0]
    assert r.status is DownloadStatus.DOWNLOADED
    assert r.extended is False
    assert Path(r.path).name == "Daft Punk - One More Time.flac"
    # Both an extended search and a standard search happened.
    assert any("extended mix" in q for q in patched.FakeClient.queries)
    assert any("extended mix" not in q for q in patched.FakeClient.queries)


async def test_extended_mix_tags_include_suffix(patched, tmp_path) -> None:
    patched.FakeClient.ext_results = [
        make_candidate(
            username="ext",
            filename="Daft Punk - One More Time (Extended Mix).flac",
            duration=480,
        )
    ]
    await dl.run_download(_config(tmp_path, extended_mix=True), TRACK_URL)
    # tag_file is called with a track whose title carries the suffix.
    assert patched.tag_calls, "tagging was not attempted"
    tagged_track = patched.tag_calls[0][1]
    assert tagged_track.title == "One More Time (Extended Mix)"


async def test_extended_mix_dry_run(patched, tmp_path) -> None:
    patched.FakeClient.ext_results = [
        make_candidate(
            username="ext",
            filename="Daft Punk - One More Time (Extended Mix).flac",
            duration=480,
        )
    ]
    results = await dl.run_download(
        _config(tmp_path, extended_mix=True, dry_run=True), TRACK_URL
    )
    assert results[0].status is DownloadStatus.DRY_RUN
    assert results[0].extended is True
    assert patched.FakeClient.download_calls == 0


async def test_extended_mix_skip_if_present(patched, tmp_path) -> None:
    out = tmp_path / "out"
    out.mkdir(parents=True)
    (out / "Daft Punk - One More Time (Extended Mix).flac").write_bytes(b"here")
    results = await dl.run_download(_config(tmp_path, extended_mix=True), TRACK_URL)
    assert results[0].status is DownloadStatus.DOWNLOADED
    assert results[0].extended is True
    assert patched.FakeClient.search_calls == 0


async def test_progress_callbacks_fire(patched, tmp_path) -> None:
    patched.state["tracks"] = [
        Track(title=f"Song {i}", artists=["Artist"], duration_ms=200000)
        for i in range(3)
    ]
    patched.FakeClient.results = [
        make_candidate(username="u", filename="Artist - Song.flac", duration=200)
    ]
    starts: list[int] = []
    dones: list = []
    results = await dl.run_download(
        _config(tmp_path), TRACK_URL,
        on_start=starts.append,
        on_track_done=dones.append,
    )
    assert starts == [3]                 # on_start called once with the total
    assert len(dones) == 3               # on_track_done called per track
    assert len(results) == 3
    assert all(hasattr(r, "status") for r in dones)


async def test_unexpected_error_does_not_abort_run(patched, monkeypatch, tmp_path) -> None:
    # Two tracks; searching raises for all, but the run must still complete with
    # a FAILED result per track rather than propagating and aborting everything.
    patched.state["tracks"] = [
        Track(title="Song A", artists=["Artist"], duration_ms=200000),
        Track(title="Song B", artists=["Artist"], duration_ms=200000),
    ]

    async def boom(self, *a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(patched.FakeClient, "search", boom)
    results = await dl.run_download(_config(tmp_path, parallel=2), TRACK_URL)

    assert len(results) == 2
    assert all(r.status is DownloadStatus.FAILED for r in results)
    assert all("boom" in (r.error or "") for r in results)


async def test_parallel_processes_all_tracks(patched, tmp_path) -> None:
    patched.state["tracks"] = [
        Track(title=f"Song {i}", artists=["Artist"], duration_ms=200000)
        for i in range(5)
    ]
    patched.FakeClient.results = [
        make_candidate(username="u", filename="Artist - Song.flac", duration=200)
    ]
    results = await dl.run_download(_config(tmp_path, parallel=3), TRACK_URL)
    # Every track is processed and saved under its own '<Artist> - <Title>' name.
    assert len(results) == 5
    downloaded = [r for r in results if r.status is DownloadStatus.DOWNLOADED]
    assert len(downloaded) == 5
    saved = {Path(r.path).name for r in downloaded}
    assert saved == {f"Artist - Song {i}.flac" for i in range(5)}
