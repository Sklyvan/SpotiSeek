"""Resolve a Spotify track to its IDs on other platforms via the Odesli API.

Odesli (song.link) is a free, credential-free public API that, given a track on
one platform, returns the matching entities on every other platform it knows.
We feed it a plain Spotify URL — which works even when we don't have the track's
ISRC (the embed metadata path never populates ``Track.isrc``).

Note: Odesli does **not** cover Qobuz, so no Qobuz ID comes back here; Qobuz is
resolved by ISRC in :mod:`spotiseek.fallback.source` instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

ODESLI_API_URL = "https://api.song.link/v1-alpha.1/links"
_TIMEOUT = 20.0
_USER_AGENT = "Mozilla/5.0 (compatible; SpotiSeek)"

# Odesli entity unique IDs look like "TIDAL_SONG::491206012"; map the prefix
# onto our internal provider keys.
_PREFIX_TO_PROVIDER = {
    "TIDAL_SONG": "tidal",
    "DEEZER_SONG": "deezer",
    "AMAZON_SONG": "amazon",
}


@dataclass(slots=True)
class OdesliResult:
    """Platform IDs (keyed by provider) resolved for a single track."""

    provider_ids: dict[str, str] = field(default_factory=dict)
    isrc: str | None = None

    def id_for(self, provider: str) -> str | None:
        return self.provider_ids.get(provider)


def resolve(
    spotify_id: str | None,
    *,
    isrc: str | None = None,
    session: requests.Session | None = None,
    api_url: str = ODESLI_API_URL,
) -> OdesliResult | None:
    """Resolve ``spotify_id`` across platforms. Never raises.

    Returns an :class:`OdesliResult` on success, an ISRC-only result when the
    lookup fails but we already know the ISRC, or ``None`` when there is nothing
    to go on.
    """
    if not spotify_id:
        return OdesliResult(isrc=isrc) if isrc else None

    params = {
        "url": f"https://open.spotify.com/track/{spotify_id}",
        "songIfSingle": "true",
    }
    getter = session.get if session is not None else requests.get
    try:
        resp = getter(
            api_url, params=params, timeout=_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.debug("Odesli resolve failed for %s: %s", spotify_id, exc)
        return OdesliResult(isrc=isrc) if isrc else None

    result = _parse(data)
    if isrc and not result.isrc:
        result.isrc = isrc
    return result


def _parse(data: dict) -> OdesliResult:
    """Extract per-provider native IDs (and any ISRC) from an Odesli payload."""
    entities = data.get("entitiesByUniqueId") or {}
    provider_ids: dict[str, str] = {}
    found_isrc: str | None = None
    for unique_id, entity in entities.items():
        if not isinstance(entity, dict):
            continue
        prefix = unique_id.split("::", 1)[0]
        provider = _PREFIX_TO_PROVIDER.get(prefix)
        if provider and provider not in provider_ids:
            native = entity.get("id")
            if not native and "::" in unique_id:
                native = unique_id.split("::", 1)[1]
            if native:
                provider_ids[provider] = str(native)
        if not found_isrc and entity.get("isrc"):
            found_isrc = entity["isrc"]
    return OdesliResult(provider_ids=provider_ids, isrc=found_isrc)
