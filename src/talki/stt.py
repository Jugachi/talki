import io
import logging
import os
import threading
from dataclasses import dataclass

import httpx
import numpy as np

from .audio import RATE, pcm16

log = logging.getLogger(__name__)

# Whisper's prompt window is 224 tokens; stay well under it.
PROMPT_CHARS = 600


@dataclass
class STTResult:
    text: str
    language: str


def build_prompt(glossary: list[str]) -> str | None:
    if not glossary:
        return None
    out, size = [], 0
    for w in glossary:
        if size + len(w) + 2 > PROMPT_CHARS:
            break
        out.append(w)
        size += len(w) + 2
    return "Glossary: " + ", ".join(out) + "."


class FasterWhisperSTT:
    def __init__(self, model: str, device: str, compute_type: str, threads: int, vad: bool):
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.threads = threads or max(1, (os.cpu_count() or 4) // 2)
        self.vad = vad
        self._model = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                log.info("loading whisper model %s (%s/%s)", self.model_name, self.device, self.compute_type)
                kw = dict(device=self.device, compute_type=self.compute_type, cpu_threads=self.threads)
                try:
                    # Stay offline once the model is cached.
                    self._model = WhisperModel(self.model_name, local_files_only=True, **kw)
                except Exception:
                    log.info("model not cached yet, downloading %s", self.model_name)
                    self._model = WhisperModel(self.model_name, **kw)

    def transcribe(self, audio: np.ndarray, languages: list[str], glossary: list[str]) -> STTResult:
        self.load()
        language = None
        if len(languages) == 1:
            language = languages[0]
        elif len(languages) > 1:
            # Restrict detection to the user's languages, once per dictation.
            _, _, probs = self._model.detect_language(audio[: RATE * 30])
            allowed = [(lang, p) for lang, p in probs if lang in languages]
            language = max(allowed, key=lambda x: x[1])[0] if allowed else languages[0]
        segments, info = self._model.transcribe(
            audio,
            language=language,
            initial_prompt=build_prompt(glossary),
            vad_filter=self.vad,
            beam_size=5,
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return STTResult(text, info.language or language or "")


class WhisperServerSTT:
    """whisper.cpp `whisper-server`, e.g. built with Vulkan for AMD GPUs."""

    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def load(self) -> None:
        try:
            httpx.get(self.url, timeout=3)
        except httpx.HTTPError as e:
            raise RuntimeError(f"whisper.cpp server not reachable at {self.url}") from e

    def transcribe(self, audio: np.ndarray, languages: list[str], glossary: list[str]) -> STTResult:
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm16(audio))
        wav = buf.getvalue()
        prompt = build_prompt(glossary)
        js = self._post(wav, languages[0] if len(languages) == 1 else "auto", prompt)
        probs = js.get("language_probabilities") or {}
        lang = js.get("detected_language") or _code(js.get("language", ""))
        if len(languages) > 1 and lang not in languages:
            allowed = [(code, p) for code, p in probs.items() if code in languages]
            lang = max(allowed, key=lambda x: x[1])[0] if allowed else languages[0]
            js = self._post(wav, lang, prompt)
        return STTResult(" ".join((js.get("text") or "").split()), lang)

    def _post(self, wav: bytes, language: str, prompt: str | None) -> dict:
        data = {"response_format": "verbose_json", "temperature": "0.0", "language": language}
        if prompt:
            data["prompt"] = prompt
        r = httpx.post(
            f"{self.url}/inference",
            files={"file": ("audio.wav", wav, "audio/wav")},
            data=data,
            timeout=120,
        )
        r.raise_for_status()
        return r.json()


def _code(name: str) -> str:
    """whisper.cpp reports full language names ("german")."""
    from .languages import LANGUAGES

    name = name.lower()
    for code, full in LANGUAGES.items():
        if full.lower() == name:
            return code
    return name


def make_stt(cfg) -> FasterWhisperSTT | WhisperServerSTT:
    if cfg.get("stt.backend") == "whisper-server":
        return WhisperServerSTT(cfg.get("stt.server_url"))
    return FasterWhisperSTT(
        cfg.get("stt.model"),
        cfg.get("stt.device"),
        cfg.get("stt.compute_type"),
        cfg.get("stt.threads"),
        cfg.get("stt.vad"),
    )
