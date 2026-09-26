from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget, QLabel,
)

from .. import audio as au
from .common import heading

MODE_LABEL = {
    "dictation": "", "command": "Command", "transform": "Transform",
    "scratchpad": "Scratchpad", "recovered": "Recovered",
}


class HistoryPage(QWidget):
    def __init__(self, store, controller):
        super().__init__()
        self.store = store
        self.ctl = controller
        lay = QVBoxLayout(self)
        lay.addWidget(heading("History", "Everything you dictated, stored only on this machine."))

        self.search = QLineEdit(placeholderText="Search transcripts…", clearButtonEnabled=True)
        self.search.textChanged.connect(self.reload)
        lay.addWidget(self.search)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Time", "Text", "App"])
        self.tree.setColumnWidth(0, 90)
        self.tree.setColumnWidth(1, 380)
        self.tree.setRootIsDecorated(False)
        self.tree.currentItemChanged.connect(self._select)
        split.addWidget(self.tree)

        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(8, 0, 0, 0)
        self.meta = QLabel()
        self.meta.setWordWrap(True)
        dl.addWidget(self.meta)
        dl.addWidget(QLabel("Final text"))
        self.text = QPlainTextEdit(readOnly=True)
        dl.addWidget(self.text, 3)
        self.raw_label = QLabel("Raw transcript")
        dl.addWidget(self.raw_label)
        self.raw = QPlainTextEdit(readOnly=True)
        dl.addWidget(self.raw, 2)
        btns = QHBoxLayout()
        self.b_copy = QPushButton("Copy")
        self.b_copy_raw = QPushButton("Copy raw (undo AI edit)")
        self.b_play = QPushButton("Play audio")
        self.b_retry = QPushButton("Retry")
        self.b_flag = QPushButton("Flag")
        self.b_del = QPushButton("Delete")
        for b in (self.b_copy, self.b_copy_raw, self.b_play, self.b_retry, self.b_flag, self.b_del):
            btns.addWidget(b)
        dl.addLayout(btns)
        split.addWidget(detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        clear = QPushButton("Clear all history")
        clear.clicked.connect(self._clear)
        foot.addWidget(clear)
        lay.addLayout(foot)

        self.b_copy.clicked.connect(lambda: self._with_row(lambda r: self.ctl.copy_text(r["text"])))
        self.b_copy_raw.clicked.connect(lambda: self._with_row(lambda r: self.ctl.copy_text(r["raw"])))
        self.b_play.clicked.connect(lambda: self._with_row(self._play))
        self.b_retry.clicked.connect(lambda: self._with_row(lambda r: self.ctl.retry(r["id"])))
        self.b_flag.clicked.connect(lambda: self._with_row(self._flag))
        self.b_del.clicked.connect(lambda: self._with_row(self._delete))
        controller.history_changed.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        cur = self._current_id()
        self.tree.clear()
        last_day = None
        select = None
        for r in self.store.transcripts(self.search.text().strip()):
            dt = datetime.fromtimestamp(r["created_at"])
            day = dt.strftime("%A, %d %B %Y")
            if day != last_day:
                hdr = QTreeWidgetItem([day])
                f = hdr.font(0)
                f.setBold(True)
                hdr.setFont(0, f)
                hdr.setFirstColumnSpanned(True)
                hdr.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.tree.addTopLevelItem(hdr)
                hdr.setFirstColumnSpanned(True)
                last_day = day
            label = MODE_LABEL.get(r["mode"], r["mode"])
            text = r["text"] or r["error"] or ""
            if r["status"] != "ok":
                text = "⚠ " + (r["error"] or "failed")
            prefix = f"[{label}] " if label else ""
            flag = "⚑ " if r["flagged"] else ""
            it = QTreeWidgetItem([dt.strftime("%H:%M"), flag + prefix + text.replace("\n", " ⏎ ")[:300], r["app"]])
            it.setData(0, Qt.ItemDataRole.UserRole, r["id"])
            self.tree.addTopLevelItem(it)
            if r["id"] == cur:
                select = it
        if select is None:
            select = next((self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())
                           if self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)), None)
        if select:
            self.tree.setCurrentItem(select)
        self._select(self.tree.currentItem(), None)

    def _current_id(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def _row(self):
        tid = self._current_id()
        return self.store.transcript(tid) if tid else None

    def _with_row(self, fn) -> None:
        r = self._row()
        if r:
            fn(r)
            self.reload()

    def _select(self, item, _prev) -> None:
        r = self._row()
        for b in (self.b_copy, self.b_copy_raw, self.b_play, self.b_retry, self.b_flag, self.b_del):
            b.setEnabled(r is not None)
        if not r:
            self.meta.setText("")
            self.text.clear()
            self.raw.clear()
            return
        has_audio = bool(r["audio_path"]) and Path(r["audio_path"]).exists()
        self.b_play.setEnabled(has_audio)
        self.b_retry.setEnabled(has_audio)
        self.b_flag.setText("Unflag" if r["flagged"] else "Flag")
        bits = [datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d %H:%M:%S")]
        if r["app"]:
            bits.append(f"{r['app']} ({r['category']})")
        if r["language"]:
            bits.append(r["language"])
        if r["duration"]:
            bits.append(f"{r['duration']:.1f}s, {r['words']} words")
        self.meta.setText(" · ".join(bits))
        self.text.setPlainText(r["text"])
        raw_title = {"transform": "Original selection", "command": "Spoken instruction"}.get(r["mode"], "Raw transcript")
        self.raw_label.setText(raw_title)
        self.raw.setPlainText(r["raw"])

    def _play(self, r) -> None:
        au.play_audio(au.read_wav(Path(r["audio_path"])))

    def _flag(self, r) -> None:
        self.store.update_transcript(r["id"], flagged=0 if r["flagged"] else 1)

    def _delete(self, r) -> None:
        self.store.delete_transcript(r["id"])

    def _clear(self) -> None:
        if QMessageBox.question(self, "Clear history", "Delete all transcripts and stored audio?") == QMessageBox.StandardButton.Yes:
            self.store.clear_history()
            self.reload()
