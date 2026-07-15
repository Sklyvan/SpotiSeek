"""Command-line interface for SpotiSeek."""

from __future__ import annotations

import asyncio
import logging
import sys

import click

from . import __version__
from .config import Config
from .errors import SpotiSeekError
from .logging_setup import configure_logging
from .models import DownloadStatus, MatchStrictness
from .spotify.parser import parse_spotify_url
from .spotify.provider import fetch_tracks

# NOTE: `.downloader` (which pulls in the heavy aioslsk stack, ~250 ms) is
# imported lazily inside the download command so that `info`, `--help` and
# `--version` start fast and don't need the Soulseek client at all.

logger = logging.getLogger("spotiseek")

_MATCH_CHOICES = click.Choice([m.value for m in MatchStrictness])
_LEVEL_CHOICES = click.Choice(
    ["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False
)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="spotiseek")
def main() -> None:
    """SpotiSeek — download Spotify tracks/albums/playlists from Soulseek."""


@main.command()
@click.argument("url")
@click.option(
    "-o", "--output", "output", default=None,
    help="Directory to save downloaded tracks into "
    "(default: your Downloads folder).",
)
@click.option(
    "-p", "--parallel", type=click.IntRange(min=1), default=3, show_default=True,
    help="Number of concurrent downloads (1 = sequential).",
)
@click.option(
    "--match", "match", type=_MATCH_CHOICES, default=MatchStrictness.BALANCED.value,
    show_default=True, help="How strictly to match Soulseek results.",
)
@click.option(
    "--search-timeout", type=float, default=15.0, show_default=True,
    help="Seconds to gather Soulseek search results per track.",
)
@click.option(
    "--min-bitrate", type=int, default=None,
    help="Reject lossy files below this bitrate (kbps).",
)
@click.option("--no-tag", is_flag=True, help="Do not write tags or embed cover art.")
@click.option(
    "--extended-mix", is_flag=True,
    help="Prefer the official '(Extended Mix)' version (remixes/edits are "
    "ignored); fall back to the standard one if none is found. Downloaded "
    "extended mixes are named '... (Extended Mix)'.",
)
@click.option(
    "--prefer-longest", is_flag=True,
    help="Prefer the longest matching version (e.g. the full/extended cut) "
    "instead of the one matching Spotify's duration. Shorter previews/radio "
    "edits are still rejected; whole-album mixes are guarded against.",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Search and match only; report picks without downloading.",
)
@click.option(
    "--fallback", is_flag=True,
    help="When Soulseek can't deliver a track, fall back to fetching lossless "
    "audio from streaming-service proxies (Tidal/Deezer/Amazon/Qobuz via Odesli). "
    "Requires SPOTISEEK_<PROVIDER>_API_URL to point at a working proxy.",
)
@click.option(
    "--fallback-providers", default=None,
    help="Comma-separated provider order for --fallback "
    "(default: tidal,deezer,amazon,qobuz).",
)
@click.option("--slsk-user", default=None, help="Override Soulseek username.")
@click.option("--slsk-pass", default=None, help="Override Soulseek password.")
@click.option("--log-level", type=_LEVEL_CHOICES, default=None, help="Set log level.")
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v = DEBUG).")
def download(
    url: str,
    output: str,
    parallel: int,
    match: str,
    search_timeout: float,
    min_bitrate: int | None,
    no_tag: bool,
    extended_mix: bool,
    prefer_longest: bool,
    dry_run: bool,
    fallback: bool,
    fallback_providers: str | None,
    slsk_user: str | None,
    slsk_pass: str | None,
    log_level: str | None,
    verbose: int,
) -> None:
    """Download every track in the given Spotify URL from Soulseek."""
    from .downloader import run_download  # heavy import; only needed to download

    configure_logging(log_level, verbose)
    providers = (
        [p.strip() for p in fallback_providers.split(",") if p.strip()]
        if fallback_providers
        else None
    )
    config = Config.load(
        output_dir=output,
        parallel=parallel,
        match_strictness=match,
        search_timeout=search_timeout,
        min_bitrate=min_bitrate,
        tag=not no_tag,
        dry_run=dry_run,
        extended_mix=extended_mix,
        prefer_longest=prefer_longest,
        fallback=fallback,
        fallback_providers=providers,
        soulseek_username=slsk_user,
        soulseek_password=slsk_pass,
    )

    try:
        results = asyncio.run(run_download(config, url))
    except SpotiSeekError as exc:
        raise click.ClickException(str(exc)) from exc
    except KeyboardInterrupt:  # pragma: no cover
        click.echo("Interrupted.", err=True)
        sys.exit(130)

    # Non-zero exit if nothing succeeded (helps scripting), but not on dry-run.
    if not dry_run:
        succeeded = any(r.status == DownloadStatus.DOWNLOADED for r in results)
        if results and not succeeded:
            sys.exit(1)


@main.command()
@click.argument("url")
@click.option("--log-level", type=_LEVEL_CHOICES, default="WARNING", help="Set log level.")
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v = DEBUG).")
def info(url: str, log_level: str | None, verbose: int) -> None:
    """Print the resolved track metadata for a Spotify URL (no download)."""
    configure_logging(log_level, verbose)
    config = Config.load()
    try:
        kind, spotify_id = parse_spotify_url(url)
        tracks, source = fetch_tracks(config, kind, spotify_id)
    except SpotiSeekError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"{kind.value} {spotify_id}  ({len(tracks)} track(s), source: {source.value})")
    for i, t in enumerate(tracks, start=1):
        dur = f"{int(t.duration_s // 60)}:{int(t.duration_s % 60):02d}" if t.duration_s else "?"
        album = f"  [{t.album}]" if t.album else ""
        click.echo(f"  {i:>3}. {t.display}  ({dur}){album}")


def gui() -> None:
    """Console entry point for the desktop GUI (``spotiseek-gui``).

    PySide6 is imported lazily so that CLI-only installs don't need it; if it is
    missing we print how to install the optional ``gui`` extra.
    """
    try:
        from .gui import run_gui
    except ImportError:
        click.echo(
            "The SpotiSeek GUI requires PySide6, which is not installed.\n"
            "Install it with:\n"
            "  uv sync --extra gui\n"
            "  # or: pip install 'spotiseek[gui]'",
            err=True,
        )
        raise SystemExit(1)
    run_gui()


if __name__ == "__main__":  # pragma: no cover
    main()
