"""Credential-free metadata provider.

Reads Spotify's public embed pages (``open.spotify.com/embed/<kind>/<id>``)
which carry a ``__NEXT_DATA__`` JSON blob describing the entity. This needs no
account or API key and works even when the official Web API is gated, but it is
an unofficial endpoint and may truncate very large playlists.
"""

from __future__ import annotations

import json
import logging
import re

import requests

from ..errors import SpotifyError
from ..models import MetadataSource, SpotifyKind, Track

logger = logging.getLogger(__name__)

EMBED_URL = "https://open.spotify.com/embed/{kind}/{spotify_id}"
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


def extract_entity(html: str) -> dict:
    """Extract the entity dict from an embed page's ``__NEXT_DATA__`` blob."""
    match = _NEXT_DATA_RE.search(html)
    if not match:
        raise SpotifyError("Could not find embedded metadata (__NEXT_DATA__) in page.")
    try:
        data = json.loads(match.group(1))
        return data["props"]["pageProps"]["state"]["data"]["entity"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SpotifyError(f"Malformed embed metadata: {exc}") from exc


def _id_from_uri(uri: str | None) -> str | None:
    return uri.split(":")[-1] if uri else None


def _split_artists(subtitle: str | None) -> list[str]:
    if not subtitle:
        return []
    # Spotify separates multiple artists with a comma in the embed subtitle.
    return [a.strip() for a in subtitle.split(",") if a.strip()]


def _release_date(entity: dict) -> str | None:
    iso = (entity.get("releaseDate") or {}).get("isoString")
    return iso.split("T")[0] if iso else None


def _best_cover_url(entity: dict) -> str | None:
    """Pick the highest-resolution cover image available on the entity."""
    sources: list[dict] = []
    cover_art = entity.get("coverArt") or {}
    if isinstance(cover_art, dict):
        sources.extend(cover_art.get("sources") or [])
    visual = entity.get("visualIdentity") or {}
    if isinstance(visual, dict):
        sources.extend(visual.get("image") or [])

    urls = [
        (s.get("maxWidth") or s.get("width") or 0, s["url"])
        for s in sources
        if isinstance(s, dict) and s.get("url")
    ]
    if not urls:
        return None
    urls.sort(key=lambda pair: pair[0])
    return urls[-1][1]


def entity_to_tracks(entity: dict, kind: SpotifyKind) -> list[Track]:
    """Normalize a parsed embed entity into a list of tracks."""
    if kind is SpotifyKind.TRACK:
        return [
            Track(
                title=entity.get("name") or entity.get("title") or "",
                artists=[a["name"] for a in entity.get("artists", []) if a.get("name")],
                album=None,  # single-track embeds do not expose the album name
                duration_ms=entity.get("duration"),
                release_date=_release_date(entity),
                cover_url=_best_cover_url(entity),
                spotify_id=entity.get("id") or _id_from_uri(entity.get("uri")),
            )
        ]

    track_list = entity.get("trackList") or []
    is_album = kind is SpotifyKind.ALBUM
    album_name = entity.get("name") if is_album else None
    album_cover = _best_cover_url(entity) if is_album else None
    release_date = _release_date(entity) if is_album else None

    tracks: list[Track] = []
    for index, item in enumerate(track_list, start=1):
        tracks.append(
            Track(
                title=item.get("title") or "",
                artists=_split_artists(item.get("subtitle")),
                album=album_name,
                track_number=index if is_album else None,
                duration_ms=item.get("duration"),
                release_date=release_date,
                cover_url=album_cover,  # None for playlists (per-track art unknown)
                spotify_id=_id_from_uri(item.get("uri")),
            )
        )
    return tracks


class EmbedProvider:
    """Metadata provider backed by the public embed pages."""

    source = MetadataSource.EMBED

    def __init__(self, timeout: float = 20.0) -> None:
        self._timeout = timeout

    def _fetch(self, url: str) -> str:
        response = requests.get(
            url, headers={"User-Agent": _USER_AGENT}, timeout=self._timeout
        )
        response.raise_for_status()
        return response.text

    def get_tracks(self, kind: SpotifyKind, spotify_id: str) -> list[Track]:
        url = EMBED_URL.format(kind=kind.value, spotify_id=spotify_id)
        logger.debug("Fetching embed metadata: %s", url)
        try:
            html = self._fetch(url)
        except requests.RequestException as exc:
            raise SpotifyError(f"Failed to fetch Spotify embed page: {exc}") from exc

        entity = extract_entity(html)
        tracks = entity_to_tracks(entity, kind)
        if not tracks:
            raise SpotifyError(f"No tracks found for {kind.value} {spotify_id!r}.")
        return tracks
