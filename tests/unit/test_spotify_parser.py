"""Unit tests for Spotify URL/URI parsing."""

from __future__ import annotations

import pytest

from spotiseek.errors import SpotifyError
from spotiseek.models import SpotifyKind
from spotiseek.spotify.parser import parse_spotify_url

TRACK_ID = "0DiWol3AO6WpXZgp0goxAV"
ALBUM_ID = "2noRn2Aes5aoNVsU6iWThc"
PLAYLIST_ID = "37i9dQZF1DXcBWIGoYBM5M"


@pytest.mark.parametrize(
    "url, kind, spotify_id",
    [
        (f"https://open.spotify.com/track/{TRACK_ID}", SpotifyKind.TRACK, TRACK_ID),
        (f"http://open.spotify.com/album/{ALBUM_ID}", SpotifyKind.ALBUM, ALBUM_ID),
        (
            f"https://open.spotify.com/playlist/{PLAYLIST_ID}?si=abc123",
            SpotifyKind.PLAYLIST,
            PLAYLIST_ID,
        ),
        (
            f"https://open.spotify.com/intl-es/track/{TRACK_ID}?si=x",
            SpotifyKind.TRACK,
            TRACK_ID,
        ),
        (f"spotify:track:{TRACK_ID}", SpotifyKind.TRACK, TRACK_ID),
        (f"spotify:album:{ALBUM_ID}", SpotifyKind.ALBUM, ALBUM_ID),
        (
            f"spotify:user:someone:playlist:{PLAYLIST_ID}",
            SpotifyKind.PLAYLIST,
            PLAYLIST_ID,
        ),
        (f"  https://open.spotify.com/track/{TRACK_ID}  ", SpotifyKind.TRACK, TRACK_ID),
    ],
)
def test_parse_valid(url: str, kind: SpotifyKind, spotify_id: str) -> None:
    assert parse_spotify_url(url) == (kind, spotify_id)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "https://example.com/track/abc",
        "https://open.spotify.com/artist/12345",  # artist not supported
        "https://open.spotify.com/",
        "not a url at all",
        "spotify:episode:12345",  # unsupported kind
    ],
)
def test_parse_invalid(url: str) -> None:
    with pytest.raises(SpotifyError):
        parse_spotify_url(url)
