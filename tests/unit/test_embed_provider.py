"""Unit tests for the credential-free embed provider (offline, from fixtures)."""

from __future__ import annotations

from pathlib import Path

import pytest

from spotiseek.errors import SpotifyError
from spotiseek.models import SpotifyKind
from spotiseek.spotify.embed import (
    EmbedProvider,
    entity_to_tracks,
    extract_entity,
)


def _entity(fixtures_dir: Path, kind: str) -> dict:
    html = (fixtures_dir / f"embed_{kind}.html").read_text(encoding="utf-8")
    return extract_entity(html)


def test_extract_entity_missing_blob() -> None:
    with pytest.raises(SpotifyError):
        extract_entity("<html><body>no next data here</body></html>")


def test_track_entity(fixtures_dir: Path) -> None:
    tracks = entity_to_tracks(_entity(fixtures_dir, "track"), SpotifyKind.TRACK)
    assert len(tracks) == 1
    t = tracks[0]
    assert t.title == "One More Time"
    assert t.artists == ["Daft Punk"]
    assert t.duration_ms == 320357
    assert t.release_date == "2001-03-12"
    assert t.cover_url and t.cover_url.startswith("https://")


def test_album_entity(fixtures_dir: Path) -> None:
    tracks = entity_to_tracks(_entity(fixtures_dir, "album"), SpotifyKind.ALBUM)
    assert len(tracks) == 14
    assert tracks[0].title == "One More Time"
    assert tracks[0].album == "Discovery"
    assert tracks[0].track_number == 1
    assert tracks[13].track_number == 14
    # Album cover art should be populated for every track.
    assert all(t.cover_url for t in tracks)


def test_playlist_entity(fixtures_dir: Path) -> None:
    tracks = entity_to_tracks(_entity(fixtures_dir, "playlist"), SpotifyKind.PLAYLIST)
    assert len(tracks) == 50
    assert all(t.title for t in tracks)
    # Playlists have no per-track album/track-number.
    assert all(t.track_number is None for t in tracks)


def test_multi_artist_split() -> None:
    entity = {
        "type": "playlist",
        "name": "PL",
        "trackList": [
            {"uri": "spotify:track:abc", "title": "Song", "subtitle": "A, B, C", "duration": 1000}
        ],
    }
    tracks = entity_to_tracks(entity, SpotifyKind.PLAYLIST)
    assert tracks[0].artists == ["A", "B", "C"]


def test_get_tracks_uses_fetch(monkeypatch, fixtures_dir: Path) -> None:
    provider = EmbedProvider()
    html = (fixtures_dir / "embed_track.html").read_text(encoding="utf-8")
    monkeypatch.setattr(provider, "_fetch", lambda url: html)
    tracks = provider.get_tracks(SpotifyKind.TRACK, "0DiWol3AO6WpXZgp0goxAV")
    assert tracks[0].title == "One More Time"


# --------------------------------------------------------------------------- #
# Artwork enrichment (provider.enrich_artwork)
# --------------------------------------------------------------------------- #
def test_enrich_artwork_fills_missing_cover(monkeypatch) -> None:
    from spotiseek.spotify import provider
    from spotiseek.models import Track

    detailed = Track(title="X", artists=["A"], cover_url="https://img/cover.jpg",
                     release_date="2015-01-01", spotify_id="abc")
    monkeypatch.setattr(
        provider.EmbedProvider, "get_tracks",
        lambda self, kind, sid: [detailed],
    )
    track = Track(title="X", artists=["A"], spotify_id="abc")  # no cover
    assert provider.enrich_artwork(track) is True
    assert track.cover_url == "https://img/cover.jpg"
    assert track.release_date == "2015-01-01"


def test_enrich_artwork_skips_when_present(monkeypatch) -> None:
    from spotiseek.spotify import provider
    from spotiseek.models import Track

    def _boom(self, kind, sid):  # must not be called
        raise AssertionError("should not fetch when cover already present")

    monkeypatch.setattr(provider.EmbedProvider, "get_tracks", _boom)
    track = Track(title="X", artists=["A"], cover_url="https://have/it.jpg",
                  spotify_id="abc")
    assert provider.enrich_artwork(track) is True


def test_enrich_artwork_survives_failure(monkeypatch) -> None:
    from spotiseek.spotify import provider
    from spotiseek.models import Track

    def _fail(self, kind, sid):
        raise RuntimeError("network down")

    monkeypatch.setattr(provider.EmbedProvider, "get_tracks", _fail)
    track = Track(title="X", artists=["A"], spotify_id="abc")
    assert provider.enrich_artwork(track) is False
    assert track.cover_url is None


def test_enrich_artwork_no_spotify_id() -> None:
    from spotiseek.spotify import provider
    from spotiseek.models import Track

    track = Track(title="X", artists=["A"])  # no spotify_id
    assert provider.enrich_artwork(track) is False
