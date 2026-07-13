"""Unit tests for model helpers (search-query cleanup)."""

from __future__ import annotations

import pytest

from spotiseek.models import Track


@pytest.mark.parametrize(
    "title, expected_suffix",
    [
        ("Bohemian Rhapsody - Remastered 2011", "Bohemian Rhapsody"),
        ("Money - 2011 Remaster", "Money"),
        ("Imagine - Mono Version", "Imagine"),
        ("SICKO MODE (feat. Drake)", "SICKO MODE"),
        ("One Dance (feat. Wizkid & Kyla)", "One Dance"),
        ("Plain Title", "Plain Title"),
    ],
)
def test_search_query_cleanup(title, expected_suffix) -> None:
    track = Track(title=title, artists=["Artist"])
    assert track.search_query == f"Artist {expected_suffix}"


def test_search_query_never_empty() -> None:
    # If cleaning would remove everything, fall back to the original title.
    track = Track(title="(feat. Someone)", artists=["Artist"])
    assert track.search_query.startswith("Artist")
    assert len(track.search_query) > len("Artist ")


def test_display_keeps_full_title() -> None:
    track = Track(title="Money - 2011 Remaster", artists=["Pink Floyd"])
    # Display / tagging still use the full, original title.
    assert track.display == "Pink Floyd - Money - 2011 Remaster"
