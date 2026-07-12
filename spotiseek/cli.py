"""Command-line interface for SpotiSeek."""

from __future__ import annotations

import asyncio
import logging
import sys

import click

from . import __version__
from .config import Config
from .downloader import run_download
from .errors import SpotiSeekError
from .logging_setup import configure_logging
from .models import DownloadStatus, MatchStrictness
from .spotify.parser import parse_spotify_url
from .spotify.provider import fetch_tracks

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
    "-o", "--output", "output", default="downloads", show_default=True,
    help="Directory to save downloaded tracks into.",
)
@click.option(
    "-p", "--parallel", type=click.IntRange(min=1), default=1, show_default=True,
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
    help="Prefer the '(Extended Mix)' version; fall back to the standard one if "
    "not found. Downloaded extended mixes are named '... (Extended Mix)'.",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Search and match only; report picks without downloading.",
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
    dry_run: bool,
    slsk_user: str | None,
    slsk_pass: str | None,
    log_level: str | None,
    verbose: int,
) -> None:
    """Download every track in the given Spotify URL from Soulseek."""
    configure_logging(log_level, verbose)
    config = Config.load(
        output_dir=output,
        parallel=parallel,
        match_strictness=match,
        search_timeout=search_timeout,
        min_bitrate=min_bitrate,
        tag=not no_tag,
        dry_run=dry_run,
        extended_mix=extended_mix,
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


if __name__ == "__main__":  # pragma: no cover
    main()
