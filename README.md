# 🎵 SpotiSeek

**SpotiSeek** reads a Spotify URL — a single track 🎧, an album 💿, or a whole
playlist 📜 — pulls the track metadata, then searches the
[Soulseek](https://www.slsknet.org/) peer-to-peer network for each track and
downloads the best match it can find. ⬇️ Downloaded files are renamed and
tagged (title, artist, album, track number, year) with the album cover art
embedded. 🏷️🖼️

It's a command-line tool. 💻 A GUI may come later.

> ⚠️ **Legal note.** SpotiSeek downloads files from other users on the Soulseek
> network. Only download material you have the right to. You are responsible for
> how you use it.

---

## ✨ What it does

- 🔗 Accepts any Spotify **track**, **album**, or **playlist** URL (or `spotify:` URI).
- 📖 Reads metadata from Spotify. It prefers the official Web API when you have
  credentials, and automatically falls back to Spotify's public data when the
  API is unavailable — so it works even without an API key.
- 🥇 Searches Soulseek and **auto-picks the best result**, preferring lossless
  (FLAC/WAV) → high-bitrate MP3 → anything playable, while checking the artist,
  title and (when the peer reports it) the track duration.
- 🎚️ Optional **`--extended-mix`** mode: prefer the *official (Extended Mix)* of a
  track (extended **remixes/edits are ignored**), and fall back to the standard
  one if no official extended mix is available.
- ⚡ Downloads **sequentially by default**, or **in parallel** with `--parallel N`.
- 🏷️ Writes tags and embeds cover art into each downloaded file.
- 🤷 If a track can't be found or downloaded, it **logs a warning and moves on** —
  one missing track never stops the rest.

---

## 📦 Requirements

- 🐍 **Python 3.14+**
- 🔑 A **Soulseek account** (username + password). SpotiSeek connects to the
  network itself; you don't need a separate Soulseek client running.
- *(Optional)* 🎼 **Spotify Developer credentials** for richest metadata — see below.

## 🛠️ Installation

Using [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv sync                 # install runtime deps
uv run spotiseek --help
```

Or with pip:

```bash
pip install -e .
spotiseek --help
```

## ⚙️ Configuration

SpotiSeek reads settings from command-line flags, environment variables, or a
`.env` file in the working directory (flags win over env, env wins over
defaults). Copy the template and fill it in:

```bash
cp .env.example .env
```

```dotenv
# Optional — richer/faster metadata via the official API.
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret

# Required — your Soulseek login.
SOULSEEK_USERNAME=your_soulseek_username
SOULSEEK_PASSWORD=your_soulseek_password
```

🔓 **Spotify credentials are optional.** Without them (or if your API app is
restricted), SpotiSeek falls back to Spotify's public metadata automatically.
To get credentials: create a free app at
<https://developer.spotify.com/dashboard>, then copy the Client ID and Secret.

---

## 🚀 Usage

```bash
# 🎧 Download a single track (sequential, into ./downloads)
spotiseek download "https://open.spotify.com/track/0DiWol3AO6WpXZgp0goxAV"

# 💿 Download an entire album, 5 downloads at a time, into a chosen folder
spotiseek download "https://open.spotify.com/album/2noRn2Aes5aoNVsU6iWThc" \
    --parallel 5 --output ~/Music/SpotiSeek

# 📜 Download a playlist
spotiseek download "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"

# 🎚️ Prefer Extended Mixes (falls back to the standard version if none found)
spotiseek download "https://open.spotify.com/track/6RN5TdlxfilLjMcy1tJlV5" --extended-mix

# 👀 See what would be downloaded, without downloading (great for checking matches)
spotiseek download "<url>" --dry-run

# ℹ️ Just print the resolved track list for a URL
spotiseek info "<url>"
```

### 🎛️ `download` options

| Option | Description | Default |
|---|---|---|
| `-o, --output DIR` | Where to save files | `downloads` |
| `-p, --parallel N` | Concurrent downloads (`1` = sequential) | `1` |
| `--extended-mix` | Prefer the official *(Extended Mix)*; fall back to standard | off |
| `--match {strict\|balanced\|lenient}` | How strictly to match results | `balanced` |
| `--search-timeout SEC` | How long to gather Soulseek results per track | `15` |
| `--min-bitrate N` | Reject lossy files below this bitrate (kbps) | — |
| `--no-tag` | Don't write tags / embed art | off |
| `--dry-run` | Search & match only; don't download | off |
| `--slsk-user`, `--slsk-pass` | Override Soulseek credentials | from env |
| `--log-level {DEBUG\|INFO\|WARNING\|ERROR}` | Logging verbosity | `INFO` |
| `-v` | Shortcut for `DEBUG` logging | — |

📁 Files are saved as `<Artist> - <Title>.<ext>` (or `<Artist> - <Title> (Extended Mix).<ext>`
when an extended mix was downloaded). If a matching file is already present in
the output folder, that track is skipped. ⏭️

### 🔚 Exit code

`download` exits non-zero if there were tracks to fetch but **none** succeeded,
which makes it easy to use in scripts.

---

## 🧭 How it works (short version)

```
Spotify URL ──▶ metadata (Web API → public fallback)
            ──▶ for each track:  search Soulseek ──▶ rank & pick best result
                                 ──▶ download (retry other peers on failure)
                                 ──▶ tag + embed cover art
            ──▶ summary: "Downloaded X/Y tracks"
```

🎚️ With `--extended-mix`, each track first searches for its *(Extended Mix)*;
if none is found it downloads the standard version and says so in the log.

📚 For the technical details — architecture, matching heuristic, and known
limitations — see [DOCUMENTATION.md](DOCUMENTATION.md).

## 🧪 Development

```bash
uv sync                                      # installs the dev group by default
uv run pytest tests/unit                     # fast, offline
uv run pytest --run-integration              # also hits the live network
```
