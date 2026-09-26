"""`talki doctor`: report what works on this machine and how to fix what doesn't."""

import grp
import os
import shutil
from pathlib import Path

from .paths import APP_ID

OK, WARN, FAIL = "✔", "!", "✘"


def _line(mark: str, what: str, fix: str = "") -> None:
    print(f" {mark} {what}" + (f"\n     → {fix}" if fix else ""))


def run() -> int:
    from .config import Config

    cfg = Config()
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "?")
    print(f"Session: {'Wayland' if wayland else 'X11'} / {desktop}\n")
    bad = 0

    def need(cond: bool, what: str, fix: str, hard: bool = True) -> None:
        nonlocal bad
        if cond:
            _line(OK, what)
        else:
            _line(FAIL if hard else WARN, what, fix)
            bad += hard

    need(shutil.which("pw-record") is not None, "PipeWire capture (pw-record)",
         "sudo pacman -S pipewire-audio")
    if wayland:
        need(shutil.which("wl-copy") is not None, "Clipboard (wl-clipboard)",
             "sudo pacman -S wl-clipboard")
    else:
        need(shutil.which("xclip") is not None, "Clipboard (xclip)", "sudo pacman -S xclip")
    need(os.access("/dev/uinput", os.W_OK), "Virtual keyboard (/dev/uinput writable)",
         "Run scripts/install.sh, which adds a udev rule granting your seat access to /dev/uinput")
    if "KDE" in desktop and wayland:
        need(shutil.which("kdotool") is not None, "Active window detection (kdotool)",
             "AUR package: yay -S kdotool (or paru -S kdotool)", hard=False)
    try:
        import PySide6

        need(PySide6.__file__.startswith("/usr/"), f"PySide6 {PySide6.__version__} from the system",
             "Use the distro package (pacman -S pyside6) so the layer-shell overlay plugin matches Qt", hard=False)
    except ImportError:
        need(False, "PySide6", "sudo pacman -S pyside6")
    need(Path("/usr/lib/qt6/qml/org/kde/layershell").exists(),
         "Layer-shell overlay (layer-shell-qt)", "sudo pacman -S layer-shell-qt", hard=False)
    try:
        import pyatspi  # noqa: F401

        _line(OK, "Accessibility context (python-atspi)")
    except ImportError:
        _line(WARN, "Accessibility context (python-atspi)",
              "Optional: sudo pacman -S python-atspi")
    desktop_file = Path.home() / ".local/share/applications" / f"{APP_ID}.desktop"
    if cfg.get("hotkeys.backend") == "portal":
        need(desktop_file.exists() or Path(f"/usr/share/applications/{APP_ID}.desktop").exists(),
             "Desktop entry for portal shortcuts", "Run scripts/install.sh")
    if cfg.get("hotkeys.backend") == "evdev":
        try:
            in_input = grp.getgrnam("input").gr_gid in os.getgroups()
        except KeyError:
            in_input = False
        need(in_input, "Member of 'input' group (evdev hotkeys)", "sudo usermod -aG input $USER, then log in again")

    from .llm import Ollama

    llm = Ollama(cfg.get("llm.url"), cfg.get("llm.model"), "0", 3, cfg.get("llm.api"))
    models = llm.models()
    if not cfg.get("llm.enabled"):
        _line(WARN, "AI cleanup disabled in settings")
    elif not models and not llm.available():
        _line(WARN, "Ollama not reachable", "sudo pacman -S ollama-rocm && sudo systemctl enable --now ollama")
    else:
        need(llm.available(), f"Ollama model {cfg.get('llm.model')}", f"ollama pull {cfg.get('llm.model')}", hard=False)
    print("\nAll required pieces are in place." if not bad else f"\n{bad} required item(s) missing.")
    return 1 if bad else 0
