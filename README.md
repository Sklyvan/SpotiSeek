# 🎵 SpotiSeek

Reads a Spotify URL, pulls the track metadata, then downloads track from SoulSeek.

It ships with both a command-line tool and a desktop GUI.

> [!WARNING]
> Only download material you have the right to. You are responsible for how you use it.

---

## ✨ Features

- Accepts any Spotify track, album, or playlist URL.
- Reads metadata from Spotify, with an automatic fallback to public data.
- Searches SoulSeek and auto-picks the best result, preferring lossless audio.
- Understands version qualifiers and rejects the wrong recording.
- Optional Extended Mix mode.
- Downloads 3 tracks in parallel by default.
- Writes tags and embeds cover art.
- Skips missing tracks with a warning instead of stopping.

---

## 📦 Requirements

- **Python 3.14+**
- **SoulSeek Credentials** (username + password). You don't need to pre-register.
- **Spotify Developer credentials** for richest metadata, this is optional.

## 📥 Prebuilt Executables

Don't want to install Python? Grab a **single-file build** for your OS:

- From the **Actions** tab
- Latest *Build Executables* run
- **Artifacts**
  - `SpotiSeek-windows.exe`
  - `SpotiSeek-macos`
  - `SpotiSeek-linux`

These launch the desktop GUI directly, no Python required. Notes:

- **MacOS:** The first time right-click it → Open.
- **Linux:** Run `chmod +x SpotiSeek-linux` first.

