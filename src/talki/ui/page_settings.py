import shutil
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QFormLayout, QGridLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from .. import audio as au
from ..hotkeys import ACTIONS
from ..languages import LANGUAGES
from ..paths import APP_ID, CONFIG_PATH
from .common import heading, hint
from .themes import BAR_PRESETS, COLOR_KEYS, bar_theme, matching_preset

WHISPER_MODELS = ["large-v3-turbo", "distil-large-v3.5", "large-v3", "medium", "small", "base", "tiny",
                  "small.en", "base.en", "tiny.en"]


class SettingsPage(QWidget):
    llm_result = Signal(str)

    def __init__(self, cfg, controller, app):
        super().__init__()
        self.cfg = cfg
        self.ctl = controller
        self.app = app
        outer = QVBoxLayout(self)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        self.lay = QVBoxLayout(inner)
        self.lay.addWidget(heading("Settings"))
        self._shortcuts()
        self._audio()
        self._languages()
        self._speech()
        self._llm()
        self._ui()
        self._appearance()
        self._privacy()
        self.lay.addWidget(hint(f"All settings live in {CONFIG_PATH}."))
        self.lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        self.llm_result.connect(self._llm_done)

    # binding helpers
    def check(self, key: str, label: str, on_change=None) -> QCheckBox:
        cb = QCheckBox(label)
        cb.setChecked(bool(self.cfg.get(key)))

        def changed(v):
            self.cfg.set(key, v)
            if on_change:
                on_change(v)

        cb.toggled.connect(changed)
        return cb

    def spin(self, key: str, lo: int, hi: int, suffix: str = "") -> QSpinBox:
        sb = QSpinBox(minimum=lo, maximum=hi, value=int(self.cfg.get(key)), suffix=suffix)
        sb.valueChanged.connect(lambda v: self.cfg.set(key, v))
        return sb

    def line(self, key: str) -> QLineEdit:
        le = QLineEdit(str(self.cfg.get(key)))
        le.editingFinished.connect(lambda: self.cfg.set(key, le.text().strip()))
        return le

    def combo(self, key: str, items: list[tuple[str, str]], editable: bool = False, on_change=None) -> QComboBox:
        cb = QComboBox(editable=editable)
        for value, label in items:
            cb.addItem(label, value)
        cur = self.cfg.get(key)
        idx = cb.findData(cur)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        elif editable:
            cb.setEditText(str(cur))

        def changed(*_):
            # Editable combos list value == label, so the text is the value.
            val = cb.currentText().strip() if editable else cb.currentData()
            self.cfg.set(key, val)
            if on_change:
                on_change()

        if editable:
            cb.lineEdit().editingFinished.connect(changed)
            cb.activated.connect(changed)
        else:
            cb.currentIndexChanged.connect(changed)
        return cb

    def group(self, title: str) -> QFormLayout:
        box = QGroupBox(title)
        form = QFormLayout(box)
        self.lay.addWidget(box)
        return form

    # sections
    def _shortcuts(self) -> None:
        f = self.group("Keyboard shortcuts")
        f.addRow("Hotkey backend", self.combo("hotkeys.backend", [
            ("portal", "Desktop portal (KDE Plasma, GNOME), recommended"),
            ("evdev", "Raw input (modifier-only chords, mouse buttons; needs 'input' group)"),
            ("none", "None (bind `talki ctl …` in your compositor)"),
        ], on_change=lambda: self._restart_note()))
        btn = QPushButton("Change shortcuts…")
        btn.clicked.connect(self.app.configure_shortcuts)
        f.addRow(btn)
        pretty = lambda t: "+".join(k.capitalize() if k.isupper() or k.islower() else k for k in t.replace("LOGO", "Meta").split("+"))
        defaults = "\n".join(f"{desc.removeprefix('Talki: ').capitalize()}: {pretty(trig)}" for desc, trig in ACTIONS.values() if "slot" not in desc)
        f.addRow(hint("Defaults with the portal backend (changeable in System Settings › Shortcuts):\n" + defaults
                      + "\nTransforms: Ctrl+Meta+1…9"))
        f.addRow(hint("Hold push-to-talk to dictate, release to insert. Double-tap it to go hands-free; "
                      "press again to finish. Tapping three times quickly cancels."))
        f.addRow("Double-tap window", self.spin("hotkeys.tap_window_ms", 200, 1000, " ms"))
        f.addRow("Hold threshold", self.spin("hotkeys.hold_threshold_ms", 100, 800, " ms"))
        ev = self.cfg.get("hotkeys.evdev")
        f.addRow(hint("Raw input chords (evdev backend). Use evdev key names joined with +, e.g. "
                      "KEY_LEFTCTRL+KEY_LEFTMETA or BTN_SIDE."))
        for action in ("ptt", "handsfree", "command", "cancel", "paste_last", "copy_last", "scratchpad"):
            le = QLineEdit(ev.get(action, ""))
            le.editingFinished.connect(lambda a=action, le=le: self._set_evdev(a, le.text()))
            f.addRow(action, le)

    def _set_evdev(self, action: str, spec: str) -> None:
        ev = self.cfg.get("hotkeys.evdev")
        ev[action] = spec.strip()
        self.cfg.set("hotkeys.evdev", ev)

    def _restart_note(self) -> None:
        QMessageBox.information(self, "Talki", "Restart Talki to apply this change.")

    def _audio(self) -> None:
        f = self.group("Microphone and sound")
        f.addRow(hint("Tick the microphones Talki may use and order them by preference. The first one "
                      "that is connected wins. With none ticked, the system default is used."))
        self.mics = QListWidget()
        self.mics.setMaximumHeight(150)
        self.mics.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.mics.model().rowsMoved.connect(self._save_mics)
        self.mics.itemChanged.connect(self._save_mics)
        f.addRow(self.mics)
        rb = QHBoxLayout()
        refresh = QPushButton("Refresh devices")
        refresh.clicked.connect(self._load_mics)
        rb.addWidget(refresh)
        self.test_btn = QPushButton("Test microphone")
        self.test_btn.clicked.connect(self._mic_test)
        rb.addWidget(self.test_btn)
        self.meter = QProgressBar(maximum=100, textVisible=False)
        rb.addWidget(self.meter, 1)
        w = QWidget()
        w.setLayout(rb)
        f.addRow(w)
        self._load_mics()
        f.addRow(self.check("audio.sounds", "Play sound cues"))
        vol = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100, value=int(self.cfg.get("audio.sound_volume") * 100))
        vol.valueChanged.connect(lambda v: self.cfg.set("audio.sound_volume", v / 100))
        f.addRow("Cue volume", vol)
        f.addRow(self.check("audio.mute_media", "Pause or mute media while dictating"))
        f.addRow(self.check("audio.whisper_mode", "Whisper mode (boost quiet speech)"))
        f.addRow("Maximum dictation length", self.spin("audio.max_minutes", 1, 60, " min"))
        f.addRow("Stop hands-free after silence", self.spin("audio.silence_stop_seconds", 0, 600, " s (0 = never)"))

    def _load_mics(self) -> None:
        self.mics.blockSignals(True)
        self.mics.clear()
        pref = self.cfg.get("audio.mic_preference")
        sources = dict(au.list_sources())
        names = pref + [n for n in sources if n not in pref]
        for n in names:
            label = sources.get(n)
            it = QListWidgetItem(f"{label or n}" + ("" if label else "  (not connected)"))
            it.setData(Qt.ItemDataRole.UserRole, n)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            it.setCheckState(Qt.CheckState.Checked if n in pref else Qt.CheckState.Unchecked)
            self.mics.addItem(it)
        self.mics.blockSignals(False)

    def _save_mics(self, *_):
        pref = []
        for i in range(self.mics.count()):
            it = self.mics.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                pref.append(it.data(Qt.ItemDataRole.UserRole))
        self.cfg.set("audio.mic_preference", pref)

    def _mic_test(self) -> None:
        if self.ctl.state != "idle":
            return
        rec = au.Recorder(on_level=lambda v: None)
        rec.start(au.pick_source(self.cfg.get("audio.mic_preference")), None)
        self.test_btn.setEnabled(False)
        timer = QTimer(self, interval=60)
        ticks = {"n": 0}

        def tick():
            ticks["n"] += 1
            chunks = rec._chunks[-2:]
            if chunks:
                import numpy as np

                lvl = float(np.sqrt((np.concatenate(chunks) ** 2).mean()))
                self.meter.setValue(min(100, int(lvl * 1200)))
            if ticks["n"] > 80:
                timer.stop()
                rec.stop()
                self.meter.setValue(0)
                self.test_btn.setEnabled(True)

        timer.timeout.connect(tick)
        timer.start()

    def _languages(self) -> None:
        f = self.group("Languages")
        f.addRow(hint("Tick the languages you speak. With none ticked Talki auto-detects from all ~100 "
                      "Whisper languages. With several ticked, detection is limited to those. "
                      "Detection runs once per dictation."))
        self.search_lang = QLineEdit(placeholderText="Filter languages…", clearButtonEnabled=True)
        f.addRow(self.search_lang)
        self.langs = QListWidget()
        self.langs.setMaximumHeight(180)
        chosen = set(self.cfg.get("stt.languages"))
        for code, name in sorted(LANGUAGES.items(), key=lambda kv: (kv[0] not in chosen, kv[1])):
            it = QListWidgetItem(f"{name} ({code})")
            it.setData(Qt.ItemDataRole.UserRole, code)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if code in chosen else Qt.CheckState.Unchecked)
            self.langs.addItem(it)
        self.langs.itemChanged.connect(self._save_langs)
        self.search_lang.textChanged.connect(self._filter_langs)
        f.addRow(self.langs)
        fav = QLineEdit(", ".join(self.cfg.get("stt.language_favorites")))
        fav.editingFinished.connect(lambda: self.cfg.set(
            "stt.language_favorites", [c.strip() for c in fav.text().split(",") if c.strip() in LANGUAGES]))
        f.addRow("Quick-switch in bar menu", fav)

    def _filter_langs(self, text: str) -> None:
        t = text.lower()
        for i in range(self.langs.count()):
            it = self.langs.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _save_langs(self, *_):
        codes = [self.langs.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.langs.count())
                 if self.langs.item(i).checkState() == Qt.CheckState.Checked]
        self.cfg.set("stt.languages", codes)

    def _speech(self) -> None:
        f = self.group("Speech recognition")
        f.addRow("Engine", self.combo("stt.backend", [
            ("faster-whisper", "faster-whisper (built in, CPU or CUDA)"),
            ("whisper-server", "whisper.cpp server (GPU via Vulkan/ROCm)"),
        ]))
        f.addRow("Model", self.combo("stt.model", [(m, m) for m in WHISPER_MODELS], editable=True))
        f.addRow("Device", self.combo("stt.device", [("cpu", "CPU"), ("cuda", "CUDA (NVIDIA)"), ("auto", "Auto")]))
        f.addRow("Compute type", self.combo("stt.compute_type", [(c, c) for c in ("int8", "int8_float16", "float16", "float32")]))
        f.addRow("CPU threads", self.spin("stt.threads", 0, 64, " (0 = auto)"))
        f.addRow("whisper.cpp server URL", self.line("stt.server_url"))
        f.addRow(self.check("stt.vad", "Skip silence with voice activity detection"))
        apply = QPushButton("Apply and reload model")
        apply.clicked.connect(self.app.reload_stt)
        f.addRow(apply)

    def _llm(self) -> None:
        f = self.group("AI cleanup (local LLM)")
        f.addRow(self.check("llm.enabled", "Use the local LLM for cleanup, styles, command mode and transforms"))
        f.addRow("API", self.combo("llm.api", [("ollama", "Ollama"), ("openai", "OpenAI-compatible (llama-server, LM Studio)")]))
        f.addRow("Server URL", self.line("llm.url"))
        models = self.ctl.pipeline.llm().models() if self.ctl.pipeline.llm() else []
        cur = self.cfg.get("llm.model")
        items = [(m, m) for m in dict.fromkeys([cur] + models)]
        f.addRow("Model", self.combo("llm.model", items, editable=True))
        f.addRow("Keep model loaded for", self.line("llm.keep_alive"))
        f.addRow("Skip AI for dictations under", self.spin("llm.min_words", 0, 50, " words"))
        test = QPushButton("Test cleanup")
        test.clicked.connect(self._llm_test)
        self.llm_out = hint("")
        f.addRow(test, self.llm_out)
        f.addRow(hint("Install: sudo pacman -S ollama-rocm (or ollama-vulkan), then "
                      f"`ollama pull {cur}`. Without Ollama Talki still works with basic formatting."))

    def _llm_test(self) -> None:
        self.llm_out.setText("Running…")

        def run():
            from ..context import Context

            try:
                sample = "um so i think we should uh meet on tuesday no wait wednesday at three"
                res = self.ctl.pipeline.process_text(sample, "en", Context())
                self.llm_result.emit(("AI: " if res.llm_used else "Basic: ") + res.text + (f"\n{res.warning}" if res.warning else ""))
            except Exception as e:
                self.llm_result.emit(f"Error: {e}")

        threading.Thread(target=run, daemon=True).start()

    def _llm_done(self, text: str) -> None:
        self.llm_out.setText(text)

    def _ui(self) -> None:
        f = self.group("Interface")
        f.addRow(self.check("ui.bar_always_visible", "Show the Talki bar at all times (click it to open this window)",
                            lambda _: self.app.overlay.sync()))
        f.addRow(self.check("keep_in_clipboard", "Keep each transcript in the clipboard after pasting it"))
        f.addRow("Dock bar at", self.combo("ui.bar_edge", [("bottom", "Bottom"), ("top", "Top"), ("left", "Left"), ("right", "Right")],
                                           on_change=self._redock))
        f.addRow(hint("Drag the bar anywhere, across monitors too. Picking an edge here, or "
                      "\"Reset position\" in the bar's right-click menu, docks it again."))
        f.addRow(self.check("ui.quiet_startup", "Start in the tray without opening this window"))
        f.addRow(self.check("ui.notifications", "Show desktop notifications for errors"))
        f.addRow(self.check("ui.autostart", "Start Talki when I log in", self._autostart))

    def _appearance(self) -> None:
        f = self.group("Appearance")
        f.addRow("Talki window", self.combo("ui.app_theme", [("system", "Follow system"), ("light", "Light"), ("dark", "Dark")],
                                            on_change=lambda: self.app.app_theme.apply(self.cfg.get("ui.app_theme"))))
        size = QSlider(Qt.Orientation.Horizontal, minimum=60, maximum=200, singleStep=5, pageStep=10,
                       value=int(round((self.cfg.get("ui.bar_scale") or 1.0) * 100)))
        size_label = QLabel(f"{size.value()}%")
        size_label.setMinimumWidth(44)
        reset = QPushButton("Reset")

        def resize(v):
            size_label.setText(f"{v}%")
            self.app.overlay.model.set_ui_scale(v / 100)

        size.valueChanged.connect(resize)
        reset.clicked.connect(lambda: size.setValue(100))
        rw = QWidget()
        rl = QHBoxLayout(rw)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(size, 1)
        rl.addWidget(size_label)
        rl.addWidget(reset)
        f.addRow("Bar size", rw)

        self.preset = QComboBox()
        self.preset.addItem("Custom", "")
        for name in BAR_PRESETS:
            self.preset.addItem(name.capitalize(), name)
        self.preset.activated.connect(self._apply_preset)
        f.addRow("Bar colours", self.preset)

        grid = QWidget()
        gl = QGridLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        self.swatches = {}
        for i, (key, label) in enumerate(COLOR_KEYS.items()):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, k=key: self._pick_color(k))
            self.swatches[key] = btn
            gl.addWidget(btn, i // 4, i % 4)
        f.addRow(grid)

        self.opacity = QSlider(Qt.Orientation.Horizontal, minimum=30, maximum=100)
        self.opacity.valueChanged.connect(lambda v: self._set_theme_value("opacity", v / 100))
        f.addRow("Background opacity", self.opacity)
        self.round = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100)
        self.round.valueChanged.connect(lambda v: self._set_theme_value("roundness", v / 100))
        f.addRow("Corner roundness", self.round)
        self.label = QLineEdit(maxLength=24, placeholderText="empty = dot only")
        self.label.textEdited.connect(lambda t: self._set_theme_value("label", t.strip()))
        f.addRow("Idle label", self.label)
        reset_theme = QPushButton("Reset appearance")
        reset_theme.clicked.connect(self._reset_theme)
        f.addRow(reset_theme)
        self._load_theme()

    def _theme(self) -> dict:
        return bar_theme(self.cfg)

    def _load_theme(self) -> None:
        t = self._theme()
        for key, btn in self.swatches.items():
            c = QColor(t[key])
            fg = "#000000" if c.lightnessF() > 0.55 or c.alphaF() < 0.4 else "#ffffff"
            btn.setStyleSheet(f"QPushButton {{ background: {c.name(QColor.NameFormat.HexArgb)}; color: {fg};"
                              f" border: 1px solid #888; border-radius: 4px; padding: 5px 8px; }}")
        for w, val in ((self.opacity, t["opacity"]), (self.round, t["roundness"])):
            w.blockSignals(True)
            w.setValue(int(round(val * 100)))
            w.blockSignals(False)
        if self.label.text() != t["label"]:
            self.label.setText(t["label"])
        idx = self.preset.findData(matching_preset(t) or "")
        self.preset.setCurrentIndex(max(0, idx))

    def _set_theme_value(self, key: str, value) -> None:
        t = self._theme()
        t[key] = value
        self.app.overlay.model.set_theme(t)
        self._load_theme()

    def _pick_color(self, key: str) -> None:
        t = self._theme()
        c = QColorDialog.getColor(QColor(t[key]), self, f"Bar colour: {COLOR_KEYS[key]}",
                                  QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            self._set_theme_value(key, c.name(QColor.NameFormat.HexArgb))

    def _apply_preset(self) -> None:
        name = self.preset.currentData()
        if name:
            t = self._theme()
            t.update(BAR_PRESETS[name])
            self.app.overlay.model.set_theme(t)
            self._load_theme()

    def _reset_theme(self) -> None:
        self.app.overlay.model.set_theme({})
        self._load_theme()

    def _redock(self) -> None:
        self.app.overlay.model.resetPos()
        self.app.overlay.model.changed.emit()

    def _autostart(self, on: bool) -> None:
        dest = Path.home() / ".config/autostart" / f"{APP_ID}.desktop"
        if on:
            dest.parent.mkdir(parents=True, exist_ok=True)
            exe = shutil.which("talki") or "talki"
            dest.write_text(
                "[Desktop Entry]\nType=Application\nName=Talki\n"
                f"Exec={exe}\nIcon=audio-input-microphone\nX-GNOME-Autostart-enabled=true\n"
            )
        else:
            dest.unlink(missing_ok=True)

    def _privacy(self) -> None:
        f = self.group("Privacy and data")
        f.addRow(hint("Everything runs on this machine. Nothing is sent anywhere."))
        f.addRow(self.check("privacy.store_history", "Keep transcript history"))
        f.addRow(self.check("privacy.store_audio", "Keep audio recordings (for playback and retry)"))
        f.addRow("Delete audio after", self.spin("privacy.audio_retention_days", 0, 365, " days (0 = never)"))
        f.addRow("Delete history after", self.spin("privacy.history_retention_days", 0, 3650, " days (0 = never)"))
        f.addRow(self.check("privacy.context_awareness",
                            "Context awareness: read the active app and text near the cursor (restart to apply)"))
        f.addRow(self.check("dictionary_auto_add", "Learn words I correct after dictating"))
