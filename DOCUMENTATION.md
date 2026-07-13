# 📖 SpotiSeek — Technical Documentation

This document covers SpotiSeek's architecture, the design decisions behind it,
the matching heuristic, and its known limitations. 🧠 For a user-facing overview
and command reference, see [README.md](README.md).

---

## 1. 🔭 Overview

SpotiSeek is a Python package that bridges two systems:

1. 🎼 **Spotify** — the source of truth for *what* to download (track metadata).
2. 🌐 **Soulseek** — the peer-to-peer network we download the actual audio from.

The pipeline for a given Spotify URL is:

```
parse URL ─▶ fetch metadata ─▶ per track: search ─▶ match/rank ─▶ download ─▶ tag
                                                        ▲ (retry next peer on failure)
```

Everything runs in a single process on `asyncio`. ⚙️ There is no external daemon.

---

## 2. 🗂️ Package layout

```
spotiseek/
  cli.py            # click entry point: `download` and `info` commands
  config.py         # Config dataclass; flags > env/.env > defaults
  logging_setup.py  # leveled logging; silences peer-connection churn
  models.py         # Track, Candidate, DownloadResult + enums
  errors.py         # exception hierarchy (incl. PremiumGateError)
  spotify/
    parser.py       # Spotify URL/URI -> (kind, id)
    base.py         # MetadataProvider ABC
    web_api.py      # SpotipyProvider (official Web API)
    embed.py        # EmbedProvider (credential-free public data)
    provider.py     # resolve/fetch with automatic fallback
  soulseek/
    client.py       # SoulseekClient: aioslsk wrapper (login/search/download)
    matcher.py      # pure scoring & ranking of candidates
  tagging.py        # mutagen tag writing + cover-art embedding
  downloader.py     # orchestrator tying it all together
  gui.py            # optional PySide6 desktop front-end
```

### 🧩 Key dependencies

