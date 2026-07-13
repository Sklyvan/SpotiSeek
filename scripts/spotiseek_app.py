"""Frozen-app entry point (used by PyInstaller).

Runs the desktop GUI. Supports ``--selftest``: import the whole stack and exit
0 without opening a window, so CI can verify the bundle is complete.
"""

from __future__ import annotations

import sys


def _echo(message: str) -> None:
    # In a windowed (--noconsole) build sys.stdout may be None; guard it.
    try:
        if sys.stdout is not None:
            sys.stdout.write(message + "\n")
            sys.stdout.flush()
    except Exception:
        pass


def main() -> None:
    if "--selftest" in sys.argv:
        import spotiseek.downloader  # noqa: F401
        import spotiseek.gui  # noqa: F401
        import spotiseek.soulseek.client  # noqa: F401
        import spotiseek.spotify.provider  # noqa: F401

        _echo("SpotiSeek self-test OK")
        return

    from spotiseek.gui import run_gui

    run_gui()


if __name__ == "__main__":
    main()
