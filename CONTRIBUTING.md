# Contributing to SpotiSeek

We welcome contributions! Here's how to get started.

## Development Setup

SpotiSeek uses [uv](https://docs.astral.sh/uv/) for dependency management (Python 3.14+).

```bash
# Clone the repository
git clone https://github.com/Sklyvan/SpotiSeek.git
cd SpotiSeek

# Install dependencies
uv sync                    # CLI only
uv sync --extra gui        # + desktop GUI (PySide6)
```

## Running Tests

```bash
# Unit tests (fast, offline, no network access)
uv run pytest tests/unit

# Integration tests (live, hit Spotify/Soulseek, download real files)
uv run pytest --run-integration

# Exclude integration tests
uv run pytest -m "not integration"

# GUI tests (requires headless Qt platform)
QT_QPA_PLATFORM=offscreen uv run pytest
```

For details, see `CLAUDE.md` (project conventions) and `DOCUMENTATION.md` (technical design).

## Commit Message Format

We use **Conventional Commits** for clear, semantic history:

```
type(scope): summary

Optional body explaining the change and why.

type: feat, fix, docs, test, refactor, chore, ci
scope: (optional) module/area affected
summary: concise, lowercase, no period
```

**Examples:**
- `feat: add --extended-mix mode`
- `fix(matcher): reject lossy files below min-bitrate`
- `docs: update README installation section`
- `test(soulseek): add client integration tests`
- `refactor(config): simplify argument parsing`

## Pull Request Process

1. **Write tests** alongside behavior changes (see `tests/` for examples).
2. **Update docs** — keep `README.md` (user-facing) and `DOCUMENTATION.md` (technical) in sync per `CLAUDE.md`.
3. **Run tests locally:**
   ```bash
   uv run pytest tests/unit
   uv run pytest --run-integration  # if touching network code
   ```
4. **Create a PR** with a clear summary, testing notes, and documentation checklist.
5. **Address feedback** and keep your commits clean.

## Building Executables

To build standalone binaries for your OS:

```bash
uv sync --extra gui --group build
uv run python scripts/build_executable.py
```

Output: `dist/SpotiSeek` (or `.exe` on Windows).

## Release Process

**Only the maintainer pushes tags.** The release flow is:

1. **Create a version tag** (maintainer only):
   ```bash
   git tag v1.2.3
   git push origin v1.2.3
   ```

2. **CI automatically builds** — `.github/workflows/build.yml` runs on `v*` tags, compiles executables for Linux, macOS, and Windows, and attaches them to a GitHub Release.

3. **No manual pushing by contributors** — always work on branches and create PRs. Maintainer reviews, merges, and tags.

## Quick Reference

- **Main code:** `spotiseek/` (CLI, config, Spotify, Soulseek, tagging, GUI)
- **Tests:** `tests/` (unit + integration)
- **Scripts:** `scripts/` (build, CI helpers)
- **Docs:** `README.md`, `DOCUMENTATION.md`, `CLAUDE.md`

## Questions?

Check `CLAUDE.md` for gotchas (e.g., the Spotify Web API 403 fallback) and architectural patterns. Open an issue or PR with questions.
