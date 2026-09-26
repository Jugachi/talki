import logging
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import ipc
from .config import Config
from .controller import Controller
from .db import Store
from .paths import APP_ID
from .stt import make_stt

log = logging.getLogger(__name__)

ICONS = {
    "idle": "audio-input-microphone",
    "recording": "media-record",
    "locked": "media-record",
    "processing": "view-refresh",
}


class TalkiApp:
    def __init__(self, argv: list[str]):
        self.qapp = QApplication(argv)
        self.qapp.setApplicationName("Talki")
        self.qapp.setDesktopFileName(APP_ID)
        self.qapp.setQuitOnLastWindowClosed(False)
        self.cfg = Config()
        from .ui.themes import AppTheme

        self.app_theme = AppTheme(self.qapp)
        self.app_theme.apply(self.cfg.get("ui.app_theme"))
        self.store = Store()
        self.store.prune(self.cfg.get("privacy.audio_retention_days"), self.cfg.get("privacy.history_retention_days"))
        self.ctl = Controller(self.cfg, self.store)
        self._hub = None
        self.hotkeys = None

        self.server = ipc.IPCServer(self.ctl.ipc_event)
        if not self.server.start():
            ipc.send("show")
            print("Talki is already running.", file=sys.stderr)
            sys.exit(0)

        from .ui.overlay import Overlay

        self.overlay = Overlay(self.cfg, self.ctl, lambda: self.hub.present())
        self._tray()
        self.ctl.message.connect(self._message)
        self.ctl.progress.connect(self.overlay.model.set_progress)
        self.ctl.state_changed.connect(self._state)
        self.ctl.open_scratchpad.connect(self._scratchpad)
        self.ctl.show_window.connect(lambda: self.hub.present())
        self.ctl.quit_requested.connect(self.quit)
        self._start_hotkeys()
        self.ctl.warm_up()
        if not self.cfg.get("ui.quiet_startup"):
            self.hub.present()

        # Let Python handle SIGINT/SIGTERM while Qt's loop runs.
        signal.signal(signal.SIGINT, lambda *_: self.quit())
        signal.signal(signal.SIGTERM, lambda *_: self.quit())
        self._sig_timer = QTimer(interval=300)
        self._sig_timer.timeout.connect(lambda: None)
        self._sig_timer.start()

    @property
    def hub(self):
        if self._hub is None:
            from .ui.hub import HubWindow

            self._hub = HubWindow(self.cfg, self.store, self.ctl, self)
        return self._hub

    def _tray(self) -> None:
        self.tray = QSystemTrayIcon(QIcon.fromTheme(ICONS["idle"]))
        self.tray.setToolTip("Talki")
        menu = QMenu()
        self.act_toggle = QAction("Start dictation", menu)
        self.act_toggle.triggered.connect(lambda: self.ctl.on_hotkey("handsfree", True))
        menu.addAction(self.act_toggle)
        menu.addAction("Paste last transcript", self.ctl.paste_last)
        menu.addAction("New scratchpad note", lambda: self.hub.show_page("notes") or self.hub.pages["notes"].new_note())
        menu.addSeparator()
        menu.addAction("Open Talki", lambda: self.hub.present())
        menu.addAction("Settings", lambda: self.hub.show_page("settings"))
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_click)
        self.tray.show()
        self._menu = menu

    def _tray_click(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self._hub is not None and self._hub.isVisible():
                self._hub.hide()
            else:
                self.hub.present()

    def _state(self, state: str, mode: str) -> None:
        self.tray.setIcon(QIcon.fromTheme(ICONS.get(state, ICONS["idle"])))
        self.act_toggle.setText("Stop dictation" if state in ("recording", "locked") else "Start dictation")
        self.tray.setToolTip(f"Talki: {state}")

    def _message(self, text: str, kind: str) -> None:
        log.log(logging.ERROR if kind == "error" else logging.INFO, "%s", text)
        self.overlay.model.show_message(text, {"info": 2.5, "done": 1.8}.get(kind, 4.0))
        if kind == "error" and self.cfg.get("ui.notifications"):
            self.tray.showMessage("Talki", text, QSystemTrayIcon.MessageIcon.Warning, 6000)

    def _scratchpad(self) -> None:
        self.hub.show_page("notes")
        self.hub.pages["notes"].new_note()

    def _start_hotkeys(self) -> None:
        backend = self.cfg.get("hotkeys.backend")
        try:
            if backend == "portal":
                from .hotkeys.portal import PortalHotkeys

                self.hotkeys = PortalHotkeys(self.ctl.hotkey_event, self._status_threadsafe)
            elif backend == "evdev":
                from .hotkeys.evdev_backend import EvdevHotkeys

                self.hotkeys = EvdevHotkeys(self.cfg.get("hotkeys.evdev"), self.ctl.hotkey_event, self._status_threadsafe)
            if self.hotkeys:
                self.hotkeys.start()
        except Exception as e:
            log.exception("hotkeys failed")
            self.ctl.message.emit(f"Hotkeys unavailable: {e}", "error")

    def _status_threadsafe(self, status: str) -> None:
        # Called from hotkey threads; Controller.message is a signal, so this is queued.
        if status != "ready":
            self.ctl.message.emit(status, "error")

    def configure_shortcuts(self) -> None:
        if self.hotkeys and hasattr(self.hotkeys, "configure"):
            self.hotkeys.configure()
        else:
            self.ctl.message.emit("Edit the evdev chords below or bind `talki ctl` in your compositor.", "info")

    def reload_stt(self) -> None:
        self.ctl.stt = make_stt(self.cfg)
        self.ctl.pipeline.stt = self.ctl.stt
        self.ctl.warm_up()

    def quit(self) -> None:
        if self._hub is not None:
            self._hub.pages["notes"].save()
        self.ctl.shutdown()
        self.server.close()
        self.qapp.quit()

    def run(self) -> int:
        return self.qapp.exec()
