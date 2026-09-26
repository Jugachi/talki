"""Raw evdev hotkeys. Works on every compositor and allows modifier-only chords and mouse
buttons, but reads all keyboard input, so it needs the user in the "input" group."""

import logging
import select
import threading
import time

log = logging.getLogger(__name__)

# Left/right variants are treated as the same key.
ALIASES = {
    "KEY_RIGHTCTRL": "KEY_LEFTCTRL",
    "KEY_RIGHTSHIFT": "KEY_LEFTSHIFT",
    "KEY_RIGHTALT": "KEY_LEFTALT",
    "KEY_RIGHTMETA": "KEY_LEFTMETA",
}


def parse_chord(spec: str) -> frozenset[str]:
    return frozenset(ALIASES.get(k.strip().upper(), k.strip().upper()) for k in spec.split("+") if k.strip())


class EvdevHotkeys:
    def __init__(self, bindings: dict[str, str], callback, on_status=None):
        from evdev import ecodes

        self.ecodes = ecodes
        self.bindings = {a: parse_chord(s) for a, s in bindings.items() if s}
        self.callback = callback
        self.on_status = on_status or (lambda s: None)
        self.pressed: set[str] = set()
        self.active: str | None = None
        self.devices = {}
        self._stop = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="evdev-hotkeys")

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop = True

    def _scan(self) -> None:
        import evdev

        e = self.ecodes
        for path in evdev.list_devices():
            if path in self.devices:
                continue
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue
            keys = dev.capabilities().get(e.EV_KEY, [])
            if dev.name == "talki-virtual-keyboard" or not (e.KEY_A in keys or e.BTN_SIDE in keys):
                dev.close()
                continue
            self.devices[path] = dev

    def _name(self, code: int) -> str | None:
        n = self.ecodes.KEY.get(code) or self.ecodes.BTN.get(code)
        if isinstance(n, list):
            n = n[0]
        return ALIASES.get(n, n) if n else None

    def _update(self) -> None:
        match = None
        for action, chord in self.bindings.items():
            if chord == self.pressed:
                match = action
                break
        if match == self.active:
            return
        if self.active:
            self.callback(self.active, False)
        if match:
            self.callback(match, True)
        self.active = match

    def _run(self) -> None:
        self._scan()
        if not self.devices:
            self.on_status("No readable input devices. Add yourself to the 'input' group and log in again.")
            return
        self.on_status("ready")
        last_scan = time.monotonic()
        while not self._stop:
            fds = {d.fd: p for p, d in self.devices.items()}
            r, _, _ = select.select(list(fds), [], [], 2.0)
            for fd in r:
                path = fds[fd]
                dev = self.devices.get(path)
                try:
                    for ev in dev.read():
                        if ev.type != self.ecodes.EV_KEY or ev.value == 2:
                            continue
                        name = self._name(ev.code)
                        if not name:
                            continue
                        if ev.value == 1:
                            self.pressed.add(name)
                        else:
                            self.pressed.discard(name)
                        self._update()
                except OSError:
                    self.devices.pop(path, None)
            if time.monotonic() - last_scan > 5:
                self._scan()
                last_scan = time.monotonic()
