"""Orchestrates the full pipeline: metadata -> search -> match -> download -> tag."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil

from .config import Config
from .errors import DownloadError
from .models import (
    Candidate,
    DownloadResult,
    DownloadStatus,
    Track,
)
from .soulseek.client import SoulseekClient
from .soulseek.matcher import score_candidates
from .spotify.parser import parse_spotify_url
from .spotify.provider import fetch_tracks
from . import tagging

logger = logging.getLogger(__name__)

#: How many ranked candidates to try before giving up on a track.
MAX_DOWNLOAD_ATTEMPTS = 5
#: Per-file transfer timeout (seconds).
DOWNLOAD_TIMEOUT = 300.0
_INCOMING_DIRNAME = ".incoming"


def _safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name[:200] or "track"


def _target_basename(track: Track) -> str:
    artist = track.primary_artist or "Unknown Artist"
    title = track.title or "Unknown Title"
    return _safe_filename(f"{artist} - {title}")


def _existing_download(output_dir: str, track: Track) -> str | None:
    """Return the path of an already-downloaded file for this track, if any."""
    stem = _target_basename(track)
    if not os.path.isdir(output_dir):
        return None
    for entry in os.listdir(output_dir):
        root, _ = os.path.splitext(entry)
        if root == stem:
            return os.path.join(output_dir, entry)
    return None


class Downloader:
    """Runs the download pipeline for a single Spotify URL."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.output_dir = os.fspath(config.output_dir)
        self.incoming_dir = os.path.join(self.output_dir, _INCOMING_DIRNAME)

    async def run(self, url: str) -> list[DownloadResult]:
        kind, spotify_id = parse_spotify_url(url)
        tracks, source = fetch_tracks(self.config, kind, spotify_id)
        logger.info(
            "Resolved %s %s: %d track(s) [%s]",
            kind.value,
            spotify_id,
            len(tracks),
            source.value,
        )

        os.makedirs(self.output_dir, exist_ok=True)

        async with SoulseekClient(
            self.config.soulseek_username,
            self.config.soulseek_password,
            self.incoming_dir,
        ) as client:
            semaphore = asyncio.Semaphore(self.config.parallel)

            async def worker(index: int, track: Track) -> DownloadResult:
                async with semaphore:
                    return await self._process_track(client, index, len(tracks), track)

            results = await asyncio.gather(
                *(worker(i, t) for i, t in enumerate(tracks, start=1))
            )

        self._cleanup_incoming()
        self._log_summary(results)
        return list(results)

    async def _process_track(
        self,
        client: SoulseekClient,
        index: int,
        total: int,
        track: Track,
    ) -> DownloadResult:
        prefix = f"[{index}/{total}] {track.display}"

        existing = _existing_download(self.output_dir, track)
        if existing and not self.config.dry_run:
            logger.info("%s — already present, skipping.", prefix)
            return DownloadResult(track, DownloadStatus.DOWNLOADED, path=existing)

        logger.info("%s — searching...", prefix)
        candidates = await client.search(track.search_query, self.config.search_timeout)
        if not candidates:
            logger.warning("%s — no Soulseek results.", prefix)
            return DownloadResult(track, DownloadStatus.SKIPPED_NO_RESULTS)

        ranked = score_candidates(
            track,
            candidates,
            self.config.match_strictness,
            self.config.min_bitrate,
        )
        if not ranked:
            logger.warning(
                "%s — %d results but none matched (strictness=%s).",
                prefix,
                len(candidates),
                self.config.match_strictness.value,
            )
            return DownloadResult(track, DownloadStatus.SKIPPED_NO_MATCH)

        best = ranked[0]
        if self.config.dry_run:
            logger.info(
                "%s — would download: %s (%s, score %.1f, from %s)",
                prefix,
                best.basename,
                best.extension or "?",
                best.score,
                best.username,
            )
            return DownloadResult(track, DownloadStatus.DRY_RUN, candidate=best)

        return await self._download_ranked(client, prefix, track, ranked)

    async def _download_ranked(
        self,
        client: SoulseekClient,
        prefix: str,
        track: Track,
        ranked: list[Candidate],
    ) -> DownloadResult:
        last_error: str | None = None
        for attempt, candidate in enumerate(ranked[:MAX_DOWNLOAD_ATTEMPTS], start=1):
            logger.info(
                "%s — downloading %s (%s, score %.1f) from %s [try %d]",
                prefix,
                candidate.basename,
                candidate.extension or "?",
                candidate.score,
                candidate.username,
                attempt,
            )
            try:
                local_path = await client.download(candidate, DOWNLOAD_TIMEOUT)
            except DownloadError as exc:
                last_error = str(exc)
                logger.warning("%s — attempt %d failed: %s", prefix, attempt, exc)
                continue

            final_path = self._finalize(local_path, track, candidate)
            if self.config.tag:
                tagging.tag_file(final_path, track, embed_art=True)
            logger.info("%s — saved to %s", prefix, final_path)
            return DownloadResult(
                track, DownloadStatus.DOWNLOADED, candidate=candidate, path=final_path
            )

        logger.warning("%s — all download attempts failed.", prefix)
        return DownloadResult(track, DownloadStatus.FAILED, error=last_error)

    def _finalize(self, local_path: str, track: Track, candidate: Candidate) -> str:
        """Move a completed download to the flat '<Artist> - <Title>.<ext>' path."""
        ext = candidate.extension or (
            local_path.rsplit(".", 1)[-1].lower() if "." in local_path else "bin"
        )
        dest = os.path.join(self.output_dir, f"{_target_basename(track)}.{ext}")
        os.makedirs(self.output_dir, exist_ok=True)
        if os.path.abspath(local_path) != os.path.abspath(dest):
            shutil.move(local_path, dest)
        return dest

    def _cleanup_incoming(self) -> None:
        """Remove the incoming scratch directory if it is empty."""
        try:
            if os.path.isdir(self.incoming_dir):
                shutil.rmtree(self.incoming_dir, ignore_errors=True)
        except OSError as exc:  # pragma: no cover
            logger.debug("Could not remove incoming dir: %s", exc)

    def _log_summary(self, results: list[DownloadResult]) -> None:
        downloaded = [r for r in results if r.status == DownloadStatus.DOWNLOADED]
        dry = [r for r in results if r.status == DownloadStatus.DRY_RUN]
        skipped = [
            r
            for r in results
            if r.status
            in (
                DownloadStatus.SKIPPED_NO_RESULTS,
                DownloadStatus.SKIPPED_NO_MATCH,
                DownloadStatus.FAILED,
            )
        ]
        total = len(results)
        if dry:
            logger.info("Dry run: %d/%d track(s) would be downloaded.", len(dry), total)
        else:
            logger.info("Downloaded %d/%d track(s).", len(downloaded), total)
        if skipped:
            logger.info("Skipped %d track(s):", len(skipped))
            for r in skipped:
                logger.info("  - %s (%s)", r.track.display, r.status.value)


async def run_download(config: Config, url: str) -> list[DownloadResult]:
    """Async entry point: process a Spotify URL end to end."""
    return await Downloader(config).run(url)