To produce these yourself, see [Building Executables](#-building-executables).

## 🛠️ Installation

Using [uv](https://docs.astral.sh/uv/) (Recommended):

```bash
uv sync                    # CLI only
uv sync --extra gui        # CLI + desktop GUI (PySide6)
uv run spotiseek --help
```

Or with pip:

```bash
pip install -e .           # CLI only
pip install -e ".[gui]"    # CLI + desktop GUI
spotiseek --help
```

## ⚙️ Configuration

Reads settings from command-line flags, environment variables, or a `.env` file in the working directory.

```bash
cp .env.example .env
```

```dotenv
# Optional: richer/faster metadata via the official API.
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret

# Required: your SoulSeek login.
SOULSEEK_USERNAME=your_soulseek_username
SOULSEEK_PASSWORD=your_soulseek_password
```

Spotify credentials are **optional**, without them (or if your API app is restricted), SpotiSeek falls back to Spotify's public metadata automatically.
To get credentials: create a free app at <https://developer.spotify.com/dashboard>, then copy the Client ID and Secret.

---

## 🚀 Usage

```bash
# Download a single track (sequential, into your Downloads folder)
spotiseek download "https://open.spotify.com/track/..."

# Download an entire album, 5 downloads at a time, into a chosen folder
spotiseek download "https://open.spotify.com/album/..." --parallel 5 --output ~/Downloads

# Download a playlist
spotiseek download "https://open.spotify.com/playlist/..."

# Prefer Extended Mixes (falls back to the standard version if none found)
spotiseek download "https://open.spotify.com/track/.." --extended-mix

# Grab the longest/full version even when peers don't label it "Extended Mix"
spotiseek download "<SpotifyURL>" --prefer-longest

# Fall back to lossless streaming sources when SoulSeek can't find a track
spotiseek download "<SpotifyURL>" --fallback

# See what would be downloaded, without downloading (great for checking matches)
spotiseek download "<SpotifyURL>" --dry-run

# Just print the resolved track list for a URL
spotiseek info "<SpotifyURL>"
```

### 🎛️ Download Options

| Option | Description                                                                         | Default                     |
|---|-------------------------------------------------------------------------------------|-----------------------------|
| `-o, --output DIR` | Where to save files                                                                 | Downloads Folder            |
| `-p, --parallel N` | Concurrent downloads (`1` = sequential)                                             | `3`                         |
| `--extended-mix` | Prefer the official *(Extended Mix)*                                                | OFF                         |
| `--prefer-longest` | Pick the longest matching version                                                   | OFF                         |
| `--match {strict\|balanced\|lenient}` | How strictly to match results                                                       | `Balanced`                  |
| `--search-timeout SEC` | How long to gather SoulSeek results                                                 | `15`                        |
| `--min-bitrate N` | Reject lossy files below this bitrate (kbps)                                        | None                        |
| `--no-tag` | Don't write tags / embed art                                                        | OFF                         |
| `--dry-run` | Search & match only; don't download                                                 | OFF                         |
| `--fallback` | Fallback to streaming-service proxies ([see below](#-lossless-fallback---fallback)) | OFF                         |
| `--fallback-providers LIST` | Comma-separated provider order                                                      | `tidal,deezer,amazon,qobuz` |
| `--slsk-user`, `--slsk-pass` | Override SoulSeek credentials                                                       | From .env                   |
| `--log-level` | Logging Verbosity                                                                   | `INFO`                      |
| `-v` | Shortcut for `DEBUG` logging                                                        | none                        |

Files are saved as `<Artist> - <Title>.<ext>` (or `<Artist> - <Title> (Extended Mix).<ext>`
when an extended mix was downloaded). If a matching file is already present in
the output folder, that track is skipped.

### 🛟 Lossless Fallback (`--fallback`)

SoulSeek is the primary source. When it can't deliver a track (no results, no
acceptable match, or every transfer fails), `--fallback` resolves the track to
its counterpart on other streaming platforms via the free public
[Odesli / song.link](https://odesli.co) API, then downloads a **lossless FLAC**
through a per-provider proxy, the same approach [SpotiFLAC](https://github.com/spotbye/SpotiFLAC)
uses. Providers are tried in order (default `tidal,deezer,amazon,qobuz`); the
first success wins, and the file is renamed and tagged exactly like a SoulSeek
download. It's **off by default**.

> [!WARNING]
> **You must supply a working proxy URL.** These proxies are third-party,
> reverse-engineered services that rotate hostnames and go offline frequently,
> so SpotiSeek ships **no default endpoints**. Point the relevant environment
> variable at a currently-working instance; providers without a configured URL
> are skipped. (Qobuz is matched by ISRC, which is only available when Spotify
> Web API credentials are configured.)

```dotenv
# Set only the providers you have a working proxy for. Examples of the API
# "shapes" SpotiSeek expects (hostnames change, find a live one yourself):
SPOTISEEK_TIDAL_API_URL=https://your-hifi-api-instance
SPOTISEEK_QOBUZ_API_URL=https://your-qobuz-rest-instance
SPOTISEEK_AMAZON_API_URL=https://your-amazon-proxy
SPOTISEEK_DEEZER_API_URL=https://your-deezer-proxy
```

---

## 🖥️ Desktop GUI

Prefer not to use the terminal? Launch the GUI:

```bash
uv run spotiseek-gui       # or just: spotiseek-gui
```

Everything runs through the exact same engine as the CLI.

---

## 🧭 How it Works

```
1. Read the Spotify URL and its metadata (Web API, or public fallback).
2. For each track:
   a. Search SoulSeek.
   b. Rank the results and pick the best one.
   c. Download it (retrying other peers on failure).
   d. Write tags and embed the cover art.
3. Print a summary: "Downloaded X/Y tracks".
```

For the technical details (architecture, matching heuristic, and known
limitations) see [DOCUMENTATION.md](DOCUMENTATION.md).

## 🔨 Building Executables

CI (GitHub Actions, `.github/workflows/build.yml`) builds a single-file
executable for **Windows, macOS and Linux** on every version tag and on manual
runs. To build one locally for your current OS:

```bash
uv sync --extra gui --group build
uv run python scripts/build_executable.py
# -> dist/SpotiSeek  (or dist/SpotiSeek.exe on Windows)
```

Uses [PyInstaller](https://pyinstaller.org/) (`--onefile`).

## 🧪 Development

```bash
uv sync                                      # installs the dev group by default
uv run pytest tests/unit                     # fast, offline
uv run pytest --run-integration              # also hits the live network
```
