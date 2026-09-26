import logging
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import Property, QMargins, QObject, QPoint, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent, QQmlEngine

from .. import audio as au
from ..languages import LANGUAGES
from .themes import bar_theme

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
BARS = 16
BAR_W, BAR_H = 236, 64


def _screen(name: str | None):
    for s in QGuiApplication.screens():
        if s.name() == name:
            return s
    return None


class BarModel(QObject):
    changed = Signal()
    levelsChanged = Signal()
    menuChanged = Signal()
    visibilityChanged = Signal()
    posChanged = Signal()
    draggingChanged = Signal()
    uiScaleChanged = Signal()
    themeChanged = Signal()

    def __init__(self, cfg, controller, open_hub):
        super().__init__()
        self.cfg = cfg
        self.ctl = controller
        self.open_hub = open_hub
        self.window = None
        self._state = "idle"
        self._mode = "dictation"
        self._message = ""
        self._ready = False
        self._progress = ""
        self._levels = deque([0.0] * BARS, maxlen=BARS)
        self._mics: list[dict] = []
        self.hidden_until = 0.0
        pos = cfg.get("ui.bar_pos")
        self._pos = (int(pos[0]), int(pos[1])) if pos else None
        self._screen_name: str | None = cfg.get("ui.bar_screen")
        self._dragging = False
        self._drag_screen = ""
        self.msg_timer = QTimer(self, singleShot=True)
        self.msg_timer.timeout.connect(lambda: self._set_message(""))
        self.unhide_timer = QTimer(self, singleShot=True)
        self.unhide_timer.timeout.connect(self.visibilityChanged.emit)

    # properties
    def _get_state(self):
        return self._state

    def _get_mode(self):
        return self._mode

    def _get_message(self):
        return self._message

    def _get_ready(self):
        return self._ready

    def _get_progress(self):
        return self._progress

    def _get_levels(self):
        return list(self._levels)

    def _get_edge(self):
        return self.cfg.get("ui.bar_edge")

    def _get_mics(self):
        return self._mics

    def _get_langs(self):
        current = self.cfg.get("stt.languages")
        choices = [{"code": "", "label": "Auto-detect", "selected": not current}]
        favorites = self.cfg.get("stt.language_favorites") or []
        codes = list(dict.fromkeys(favorites + current))
        for code in codes:
            choices.append({"code": code, "label": LANGUAGES.get(code, code),
                            "selected": current == [code]})
        return choices

    def _get_has_pos(self):
        return self._pos is not None

    def _get_has_screen(self):
        return self._screen_name is not None

    def _get_pos_x(self):
        return self._pos[0] if self._pos else 0

    def _get_pos_y(self):
        return self._pos[1] if self._pos else 0

    def _get_margins(self):
        # Layer-shell positions a top-left anchored surface by its margins.
        if self._dragging or not self._pos:
            return QMargins()
        return QMargins(self._pos[0], self._pos[1], 0, 0)

    def _get_dragging(self):
        return self._dragging

    def _get_ui_scale(self):
        return float(self.cfg.get("ui.bar_scale") or 1.0)

    def set_ui_scale(self, value: float) -> None:
        self.cfg.set("ui.bar_scale", round(min(2.0, max(0.6, value)), 2))
        self.uiScaleChanged.emit()
        self.posChanged.emit()

    def _get_theme(self):
        return bar_theme(self.cfg)

    def set_theme(self, theme: dict) -> None:
        self.cfg.set("ui.bar_theme", theme)
        self.themeChanged.emit()

    def _get_target_screen(self):
        return _screen(self._screen_name)

    def _get_drag_screen(self):
        return self._drag_screen

    hasPos = Property(bool, _get_has_pos, notify=posChanged)
    hasScreen = Property(bool, _get_has_screen, notify=posChanged)
    posX = Property(int, _get_pos_x, notify=posChanged)
    posY = Property(int, _get_pos_y, notify=posChanged)
    margins = Property(QMargins, _get_margins, notify=posChanged)
    dragging = Property(bool, _get_dragging, notify=draggingChanged)
    dragScreen = Property(str, _get_drag_screen, notify=posChanged)
    uiScale = Property(float, _get_ui_scale, notify=uiScaleChanged)
    theme = Property("QVariantMap", _get_theme, notify=themeChanged)
    # layer-shell-qt picks the output from its own screen property, not QWindow::screen().
    targetScreen = Property(QObject, _get_target_screen, notify=posChanged)

    state = Property(str, _get_state, notify=changed)
    mode = Property(str, _get_mode, notify=changed)
    message = Property(str, _get_message, notify=changed)
    ready = Property(bool, _get_ready, notify=changed)
    progress = Property(str, _get_progress, notify=changed)
    edge = Property(str, _get_edge, notify=changed)
    levels = Property("QVariantList", _get_levels, notify=levelsChanged)
    mics = Property("QVariantList", _get_mics, notify=menuChanged)
    languageChoices = Property("QVariantList", _get_langs, notify=menuChanged)

    def set_state(self, state: str, mode: str) -> None:
        self._state, self._mode = state, mode
        if state == "idle":
            self._levels = deque([0.0] * BARS, maxlen=BARS)
            self.levelsChanged.emit()
        self.changed.emit()
        self.visibilityChanged.emit()

    def set_progress(self, text: str) -> None:
        self._progress = text
        self.changed.emit()

    def set_model_status(self, text: str) -> None:
        self._ready = text.endswith("ready")
        self.changed.emit()

    def push_level(self, v: float) -> None:
        self._levels.append(v)
        self.levelsChanged.emit()

    def _set_message(self, text: str) -> None:
        self._message = text
        self.changed.emit()
        self.visibilityChanged.emit()

    def show_message(self, text: str, seconds: float = 3.0) -> None:
        self._set_message(text)
        self.msg_timer.start(int(seconds * 1000))

    def refresh_menu(self) -> None:
        pref = self.cfg.get("audio.mic_preference")
        chosen = au.pick_source(pref)
        self._mics = [{"name": "", "label": "System default", "selected": chosen is None}] + [
            {"name": n, "label": d or n, "selected": n == chosen} for n, d in au.list_sources()
        ]
        self.menuChanged.emit()

    def should_show(self) -> bool:
        if self._state != "idle" or self._message or self._dragging:
            return True
        return self.cfg.get("ui.bar_always_visible") and time.time() > self.hidden_until

    # monitors
    def place_on_saved_screen(self) -> None:
        """Before the first show: put the surface on the monitor it was left on."""
        scr = _screen(self._screen_name)
        if scr and self.window is not None:
            self.window.setScreen(scr)
        elif self._screen_name:
            # Monitor unplugged: dock on the active one instead.
            self._screen_name, self._pos = None, None
            self.posChanged.emit()

    # slots for QML
    @Slot()
    def startHandsFree(self):
        self.ctl.on_hotkey("handsfree", True)

    @Slot()
    def stop(self):
        self.ctl.stop_and_process()

    @Slot()
    def cancel(self):
        self.ctl.cancel()

    @Slot(str)
    def selectMic(self, name: str):
        pref = [p for p in self.cfg.get("audio.mic_preference") if p != name]
        self.cfg.set("audio.mic_preference", ([name] + pref) if name else [])
        self.refresh_menu()

    @Slot(str)
    def selectLanguage(self, code: str):
        self.cfg.set("stt.languages", [code] if code else [])
        self.menuChanged.emit()

    @Slot(int, int)
    def moveTo(self, x: int, y: int):
        self._pos = (max(0, x), max(0, y))
        self.posChanged.emit()

    @Slot(bool)
    def setDragging(self, on: bool):
        if on != self._dragging:
            if on and self.window is not None:
                self._drag_screen = self.window.screen().name()
            self._dragging = on
            self.draggingChanged.emit()
            self.posChanged.emit()
            self.visibilityChanged.emit()

    @Slot(int, int, int, int)
    def dragTo(self, cx: int, cy: int, gx: int, gy: int):
        """Cursor at (cx, cy) relative to the bar's own monitor, possibly outside it;
        (gx, gy) is where inside the bar it was grabbed."""
        w = self.window
        home = w.screen().geometry()
        p = QPoint(home.x() + cx, home.y() + cy)
        scr = QGuiApplication.screenAt(p) or _screen(self._drag_screen) or w.screen()
        g = scr.geometry()
        self._drag_screen = scr.name()
        bw, bh = round(BAR_W * self._get_ui_scale()), round(BAR_H * self._get_ui_scale())
        self._pos = (min(max(0, p.x() - g.x() - gx), g.width() - bw),
                     min(max(0, p.y() - g.y() - gy), g.height() - bh))
        self.posChanged.emit()

    @Slot()
    def endDrag(self):
        target = _screen(self._drag_screen)
        w = self.window
        if target is not None and w is not None and target.name() != w.screen().name():
            # Dropped on another monitor: recreate the surface there, already bar-sized.
            # Deferred, because we are inside the surface's own mouse-release handler.
            def move():
                w.setVisible(False)
                self._screen_name = target.name()
                self.setDragging(False)
                w.setScreen(target)
                w.setVisible(True)
                self.savePos()

            QTimer.singleShot(0, move)
        else:
            self.setDragging(False)
            self.savePos()

    @Slot()
    def savePos(self):
        if self._pos:
            self.cfg.set("ui.bar_pos", list(self._pos))
        if self.window is not None:
            self._screen_name = self.window.screen().name()
            self.cfg.set("ui.bar_screen", self._screen_name)

    @Slot()
    def resetPos(self):
        self._pos = None
        self.cfg.set("ui.bar_pos", None)
        self.posChanged.emit()

    @Slot()
    def hideForHour(self):
        self.hidden_until = time.time() + 3600
        self.unhide_timer.start(3600 * 1000 + 500)
        self.visibilityChanged.emit()

    @Slot()
    def openHub(self):
        self.open_hub()


