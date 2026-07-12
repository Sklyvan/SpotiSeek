"""Spotify metadata layer: URL parsing and pluggable metadata providers."""

from .base import MetadataProvider
from .parser import parse_spotify_url
from .provider import resolve_provider

__all__ = ["MetadataProvider", "parse_spotify_url", "resolve_provider"]
