"""Active window detection and best-effort AT-SPI text context around the caret."""

import concurrent.futures
import json
import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class WindowInfo:
    cls: str = ""
    title: str = ""
    pid: int = 0


@dataclass
class Context:
    window: WindowInfo = field(default_factory=WindowInfo)
    category: str = "other"
    terminal: bool = False
    ide: bool = False
    messaging: bool = False
    before: str | None = None
    after: str | None = None
    identifiers: list[str] = field(default_factory=list)
    accessible: object = None


def _out(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=1.5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def active_window() -> WindowInfo:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        try:
            js = json.loads(_out(["hyprctl", "activewindow", "-j"]) or "{}")
            return WindowInfo(js.get("class", ""), js.get("title", ""), int(js.get("pid") or 0))
        except ValueError:
            return WindowInfo()
    if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
        try:
            tree = json.loads(_out(["swaymsg", "-t", "get_tree"]) or "{}")
        except ValueError:
            return WindowInfo()
        stack = [tree]
        while stack:
            n = stack.pop()
            if n.get("focused") and n.get("type") in ("con", "floating_con"):
                cls = n.get("app_id") or (n.get("window_properties") or {}).get("class", "")
                return WindowInfo(cls or "", n.get("name") or "", int(n.get("pid") or 0))
            stack += n.get("nodes", []) + n.get("floating_nodes", [])
        return WindowInfo()
    if wayland and "kde" in desktop and shutil.which("kdotool"):
        lines = _out(["kdotool", "getactivewindow", "getwindowclassname", "getwindowname", "getwindowpid"]).splitlines()
        lines += [""] * 3
        return WindowInfo(lines[0].strip(), lines[1].strip(), int(lines[2]) if lines[2].strip().isdigit() else 0)
    if not wayland and shutil.which("xdotool"):
        lines = _out(["xdotool", "getactivewindow", "getwindowclassname", "getwindowname", "getwindowpid"]).splitlines()
        lines += [""] * 3
        return WindowInfo(lines[0].strip(), lines[1].strip(), int(lines[2]) if lines[2].strip().isdigit() else 0)
    return WindowInfo()


def classify(win: WindowInfo, cfg) -> tuple[str, bool, bool, bool]:
    """Returns (category, terminal, ide, messaging)."""
    cls = win.cls.lower()
    title = win.title.lower()
    category = "other"
    for key, cat in cfg.get("app_categories").items():
        if key.lower() in cls:
            category = cat
            break
    else:
        for key, cat in cfg.get("title_categories").items():
            if key.lower() in title:
                category = cat
                break
    terminal = any(t in cls for t in cfg.get("terminals"))
    ide = any(t in cls for t in cfg.get("ides"))
    messaging = any(m in cls or m in title for m in cfg.get("messaging_apps"))
    return category, terminal, ide, messaging


class A11yTracker:
    """Follows the focused accessible object so the caret text can be read on demand.

    Needs python-atspi (pacman: python-atspi, apt: python3-pyatspi). Chromium/Electron apps
    only expose text with --force-renderer-accessibility, Qt apps with
    QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1.
    """

    def __init__(self):
        self.focused = None
        self.ok = False
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            import pyatspi  # noqa: F401
        except ImportError:
            log.info("python-atspi not installed; context awareness limited to window class")
            return
        subprocess.run(
            ["busctl", "--user", "set-property", "org.a11y.Bus", "/org/a11y/bus",
             "org.a11y.Status", "IsEnabled", "b", "true"],
            capture_output=True,
        )
        threading.Thread(target=self._loop, daemon=True, name="atspi").start()
        self.ok = True

    def _loop(self) -> None:
        import pyatspi

        def on_focus(ev):
            if ev.detail1:
                self.focused = ev.source

        try:
            pyatspi.Registry.registerEventListener(on_focus, "object:state-changed:focused")
            pyatspi.Registry.start()
        except Exception as e:
            log.warning("AT-SPI loop stopped: %s", e)
            self.ok = False

    def _read(self, obj):
        import pyatspi

        if obj is None:
            return None
        if obj.getRole() in (pyatspi.ROLE_PASSWORD_TEXT,):
            return None
        state = obj.getState()
        if not state.contains(pyatspi.STATE_EDITABLE):
            return None
        ti = obj.queryText()
        caret = max(0, ti.caretOffset)
        return ti.getText(max(0, caret - 2000), caret), ti.getText(caret, caret + 500)

    def caret_text(self, timeout: float = 0.15) -> tuple[str, str, object] | None:
        if not self.ok or self.focused is None:
            return None
        obj = self.focused
        try:
            got = self._pool.submit(self._read, obj).result(timeout=timeout)
        except Exception as e:
            log.debug("AT-SPI read failed: %s", e)
            return None
        return (got[0], got[1], obj) if got else None

    def full_text(self, obj, timeout: float = 0.3) -> str | None:
        def read():
            ti = obj.queryText()
            return ti.getText(0, min(ti.characterCount, 20000))

        try:
            return self._pool.submit(read).result(timeout=timeout)
        except Exception:
            return None
