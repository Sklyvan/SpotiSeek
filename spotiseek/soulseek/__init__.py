"""Soulseek layer: network client (aioslsk wrapper) and candidate matcher."""

from .matcher import score_candidates
from .client import SoulseekClient

__all__ = ["SoulseekClient", "score_candidates"]