class Overlay:
    def __init__(self, cfg, controller, open_hub):
        self.model = BarModel(cfg, controller, open_hub)
        self.engine = QQmlApplicationEngine()
        self.engine.rootContext().setContextProperty("bar", self.model)
        self.window = None
        for name in ("overlay.qml", "overlay_plain.qml"):
            self.engine.load(QUrl.fromLocalFile(str(HERE / name)))
            roots = self.engine.rootObjects()
            if roots:
                self.window = roots[-1]
                log.info("overlay loaded from %s", name)
                break
            log.warning("overlay %s failed to load", name)
        self.model.window = self.window
        self.ghosts = []
        if self.window is not None and self.window.property("layerShell"):
            self.model.place_on_saved_screen()
            self._build_ghosts()
            QGuiApplication.instance().screenAdded.connect(self._build_ghosts)
            QGuiApplication.instance().screenRemoved.connect(self._build_ghosts)
            self.model.draggingChanged.connect(self._show_ghosts)
        controller.state_changed.connect(self.model.set_state)
        controller.level.connect(self.model.push_level)
        controller.model_status.connect(self.model.set_model_status)
        self.model.visibilityChanged.connect(self.sync)
        self.model.changed.connect(self.sync)
        self.model.refresh_menu()
        self.sync()

    def _build_ghosts(self, *_):
        """One hidden click-through surface per monitor, shown while the bar is dragged."""
        for g in self.ghosts:
            g.deleteLater()
        self.ghosts = []
        self._ghost_comp = QQmlComponent(self.engine, QUrl.fromLocalFile(str(HERE / "ghost.qml")))
        for scr in QGuiApplication.screens():
            g = self._ghost_comp.create()
            if g is None:
                log.warning("ghost surface failed: %s", self._ghost_comp.errorString())
                return
            # Parentless objects from create() are owned by the JS engine and get collected.
            QQmlEngine.setObjectOwnership(g, QQmlEngine.ObjectOwnership.CppOwnership)
            g.setScreen(scr)
            self.ghosts.append(g)

    def _show_ghosts(self) -> None:
        home = self.window.screen().name()
        for g in self.ghosts:
            g.setVisible(self.model.dragging and g.screen().name() != home)

    def sync(self) -> None:
        if self.window is None:
            return
        want = self.model.should_show()
        if want != self.window.isVisible():
            self.window.setVisible(want)
