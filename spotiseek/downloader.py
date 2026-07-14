"""Orchestrates the full pipeline: metadata -> search -> match -> download -> tag."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import re
import shutil

from .config import Config
from .errors import ConfigError, DownloadError
from .fallback import FallbackSource
from .models import (
    Candidate,
    DownloadResult,
    DownloadStatus,
    Track,
)
from .soulseek.client import SoulseekClient
from .soulseek.matcher import has_ready_lossless_match, score_candidates
from .spotify.parser import parse_spotify_url
from .spotify.provider import fetch_tracks
from . import tagging

logger = logging.getLogger(__name__)

#: How many ranked candidates to try before giving up on a track.
MAX_DOWNLOAD_ATTEMPTS = 5
#: Per-file transfer timeout (seconds).
DOWNLOAD_TIMEOUT = 300.0
_INCOMING_DIRNAME = ".incoming"
#: Soulseek outcomes that warrant trying the lossless fallback source.
_FALLBACK_STATUSES = frozenset(
    {
        DownloadStatus.SKIPPED_NO_RESULTS,
        DownloadStatus.SKIPPED_NO_MATCH,
        DownloadStatus.FAILED,
    }
)


def _safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name[:200] or "track"


EXTENDED_SUFFIX = "(Extended Mix)"


def _extended_title(track: Track) -> str:
    return f"{track.title} {EXTENDED_SUFFIX}"


def _target_basename(track: Track, extended: bool = False) -> str:
    artist = track.primary_artist or "Unknown Artist"
    title = _extended_title(track) if extended else (track.title or "Unknown Title")
    return _safe_filename(f"{artist} - {title}")


def _existing_download(output_dir: str, stem: str) -> str | None:
    """Return the path of an already-downloaded file with this stem, if any."""
    if not os.path.isdir(output_dir):
        return None
    for entry in os.listdir(output_dir):
        root, _ = os.path.splitext(entry)
        if root == stem:
            return os.path.join(output_dir, entry)
    return None


class Downloader:
    """Runs the download pipeline for a single Spotify URL.

    Optional callbacks let a front-end (e.g. the GUI) report progress:
      * ``on_start(total)`` is called once the track list is resolved.
      * ``on_track_done(result)`` is called as each track finishes.
    Both are invoked from the download's event loop; a GUI must marshal them
    back onto its own thread. Exceptions raised by a callback are swallowed.
    """

    def __init__(
        self,
        config: Config,
        on_start=None,
        on_track_done=None,
    ) -> None:
        self.config = config
        self.output_dir = os.fspath(config.output_dir)
        self.incoming_dir = os.path.join(self.output_dir, _INCOMING_DIRNAME)
        self._on_start = on_start
        self._on_track_done = on_track_done

    def _notify(self, callback, *args) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:  # a front-end callback must never break a run
            logger.debug("Progress callback raised (ignored): %s", exc)

    async def run(self, url: str) -> list[DownloadResult]:
        if not self.config.has_soulseek_credentials:
            raise ConfigError(
                "Soulseek credentials are required to download. Set your "
                "username and password (in the GUI's Settings, via --slsk-user/"
                "--slsk-pass, or SOULSEEK_USERNAME/SOULSEEK_PASSWORD in .env)."
            )
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
        self._notify(self._on_start, len(tracks))

        async with SoulseekClient(
            self.config.soulseek_username,
            self.config.soulseek_password,
            self.incoming_dir,
        ) as client:
            semaphore = asyncio.Semaphore(self.config.parallel)

            async def worker(index: int, track: Track) -> DownloadResult:
                async with semaphore:
                    try:
                        result = await self._process_track(
                            client, index, len(tracks), track
                        )
                    except Exception as exc:
                        # One track's unexpected failure must never abort the run.
                        logger.warning(
                            "[%d/%d] %s — unexpected error: %s",
                            index, len(tracks), track.display, exc,
                        )
                        result = DownloadResult(
                            track, DownloadStatus.FAILED, error=str(exc)
                        )
                    self._notify(self._on_track_done, result)
                    return result

            results = await asyncio.gather(
                *(worker(i, t) for i, t in enumerate(tracks, start=1))
            )

        self._cleanup_incoming()
        self._log_summary(results)
        return list(results)

    def _early_stop(self, track: Track, require_extended: bool):
        """Predicate that lets a search return early once a strong match arrives.

        We stop as soon as an acceptable **lossless** candidate with a free
        upload slot is available — that is the ideal outcome, so there is no
        point waiting out the full search timeout for it. Uses a cheap
        short-circuiting check (not a full ranked scoring pass) so it is safe to
        call repeatedly while the search is still collecting results.
        """

        def predicate(candidates: list[Candidate]) -> bool:
            return has_ready_lossless_match(
                track,
                candidates,
                self.config.match_strictness,
                require_extended=require_extended,
            )

        return predicate

    async def _process_track(
        self,
        client: SoulseekClient,
        index: int,
        total: int,
        track: Track,
    ) -> DownloadResult:
        prefix = f"[{index}/{total}] {track.display}"

        # When --extended-mix is on, try to find the Extended Mix first; if none
        # is available, fall through to the standard version.
        if self.config.extended_mix:
            extended_result = await self._try_extended(client, prefix, track)
            if extended_result is not None:
                return extended_result

        result = await self._try_standard(client, prefix, track)
        if self.config.fallback and result.status in _FALLBACK_STATUSES:
            if self.config.dry_run:
                await self._report_fallback(prefix, track)
            else:
                return await self._try_fallback(prefix, track, result)
        return result

    async def _try_fallback(
        self, prefix: str, track: Track, soulseek_result: DownloadResult
    ) -> DownloadResult:
        """Download ``track`` from a lossless streaming-service proxy.

        Returns the original Soulseek result unchanged if the fallback can't
        deliver — a fallback failure must never look worse than a plain skip.
        """
        logger.info("%s — Soulseek came up empty; trying lossless fallback...", prefix)
        source = FallbackSource(self.config)
        try:
            outcome = await asyncio.to_thread(
                source.download, track, self.incoming_dir
            )
        except Exception as exc:  # the fallback must never break a run
            logger.warning("%s — fallback source error: %s", prefix, exc)
            return soulseek_result
        if outcome is None:
            return soulseek_result

        final_path = self._place_file(outcome.path, track, outcome.extension)
        if self.config.tag:
            await asyncio.to_thread(tagging.tag_file, final_path, track, True)
        logger.info(
            "%s — saved via %s fallback to %s", prefix, outcome.provider, final_path
        )
        return DownloadResult(
            track,
            DownloadStatus.DOWNLOADED,
            path=final_path,
            source=outcome.provider,
        )

    async def _report_fallback(self, prefix: str, track: Track) -> None:
        """Dry-run: report which fallback providers *would* have the track."""
        source = FallbackSource(self.config)
        try:
            resolved = await asyncio.to_thread(source.resolve, track)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("%s — fallback resolve error: %s", prefix, exc)
            return
        if resolved is None:
            logger.info("%s — fallback: not found on any platform.", prefix)
            return
        available = source.available_providers(resolved)
        if available:
            logger.info("%s — fallback would try: %s.", prefix, ", ".join(available))
        else:
            logger.info(
                "%s — fallback: resolved, but no configured provider has it "
                "(set SPOTISEEK_<PROVIDER>_API_URL).",
                prefix,
            )

    async def _try_extended(
        self, client: SoulseekClient, prefix: str, track: Track
    ) -> DownloadResult | None:
        """Attempt the Extended Mix. Returns a result, or None to fall back."""
        if not self.config.dry_run:
            existing = _existing_download(
                self.output_dir, _target_basename(track, extended=True)
            )
            if existing:
                logger.info("%s — Extended Mix already present, skipping.", prefix)
                return DownloadResult(
                    track, DownloadStatus.DOWNLOADED, path=existing, extended=True
                )

        logger.info("%s — searching for Extended Mix...", prefix)
        query = f"{track.search_query} extended mix"
        candidates = await client.search(
            query,
            self.config.search_timeout,
            stop_when=self._early_stop(track, require_extended=True),
        )
        ranked = score_candidates(
            track,
            candidates,
            self.config.match_strictness,
            self.config.min_bitrate,
            require_extended=True,
        )
        if not ranked:
            logger.info(
                "%s — no Extended Mix found; downloading the standard version instead.",
                prefix,
            )
            return None

        best = ranked[0]
        if self.config.dry_run:
            logger.info(
                "%s — would download Extended Mix: %s (%s, score %.1f, from %s)",
                prefix, best.basename, best.extension or "?", best.score, best.username,
            )
            return DownloadResult(
                track, DownloadStatus.DRY_RUN, candidate=best, extended=True
            )

        return await self._download_ranked(client, prefix, track, ranked, extended=True)

    async def _try_standard(
        self, client: SoulseekClient, prefix: str, track: Track
    ) -> DownloadResult:
        if not self.config.dry_run:
            existing = _existing_download(
                self.output_dir, _target_basename(track, extended=False)
            )
            if existing:
                logger.info("%s — already present, skipping.", prefix)
                return DownloadResult(track, DownloadStatus.DOWNLOADED, path=existing)

        logger.info("%s — searching...", prefix)
        candidates = await client.search(
            track.search_query,
            self.config.search_timeout,
            stop_when=self._early_stop(track, require_extended=False),
        )
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
        extended: bool = False,
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

            final_path = self._finalize(local_path, track, candidate, extended)
            if self.config.tag:
                # Keep tag title consistent with the (Extended Mix) filename.
                tag_track = (
                    dataclasses.replace(track, title=_extended_title(track))
                    if extended
                    else track
                )
                # Tagging does blocking I/O (cover download + mutagen writes);
                # run it off the event loop so parallel downloads aren't stalled.
                await asyncio.to_thread(tagging.tag_file, final_path, tag_track, True)
            logger.info("%s — saved to %s", prefix, final_path)
            return DownloadResult(
                track,
                DownloadStatus.DOWNLOADED,
                candidate=candidate,
                path=final_path,
                extended=extended,
            )

        logger.warning("%s — all download attempts failed.", prefix)
        return DownloadResult(track, DownloadStatus.FAILED, error=last_error)

    def _finalize(
        self, local_path: str, track: Track, candidate: Candidate, extended: bool = False
    ) -> str:
        """Move a completed Soulseek download to its final flat path."""
        ext = candidate.extension or (
            local_path.rsplit(".", 1)[-1].lower() if "." in local_path else "bin"
        )
        return self._place_file(local_path, track, ext, extended)

    def _place_file(
        self, local_path: str, track: Track, ext: str, extended: bool = False
    ) -> str:
        """Move a completed download to the flat '<Artist> - <Title>.<ext>' path.

        Adds the ' (Extended Mix)' suffix to the filename when ``extended``.
        """
        stem = _target_basename(track, extended=extended)
        dest = os.path.join(self.output_dir, f"{stem}.{ext}")
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
            notes: list[str] = []
            extended_count = sum(1 for r in downloaded if r.extended)
            if extended_count:
                notes.append(f"{extended_count} as Extended Mix")
            fallback_count = sum(1 for r in downloaded if r.source)
            if fallback_count:
                notes.append(f"{fallback_count} via fallback")
            extra = f" ({', '.join(notes)})" if notes else ""
            logger.info("Downloaded %d/%d track(s)%s.", len(downloaded), total, extra)
        if skipped:
            logger.info("Skipped %d track(s):", len(skipped))
            for r in skipped:
                logger.info("  - %s (%s)", r.track.display, r.status.value)


async def run_download(
    config: Config, url: str, on_start=None, on_track_done=None
) -> list[DownloadResult]:
    """Async entry point: process a Spotify URL end to end.

    ``on_start(total)`` and ``on_track_done(result)`` are optional progress
    callbacks (see :class:`Downloader`).
    """
    return await Downloader(config, on_start, on_track_done).run(url)
