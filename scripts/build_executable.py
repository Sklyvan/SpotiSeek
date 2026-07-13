"""Build a single-file SpotiSeek executable for the current OS with PyInstaller.

Cross-platform (run the same on Windows/macOS/Linux). Bundles the assets folder
so the GUI logo works, and — if ``spotiseek/assets/icon.png`` exists — converts
it to the platform's executable-icon format (.ico / .icns) via Pillow.

Usage:  python scripts/build_executable.py
Output: dist/SpotiSeek  (or dist/SpotiSeek.exe on Windows)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "spotiseek" / "assets"
ENTRY = ROOT / "scripts" / "spotiseek_app.py"
APP_NAME = "SpotiSeek"


def _make_platform_icon() -> Path | None:
    """Convert assets/icon.png to a .ico (Windows) or .icns (macOS) if present."""
    png = ASSETS / "icon.png"
    if not png.exists():
        return None
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not available; building without an executable icon.")
        return None

    try:
        image = Image.open(png).convert("RGBA")
        if sys.platform.startswith("win"):
            out = ASSETS / "icon.ico"
            image.save(
                out,
                sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
            )
            return out
        if sys.platform == "darwin":
            out = ASSETS / "icon.icns"
            image.save(out)
            return out
    except Exception as exc:  # never fail the build over an icon
        print(f"Could not build platform icon ({exc}); continuing without one.")
    return None  # Linux: the runtime window icon comes from the bundled PNG


def main() -> int:
    add_data = f"{ASSETS}{os.pathsep}spotiseek/assets"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", APP_NAME,
        "--add-data", add_data,
        # aioslsk loads protocol/message classes dynamically.
        "--collect-submodules", "aioslsk",
    ]
    icon = _make_platform_icon()
    if icon is not None:
        cmd += ["--icon", str(icon)]
    cmd.append(str(ENTRY))

    print("Running:", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
