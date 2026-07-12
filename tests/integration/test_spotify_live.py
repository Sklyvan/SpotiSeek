"""Live Spotify metadata integration tests.

The embed path works without credentials. The Web API path only runs if
credentials are configured AND the account is not premium-gated; otherwise it
is expected to fall back to embed, which we assert.
"""

from __future__ import annotations

import pytest

from spotiseek.config import Config
from spotiseek.models import SpotifyKind
from spotiseek.spotify.embed import EmbedProvider
from spotiseek.spotify.parser import parse_spotify_url
from spotiseek.spotify.provider import fetch_tracks

pytestmark = pytest.mark.integration

TRACK_URL = "https://open.spotify.com/track/0DiWol3AO6WpXZgp0goxAV"
ALBUM_URL = "https://open.spotify.com/album/2noRn2Aes5aoNVsU6iWThc"


def test_embed_track_live() -> None:
    kind, spotify_id = parse_spotify_url(TRACK_URL)
    tracks = EmbedProvider().get_tracks(kind, spotify_id)
    assert len(tracks) == 1
    assert tracks[0].title == "One More Time"
    assert "Daft Punk" in tracks[0].artists
    assert tracks[0].duration_ms and tracks[0].duration_ms > 0


def test_embed_album_live() -> None:
    kind, spotify_id = parse_spotify_url(ALBUM_URL)
    tracks = EmbedProvider().get_tracks(kind, spotify_id)
    assert len(tracks) == 14
    assert tracks[0].album == "Discovery"


def test_fetch_tracks_resolves_something() -> None:
    kind, spotify_id = parse_spotify_url(TRACK_URL)
    tracks, source = fetch_tracks(Config.load(), kind, spotify_id)
    assert tracks and tracks[0].title == "One More Time"
    assert source.value in ("web_api", "embed")
