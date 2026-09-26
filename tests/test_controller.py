"""Hotkey state machine: hold, double-tap lock, triple-tap cancel, hands-free toggle."""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from talki import controller as ctl_mod  # noqa: E402
from talki.config import Config  # noqa: E402
from talki.db import Store  # noqa: E402
from talki.context import Context, WindowInfo  # noqa: E402


class FakeRecorder:
    def __init__(self, on_level=None):
        self.running = False
        self.spool_path = None
        self.error = None
        self.started_at = self.last_signal_at = 0.0

    def start(self, source, spool_dir):
        self.running = True

    def stop(self):
        self.running = False
        return np.zeros(16000, np.float32)

    def elapsed(self):
        return 0.0

    def discard_spool(self):
        pass


class FakeInjector:
    def __init__(self):
        self.kb = type("KB", (), {"close": lambda self: None})()
        self.pasted, self.copied = [], []

    def paste(self, text, terminal=False, press_enter=False, restore=False):
        self.pasted.append(text)

    def copy_to_clipboard(self, text):
        self.copied.append(text)


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    clock = Clock()
    monkeypatch.setattr(ctl_mod.time, "monotonic", clock)
    monkeypatch.setattr(ctl_mod.au, "Recorder", FakeRecorder)
    monkeypatch.setattr(ctl_mod.au, "pick_source", lambda pref: None)
    monkeypatch.setattr(ctl_mod, "Injector", FakeInjector)
    monkeypatch.setattr(ctl_mod, "active_window", lambda: WindowInfo())
    cfg = Config(tmp_path / "c.json")
    cfg.set("privacy.context_awareness", False)
    c = ctl_mod.Controller(cfg, Store(tmp_path / "t.db"))
    c.cues = []
    c._cue = c.cues.append
    c.clock = clock
    yield c
    c.pool.shutdown(wait=True)


def at(c, dt):
    c.clock.t += dt


def test_hold_and_release_stops(ctl):
    ctl.on_hotkey("ptt", True)
    assert ctl.state == "recording" and not ctl.locked
    at(ctl, 1.0)
    ctl.on_hotkey("ptt", False)
    assert ctl.cues == ["start", "stop"]
    assert ctl.state == "idle"  # silent audio: nothing to process


def test_autorepeat_is_ignored(ctl):
    ctl.on_hotkey("ptt", True)
    for _ in range(5):
        at(ctl, 0.03)
        ctl.on_hotkey("ptt", True)
    assert ctl.state == "recording" and ctl.cues == ["start"]


def test_single_tap_cancels_quietly(ctl):
    ctl.on_hotkey("ptt", True)
    at(ctl, 0.1)
    ctl.on_hotkey("ptt", False)
    assert ctl.tap_pending
    ctl._tap_expired()
    assert ctl.state == "idle" and ctl.cues == ["start"]


def test_double_tap_locks_then_press_stops(ctl):
    ctl.on_hotkey("ptt", True)
    at(ctl, 0.1)
    ctl.on_hotkey("ptt", False)
    at(ctl, 0.15)
    ctl.on_hotkey("ptt", True)
    assert ctl.locked and ctl.cues == ["start", "lock"]
    at(ctl, 0.1)
    ctl.on_hotkey("ptt", False)
    assert ctl.state == "recording"
    at(ctl, 3.0)
    ctl.on_hotkey("ptt", True)
    assert ctl.cues[-1] == "stop" and ctl.state == "idle"


def test_triple_tap_cancels(ctl):
    for _ in range(2):
        ctl.on_hotkey("ptt", True)
        at(ctl, 0.08)
        ctl.on_hotkey("ptt", False)
        at(ctl, 0.08)
    assert ctl.locked
    ctl.on_hotkey("ptt", True)
    assert ctl.cues[-1] == "cancel" and ctl.state == "idle"


def test_handsfree_toggle(ctl):
    ctl.on_hotkey("handsfree", True)
    assert ctl.locked
    at(ctl, 2.0)
    ctl.on_hotkey("handsfree", True)
    assert ctl.cues[-1] == "stop" and ctl.state == "idle"


def test_handsfree_double_press_cancels(ctl):
    ctl.on_hotkey("handsfree", True)
    at(ctl, 0.2)
    ctl.on_hotkey("handsfree", True)
    assert ctl.cues[-1] == "cancel"


def test_ptt_then_command_switches_mode(ctl):
    ctl.on_hotkey("ptt", True)
    at(ctl, 0.1)
    ctl.on_hotkey("command", True)
    assert ctl.mode == "command" and ctl.held_by == "command"


def test_missed_release_recovers(ctl):
    ctl.on_hotkey("ptt", True)
    at(ctl, 5.0)
    # No release arrived; the next press after a long gap ends the recording.
    ctl.on_hotkey("ptt", True)
    assert ctl.cues[-1] == "stop"


def _deliver(ctl, monkeypatch, start, now):
    monkeypatch.setattr(ctl_mod, "active_window", lambda: now)
    ctx = Context(window=start)
    ctl._deliver("hello", ctx)
    return ctl.injector.pasted, ctl.injector.copied


def test_deliver_pastes_into_same_window(ctl, monkeypatch):
    w = WindowInfo("kate", "doc", 42)
    assert _deliver(ctl, monkeypatch, w, w) == (["hello"], [])


def test_deliver_copies_when_focus_moved(ctl, monkeypatch):
    start = WindowInfo("kate", "doc", 42)
    assert _deliver(ctl, monkeypatch, start, WindowInfo("firefox", "x", 7)) == ([], ["hello"])


def test_deliver_never_pastes_on_desktop(ctl, monkeypatch):
    desk = WindowInfo("plasmashell", "Desktop", 5)
    assert _deliver(ctl, monkeypatch, desk, desk) == ([], ["hello"])
    start = WindowInfo("kate", "doc", 42)
    assert _deliver(ctl, monkeypatch, start, WindowInfo()) == ([], ["hello", "hello"])
