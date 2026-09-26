"""Recording state machine and background processing. Lives on the Qt main thread; hotkey
and IPC events are marshalled onto it through signals."""

import concurrent.futures
import difflib
import logging
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from . import audio as au
from . import textproc as tp
from .context import A11yTracker, Context, active_window, classify
from .inject import Injector, InjectError
from .llm import LLMError
from .media import MediaDucker
from .paths import AUDIO_DIR, SPOOL_DIR
from .pipeline import Pipeline
from .stt import make_stt

log = logging.getLogger(__name__)


class Controller(QObject):
    state_changed = Signal(str, str)  # state (idle/recording/locked/processing), mode
    level = Signal(float)
    message = Signal(str, str)  # text, kind (info/warn/error)
    history_changed = Signal()
    scratchpad_text = Signal(str)
    open_scratchpad = Signal()
    show_window = Signal()
    quit_requested = Signal()
    model_status = Signal(str)
    progress = Signal(str)  # short status shown in the bar while processing

    _hotkey = Signal(str, bool)
    _ipc = Signal(str, list)
    _finished = Signal()

    def __init__(self, cfg, store):
        super().__init__()
        self.cfg = cfg
        self.store = store
        self.stt = make_stt(cfg)
        self.pipeline = Pipeline(cfg, store, self.stt)
        self.injector = Injector()
        self.media = MediaDucker()
        self.a11y = A11yTracker() if cfg.get("privacy.context_awareness") else None
        self.recorder = au.Recorder(on_level=self.level.emit)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)

        self.state = "idle"
        self.mode = "dictation"
        self.locked = False
        self.held_by: str | None = None
        self.press_time = 0.0
        self.lock_time = 0.0
        self.started = 0.0
        self.tap_pending = False
        self.keys_down: dict[str, float] = {}
        self.ctx_future: concurrent.futures.Future | None = None
        self.warned_limit = False
        self.warned_silent = False

        self.tap_timer = QTimer(self, singleShot=True)
        self.tap_timer.timeout.connect(self._tap_expired)
        self.tick = QTimer(self, interval=250)
        self.tick.timeout.connect(self._on_tick)

        self._hotkey.connect(self.on_hotkey)
        self._ipc.connect(self._on_ipc)
        self._finished.connect(self._finish)

    # threads -> Qt thread
    def hotkey_event(self, action: str, pressed: bool) -> None:
        self._hotkey.emit(action, pressed)

    def ipc_event(self, cmd: str, args: list) -> str:
        if cmd == "status":
            return f"{self.state} {self.mode}{' locked' if self.locked else ''}"
        self._ipc.emit(cmd, args)
        return "ok"

    def warm_up(self) -> None:
        def run():
            self.model_status.emit("Loading speech model…")
            try:
                self.stt.load()
                self.model_status.emit("Speech model ready")
            except Exception as e:
                log.exception("STT load failed")
                self.model_status.emit(f"Speech model failed: {e}")
                self.message.emit(f"Speech model failed to load: {e}", "error")
            llm = self.pipeline.llm()
            if llm and self.cfg.get("cleanup.level") != "none":
                if llm.available():
                    llm.warm()
                else:
                    self.message.emit(
                        f"LLM model '{llm.model}' not reachable at {llm.url}. AI cleanup falls back to basic formatting.",
                        "warn",
                    )
            self._recover_spool()

        threading.Thread(target=run, daemon=True).start()

    # hotkeys
    @Slot(str, bool)
    def on_hotkey(self, action: str, pressed: bool) -> None:
        if action in ("ptt", "command"):
            self._hold_key(action, pressed)
        elif not pressed:
            return
        elif action == "handsfree":
            self._handsfree()
        elif action == "cancel":
            if self.state == "recording":
                self.cancel()
        elif action == "paste_last":
            self.paste_last()
        elif action == "copy_last":
            self.copy_last()
        elif action == "scratchpad":
            self.start_scratchpad()
        elif action.startswith("transform_"):
            self.transform(int(action.split("_")[1]))

    @Slot(str, list)
    def _on_ipc(self, cmd: str, args: list) -> None:
        if cmd == "toggle":
            self._handsfree()
        elif cmd == "start" and self.state == "idle":
            self._handsfree()
        elif cmd == "stop" and self.state == "recording":
            self.stop_and_process()
        elif cmd == "cancel" and self.state == "recording":
            self.cancel()
        elif cmd in ("ptt-down", "ptt-up"):
            self._hold_key("ptt", cmd.endswith("down"))
        elif cmd in ("command-down", "command-up"):
            self._hold_key("command", cmd.endswith("down"))
        elif cmd == "paste-last":
            self.paste_last()
        elif cmd == "copy-last":
            self.copy_last()
        elif cmd == "scratchpad":
            self.start_scratchpad()
        elif cmd == "transform" and args and args[0].isdigit():
            self.transform(int(args[0]))
        elif cmd == "show":
            self.show_window.emit()
        elif cmd == "quit":
            self.quit_requested.emit()

    def _tap_window(self) -> float:
        return self.cfg.get("hotkeys.tap_window_ms") / 1000

    def _lock(self, mode: str | None = None) -> None:
        self.tap_timer.stop()
        self.tap_pending = False
        self.locked = True
        self.held_by = None
        self.lock_time = time.monotonic()
        if mode:
            self.mode = mode
        self._cue("lock")
        self._emit_state()

    def _hold_key(self, action: str, pressed: bool) -> None:
        mode = "dictation" if action == "ptt" else "command"
        now = time.monotonic()
        if pressed:
            last = self.keys_down.get(action)
            self.keys_down[action] = now
            # Autorepeat arrives every few dozen ms; a long gap means we missed a release.
            if last is not None and now - last < 1.5:
                return
            if self.state == "idle":
                self.press_time = now
                self.start(mode, locked=False, held_by=action)
            elif self.state == "recording":
                if self.tap_pending:
                    self._lock(mode)
                elif self.locked:
                    if now - self.lock_time < self._tap_window():
                        self.cancel()
                    else:
                        self.stop_and_process()
                elif self.held_by and self.held_by != action and now - self.started < 0.8:
                    self.mode, self.held_by, self.press_time = mode, action, now
                    self._emit_state()
                elif self.held_by == action:
                    self.stop_and_process()
            return
        self.keys_down.pop(action, None)
        if self.state != "recording" or self.locked or self.held_by != action:
            return
        if now - self.press_time < self.cfg.get("hotkeys.hold_threshold_ms") / 1000:
            self.tap_pending = True
            self.tap_timer.start(int(self._tap_window() * 1000))
        else:
            self.stop_and_process()

    def _handsfree(self) -> None:
        now = time.monotonic()
        if self.state == "idle":
            self.start("dictation", locked=True)
            self.lock_time = now
        elif self.state == "recording":
            if self.tap_pending or not self.locked:
                self._lock()
            elif now - self.lock_time < self._tap_window():
                self.cancel()
            else:
                self.stop_and_process()

    def _tap_expired(self) -> None:
        if self.tap_pending and self.state == "recording":
            self.tap_pending = False
            self.cancel(quiet=True)

    # recording
    def _cue(self, name: str) -> None:
        if self.cfg.get("audio.sounds"):
            au.play_cue(name, self.cfg.get("audio.sound_volume"))

    def _emit_state(self) -> None:
        state = self.state
        if state == "recording" and self.locked:
            state = "locked"
        self.state_changed.emit(state, self.mode)

    def _capture_context(self, mode: str) -> Context:
        ctx = Context()
        if mode == "scratchpad":
            return ctx
        try:
            ctx.window = active_window()
            ctx.category, ctx.terminal, ctx.ide, ctx.messaging = classify(ctx.window, self.cfg)
            if self.a11y and not ctx.terminal:
                got = self.a11y.caret_text()
                if got:
                    ctx.before, ctx.after, ctx.accessible = got
                    if ctx.ide:
                        ctx.identifiers = tp.extract_identifiers(ctx.before + " " + ctx.after)
        except Exception:
            log.exception("context capture failed")
        return ctx

    def start(self, mode: str, locked: bool, held_by: str | None = None) -> None:
        if self.state != "idle":
            return
        self.mode, self.locked, self.held_by = mode, locked, held_by
        self.tap_pending = False
        self.warned_limit = False
        self.warned_silent = False
        self.started = time.monotonic()
        self.ctx_future = self.pool.submit(self._capture_context, mode)
        self._cue("start")
        if self.cfg.get("audio.mute_media"):
            self.pool.submit(self.media.duck)
        source = au.pick_source(self.cfg.get("audio.mic_preference"))
        try:
            self.recorder.start(source, SPOOL_DIR)
        except Exception as e:
            log.exception("recording failed")
            self.message.emit(f"Could not open microphone: {e}", "error")
            self._cue("error")
            return
        self.state = "recording"
        self.tick.start()
        self._emit_state()

    def _on_tick(self) -> None:
        if self.state != "recording":
            self.tick.stop()
            return
        el = self.recorder.elapsed()
        limit = self.cfg.get("audio.max_minutes") * 60
        if self.recorder.error:
            self.message.emit(f"Recording stopped: {self.recorder.error}", "error")
            self.stop_and_process()
        elif el >= limit:
            self.message.emit("Maximum dictation length reached", "warn")
            self.stop_and_process()
        elif el >= limit - 60 and not self.warned_limit:
            self.warned_limit = True
            self.message.emit("One minute of recording left", "warn")
        elif el > 4 and not self.warned_silent and self.recorder.last_signal_at <= self.recorder.started_at:
            self.warned_silent = True
            self.message.emit("Microphone seems silent. Check the input device.", "warn")
        silence = self.cfg.get("audio.silence_stop_seconds")
        if self.locked and silence and time.monotonic() - self.recorder.last_signal_at > silence:
            self.stop_and_process()

    def cancel(self, quiet: bool = False) -> None:
        if self.state != "recording":
            return
        self.tick.stop()
        self.tap_timer.stop()
        self.recorder.stop()
        self.recorder.discard_spool()
        if self.cfg.get("audio.mute_media"):
            self.pool.submit(self.media.restore)
        if not quiet:
            self._cue("cancel")
        self.state = "idle"
        self._emit_state()

    def stop_and_process(self) -> None:
        if self.state != "recording":
            return
        self.tick.stop()
        self.tap_timer.stop()
        audio = self.recorder.stop()
        spool = self.recorder.spool_path
        duration = len(audio) / au.RATE
        if self.cfg.get("audio.mute_media"):
            self.pool.submit(self.media.restore)
        self._cue("stop")
        speech = au.speech_seconds(audio)
        if speech < 0.2 and not self.cfg.get("audio.whisper_mode"):
            self.recorder.discard_spool()
            self.state = "idle"
            self._emit_state()
            self.message.emit("No speech detected", "info")
            return
        self.state = "processing"
        self._emit_state()
        mode, ctx_future = self.mode, self.ctx_future
        threading.Thread(
            target=self._process, args=(mode, audio, spool, duration, speech, ctx_future), daemon=True
        ).start()

    def _finish(self) -> None:
        self.progress.emit("")
        self.state = "idle"
        self.mode = "dictation"
        self._emit_state()
        self.history_changed.emit()

    # processing (worker thread)
    def _process(self, mode, audio, spool: Path | None, duration, speech, ctx_future) -> None:
        tid = None
        try:
            ctx: Context = ctx_future.result(timeout=5) if ctx_future else Context()
            audio_in = au.normalize(audio, self.cfg.get("audio.whisper_mode"))
            self.progress.emit("Transcribing…")
            raw, lang = self.pipeline.transcribe(audio_in, ctx)
            audio_path = self._keep_audio(spool)
            if not raw:
                self.message.emit("Nothing was transcribed", "info")
                return
            if mode == "command":
                self._command(raw, ctx, duration, speech, audio_path, lang)
                return
            if mode == "scratchpad":
                ctx.category = "other"
            res = self.pipeline.process_text(raw, lang, ctx, on_stage=self.progress.emit)
            if res.warning:
                self.message.emit(res.warning, "warn")
            if mode == "scratchpad":
                self.scratchpad_text.emit(res.text)
                if not self._restore_clip():
                    self.injector.copy_to_clipboard(res.text)
            else:
                self._deliver(res.text, ctx, press_enter=res.press_enter)
            tid = self._save(
                mode=mode, raw=raw, text=res.text, ctx=ctx, language=lang, duration=duration,
                speech=speech, audio_path=audio_path,
            )
            self.store.bump_words(res.dictionary_hits + [w for w in self.pipeline.glossary(None) if w in res.text])
            for sid in res.snippet_ids:
                self.store.bump_snippet(sid)
            if self.cfg.get("dictionary_auto_add") and ctx.accessible is not None and mode == "dictation":
                threading.Timer(8.0, self._auto_add, args=(res.text, ctx)).start()
        except InjectError as e:
            self.message.emit(f"Could not paste automatically ({e}). The text is in your clipboard.", "error")
        except LLMError as e:
            self.message.emit(f"AI edit failed: {e}", "error")
        except Exception as e:
            log.exception("processing failed")
            self.message.emit(f"Transcription failed: {e}", "error")
            if spool and spool.exists() and tid is None:
                self._save(mode=mode, raw="", text="", ctx=Context(), language="", duration=duration,
                           speech=speech, audio_path=self._keep_audio(spool), status="failed", error=str(e))
        finally:
            self.recorder.discard_spool()
            self._finished.emit()

    def _paste_blocked(self, win) -> bool:
        """Ctrl+V on the desktop or in a file manager creates a file, so never paste there."""
        cls = win.cls.lower()
        return any(b in cls for b in self.cfg.get("no_paste_apps"))

    def _deliver(self, text: str, ctx: Context, press_enter: bool = False, check_focus: bool = True) -> None:
        """Pastes into the app the dictation started in. If focus has moved elsewhere, or the
        target can't take text, the result only goes to the clipboard."""
        now = active_window()
        started = ctx.window
        moved = check_focus and bool(started.cls) and (
            now.cls != started.cls or (now.pid and started.pid and now.pid != started.pid)
        )
        # Detection works (we knew the start window) but nothing is focused now: the desktop.
        nothing = bool(started.cls) and not now.cls
        if moved or nothing or self._paste_blocked(now):
            self.injector.copy_to_clipboard(text)
            self.progress.emit("")
            self.message.emit("Copied to clipboard", "done")
            return
        self.progress.emit("Pasting…")
        self.injector.paste(text, terminal=ctx.terminal, press_enter=press_enter, restore=self._restore_clip())
        self.progress.emit("")
        self.message.emit("Pasted" + ("" if self._restore_clip() else " · in clipboard"), "done")

    def _restore_clip(self) -> bool:
        return not self.cfg.get("keep_in_clipboard")

    def _keep_audio(self, spool: Path | None) -> str | None:
        if not spool or not spool.exists():
            return None
        if not (self.cfg.get("privacy.store_audio") and self.cfg.get("privacy.store_history")):
            return None
        dest = AUDIO_DIR / spool.name
        shutil.move(spool, dest)
        return str(dest)

    def _save(self, *, mode, raw, text, ctx: Context, language, duration, speech, audio_path,
              status="ok", error=None) -> int | None:
        if not self.cfg.get("privacy.store_history"):
            return None
        return self.store.add_transcript(
            mode=mode, status=status, raw=raw, text=text, app=ctx.window.cls, title=ctx.window.title[:200],
            category=ctx.category, language=language, duration=duration, speech_seconds=speech,
            words=tp.word_count(text if mode in ("dictation", "scratchpad", "recovered") else raw),
            audio_path=audio_path, error=error,
        )

    def _command(self, instruction, ctx: Context, duration, speech, audio_path, lang) -> None:
        selection = self.injector.copy_selection(terminal=ctx.terminal)
        if selection:
            self.message.emit("Editing selection…", "info")
            new = self.pipeline.edit(selection, instruction)
            self._deliver(new, ctx)
            self._save(mode="command", raw=instruction, text=new, ctx=ctx, language=lang,
                       duration=duration, speech=speech, audio_path=audio_path)
            return
        url = tp.search_url(instruction)
        if url:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._save(mode="command", raw=instruction, text=url, ctx=ctx, language=lang,
                       duration=duration, speech=speech, audio_path=audio_path)
            return
        self.message.emit("Command mode: select some text first", "info")

    def _auto_add(self, inserted: str, ctx: Context) -> None:
        """If the user retyped a word we inserted, learn the corrected spelling."""
        full = self.a11y.full_text(ctx.accessible) if self.a11y else None
        if not full:
            return
        words = lambda s: set(re.findall(r"[A-Za-zÀ-ÿ][\w'’-]{2,}", s))
        now, ins, before = words(full), words(inserted), words(ctx.before or "")
        new_words = now - ins - before
        known = {e.word.lower() for e in self.store.dictionary()}
        for w in ins - now:
            m = difflib.get_close_matches(w, list(new_words), n=1, cutoff=0.75)
            if m and m[0].lower() != w.lower() and m[0].lower() not in known:
                self.store.add_word(m[0], w, source="auto")
                self.message.emit(f"Added “{m[0]}” to your dictionary", "info")

    # other actions
    def _run_bg(self, fn, *args) -> None:
        def wrap():
            try:
                fn(*args)
            except InjectError as e:
                self.message.emit(f"Could not insert text: {e}", "error")
            except LLMError as e:
                self.message.emit(f"AI edit failed: {e}", "error")
            except Exception as e:
                log.exception("action failed")
                self.message.emit(str(e), "error")

        threading.Thread(target=wrap, daemon=True).start()

    def paste_last(self) -> None:
        row = self.store.last_transcript()
        if not row:
            self.message.emit("No transcript yet", "info")
            return
        self.paste_text(row["text"])

    def paste_text(self, text: str) -> None:
        def run():
            ctx = self._capture_context("dictation")
            self._deliver(text, ctx, check_focus=False)

        self._run_bg(run)

    def copy_last(self) -> None:
        row = self.store.last_transcript()
        if row:
            self.copy_text(row["text"])
            self.message.emit("Last transcript copied", "info")

    def copy_text(self, text: str) -> None:
        self._run_bg(self.injector.copy_to_clipboard, text)

    def start_scratchpad(self) -> None:
        if self.state != "idle":
            return
        self.open_scratchpad.emit()
        self.start("scratchpad", locked=True)
        self.lock_time = time.monotonic()

    def transform(self, slot: int) -> None:
        transforms = self.cfg.get("transforms")
        if not 1 <= slot <= len(transforms) or self.state != "idle":
            if self.state == "idle":
                self.message.emit(f"No transform in slot {slot}", "info")
            return
        t = transforms[slot - 1]

        def run():
            ctx = self._capture_context("dictation")
            selection = self.injector.copy_selection(terminal=ctx.terminal)
            if not selection:
                self.message.emit(f"{t['name']}: select some text first", "info")
                return
            if len(selection.split()) > 1000:
                self.message.emit(f"{t['name']}: selection is over 1000 words", "warn")
                return
            self.state_changed.emit("processing", "transform")
            try:
                new = self.pipeline.edit(selection, t["instruction"])
                self._deliver(new, ctx)
                self._save(mode="transform", raw=selection, text=new, ctx=ctx, language="",
                           duration=0, speech=0, audio_path=None)
                self.message.emit(f"{t['name']} applied. Undo with Ctrl+Z.", "info")
            finally:
                self._finished.emit()

        self._run_bg(run)

    def retry(self, tid: int) -> None:
        row = self.store.transcript(tid)
        if not row or not row["audio_path"] or not Path(row["audio_path"]).exists():
            self.message.emit("No audio stored for this entry", "warn")
            return

        def run():
            audio = au.normalize(au.read_wav(Path(row["audio_path"])), self.cfg.get("audio.whisper_mode"))
            ctx = Context(category=row["category"])
            raw, lang = self.pipeline.transcribe(audio, ctx)
            res = self.pipeline.process_text(raw, lang, ctx)
            self.store.update_transcript(tid, raw=raw, text=res.text, language=lang, status="ok", error=None,
                                         words=tp.word_count(res.text))
            self.history_changed.emit()
            self.message.emit("Transcript re-processed", "info")

        self._run_bg(run)

    def _recover_spool(self) -> None:
        """Transcribe recordings left behind by a crash."""
        for wav in sorted(SPOOL_DIR.glob("rec-*.wav")):
            if self.recorder.running and wav == self.recorder.spool_path:
                continue
            try:
                audio = au.read_wav(wav)
                if au.speech_seconds(audio) < 0.3:
                    wav.unlink()
                    continue
                raw, lang = self.pipeline.transcribe(au.normalize(audio, False), None)
                res = self.pipeline.process_text(raw, lang, Context()) if raw else None
                mtime = wav.stat().st_mtime
                dest = AUDIO_DIR / wav.name
                shutil.move(wav, dest)
                self.store.add_transcript(
                    mode="recovered", raw=raw, text=res.text if res else "", language=lang,
                    duration=len(audio) / au.RATE, speech_seconds=au.speech_seconds(audio),
                    words=tp.word_count(res.text) if res else 0, audio_path=str(dest),
                    created_at=mtime,
                )
                self.message.emit("Recovered an unfinished dictation. See History.", "info")
                self.history_changed.emit()
            except Exception:
                log.exception("spool recovery failed for %s", wav)

    def shutdown(self) -> None:
        if self.state == "recording":
            self.recorder.stop()
        self.injector.kb.close()
