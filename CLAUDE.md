# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What SpotiSeek is

A Python CLI + desktop GUI that reads a Spotify URL (track / album / playlist),
pulls the track metadata, then searches the **Soulseek** P2P network and
downloads the best match for each track, renaming and tagging the files
(`<Artist> - <Title>.<ext>`, flat layout) with embedded cover art. Package name
`spotiseek`, uv-managed, requires **Python 3.14+**.

For deep design details, see `DOCUMENTATION.md` (technical) and `README.md`
(user-facing). Keep both in sync when you change behavior.

## Commands

Everything runs through **uv** (do not use bare `pip`/`python` for dev tasks).

```bash
uv sync                         # install deps (dev group incl. PySide6 by default)
uv sync --extra gui --group build   # + GUI extra + PyInstaller build tools

uv run spotiseek download <spotify-url>   # CLI download
uv run spotiseek info <spotify-url>       # print resolved metadata, no download
uv run spotiseek-gui                      # launch the PySide6 desktop GUI

uv run pytest tests/unit        # offline unit tests (fast, no network)
uv run pytest --run-integration # ALSO run live tests (hit Spotify/Soulseek, download real files)
uv run pytest -m "not integration"  # explicitly deselect integration tests

uv run python scripts/build_executable.py   # build single-file PyInstaller binary into dist/
```

Notable `download` flags: `-o/--output` (default: OS Downloads folder),
`-p/--parallel N`, `--match {strict,balanced,loose}`, `--search-timeout`,
`--min-bitrate`, `--extended-mix`, `--no-tag`, `--dry-run`, `--slsk-user/--slsk-pass`.

## Architecture (package `spotiseek/`)

- `cli.py` — Click entry points: `main` (`download`, `info`) and `gui`.
- `config.py` — `Config` dataclass. Resolution order: CLI args > env/`.env` > defaults.
- `spotify/` — pluggable metadata layer over a common `base.py` interface:
  - `parser.py` parses track/album/playlist URLs & `spotify:` URIs.
  - `web_api.py` — official `spotipy` Web API path.
  - `embed.py` — credential-free public-embed scraper.
  - `provider.py` — tries Web API, then **automatically falls back to embed**.
- `soulseek/` — `client.py` wraps embedded `aioslsk` (no external daemon;
  connects to the network itself). `matcher.py` scores candidates: prefers
  lossless (FLAC/WAV) > MP3 320 > anything playable, checking artist/title and
  (when reported) duration.
- `tagging.py` — writes tags + embeds cover art via `mutagen` (MP3/FLAC/WAV/M4A/AIFF,
  with a universal fallback tagger).
- `downloader.py` — orchestration; sequential by default, `--parallel N` opt-in.
  A track that can't be found/downloaded logs a warning and is skipped (one
  failure never stops the rest).
- `gui.py` — optional PySide6 front-end over the same core; runs downloads on a
  QThread and persists credentials to `.env`. PySide6 imported lazily.
- `models.py`, `errors.py`, `logging_setup.py` — shared types, exceptions, logging.

## Important gotcha: Spotify Web API 403

The user's Spotify Developer app currently returns HTTP 403 *"Active premium
subscription required for the owner of the app"* on all Web API data endpoints
(token issues fine, account is gated). So the **embed fallback is the de-facto
working metadata path**. When the account becomes Premium the Web API path
activates automatically — no code change needed. Don't "fix" the fallback away.

## Configuration & secrets

Secrets live in a **gitignored `.env`** (`SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`,
`SOULSEEK_USERNAME`, `SOULSEEK_PASSWORD`); see `.env.example`. Never commit `.env`,
credentials, or downloaded audio (`downloads/` is gitignored). Do not hard-code
default Soulseek credentials in source — a past commit explicitly removed shipped
defaults.

## Testing

- Unit tests are offline and should stay that way; use fixtures in
  `tests/fixtures/` (embed HTML + sample audio files).
- Integration tests are marked `@pytest.mark.integration`, skipped unless
  `--run-integration`, and require a populated `.env`. **You may run them freely**
  when useful — they hit the live network and download real files.
- GUI tests require a headless Qt platform: run with `QT_QPA_PLATFORM=offscreen`.
- `asyncio_mode = "auto"` is set, so `async def test_*` works without decorators.

## Conventions

- **Match the existing style** — no linter/formatter/type-checker is configured.
  Mirror surrounding code: `from __future__ import annotations`, full type hints,
  concise docstrings, Click for CLI.
- Add tests alongside behavior changes; update `README.md` and `DOCUMENTATION.md`
  when user-facing or architectural behavior changes.

## Git

- **Commit when asked, but NEVER push.** The user handles all pushing and tagging
  manually. (The `.github/workflows/build.yml` CI builds executables on `v*` tag
  pushes — those tags are pushed by the user, not by Claude.)
- **Use Conventional Commits style** for commit messages
  (`type(scope): summary`, e.g. `feat: add --extended-mix mode`,
  `fix(matcher): reject lossy files below min-bitrate`, `docs:`, `test:`,
  `refactor:`, `chore:`, `ci:`).