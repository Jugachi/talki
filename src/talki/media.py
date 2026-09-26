"""Pause media players (MPRIS via playerctl) or mute the default sink while dictating."""

import shutil
import subprocess


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


class MediaDucker:
    def __init__(self):
        self.paused: list[str] = []
        self.muted = False

    def duck(self) -> None:
        self.paused, self.muted = [], False
        if shutil.which("playerctl"):
            for player in _run(["playerctl", "-l"]).splitlines():
                if _run(["playerctl", "-p", player, "status"]) == "Playing":
                    _run(["playerctl", "-p", player, "pause"])
                    self.paused.append(player)
            return
        if shutil.which("wpctl"):
            state = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
            if "MUTED" not in state:
                _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"])
                self.muted = True

    def restore(self) -> None:
        for player in self.paused:
            _run(["playerctl", "-p", player, "play"])
        if self.muted:
            _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"])
        self.paused, self.muted = [], False
