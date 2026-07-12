"""Abstract base class for Spotify metadata providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import MetadataSource, SpotifyKind, Track


class MetadataProvider(ABC):
    """Resolves a Spotify entity into a flat list of tracks."""

    #: Which source this provider represents (for logging).
    source: MetadataSource

    @abstractmethod
    def get_tracks(self, kind: SpotifyKind, spotify_id: str) -> list[Track]:
        """Return the tracks for the given entity.

        For a track this is a single-element list; for an album or playlist it
        is every contained track. Implementations should raise a
        :class:`~spotiseek.errors.SpotifyError` subclass on failure.
        """

    def get_tracks_for_url(self, kind: SpotifyKind, spotify_id: str) -> list[Track]:
        return self.get_tracks(kind, spotify_id)
