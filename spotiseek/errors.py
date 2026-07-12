"""Custom exception hierarchy for SpotiSeek."""

from __future__ import annotations


class SpotiSeekError(Exception):
    """Base class for all SpotiSeek errors."""


class ConfigError(SpotiSeekError):
    """Raised when configuration is missing or invalid."""


class SpotifyError(SpotiSeekError):
    """Raised for Spotify URL parsing or metadata retrieval problems."""


class PremiumGateError(SpotifyError):
    """Raised when the Spotify Web API rejects the request because the app
    owner's account requires an active Premium subscription (HTTP 403).

    This signals the metadata layer to fall back to the credential-free
    embed provider.
    """


class SoulseekError(SpotiSeekError):
    """Raised for Soulseek connection, login, search or download failures."""


class DownloadError(SoulseekError):
    """Raised when a specific file transfer fails or times out."""
