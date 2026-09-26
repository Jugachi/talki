"""Unix socket control interface used by `talki ctl` and compositor keybindings."""

import logging
import os
import socket
import threading

from .paths import socket_path

log = logging.getLogger(__name__)

COMMANDS = {
    "toggle": "start or stop hands-free dictation",
    "start": "start hands-free dictation",
    "stop": "stop recording and insert the text",
    "cancel": "discard the current recording",
    "ptt-down": "push-to-talk pressed (bind to key press)",
    "ptt-up": "push-to-talk released (bind to key release)",
    "command-down": "command mode pressed",
    "command-up": "command mode released",
    "paste-last": "paste the last transcript",
    "copy-last": "copy the last transcript to the clipboard",
    "scratchpad": "dictate into a new scratchpad note",
    "transform": "transform the selection: transform <slot 1-9>",
    "show": "open the Talki window",
    "status": "print the current state",
    "quit": "quit Talki",
}


class IPCServer:
    def __init__(self, handler):
        self.handler = handler
        self.path = socket_path()

    def start(self) -> bool:
        if self.path.exists():
            if send("status") is not None:
                return False
            self.path.unlink()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.sock.listen(8)
        threading.Thread(target=self._serve, daemon=True, name="ipc").start()
        return True

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    data = conn.recv(4096).decode().strip().split()
                    if not data:
                        continue
                    reply = self.handler(data[0], data[1:]) or "ok"
                    conn.sendall(reply.encode())
                except Exception as e:
                    log.exception("ipc error")
                    try:
                        conn.sendall(f"error: {e}".encode())
                    except OSError:
                        pass

    def close(self) -> None:
        try:
            self.sock.close()
            self.path.unlink(missing_ok=True)
        except (OSError, AttributeError):
            pass


def send(cmd: str, *args: str) -> str | None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(str(socket_path()))
            s.sendall(" ".join((cmd, *args)).encode())
            s.shutdown(socket.SHUT_WR)
            return s.recv(65536).decode()
    except OSError:
        return None
