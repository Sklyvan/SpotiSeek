"""PySide6 desktop GUI for SpotiSeek.

A thin front-end over the same core pipeline the CLI uses. It lets users paste a
Spotify URL, tweak options, manage credentials (saved to ``.env`` — no manual
editing), and watch live progress + logs. The download runs on a background
``QThread`` so the UI stays responsive; progress and log lines are delivered to
the main thread via Qt signals.

Launch with ``spotiseek-gui`` (requires the optional ``gui`` extra).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .config import (
    Config,
    default_download_dir,
    save_env,
)
from .downloader import run_download
from .errors import SpotiSeekError
from .logging_setup import configure_logging
from .models import DownloadStatus, MatchStrictness
from .spotify.parser import parse_spotify_url
from .spotify.provider import fetch_tracks

logger = logging.getLogger("spotiseek")

# Application logo/icon. Drop a PNG here and it is used automatically as the
# window/dock/taskbar icon; if it's absent the GUI still works without one.
ICON_PATH = Path(__file__).parent / "assets" / "icon.png"


def _app_icon() -> QIcon | None:
    return QIcon(str(ICON_PATH)) if ICON_PATH.exists() else None


# --------------------------------------------------------------------------- #
# Logging bridge: route log records to the GUI thread via a Qt signal.
# --------------------------------------------------------------------------- #
class _LogBridge(QObject):
    message = Signal(str)


class _QtLogHandler(logging.Handler):
    def __init__(self, bridge: _LogBridge) -> None:
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._bridge.message.emit(self.format(record))
        except Exception:  # never let logging break the app
            pass


# --------------------------------------------------------------------------- #
# Background worker: runs the async pipeline (or a metadata lookup) off the UI.
# --------------------------------------------------------------------------- #
class _Worker(QThread):
    started_total = Signal(int)          # number of tracks resolved
    track_done = Signal(object)          # DownloadResult
    finished_ok = Signal(list)           # list[DownloadResult]
    info_ready = Signal(str, str, list)  # kind, source, list[Track]
    failed = Signal(str)

    def __init__(self, config: Config, url: str, mode: str) -> None:
        super().__init__()
        self._config = config
        self._url = url
        self._mode = mode  # "download" | "info"

    def run(self) -> None:  # executes in the worker thread
        try:
            if self._mode == "info":
                kind, spotify_id = parse_spotify_url(self._url)
                tracks, source = fetch_tracks(self._config, kind, spotify_id)
                self.info_ready.emit(kind.value, source.value, tracks)
                return
            results = asyncio.run(
                run_download(
                    self._config,
                    self._url,
                    on_start=self.started_total.emit,
                    on_track_done=self.track_done.emit,
                )
            )
            self.finished_ok.emit(results)
        except SpotiSeekError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self.failed.emit(f"Unexpected error: {exc}")


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"SpotiSeek {__version__}")
        icon = _app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        self._worker: _Worker | None = None
        self._build_ui()
        self._setup_logging()
        self._load_settings()

    # -- UI construction --------------------------------------------------- #
    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)

        # URL + actions
        url_row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.returnPressed.connect(self._start_download)
        self.download_btn = QPushButton("Download")
        self.download_btn.clicked.connect(self._start_download)
        self.info_btn = QPushButton("Info")
        self.info_btn.clicked.connect(self._show_info)
        url_row.addWidget(QLabel("URL:"))
        url_row.addWidget(self.url_edit, 1)
        url_row.addWidget(self.download_btn)
        url_row.addWidget(self.info_btn)
        layout.addLayout(url_row)

        # Options
        opts = QGroupBox("Download Options")
        form = QFormLayout(opts)

        out_row = QHBoxLayout()
        self.output_edit = QLineEdit(str(default_download_dir()))
        # Wide enough to show a full path, and scrolled to the start so the
        # beginning of the path is visible rather than the tail.
        self.output_edit.setMinimumWidth(420)
        self.output_edit.setCursorPosition(0)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_output)
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(browse)
        form.addRow("Output Folder:", out_row)

        self.parallel = QSpinBox()
        self.parallel.setRange(1, 16)
        self.parallel.setValue(1)
        self.parallel.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.parallel.setToolTip("Concurrent downloads (1 = sequential).")
        form.addRow("Parallel Downloads:", self.parallel)

        self.match = QComboBox()
        for strictness in MatchStrictness:
            # Show a capitalized label; keep the enum value as item data.
            self.match.addItem(strictness.value.capitalize(), strictness.value)
        self.match.setCurrentIndex(
            self.match.findData(MatchStrictness.BALANCED.value)
        )
        form.addRow("Match Strictness:", self.match)

        self.search_timeout = QDoubleSpinBox()
        self.search_timeout.setRange(5.0, 60.0)
        self.search_timeout.setValue(15.0)
        self.search_timeout.setSuffix(" s")
        self.search_timeout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        form.addRow("Search Timeout:", self.search_timeout)

        self.min_bitrate = QSpinBox()
        self.min_bitrate.setRange(0, 1411)
        self.min_bitrate.setSingleStep(32)
        self.min_bitrate.setSpecialValueText("No Minimum")
        self.min_bitrate.setSuffix(" kbps")
        self.min_bitrate.setAlignment(Qt.AlignmentFlag.AlignLeft)
        # The default size hint is computed for the numeric range, which is
        # narrower than the "No Minimum" special text — give it room.
        self.min_bitrate.setMinimumWidth(160)
        form.addRow("Min Bitrate (Lossy):", self.min_bitrate)

        self.extended_check = QCheckBox("Prefer Official Extended Mix")
        form.addRow(self.extended_check)
        self.tag_check = QCheckBox("Write Tags and Embed Cover Art")
        self.tag_check.setChecked(True)
        form.addRow(self.tag_check)
        self.dryrun_check = QCheckBox("Dry Run")
        form.addRow(self.dryrun_check)
        self.fallback_check = QCheckBox(
            "Use lossless fallback (Tidal/Deezer/Amazon/Qobuz) when Soulseek fails"
        )
        self.fallback_check.setToolTip(
            "When Soulseek can't find a track, fetch it in lossless FLAC from "
            "streaming-service proxies via Odesli. Requires a working proxy URL "
            "in SPOTISEEK_<PROVIDER>_API_URL (see the README)."
        )
        form.addRow(self.fallback_check)

        layout.addWidget(opts)

        # Credentials
        creds = QGroupBox("Settings")
        cform = QFormLayout(creds)
        self.spotify_id = QLineEdit()
        self.spotify_id.setPlaceholderText("Optional")
        self.spotify_secret = QLineEdit()
        self.spotify_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.spotify_secret.setPlaceholderText("Optional")
        _slsk_tip = (
            "Any username/password works — Soulseek claims the name on first "
            "login, so no separate account registration is needed. It cannot "
            "be left blank."
        )
        self.slsk_user = QLineEdit()
        self.slsk_user.setPlaceholderText("Required")
        self.slsk_user.setToolTip(_slsk_tip)
        self.slsk_pass = QLineEdit()
        self.slsk_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.slsk_pass.setPlaceholderText("Required")
        self.slsk_pass.setToolTip(_slsk_tip)
        cform.addRow("Spotify Client ID:", self.spotify_id)
        cform.addRow("Spotify Client Secret:", self.spotify_secret)
        cform.addRow("Soulseek Username:", self.slsk_user)
        cform.addRow("Soulseek Password:", self.slsk_pass)
        save_btn = QPushButton("Save Settings")
        save_btn.clicked.connect(self._save_settings)
        cform.addRow(save_btn)
        layout.addWidget(creds)

        # Progress + log
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Logs will appear here…")
        layout.addWidget(self.log_view, 1)

        self.setCentralWidget(root)
        self.statusBar().showMessage("Ready")

    # -- logging ----------------------------------------------------------- #
    def _setup_logging(self) -> None:
        configure_logging(log_level="INFO")
        self._log_bridge = _LogBridge()
        self._log_bridge.message.connect(self._append_log)
        handler = _QtLogHandler(self._log_bridge)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(handler)

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)

    # -- settings ---------------------------------------------------------- #
    def _load_settings(self) -> None:
        cfg = Config.load()
        self.spotify_id.setText(cfg.spotify_client_id or "")
        self.spotify_secret.setText(cfg.spotify_client_secret or "")
        self.slsk_user.setText(cfg.soulseek_username or "")
        self.slsk_pass.setText(cfg.soulseek_password or "")

    def _save_settings(self) -> None:
        try:
            save_env(
                {
                    "SPOTIFY_CLIENT_ID": self.spotify_id.text().strip(),
                    "SPOTIFY_CLIENT_SECRET": self.spotify_secret.text().strip(),
                    "SOULSEEK_USERNAME": self.slsk_user.text().strip(),
                    "SOULSEEK_PASSWORD": self.slsk_pass.text().strip(),
                }
            )
        except OSError as exc:
            QMessageBox.critical(self, "SpotiSeek", f"Could not save settings:\n{exc}")
            return
        self.statusBar().showMessage("Settings Saved")
        logger.info("Settings saved.")

    # -- config from fields ------------------------------------------------ #
    def _current_config(self) -> Config:
        cfg = Config(
            spotify_client_id=self.spotify_id.text().strip() or None,
            spotify_client_secret=self.spotify_secret.text().strip() or None,
            soulseek_username=self.slsk_user.text().strip(),
            soulseek_password=self.slsk_pass.text().strip(),
            output_dir=Path(self.output_edit.text().strip() or default_download_dir()),
            parallel=self.parallel.value(),
            match_strictness=MatchStrictness(self.match.currentData()),
            search_timeout=self.search_timeout.value(),
            min_bitrate=self.min_bitrate.value() or None,
            tag=self.tag_check.isChecked(),
            dry_run=self.dryrun_check.isChecked(),
            extended_mix=self.extended_check.isChecked(),
            fallback=self.fallback_check.isChecked(),
        )
        # Fallback proxy base URLs have no GUI field; they come from env/.env.
        # Reuse Config.load's resolution so there's a single source of truth.
        env_cfg = Config.load()
        cfg.tidal_api_url = env_cfg.tidal_api_url
        cfg.qobuz_api_url = env_cfg.qobuz_api_url
        cfg.amazon_api_url = env_cfg.amazon_api_url
        cfg.deezer_api_url = env_cfg.deezer_api_url
        return cfg

    # -- actions ----------------------------------------------------------- #
    def _browse_output(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose output folder", self.output_edit.text() or "."
        )
        if chosen:
            self.output_edit.setText(chosen)
            self.output_edit.setCursorPosition(0)

    def _start_download(self) -> None:
        self._start("download")

    def _show_info(self) -> None:
        self._start("info")

    def _start(self, mode: str) -> None:
        if self._worker is not None:
            return  # a job is already running
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "SpotiSeek", "Please enter a Spotify URL.")
            return

        self.log_view.clear()
        self.progress.setRange(0, 0)  # busy until the track count is known
        self._set_running(True)

        self._worker = _Worker(self._current_config(), url, mode)
        self._worker.started_total.connect(self._on_started_total)
        self._worker.track_done.connect(self._on_track_done)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.info_ready.connect(self._on_info_ready)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._clear_worker)
        self._worker.start()

    # -- worker signal handlers ------------------------------------------- #
    def _on_started_total(self, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(0)
        self.progress.setFormat(f"%v / {total}")

    def _on_track_done(self, result) -> None:
        self.progress.setValue(self.progress.value() + 1)

    def _on_finished(self, results: list) -> None:
        done = sum(1 for r in results if r.status == DownloadStatus.DOWNLOADED)
        dry = sum(1 for r in results if r.status == DownloadStatus.DRY_RUN)
        total = len(results)
        if not self.progress.maximum():
            self.progress.setRange(0, 1)
        self.progress.setValue(self.progress.maximum())
        msg = (
            f"Dry run: {dry}/{total} would download."
            if dry
            else f"Downloaded {done}/{total} track(s)."
        )
        self.statusBar().showMessage(msg)
        self._set_running(False)

    def _on_info_ready(self, kind: str, source: str, tracks: list) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self._append_log(f"{kind} — {len(tracks)} track(s), source: {source}")
        for i, t in enumerate(tracks, start=1):
            dur = (
                f"{int(t.duration_s // 60)}:{int(t.duration_s % 60):02d}"
                if t.duration_s
                else "?"
            )
            album = f"  [{t.album}]" if t.album else ""
            self._append_log(f"  {i:>3}. {t.display}  ({dur}){album}")
        self.statusBar().showMessage(f"Resolved {len(tracks)} track(s)")
        self._set_running(False)

    def _on_failed(self, message: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self._append_log(f"ERROR: {message}")
        QMessageBox.critical(self, "SpotiSeek", message)
        self.statusBar().showMessage("Failed")
        self._set_running(False)

    def _clear_worker(self) -> None:
        self._worker = None

    def _set_running(self, running: bool) -> None:
        self.download_btn.setEnabled(not running)
        self.info_btn.setEnabled(not running)
        self.url_edit.setEnabled(not running)
        if running:
            self.statusBar().showMessage("Working…")


def run_gui() -> None:
    """Create the application and show the main window (blocks until closed)."""
    import sys

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("SpotiSeek")
    icon = _app_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    window = MainWindow()
    window.resize(780, 680)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":  # pragma: no cover
    run_gui()
