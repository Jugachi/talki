"""Global hotkey backends. Each reports (action, pressed) to a callback from its own thread."""

ACTIONS = {
    "ptt": ("Talki: push-to-talk (hold, double-tap for hands-free)", "CTRL+LOGO+space"),
    "handsfree": ("Talki: hands-free dictation (toggle)", "CTRL+LOGO+H"),
    "command": ("Talki: command mode (hold, speak an edit)", "CTRL+LOGO+C"),
    "cancel": ("Talki: cancel recording", "CTRL+LOGO+Escape"),
    "paste_last": ("Talki: paste last transcript", "CTRL+LOGO+V"),
    "copy_last": ("Talki: copy last transcript", "CTRL+SHIFT+LOGO+C"),
    "scratchpad": ("Talki: dictate into a new scratchpad note", "CTRL+LOGO+N"),
    **{
        f"transform_{i}": (f"Talki: transform selection (slot {i})", f"CTRL+LOGO+{i}")
        for i in range(1, 10)
    },
}
