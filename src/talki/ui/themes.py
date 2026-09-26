"""Bar colour presets and the optional light/dark palette for Talki's own window."""

from PySide6.QtGui import QColor, QPalette

# Colours are QML-compatible strings; 8-digit values are #AARRGGBB.
BAR_PRESETS: dict[str, dict] = {
    "midnight": {
        "background": "#141418", "border": "#33ffffff", "text": "#f2f2f2", "accent": "#f2f2f2",
        "command": "#a89bff", "recording": "#ff4d4f", "ready": "#3ecf6e",
    },
    "light": {
        "background": "#f7f7f9", "border": "#26000000", "text": "#1d1d22", "accent": "#2b2b33",
        "command": "#6a55e8", "recording": "#e5484d", "ready": "#1f9d55",
    },
    "ocean": {
        "background": "#0b2233", "border": "#5555c2ff", "text": "#e8f6ff", "accent": "#5cc8ff",
        "command": "#b69cff", "recording": "#ff6b6b", "ready": "#45e0a8",
    },
    "sunset": {
        "background": "#2a1426", "border": "#66ff9d6b", "text": "#fff1e6", "accent": "#ffb36b",
        "command": "#ff7ab8", "recording": "#ff4d4f", "ready": "#ffd166",
    },
    "forest": {
        "background": "#102117", "border": "#5568d391", "text": "#e9f7ee", "accent": "#7ee2a8",
        "command": "#c3a6ff", "recording": "#ff5d5d", "ready": "#7ee2a8",
    },
    "neon": {
        "background": "#0a0a12", "border": "#cc00e5ff", "text": "#ffffff", "accent": "#00e5ff",
        "command": "#ff2bd6", "recording": "#ff2b4e", "ready": "#39ff14",
    },
}

BAR_DEFAULTS = {**BAR_PRESETS["midnight"], "opacity": 0.9, "roundness": 1.0, "label": "Talki"}

COLOR_KEYS = {
    "background": "Background",
    "border": "Border",
    "text": "Text",
    "accent": "Waveform",
    "command": "Command mode",
    "recording": "Recording",
    "ready": "Ready dot",
}


def bar_theme(cfg) -> dict:
    return {**BAR_DEFAULTS, **(cfg.get("ui.bar_theme") or {})}


def matching_preset(theme: dict) -> str | None:
    for name, colors in BAR_PRESETS.items():
        if all(theme.get(k, "").lower() == v.lower() for k, v in colors.items()):
            return name
    return None


def _palette(dark: bool) -> QPalette:
    p = QPalette()
    if dark:
        base, alt, window, text, button, hl = "#1b1b1f", "#232329", "#141417", "#e8e8ec", "#26262c", "#7c6cf0"
    else:
        base, alt, window, text, button, hl = "#ffffff", "#f3f3f6", "#ececf0", "#1d1d22", "#f7f7f9", "#5b4ae0"
    roles = {
        QPalette.ColorRole.Window: window, QPalette.ColorRole.WindowText: text,
        QPalette.ColorRole.Base: base, QPalette.ColorRole.AlternateBase: alt,
        QPalette.ColorRole.Text: text, QPalette.ColorRole.Button: button,
        QPalette.ColorRole.ButtonText: text, QPalette.ColorRole.ToolTipBase: base,
        QPalette.ColorRole.ToolTipText: text, QPalette.ColorRole.Highlight: hl,
        QPalette.ColorRole.HighlightedText: "#ffffff", QPalette.ColorRole.Link: hl,
        QPalette.ColorRole.PlaceholderText: "#8a8a94", QPalette.ColorRole.Mid: "#5a5a64" if dark else "#c4c4cc",
    }
    for role, col in roles.items():
        p.setColor(role, QColor(col))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#77777f" if dark else "#9a9aa3"))
    return p


class AppTheme:
    """System follows the desktop (Breeze on KDE); light/dark use Fusion with a fixed palette."""

    def __init__(self, app):
        self.app = app
        self.system_style = app.style().name()
        self.system_palette = app.palette()

    def apply(self, mode: str) -> None:
        if mode in ("light", "dark"):
            self.app.setStyle("Fusion")
            self.app.setPalette(_palette(mode == "dark"))
        else:
            self.app.setStyle(self.system_style)
            self.app.setPalette(self.system_palette)
