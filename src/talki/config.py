import copy
import json
import logging
import threading

from .paths import CONFIG_PATH, ensure_dirs

log = logging.getLogger(__name__)

CATEGORIES = ("personal", "work", "email", "other")
STYLES = ("formal", "casual", "very_casual", "excited")
CLEANUP_LEVELS = ("none", "light", "medium", "high")

DEFAULTS: dict = {
    "hotkeys": {
        # portal: xdg-desktop-portal GlobalShortcuts (KDE, GNOME 48+). evdev: raw input,
        # needs the "input" group but allows modifier-only chords. none: use `talki ctl`.
        "backend": "portal",
        "tap_window_ms": 500,
        "hold_threshold_ms": 300,
        "evdev": {
            "ptt": "KEY_LEFTCTRL+KEY_LEFTMETA",
            "handsfree": "KEY_LEFTCTRL+KEY_LEFTMETA+KEY_SPACE",
            "command": "KEY_LEFTCTRL+KEY_LEFTMETA+KEY_LEFTALT",
            "cancel": "KEY_ESC",
            "paste_last": "KEY_LEFTSHIFT+KEY_LEFTALT+KEY_Z",
            "copy_last": "KEY_LEFTSHIFT+KEY_LEFTALT+KEY_X",
            "scratchpad": "KEY_LEFTALT+KEY_LEFTMETA+KEY_S",
        },
    },
    "audio": {
        # Ranked PipeWire node names. First one present wins; empty means system default.
        "mic_preference": [],
        "sounds": True,
        "sound_volume": 0.35,
        "mute_media": False,
        "whisper_mode": False,
        "max_minutes": 20,
        "silence_stop_seconds": 0,
    },
    "stt": {
        "backend": "faster-whisper",
        "model": "large-v3-turbo",
        "device": "cpu",
        "compute_type": "int8",
        "threads": 0,
        "server_url": "http://127.0.0.1:8178",
        # Empty list means auto-detect across all languages.
        "languages": [],
        # Shown in the bar's quick language menu.
        "language_favorites": ["en", "de", "es", "fr"],
        "vad": True,
    },
    "llm": {
        "enabled": True,
        # "ollama" (native API) or "openai" (llama-server, LM Studio, vLLM).
        "api": "ollama",
        "url": "http://127.0.0.1:11434",
        "model": "qwen3.5:4b",
        "keep_alive": "30m",
        "timeout": 30,
        "min_words": 3,
    },
    "cleanup": {
        "level": "light",
        "smart_formatting": True,
        "press_enter_command": False,
        "drop_period_in_messages": True,
        "ide_features": True,
    },
    "styles": {"personal": "casual", "work": "formal", "email": "formal", "other": "formal"},
    # Lowercased window class (or substring) to category. First match wins.
    "app_categories": {
        "signal": "personal",
        "whatsapp": "personal",
        "telegram": "personal",
        "discord": "personal",
        "vesktop": "personal",
        "element": "personal",
        "instagram": "personal",
        "slack": "work",
        "teams": "work",
        "mattermost": "work",
        "linkedin": "work",
        "zulip": "work",
        "thunderbird": "email",
        "evolution": "email",
        "kmail": "email",
        "geary": "email",
        "betterbird": "email",
        "mailspring": "email",
    },
    # Browser tabs are matched by window title instead of class.
    "title_categories": {
        "gmail": "email",
        "outlook": "email",
        "proton mail": "email",
        "whatsapp": "personal",
        "messenger": "personal",
        "discord": "personal",
        "slack": "work",
        "linkedin": "work",
        "microsoft teams": "work",
    },
    "messaging_apps": [
        "signal", "whatsapp", "telegram", "discord", "vesktop", "element",
        "slack", "teams", "mattermost", "zulip", "messenger",
    ],
    "terminals": [
        "konsole", "kitty", "alacritty", "foot", "wezterm", "ghostty", "gnome-terminal",
        "xterm", "tilix", "terminator", "yakuake", "st-256color", "urxvt", "ptyxis", "rio",
    ],
    # Never auto-paste here (Ctrl+V would create a file); the text only goes to the clipboard.
    "no_paste_apps": [
        "plasmashell", "dolphin", "nautilus", "thunar", "nemo", "pcmanfm", "caja", "krunner",
        "spectacle", "dev.talki", "talki", "xdg-desktop-portal",
    ],
    "ides": ["code", "cursor", "windsurf", "vscodium", "zed", "jetbrains", "kate", "neovide"],
    "transforms": [
        {
            "name": "Polish",
            "instruction": "Rewrite the text so it reads clearly and professionally. Fix grammar, "
            "tighten wording and improve flow while keeping the meaning, facts and voice.",
        },
        {
            "name": "Prompt Engineer",
            "instruction": "Turn the text into a clear, well structured prompt for an AI assistant. "
            "State the goal, relevant context, constraints and the desired output format.",
        },
    ],
    "ui": {
        "bar_always_visible": True,
        "bar_edge": "bottom",
        # Size of the bar, 0.6 to 2.0.
        "bar_scale": 1.0,
        # Bar colours, opacity, roundness and idle label; see ui/themes.py for keys.
        "bar_theme": {},
        # Talki window: "system", "light" or "dark".
        "app_theme": "system",
        # [x, y] in logical pixels on the active screen once the bar has been dragged.
        "bar_pos": None,
        # Monitor (connector name, e.g. DP-1) the bar was left on.
        "bar_screen": None,
        "quiet_startup": True,
        "notifications": True,
        "autostart": False,
    },
    "privacy": {
        "store_history": True,
        "store_audio": True,
        "audio_retention_days": 14,
        "history_retention_days": 0,
        "context_awareness": True,
    },
    "dictionary_auto_add": True,
    # Leave each result on the clipboard (replacing what was there) after pasting it.
    "keep_in_clipboard": True,
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in (
            "app_categories", "title_categories", "styles", "evdev"
        ):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """Thread-safe JSON settings with dotted-path access."""

    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        ensure_dirs()
        if self.path.exists():
            try:
                self.data = _merge(DEFAULTS, json.loads(self.path.read_text()))
            except (OSError, json.JSONDecodeError) as e:
                log.error("config unreadable, using defaults: %s", e)
        else:
            self.save()

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))
            tmp.replace(self.path)

    def get(self, dotted: str, default=None):
        with self._lock:
            node = self.data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, dotted: str, value, save: bool = True) -> None:
        with self._lock:
            parts = dotted.split(".")
            node = self.data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
            if save:
                self.save()
