"""Official Spotify Web API metadata provider (spotipy, Client Credentials).

Preferred source when credentials are present and the app's owner account is
in good standing. If Spotify returns HTTP 403 (currently used to enforce the
"owner must have Premium" rule), this raises :class:`PremiumGateError` so the
caller can transparently fall back to the embed provider.
"""

from __future__ import annotations

import logging

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

from ..errors import PremiumGateError, SpotifyError
from ..models import MetadataSource, SpotifyKind, Track

logger = logging.getLogger(__name__)


def _images_best(images: list[dict] | None) -> str | None:
    if not images:
        return None
    # Spotify returns images largest-first, but sort defensively.
    ranked = sorted(images, key=lambda i: (i.get("width") or 0), reverse=True)
    return ranked[0].get("url")


def _artist_names(artists: list[dict] | None) -> list[str]:
    return [a["name"] for a in (artists or []) if a.get("name")]


def _track_from_full(obj: dict) -> Track:
    """Build a Track from a full Spotify track object (has an ``album`` field)."""
    album = obj.get("album") or {}
    return Track(
        title=obj.get("name") or "",
        artists=_artist_names(obj.get("artists")),
        album=album.get("name"),
        track_number=obj.get("track_number"),
        disc_number=obj.get("disc_number"),
        duration_ms=obj.get("duration_ms"),
        release_date=album.get("release_date"),
        cover_url=_images_best(album.get("images")),
        isrc=(obj.get("external_ids") or {}).get("isrc"),
        spotify_id=obj.get("id"),
    )


class SpotipyProvider:
    """Metadata provider backed by the official Spotify Web API."""

    source = MetadataSource.WEB_API

    def __init__(self, client_id: str, client_secret: str) -> None:
        auth = SpotifyClientCredentials(
            client_id=client_id, client_secret=client_secret
        )
        self._sp = spotipy.Spotify(auth_manager=auth, retries=2)

    # -- helpers ----------------------------------------------------------
    def _call(self, func, *args, **kwargs):
        """Invoke a spotipy call, translating errors to our hierarchy."""
        try:
            return func(*args, **kwargs)
        except spotipy.SpotifyException as exc:
            if exc.http_status == 403:
                raise PremiumGateError(
                    "Spotify Web API returned 403 "
                    f"({exc.msg or 'forbidden'}). The app owner's account is "
                    "likely not Premium; falling back to the embed provider."
                ) from exc
            raise SpotifyError(
                f"Spotify Web API error {exc.http_status}: {exc.msg}"
            ) from exc

    def probe(self) -> None:
        """Cheap call to verify credentials/account before committing to this
        provider. Raises PremiumGateError/SpotifyError if unusable."""
        self._call(self._sp.search, q="a", type="track", limit=1)

    # -- entity resolvers -------------------------------------------------
    def _get_track(self, spotify_id: str) -> list[Track]:
        obj = self._call(self._sp.track, spotify_id)
        return [_track_from_full(obj)]

    def _get_album(self, spotify_id: str) -> list[Track]:
        album = self._call(self._sp.album, spotify_id)
        album_meta = {
            "name": album.get("name"),
            "release_date": album.get("release_date"),
            "images": album.get("images"),
        }
        cover = _images_best(album_meta["images"])

        tracks: list[Track] = []
        page = album.get("tracks")
        while page:
            for item in page.get("items", []):
                tracks.append(
                    Track(
                        title=item.get("name") or "",
                        artists=_artist_names(item.get("artists")),
                        album=album_meta["name"],
                        track_number=item.get("track_number"),
                        disc_number=item.get("disc_number"),
                        duration_ms=item.get("duration_ms"),
                        release_date=album_meta["release_date"],
                        cover_url=cover,
                        spotify_id=item.get("id"),
                    )
                )
            page = self._call(self._sp.next, page) if page.get("next") else None
        return tracks

    def _get_playlist(self, spotify_id: str) -> list[Track]:
        tracks: list[Track] = []
        page = self._call(self._sp.playlist_items, spotify_id, additional_types=("track",))
        while page:
            for item in page.get("items", []):
                obj = item.get("track")
                if obj and obj.get("type", "track") == "track":
                    tracks.append(_track_from_full(obj))
            page = self._call(self._sp.next, page) if page.get("next") else None
        return tracks

    # -- public API -------------------------------------------------------
    def get_tracks(self, kind: SpotifyKind, spotify_id: str) -> list[Track]:
        if kind is SpotifyKind.TRACK:
            tracks = self._get_track(spotify_id)
        elif kind is SpotifyKind.ALBUM:
            tracks = self._get_album(spotify_id)
        else:
            tracks = self._get_playlist(spotify_id)
        if not tracks:
            raise SpotifyError(f"No tracks found for {kind.value} {spotify_id!r}.")
        return tracks
