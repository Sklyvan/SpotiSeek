"""Unit tests for the version-qualifier classifier (spotiseek/version.py)."""

from __future__ import annotations

import pytest

from spotiseek.version import (
    ExtendedPlan,
    Identity,
    Length,
    VersionKind,
    apply_qualifier,
    classify,
    plan_extended,
    strip_for_search,
)


# --------------------------------------------------------------------------- #
# strip_for_search — must stay byte-identical to the former models._search_title
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "title, expected",
    [
        ("Bohemian Rhapsody - Remastered 2011", "Bohemian Rhapsody"),
        ("Money - 2011 Remaster", "Money"),
        ("SICKO MODE (feat. Drake)", "SICKO MODE"),
        ("One Dance (feat. Wizkid & Kyla)", "One Dance"),
        ("Plain Title", "Plain Title"),
        ("Imagine - Mono Version", "Imagine - Mono Version"),
        ("Song (Live with Strings)", "Song (Live with Strings)"),
        ("Kill Me - Radio Edit", "Kill Me"),
        ("Blood, Sweat & Tears - Radio Edit", "Blood, Sweat & Tears"),
        ("River of Souls (Mixed)", "River of Souls"),
        ("Brutal 3.0 (Mixed)", "Brutal 3.0"),
        ("Strobe - Original Mix", "Strobe"),
        ("Song (Extended Mix)", "Song"),
        ("Track - Radio Version", "Track"),
        ("Levels (Skrillex Remix)", "Levels (Skrillex Remix)"),
        ("Song - Live", "Song - Live"),
        ("Funfair (The Official 2015 Anthem)", "Funfair (The Official 2015 Anthem)"),
        ("Anthem (Mixed) - Radio Edit", "Anthem"),
    ],
)
def test_strip_for_search_parity(title, expected) -> None:
    assert strip_for_search(title) == expected


def test_strip_for_search_never_empty() -> None:
    assert strip_for_search("(feat. Someone)").strip() != ""


# --------------------------------------------------------------------------- #
# classify — identity / length / base_title
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "title, identity, length, base",
    [
        # STANDARD / neutral
        ("Plain Title", Identity.ORIGINAL, Length.NEUTRAL, "Plain Title"),
        ("", Identity.ORIGINAL, Length.NEUTRAL, ""),
        ("Funfair (The Official 2015 Anthem)", Identity.ORIGINAL, Length.NEUTRAL,
         "Funfair (The Official 2015 Anthem)"),
        ("Radio Edit", Identity.ORIGINAL, Length.NEUTRAL, "Radio Edit"),  # would-empty guard
        # SHORT
        ("Oxygen - Radio Edit", Identity.ORIGINAL, Length.SHORT, "Oxygen"),
        ("Song (Edit)", Identity.OTHER, Length.SHORT, "Song"),
        ("Song - Single Edit", Identity.ORIGINAL, Length.SHORT, "Song"),
        ("Song (Video Edit)", Identity.ORIGINAL, Length.SHORT, "Song"),
        ("Song (Mix Cut)", Identity.ORIGINAL, Length.SHORT, "Song"),
        ("Song - Album Edit", Identity.ORIGINAL, Length.SHORT, "Song"),
        # LONG
        ("Song (Extended Mix)", Identity.ORIGINAL, Length.LONG, "Song"),
        ("Strobe - Original Mix", Identity.ORIGINAL, Length.LONG, "Strobe"),
        ("Song (Club Mix)", Identity.ORIGINAL, Length.LONG, "Song"),
        ("Song - Album Version", Identity.ORIGINAL, Length.LONG, "Song"),
        ("Song [Extended Mix]", Identity.ORIGINAL, Length.LONG, "Song"),
        # DERIVATIVE
        ("Levels (Skrillex Remix)", Identity.REMIX, Length.NEUTRAL, "Levels"),
        ("Song (VIP)", Identity.VIP, Length.NEUTRAL, "Song"),
        ("Song (Bootleg)", Identity.BOOTLEG, Length.NEUTRAL, "Song"),
        ("Song (Mashup)", Identity.MASHUP, Length.NEUTRAL, "Song"),
        ("Song - Live", Identity.LIVE, Length.NEUTRAL, "Song"),
        ("Song (Acoustic)", Identity.ACOUSTIC, Length.NEUTRAL, "Song"),
        ("Song - Instrumental", Identity.INSTRUMENTAL, Length.NEUTRAL, "Song"),
        ("Song (Nightcore)", Identity.NIGHTCORE, Length.NEUTRAL, "Song"),
        ("Toxic (Slowed + Reverb)", Identity.SPEEDMOD, Length.NEUTRAL, "Toxic"),
        ("Wonderwall - 2011 Remaster", Identity.REMASTER, Length.NEUTRAL, "Wonderwall"),
        ("Song (Extended Remix)", Identity.REMIX, Length.LONG, "Song"),
        # STYLE-EDIT
        ("Imaginary (Uptempo Edit)", Identity.STYLE_EDIT, Length.NEUTRAL, "Imaginary"),
        ("Song (Festival Edit)", Identity.STYLE_EDIT, Length.NEUTRAL, "Song"),
        ("Song (Big Room Edit)", Identity.STYLE_EDIT, Length.NEUTRAL, "Song"),
        ("Song (Hardstyle Edit)", Identity.STYLE_EDIT, Length.NEUTRAL, "Song"),
        ("Song (Club Edit)", Identity.STYLE_EDIT, Length.NEUTRAL, "Song"),
        # feat guard stays on base
        ("SICKO MODE (feat. Drake) - Radio Edit", Identity.ORIGINAL, Length.SHORT,
         "SICKO MODE (feat. Drake)"),
    ],
)
def test_classify(title, identity, length, base) -> None:
    info = classify(title)
    assert info.identity is identity, f"{title}: identity"
    assert info.length is length, f"{title}: length"
    assert info.base_title == base, f"{title}: base_title"


