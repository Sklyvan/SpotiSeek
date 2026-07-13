"""Offscreen smoke tests for the PySide6 GUI (skipped if PySide6 is absent)."""

from __future__ import annotations

import os

import pytest

# Render without a display; must be set before PySide6 is imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from spotiseek.gui import MainWindow, _Worker  # noqa: E402
from spotiseek.models import MatchStrictness  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_window_builds(qapp) -> None:
    window = MainWindow()
    assert "SpotiSeek" in window.windowTitle()
    assert [
        window.match.itemText(i) for i in range(window.match.count())
    ] == [m.value for m in MatchStrictness]
    assert window.tag_check.isChecked()
    assert window.parallel.value() == 1
    # Soulseek username defaults to the project account.
    assert window.slsk_user.text()


def test_current_config_reflects_fields(qapp) -> None:
    window = MainWindow()
    window.parallel.setValue(4)
    window.match.setCurrentText("strict")
    window.extended_check.setChecked(True)
    window.tag_check.setChecked(False)
    window.dryrun_check.setChecked(True)
    window.min_bitrate.setValue(0)  # 0 -> no minimum
    window.output_edit.setText("/tmp/spotiseek-out")

    cfg = window._current_config()
    assert cfg.parallel == 4
    assert cfg.match_strictness is MatchStrictness.STRICT
    assert cfg.extended_mix is True
    assert cfg.tag is False
    assert cfg.dry_run is True
    assert cfg.min_bitrate is None
    assert str(cfg.output_dir) == "/tmp/spotiseek-out"


def test_min_bitrate_passthrough(qapp) -> None:
    window = MainWindow()
    window.min_bitrate.setValue(256)
    assert window._current_config().min_bitrate == 256


def test_worker_construction(qapp) -> None:
    # The worker is a QThread; constructing it should not start any work.
    window = MainWindow()
    worker = _Worker(window._current_config(), "spotify:track:abc", "info")
    assert not worker.isRunning()
