import json
import logging
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

RATE = 16000
CHUNK = 1600  # 100 ms
SPEECH_RMS = 0.01


def list_sources() -> list[tuple[str, str]]:
    """PipeWire capture nodes as (node.name, description)."""
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout
        objs = json.loads(out)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    res = []
    for o in objs:
        props = (o.get("info") or {}).get("props") or {}
        if props.get("media.class") == "Audio/Source":
            res.append((props.get("node.name", ""), props.get("node.description", "")))
    return res


def pick_source(preference: list[str]) -> str | None:
    present = {name for name, _ in list_sources()}
    for name in preference:
        if name in present:
            return name
    return None


def pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()


def write_wav(path: Path, audio: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm16(audio))


def read_wav(path: Path) -> np.ndarray:
    """Tolerates spool files left behind by a crash, whose header sizes are wrong."""
    raw = Path(path).read_bytes()
    data = raw[44:] if raw[:4] == b"RIFF" else raw
    data = data[: len(data) // 2 * 2]
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def speech_seconds(audio: np.ndarray) -> float:
    frame = 480
    n = len(audio) // frame
    if n == 0:
        return 0.0
    frames = audio[: n * frame].reshape(n, frame)
    rms = np.sqrt((frames**2).mean(axis=1))
    return float((rms > SPEECH_RMS).sum() * frame / RATE)


def normalize(audio: np.ndarray, whisper_mode: bool) -> np.ndarray:
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    if peak <= 1e-4:
        return audio
    if whisper_mode or peak < 0.25:
        gain = min(0.9 / peak, 20.0 if whisper_mode else 6.0)
        return (audio * gain).astype(np.float32)
    return audio


class Recorder:
    """Captures 16 kHz mono float32 from PipeWire via pw-record (exact node targeting),
    with sounddevice as a fallback. Audio is spooled to disk for crash recovery."""

    def __init__(self, on_level=None):
        self.on_level = on_level
        self._proc: subprocess.Popen | None = None
        self._sd_stream = None
        self._thread: threading.Thread | None = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._spool: wave.Wave_write | None = None
        self.spool_path: Path | None = None
        self.started_at = 0.0
        self.last_signal_at = 0.0
        self.running = False
        self.error: str | None = None

    def start(self, source: str | None, spool_dir: Path | None) -> None:
        self._chunks = []
        self.error = None
        self.started_at = self.last_signal_at = time.monotonic()
        if spool_dir:
            self.spool_path = spool_dir / f"rec-{int(time.time() * 1000)}.wav"
            self._spool = wave.open(str(self.spool_path), "wb")
            self._spool.setnchannels(1)
            self._spool.setsampwidth(2)
            self._spool.setframerate(RATE)
        else:
            self.spool_path = None
            self._spool = None
        self.running = True
        if shutil.which("pw-record"):
            cmd = [
                "pw-record", "--raw", "--rate", str(RATE), "--channels", "1", "--format", "f32",
                "--media-category", "Capture", "--media-role", "Communication",
                "-P", "{ node.description = \"Talki\", application.name = \"Talki\" }",
            ]
            if source:
                cmd += ["--target", source]
            cmd.append("-")
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self._thread = threading.Thread(target=self._read_pipe, daemon=True)
            self._thread.start()
        else:
            import sounddevice as sd

            def cb(indata, frames, t, status):
                self._push(indata[:, 0].copy())

            self._sd_stream = sd.InputStream(
                samplerate=RATE, channels=1, dtype="float32", blocksize=CHUNK, callback=cb
            )
            self._sd_stream.start()

    def _read_pipe(self) -> None:
        assert self._proc and self._proc.stdout
        size = CHUNK * 4
        while self.running:
            buf = self._proc.stdout.read(size)
            if not buf:
                if self.running:
                    self.error = "microphone stream ended"
                break
            buf = buf[: len(buf) // 4 * 4]
            self._push(np.frombuffer(buf, dtype="<f4").copy())

    def _push(self, chunk: np.ndarray) -> None:
        with self._lock:
            self._chunks.append(chunk)
            if self._spool:
                self._spool.writeframes(pcm16(chunk))
        rms = float(np.sqrt((chunk**2).mean())) if len(chunk) else 0.0
        if rms > SPEECH_RMS:
            self.last_signal_at = time.monotonic()
        if self.on_level:
            self.on_level(min(1.0, rms * 12))

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at if self.running else 0.0

    def stop(self) -> np.ndarray:
        self.running = False
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self._sd_stream:
            self._sd_stream.stop()
            self._sd_stream.close()
            self._sd_stream = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        with self._lock:
            if self._spool:
                self._spool.close()
                self._spool = None
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
            self._chunks = []
        return audio

    def discard_spool(self) -> None:
        if self.spool_path:
            self.spool_path.unlink(missing_ok=True)
            self.spool_path = None


def _tone(freqs: list[float], dur: float, rate: int = 48000) -> np.ndarray:
    parts = []
    for f in freqs:
        t = np.arange(int(rate * dur)) / rate
        env = np.minimum(1, np.minimum(t / 0.008, (dur - t) / 0.03))
        parts.append(np.sin(2 * np.pi * f * t) * env)
    return np.concatenate(parts).astype(np.float32)


CUES = {
    "start": _tone([660, 990], 0.07),
    "stop": _tone([990, 660], 0.07),
    "cancel": _tone([440, 330], 0.09),
    "error": _tone([300, 300], 0.12),
    "lock": _tone([660, 990, 1320], 0.06),
}


def play_cue(name: str, volume: float) -> None:
    def run():
        try:
            import sounddevice as sd

            sd.play(CUES[name] * volume, 48000, blocking=True)
        except Exception as e:  # audio output is optional
            log.debug("cue failed: %s", e)

    threading.Thread(target=run, daemon=True).start()


def play_audio(audio: np.ndarray) -> None:
    import sounddevice as sd

    sd.stop()
    sd.play(audio, RATE)