def test_classify_named_cases_kind() -> None:
    assert classify("Oxygen - Radio Edit").kind is VersionKind.SHORT
    assert classify("Imaginary (Uptempo Edit)").kind is VersionKind.STYLE_EDIT
    assert classify("Plain").kind is VersionKind.STANDARD
    assert classify("Song (Extended Mix)").kind is VersionKind.LONG
    assert classify("Levels (Skrillex Remix)").kind is VersionKind.DERIVATIVE


def test_classify_artist_edit_vs_genre_edit() -> None:
    # A known artist before "Edit" -> a derivative re-edit, not a genre edit.
    info = classify("Scream (Da Tweekaz Edit)", frozenset({"Da Tweekaz"}))
    assert info.identity is Identity.REMIX
    # A genre word -> STYLE-EDIT.
    assert classify("Scream (Hardstyle Edit)").identity is Identity.STYLE_EDIT


def test_classify_vip_tokens() -> None:
    info = classify("Song (VIP)")
    assert "vip" in info.tokens
    assert info.is_restrictive


def test_classify_unicode_preserved_in_base() -> None:
    info = classify("Été (Rework)")
    assert info.base_title == "Été"
    assert info.identity is Identity.REMIX


# --------------------------------------------------------------------------- #
# apply_qualifier
# --------------------------------------------------------------------------- #
def test_apply_qualifier() -> None:
    assert apply_qualifier("Oxygen") == "Oxygen (Extended Mix)"
    # Named case: SHORT stripped, not doubled.
    assert apply_qualifier("Oxygen - Radio Edit") == "Oxygen (Extended Mix)"
    # Already long -> unchanged (double-suffix guard).
    assert apply_qualifier("Song (Extended Mix)") == "Song (Extended Mix)"
    assert apply_qualifier("Song (Extended Remix)") == "Song (Extended Remix)"
    # STYLE-EDIT preserved (the qualifier stays; no fabricated extended).
    assert "Uptempo Edit" in apply_qualifier("Imaginary (Uptempo Edit)")


def test_apply_qualifier_idempotent() -> None:
    once = apply_qualifier("Oxygen")
    assert apply_qualifier(once) == once


# --------------------------------------------------------------------------- #
# plan_extended
# --------------------------------------------------------------------------- #
def test_plan_extended_short() -> None:
    plan = plan_extended(classify("Oxygen - Radio Edit"))
    assert plan.output_suffix == "(Extended Mix)"
    assert plan.skip_note is None
    assert "Oxygen (Extended Mix)" in plan.search_titles
    assert "Oxygen" in plan.search_titles  # base fallback


def test_plan_extended_already_long() -> None:
    plan = plan_extended(classify("Song (Extended Mix)"))
    assert plan.output_suffix is None
    assert plan.skip_note is not None


def test_plan_extended_style_edit_preserved() -> None:
    plan = plan_extended(classify("Imaginary (Uptempo Edit)"))
    assert plan.output_suffix is None
    assert any("Uptempo Edit" in t for t in plan.search_titles)


def test_plan_extended_plain() -> None:
    plan = plan_extended(classify("Anthem"))
    assert plan.output_suffix == "(Extended Mix)"
    assert "Anthem (Extended Mix)" in plan.search_titles
    assert "Anthem" in plan.search_titles


def test_bare_edit_pursues_extended() -> None:
    # A bare short "- Edit" is stripped and pursues the Extended Mix (with a
    # base fallback) — what --extended-mix asks for.
    assert apply_qualifier("Encore - Edit") == "Encore (Extended Mix)"
    plan = plan_extended(classify("Encore - Edit"))
    assert plan.output_suffix == "(Extended Mix)"
    assert "Encore (Extended Mix)" in plan.search_titles
    assert "Encore" in plan.search_titles


def test_ambiguous_word_edit_preserved() -> None:
    # An unknown "<word> Edit" (no known artist, not a genre) stays put — we
    # don't fabricate an extended mix for something we can't place.
    assert apply_qualifier("Song (Kaskade Edit)") == "Song (Kaskade Edit)"
    assert plan_extended(classify("Song (Kaskade Edit)")).output_suffix is None
