"""Orchestrates the lossless fallback: resolve, then try providers in order."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from ..config import Config
from ..models import Track
from . import odesli
from .providers import PROVIDER_REGISTRY

logger = logging.getLogger(__name__)

# Providers whose native ID we get from Odesli; the rest ("qobuz") are keyed by
# ISRC instead.
_ODESLI_PROVIDERS = frozenset({"tidal", "deezer", "amazon"})


@dataclass(slots=True)
class FallbackOutcome:
    """A successful fallback download: a temp file plus its source provider."""

    path: str
    extension: str
    provider: str


class FallbackSource:
    """Resolve a :class:`Track` via Odesli and download it from a proxy provider.

    Synchronous by design (uses ``requests``); the async downloader dispatches it
    with ``asyncio.to_thread``, mirroring how tagging is offloaded.
    """

    def __init__(self, config: Config, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    def _build_provider(self, name: str):
        entry = PROVIDER_REGISTRY.get(name)
        if entry is None:
            logger.debug("Fallback: unknown provider %r, ignoring.", name)
            return None
        cls, attr = entry
        base_url = getattr(self.config, attr, "") or ""
        return cls(base_url, session=self.session)

    def resolve(self, track: Track) -> odesli.OdesliResult | None:
        """Resolve platform IDs for ``track`` (used by dry-run reporting)."""
        return odesli.resolve(
            track.spotify_id, isrc=track.isrc, session=self.session
        )

    def available_providers(self, resolved: odesli.OdesliResult) -> list[str]:
        """Which configured providers have an identifier we could try."""
        out: list[str] = []
        for name in self.config.fallback_providers:
            if name not in PROVIDER_REGISTRY:
                continue
            if self._identifier_for(name, resolved):
                out.append(name)
        return out

    @staticmethod
    def _identifier_for(name: str, resolved: odesli.OdesliResult) -> str | None:
        if name in _ODESLI_PROVIDERS:
            return resolved.id_for(name)
        # Qobuz (and any future non-Odesli provider) is keyed by ISRC.
        return resolved.isrc

    def download(self, track: Track, dest_dir: str) -> FallbackOutcome | None:
        """Try each configured provider in order; return the first success."""
        resolved = self.resolve(track)
        if resolved is None:
            logger.info("Fallback: could not resolve %s on any platform.", track.display)
            return None

        for name in self.config.fallback_providers:
            provider = self._build_provider(name)
            if provider is None:
                continue
            identifier = self._identifier_for(name, resolved)
            if not identifier:
                logger.debug("Fallback: no %s match for %s; skipping.",
                             name, track.display)
                continue
            logger.info("Fallback: trying %s for %s...", name, track.display)
            outcome = provider.fetch(identifier, dest_dir)
            if outcome is not None:
                path, ext = outcome
                logger.info("Fallback: got %s from %s.", track.display, name)
                return FallbackOutcome(path=path, extension=ext, provider=name)

        logger.info("Fallback: no provider could deliver %s.", track.display)
        return None
