"""Unit tests for model helpers (search-query cleanup)."""

from __future__ import annotations

import pytest

from spotiseek.models import Track


@pytest.mark.parametrize(
    "title, expected_suffix",
    [
        # Remaster qualifiers are stripped (they hurt Soulseek recall).
        ("Bohemian Rhapsody - Remastered 2011", "Bohemian Rhapsody"),
        ("Money - 2011 Remaster", "Money"),
        # feat./featuring segments are stripped.
        ("SICKO MODE (feat. Drake)", "SICKO MODE"),
        ("One Dance (feat. Wizkid & Kyla)", "One Dance"),
        ("Plain Title", "Plain Title"),
        # Mono/Stereo are KEPT — they can be the master the user wants.
        ("Imagine - Mono Version", "Imagine - Mono Version"),
        # "with" is a normal word, not a feature credit -> KEPT.
        ("Song (Live with Strings)", "Song (Live with Strings)"),
        # Version/edit qualifiers are stripped: Soulseek AND-matches every query
        # word, so "Radio Edit"/"Mixed" exclude the files we actually want and
        # make an "extended mix" search impossible.
        ("Kill Me - Radio Edit", "Kill Me"),
        ("Blood, Sweat & Tears - Radio Edit", "Blood, Sweat & Tears"),
        ("River of Souls (Mixed)", "River of Souls"),
        ("Brutal 3.0 (Mixed)", "Brutal 3.0"),
        ("Strobe - Original Mix", "Strobe"),
        ("Song (Extended Mix)", "Song"),
        ("Track - Radio Version", "Track"),
        # remix / live are genuinely different recordings -> KEPT.
        ("Levels (Skrillex Remix)", "Levels (Skrillex Remix)"),
        ("Song - Live", "Song - Live"),
        # A non-version parenthetical subtitle is KEPT (conservative).
        ("Funfair (The Official 2015 Anthem)", "Funfair (The Official 2015 Anthem)"),
        # Multiple trailing qualifiers are all stripped.
        ("Anthem (Mixed) - Radio Edit", "Anthem"),
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


def test_track_version_property() -> None:
    from spotiseek.version import VersionKind

    assert Track(title="Song (Extended Mix)").version.kind is VersionKind.LONG
    assert Track(title="Plain").version.kind is VersionKind.STANDARD
    # Cached: repeated access returns the same object.
    t = Track(title="Song (Radio Edit)")
    assert t.version is t.version
