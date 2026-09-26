"""Clipboard handling and synthetic key presses for inserting text into the focused app.

On Wayland the only compositor-agnostic way to press keys is a uinput virtual keyboard,
so Talki pastes instead of typing: it works for any Unicode text and any keyboard layout
that keeps V and C on their usual physical keys.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time

log = logging.getLogger(__name__)

WAYLAND = bool(os.environ.get("WAYLAND_DISPLAY"))
TEXT_TYPES = ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING", "STRING", "TEXT")


class InjectError(Exception):
    pass


def _run(cmd: list[str], data: bytes | None = None, timeout: float = 2.0) -> bytes:
    r = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise InjectError(f"{cmd[0]}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


class Clipboard:
    def __init__(self):
        if WAYLAND and not shutil.which("wl-copy"):
            log.error("wl-clipboard not installed; text insertion will fail")

    def get(self) -> tuple[str, bytes] | None:
        """Current clipboard as (mime, data), preferring text. None when empty."""
        try:
            if WAYLAND:
                types = _run(["wl-paste", "--list-types"]).decode().split()
                if not types:
                    return None
                mime = next((t for t in TEXT_TYPES if t in types), types[0])
                return mime, _run(["wl-paste", "--no-newline", "--type", mime])
            types = _run(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"]).decode().split()
            mime = next((t for t in TEXT_TYPES if t in types), types[0] if types else "UTF8_STRING")
            return mime, _run(["xclip", "-selection", "clipboard", "-t", mime, "-o"])
        except (InjectError, subprocess.SubprocessError, OSError, StopIteration):
            return None

    def get_text(self) -> str | None:
        got = self.get()
        if not got or got[0] not in TEXT_TYPES:
            return None
        return got[1].decode("utf-8", errors="replace")

    def set(self, data: bytes, mime: str = "text/plain;charset=utf-8", sensitive: bool = False) -> None:
        if WAYLAND:
            cmd = ["wl-copy", "--type", mime]
            if sensitive:
                cmd.insert(1, "--sensitive")
            # wl-copy forks a child that keeps serving the selection. That child inherits
            # stdout/stderr, so a pipe would never reach EOF; use a file and wait for the parent only.
            with tempfile.TemporaryFile() as errf:
                p = subprocess.run(cmd, input=data, stdout=subprocess.DEVNULL, stderr=errf, timeout=3)
                errf.seek(0)
                err = errf.read()
            if p.returncode != 0:
                if sensitive and b"sensitive" in err:
                    return self.set(data, mime, sensitive=False)
                raise InjectError(f"wl-copy: {err.decode(errors='replace').strip()}")
        else:
            p = subprocess.Popen(
                ["xclip", "-selection", "clipboard", "-t", mime, "-i"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            p.communicate(data, timeout=3)

    def set_text(self, text: str, sensitive: bool = False) -> None:
        self.set(text.encode(), sensitive=sensitive)

    def clear(self) -> None:
        if WAYLAND:
            subprocess.run(["wl-copy", "--clear"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)


class Keyboard:
    """Virtual keyboard. uinput works on every compositor; xdotool is the X11 fallback."""

    def __init__(self):
        self.ui = None
        try:
            from evdev import UInput, ecodes as e

            self.e = e
            keys = [e.KEY_LEFTCTRL, e.KEY_LEFTSHIFT, e.KEY_LEFTALT, e.KEY_LEFTMETA, e.KEY_RIGHTMETA,
                    e.KEY_RIGHTCTRL, e.KEY_RIGHTSHIFT, e.KEY_RIGHTALT,
                    e.KEY_V, e.KEY_C, e.KEY_ENTER, e.KEY_INSERT, e.KEY_ESC]
            self.ui = UInput({e.EV_KEY: keys}, name="talki-virtual-keyboard")
            # Give the compositor time to pick up the new device before first use.
            time.sleep(0.3)
        except Exception as ex:
            log.warning("uinput unavailable (%s); falling back to xdotool", ex)
            if WAYLAND:
                log.error("no key injection available on Wayland without /dev/uinput access")

    @property
    def available(self) -> bool:
        return self.ui is not None or (not WAYLAND and shutil.which("xdotool") is not None)

    def _combo_uinput(self, names: list[str]) -> None:
        e = self.e
        codes = [getattr(e, "KEY_" + n.upper()) for n in names]
        for c in codes:
            self.ui.write(e.EV_KEY, c, 1)
            self.ui.syn()
            time.sleep(0.008)
        for c in reversed(codes):
            self.ui.write(e.EV_KEY, c, 0)
            self.ui.syn()
            time.sleep(0.008)

    def combo(self, *names: str) -> None:
        """names like ('leftctrl', 'v')."""
        if self.ui:
            self._combo_uinput(list(names))
        elif shutil.which("xdotool"):
            xd = {"leftctrl": "ctrl", "leftshift": "shift", "leftalt": "alt", "leftmeta": "super",
                  "enter": "Return", "insert": "Insert", "esc": "Escape"}
            _run(["xdotool", "key", "--clearmodifiers", "+".join(xd.get(n, n) for n in names)])
        else:
            raise InjectError("no virtual keyboard available")

    def release_modifiers(self) -> None:
        """Our virtual device never holds modifiers, but a stuck one from a previous crash would."""
        if not self.ui:
            return
        e = self.e
        for c in (e.KEY_LEFTCTRL, e.KEY_LEFTSHIFT, e.KEY_LEFTALT, e.KEY_LEFTMETA):
            self.ui.write(e.EV_KEY, c, 0)
        self.ui.syn()

    def close(self) -> None:
        if self.ui:
            self.ui.close()


class Injector:
    RESTORE_DELAY = 0.45

    def __init__(self):
        self.clip = Clipboard()
        self.kb = Keyboard()
        self._lock = threading.Lock()

    def paste(self, text: str, terminal: bool = False, press_enter: bool = False, restore: bool = False) -> None:
        """Puts text on the clipboard and pastes it. With restore, the previous clipboard comes
        back afterwards and clipboard managers are told to skip the transient entry."""
        with self._lock:
            saved = self.clip.get() if restore else None
            self.clip.set_text(text, sensitive=restore)
            time.sleep(0.06)
            self.kb.release_modifiers()
            if terminal:
                self.kb.combo("leftctrl", "leftshift", "v")
            else:
                self.kb.combo("leftctrl", "v")
            if press_enter:
                time.sleep(0.12)
                self.kb.combo("enter")
            if restore:
                time.sleep(self.RESTORE_DELAY)
                if saved:
                    self.clip.set(saved[1], saved[0])
                else:
                    self.clip.clear()

    def copy_selection(self, terminal: bool = False) -> str | None:
        """Selected text in the focused app via a Ctrl+C round trip. None if nothing selected."""
        with self._lock:
            saved = self.clip.get()
            sentinel = f"⁣talki-{time.monotonic_ns()}⁣"
            self.clip.set_text(sentinel, sensitive=True)
            time.sleep(0.06)
            self.kb.release_modifiers()
            self.kb.combo("leftctrl", "leftshift", "c") if terminal else self.kb.combo("leftctrl", "c")
            got = None
            for _ in range(10):
                time.sleep(0.04)
                got = self.clip.get_text()
                if got is not None and got != sentinel:
                    break
            if saved:
                self.clip.set(saved[1], saved[0])
            else:
                self.clip.clear()
            if got is None or got == sentinel or not got.strip():
                return None
            return got

    def copy_to_clipboard(self, text: str) -> None:
        self.clip.set_text(text)
