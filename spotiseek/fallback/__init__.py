"""Lossless fallback download source.

When Soulseek can't deliver a track, this package resolves the Spotify track to
its counterpart on other streaming platforms (via the public Odesli/song.link
API) and downloads a lossless file through a configurable per-provider proxy API
— the same approach SpotiFLAC uses.

The proxy endpoints are third-party, reverse-engineered services that rotate and
go offline frequently, so their base URLs are **not hard-coded**: the user must
point ``SPOTISEEK_<PROVIDER>_API_URL`` at a currently-working instance (see
``README.md``). Providers without a configured base URL are skipped, and any
single provider failing never aborts a run.
"""

from __future__ import annotations

from .source import FallbackOutcome, FallbackSource

__all__ = ["FallbackOutcome", "FallbackSource"]
