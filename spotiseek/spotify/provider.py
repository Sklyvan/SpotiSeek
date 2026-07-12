"""Provider resolution with automatic fallback.

Strategy:
  * If Spotify credentials are configured, use the official Web API first.
  * If they are absent, or the Web API raises a premium-gate 403, fall back to
    the credential-free embed provider.
The metadata source actually used is logged at INFO level.
"""

from __future__ import annotations

import logging

from ..config import Config
from ..errors import PremiumGateError
from ..models import MetadataSource, SpotifyKind, Track
from .base import MetadataProvider
from .embed import EmbedProvider
from .web_api import SpotipyProvider

logger = logging.getLogger(__name__)


def resolve_provider(config: Config) -> MetadataProvider:
    """Return the provider to try first, based on available credentials."""
    if config.has_spotify_credentials:
        return SpotipyProvider(
            config.spotify_client_id, config.spotify_client_secret  # type: ignore[arg-type]
        )
    return EmbedProvider()


def fetch_tracks(
    config: Config, kind: SpotifyKind, spotify_id: str
) -> tuple[list[Track], MetadataSource]:
    """Fetch tracks, transparently falling back to the embed provider.

    Returns the tracks alongside the :class:`MetadataSource` that produced them.
    """
    if config.has_spotify_credentials:
        provider = SpotipyProvider(
            config.spotify_client_id, config.spotify_client_secret  # type: ignore[arg-type]
        )
        try:
            tracks = provider.get_tracks(kind, spotify_id)
            logger.info("Metadata source: Spotify Web API (%d tracks)", len(tracks))
            return tracks, MetadataSource.WEB_API
        except PremiumGateError as exc:
            logger.warning("%s", exc)
    else:
        logger.info("No Spotify API credentials set; using public embed metadata.")

    embed = EmbedProvider()
    tracks = embed.get_tracks(kind, spotify_id)
    logger.info("Metadata source: public embed (%d tracks)", len(tracks))
    return tracks, MetadataSource.EMBED