| Library | Role |
|---|---|
| [`aioslsk`](https://github.com/JurgenR/aioslsk) | Pure-Python asyncio Soulseek protocol client |
| [`spotipy`](https://spotipy.readthedocs.io/) | Official Spotify Web API client |
| [`mutagen`](https://mutagen.readthedocs.io/) | Reading/writing audio tags & artwork |
| [`rapidfuzz`](https://github.com/rapidfuzz/RapidFuzz) | Fast fuzzy string matching |
| [`click`](https://click.palletsprojects.com/) | CLI framework |
| `requests` / `python-dotenv` | Cover-art download / `.env` loading |

---

## 3. 🎼 Metadata layer

### 3.1 🔗 URL parsing (`spotify/parser.py`)

`parse_spotify_url()` accepts:

- Web URLs: `https://open.spotify.com/<kind>/<id>`
- Locale-prefixed URLs: `.../intl-es/<kind>/<id>` (the prefix is ignored)
- URIs: `spotify:<kind>:<id>` and `spotify:user:...:playlist:<id>`
- Surrounding whitespace and `?si=...` query strings.

`kind` ∈ {`track`, `album`, `playlist`}. Anything else (e.g. `artist`,
`episode`, a foreign host, or a malformed string) raises `SpotifyError`.

### 3.2 🔌 Providers

Both providers implement the same contract: `get_tracks(kind, id) -> list[Track]`.
A `Track` is a normalized dataclass (`title`, `artists`, `album`,
`track_number`, `duration_ms`, `release_date`, `cover_url`, `isrc`, `spotify_id`).

- 🟢 **`SpotipyProvider`** (`web_api.py`) — uses the Client Credentials flow.
  Albums and playlists are fully paginated. Full track objects carry the album
  name, cover images and ISRC. A `403` response (currently used by Spotify to
  enforce an "owner must be Premium" rule) is translated into `PremiumGateError`.

- 🔵 **`EmbedProvider`** (`embed.py`) — needs **no credentials**. It fetches
  `https://open.spotify.com/embed/<kind>/<id>`, extracts the `__NEXT_DATA__`
  JSON blob embedded in the page, and normalizes the `entity` (single track) or
  `entity.trackList` (album/playlist). Parsing is split into pure functions
  (`extract_entity`, `entity_to_tracks`) so it can be unit-tested from saved
  HTML fixtures with no network.

### 3.3 🔁 Automatic fallback (`provider.py`)

`fetch_tracks(config, kind, id)` implements the policy:

1. If Spotify credentials are configured, try the Web API.
2. If that raises `PremiumGateError` (403), **fall back to the embed provider**.
3. If no credentials are configured, use the embed provider directly.

The metadata source actually used is logged at INFO. This means SpotiSeek works
out of the box without an API key, and transparently upgrades to the richer API
the moment valid, unrestricted credentials are present — no code change needed.

> ℹ️ **Why the fallback exists.** Spotify recently began returning
> `403 Active premium subscription required for the owner of the app` on Web API
> data endpoints when the app's owning account isn't Premium. The token is
> issued fine, but data calls are gated. The embed path sidesteps this because
> it uses Spotify's own public web-player data rather than your API app.

---

## 4. 🌐 Soulseek layer

### 4.1 🔌 Client (`soulseek/client.py`)

`SoulseekClient` wraps `aioslsk.client.SoulSeekClient` and is used as an async
context manager. It is a **download-only** client: sharing and share-scanning
are disabled, and completed files land in a dedicated `.incoming` scratch
directory before being moved to their final name.

- 🔍 **`search(query, timeout)`** issues one search and collects results for
  `timeout` seconds (Soulseek search is push-based: peers reply asynchronously,
  so we wait a fixed window). Each shared file becomes a `Candidate`. File
  attributes are decoded from the protocol's integer keys
  (`BITRATE=0, DURATION=1, VBR=2, SAMPLE_RATE=4, BIT_DEPTH=5`) and mapped
  defensively — peers vary in what they report.

- ⬇️ **`download(candidate, timeout)`** requests the transfer and polls it to a
  terminal state (`COMPLETE` / `FAILED` / `ABORTED` / `INCOMPLETE`). On success
  it returns the absolute local path; on failure or timeout it raises
  `DownloadError` (and aborts a timed-out transfer).

🔇 The Soulseek network constantly produces failed peer connections (users
behind NAT, offline, etc.). This is normal churn, so `logging_setup.py` silences
the `aioslsk`/`asyncio` loggers below `CRITICAL` unless `--log-level DEBUG`/`-v`
is set. Real login/search/download failures are surfaced by SpotiSeek itself.

### 4.2 🎯 Matching (`soulseek/matcher.py`)

`score_candidates(track, candidates, strictness, min_bitrate, require_extended)`
is **pure** (no I/O) and deterministic, which makes it fully unit-testable. It
filters, then ranks.

🚫 **Filtering** removes a candidate if:
- its extension isn't a known audio type;
- it's a lossy file below `--min-bitrate` (lossless always passes);
- its fuzzy name score is below the strictness threshold;
- its reported duration is outside the strictness tolerance (only when the peer
  reports a duration — unknown durations are never rejected);
- `require_extended` is set and the filename is **not** an Extended Mix (see §4.3).

🧮 **Scoring** (0–100) is a weighted sum:

```
score = 100 · (0.50·name + 0.30·format + 0.20·availability)
```

| Component | How it's computed |
|---|---|
| **name** 🔤 | `rapidfuzz.token_set_ratio` of normalized `"artist title"` vs the filename (basename + full path), combined with a title-only check against the basename to avoid matching whole-discography folders. Text is accent-folded, lowercased and stripped of punctuation. |
| **format** 💾 | Lossless (FLAC/WAV/…) = 1.0; MP3 scaled by bitrate (320→0.75, 256→0.60, 192→0.45, 128→0.30); other audio = 0.20. |
| **availability** 📶 | Free upload slot (+), queue length (−), advertised speed (+). |

🎚️ **Strictness presets:**

| Preset | Name threshold | Duration tolerance |
|---|---|---|
| `strict` | 0.80 | ±7 s |
| `balanced` (default) | 0.58 | ±15 s |
| `lenient` | 0.42 | ignored |

The matcher returns the surviving candidates ranked best-first, so the
downloader can try the top pick and **fall back to the next** if a transfer
fails.

### 4.3 🎚️ Extended Mix mode

When the user passes `--extended-mix` (`Config.extended_mix`), the downloader
runs an **extended-first** strategy for each track (see §6):

1. It searches Soulseek with `"<artist> <title> extended mix"`.
2. It matches with `require_extended=True`, which keeps only candidates whose
   filename `is_extended_mix(...)` — i.e. the normalized name contains **both**
   `"extended"` and `"mix"` (so a "Radio Mix" or a bare "Extended Version" does
   not qualify). The duration filter is **disabled** for this pass, because an
   extended mix is legitimately longer than the standard track's Spotify
   duration and would otherwise be rejected.
3. 🎯 It favours the **official** extended mix. `is_official_extended_mix(...)`
   rejects any extended-labelled file that also carries an alternate-version
   marker (`_ALT_VERSION_KEYWORDS`: remix, flip, bootleg, VIP, mashup, edit,
   dub, instrumental, live, cover, …) appearing *outside* the artist/title — so
   `"Levels (RetroVision Flip) [Extended Mix]"` and `"… (Skrillex Remix) [Extended
   Mix]"` are discarded. Among the survivors, a light **specificity** tiebreak
   (`0.85 + 0.15/(1+extras)`) prefers the cleanest name, where "extras" are
   descriptive tokens that are neither the artist/title nor common noise
   (formats, track numbers, "remastered", "feat", …).
4. If an official extended mix is found, it is downloaded, the filename gets a
   ` (Extended Mix)` suffix, and the written **title tag** is suffixed to match.
5. If none is found (nothing extended, or only remixes/edits), SpotiSeek logs
   *"no Extended Mix found; downloading the standard version instead."* and
   continues with the normal flow.

---

## 5. 🏷️ Tagging (`tagging.py`)

After a successful download, `tag_file()` writes metadata and embeds cover art
using mutagen, dispatched by file extension:

| Format | Tag container | Cover art |
|---|---|---|
| MP3 | ID3v2 (`TIT2`/`TPE1`/`TALB`/`TRCK`/`TDRC`) | `APIC` |
| FLAC | Vorbis comments | `Picture` block |
| OGG/Opus | Vorbis comments | base64 `metadata_block_picture` |
| MP4/M4A/AAC | iTunes atoms | `covr` |
| WAV | ID3 chunk | `APIC` |
| AIFF/AIF/AIFC | ID3 chunk | `APIC` |

🖼️ Cover art is downloaded once from `track.cover_url`. Only fields SpotiSeek
actually knows are written, so existing tags on the file (e.g. an album name the
peer already set, when we resolved a single-track URL that lacks it) are
preserved. When an Extended Mix was downloaded, the title tag is written as
`"<Title> (Extended Mix)"` to stay consistent with the filename. Tagging is
**best-effort**: any failure is logged and the download is still counted as
successful — it never aborts a good file. `--no-tag` skips this step entirely.

---

## 6. 🎬 Orchestration (`downloader.py`)

`Downloader.run(url)`:

1. Parses the URL and fetches the track list.
2. Opens one `SoulseekClient` for the whole run.
3. Processes tracks through an `asyncio.Semaphore(parallel)` — so `--parallel 1`
   (default) is sequential and `--parallel N` runs N tracks concurrently over
   the single connection.
4. Per track (`_process_track`):
   - 🎚️ If `--extended-mix` is set, first try `_try_extended` (search + match the
     Extended Mix). If it produces a result, use it; otherwise fall through.
   - 📥 `_try_standard`: skip if already on disk → search → rank → try up to
     `MAX_DOWNLOAD_ATTEMPTS` (5) ranked candidates → move to
     `<Artist> - <Title>.<ext>` → tag.
5. Cleans up the `.incoming` directory and logs a summary
   (`Downloaded X/Y tracks`, plus how many were Extended Mixes, plus the list of
   skipped/failed tracks).

Each track yields a `DownloadResult` with a `DownloadStatus`
(`DOWNLOADED`, `SKIPPED_NO_RESULTS`, `SKIPPED_NO_MATCH`, `FAILED`, `DRY_RUN`) and
an `extended` flag. 🤷 A missing or failing track is logged and never stops the
others.

---

## 7. ⚙️ Configuration & logging

`Config.load()` layers **CLI flags > environment/`.env` > defaults**.
`python-dotenv` loads `.env` without overriding real environment variables.
Soulseek credentials default to the project's account but can be overridden by
env or `--slsk-user/--slsk-pass`.

🔊 Logging is leveled (`--log-level`, or `-v` for DEBUG). Third-party noise is
suppressed unless debugging (see §4.1).

`save_env(values, env_file)` (used by the GUI) persists credentials/settings to
`.env` via python-dotenv's `set_key` — existing keys are preserved, the file is
created if missing, and `os.environ` is updated so a same-process reload sees
the change.

---

## 8. 🖥️ Desktop GUI (`gui.py`)

An optional PySide6 front-end over the exact same core pipeline — chosen over
PyQt5 for its LGPL licence and Qt6 base. It is an **opt-in extra** (`gui`) so
CLI-only installs stay lightweight; the `spotiseek-gui` entry point
(`spotiseek.cli:gui`) imports PySide6 lazily and prints an install hint if it's
missing.

Architecture:

- 🧵 **Threading.** The download runs on a background `QThread` (`_Worker`) that
  calls `asyncio.run(run_download(...))`, keeping the UI responsive. Results and
  progress cross back to the UI thread through Qt **signals** (queued, hence
  thread-safe).
- 📊 **Progress.** `_Worker` passes `on_start`/`on_track_done` callbacks into
  `run_download`; these emit signals that drive the progress bar. The callbacks
  fire from the download's event loop and are wrapped so an exception can never
  break a run.
- 📜 **Logging.** A `_QtLogHandler` attached to the root logger forwards every
  record through a `_LogBridge` signal into the on-screen log view, so the GUI
  shows the same messages as the CLI (third-party noise already suppressed by
  `configure_logging`).
- 💾 **Settings.** Credentials/options are read on startup via `Config.load()`
  and written back with `save_env()` — users never edit `.env` by hand. A run
  builds its `Config` straight from the widget values, so unsaved tweaks still
  apply.

The GUI adds no logic of its own: metadata resolution, matching, downloading and
tagging are all the same functions the CLI uses.

---

## 9. 🧪 Testing

```bash
uv run pytest tests/unit                 # offline, deterministic
uv run pytest --run-integration          # also exercises the live network
```

- ✅ **Unit tests** (no network): URL parsing, embed parsing from saved HTML
  fixtures, the matcher (ranking, rejection, min-bitrate, strictness,
  `require_extended` / `is_official_extended_mix` and the cheap
  `has_ready_lossless_match` early-stop), tagging round-trips on real generated
  audio fixtures (mp3/flac/wav/m4a/aiff) plus the generic fallback, search-query
  cleanup, config precedence and `save_env`, the full orchestrator with a mocked
  Soulseek client (success, no-results, no-match, peer fallback, all-fail,
  dry-run, `--no-tag`, skip-if-present, parallel, extended-mix found/fallback/
  dry-run/skip/tag-suffix, progress callbacks, run resilience), the client's
  search collection/early-exit, and an **offscreen GUI smoke test** (skipped if
  PySide6 is absent).
- 🌍 **Integration tests** (opt-in via `--run-integration`): live Spotify embed
  metadata, live Soulseek login + search, and a real end-to-end download that
  verifies a genuine audio file lands on disk. Peer-dependent outcomes are
  handled — the tests accept a graceful "not available" as valid.

🔧 Audio fixtures are generated with `ffmpeg` (a 1-second sine tone per format).
GUI tests run under `QT_QPA_PLATFORM=offscreen`, so no display is required.

---

## 10. 📦 Packaging & CI

Standalone executables are produced with **PyInstaller** (`--onefile --windowed`)
driven by `scripts/build_executable.py`, which runs identically on all three
OSes. The frozen entry point is `scripts/spotiseek_app.py` (it launches the GUI,
or does an import-only `--selftest` used to verify a bundle). The build bundles
`spotiseek/assets` so the runtime logo works, and converts `icon.png` to
`.ico`/`.icns` via Pillow when present.

CI lives in `.github/workflows/build.yml`:

- 🧱 A **matrix** over `ubuntu-latest`, `windows-latest`, `macos-latest`
  (executables can't be cross-compiled, so each is built on its own runner).
- 🐍 Python 3.14 is provisioned via `uv python install`; deps come from
  `uv sync --extra gui --group build`.
- ✅ Each build runs the executable with `--selftest` before uploading.
- 📤 Binaries are uploaded as workflow **artifacts**, and on a `v*` **tag** they
  are also attached to a **GitHub Release**.
- Triggers: manual (`workflow_dispatch`) or pushing a version tag.

---

## 11. ⚠️ Known limitations

- 🔒 **Spotify API premium gate.** As noted in §3.3, the official Web API may
  return 403 until the app's owner account is Premium (propagation can take
  hours; rotating the client secret sometimes helps). SpotiSeek keeps working
  via the embed fallback in the meantime.
- 🧪 **Embed provider is unofficial.** It parses Spotify's public embed pages,
  which can change without notice, and it **may truncate very large playlists**.
  The official API paginates completely — prefer it for big playlists once your
  account is unrestricted. Single-track embeds also don't expose the album name.
- 📡 **Soulseek availability is peer-dependent.** A track may simply not be
  shared, or the only peers may be offline/queued/slow. SpotiSeek retries other
  peers and then skips, but it cannot download what nobody is sharing.
- 🎯 **Match accuracy isn't perfect.** Filenames on Soulseek are inconsistent and
  many peers don't report duration. `balanced` aims for a good precision/recall
  trade-off; use `strict` to avoid wrong versions (at the cost of more misses)
  or `lenient` to maximize hits (at the risk of remixes/live versions).
  `--extended-mix` targets the *official* extended mix and rejects extended
  remixes/edits; keyword-based, so a canonical mix that a peer mislabels with a
  remix word could be skipped, and if no official extended mix is shared it
  falls back to the standard track.
- 🔄 **No transcoding.** SpotiSeek downloads whatever format the chosen peer
  offers; it never converts between formats.

---

## 12. 🔮 Future work

- 🎚️ A `--format`/quality-policy flag to make the format preference configurable.
- 📝 Optional per-track "missing report" output file.
- 🧱 Support for `slskd` as an alternative Soulseek backend.
- ⏹️ A cancel button + queued-download view in the GUI.
