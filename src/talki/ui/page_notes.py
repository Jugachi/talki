from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QSplitter, QTextEdit,
    QVBoxLayout, QWidget,
)

from .common import heading

SNAPSHOT_SECONDS = 60


class NotesPage(QWidget):
    """Scratchpad: rich-text notes with autosave, pinning and version history."""

    def __init__(self, store, controller):
        super().__init__()
        self.store = store
        self.ctl = controller
        self.current: int | None = None
        self.last_snapshot = 0.0
        lay = QVBoxLayout(self)
        lay.addWidget(heading("Scratchpad", "Quick notes. Dictate straight in with the mic button or Ctrl+Meta+N."))

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit(placeholderText="Search notes…", clearButtonEnabled=True)
        self.search.textChanged.connect(self.reload)
        ll.addWidget(self.search)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._open_item)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        ll.addWidget(self.list, 1)
        nb = QPushButton("New note")
        nb.clicked.connect(lambda: self.new_note())
        ll.addWidget(nb)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        tools = QHBoxLayout()
        self.title = QLineEdit(placeholderText="Title")
        self.title.textEdited.connect(self._changed)
        tools.addWidget(self.title, 1)
        for label, fn in (("B", self._bold), ("I", self._italic), ("• List", self._list)):
            b = QPushButton(label)
            b.setFixedWidth(56 if len(label) > 1 else 32)
            b.clicked.connect(fn)
            tools.addWidget(b)
        self.mic = QPushButton("🎤 Dictate")
        self.mic.clicked.connect(self._dictate)
        tools.addWidget(self.mic)
        self.versions = QPushButton("History")
        self.versions.clicked.connect(self._versions_menu)
        tools.addWidget(self.versions)
        rl.addLayout(tools)
        self.editor = QTextEdit()
        self.editor.setAcceptRichText(True)
        self.editor.textChanged.connect(self._changed)
        rl.addWidget(self.editor, 1)
        split.addWidget(right)
        split.setSizes([260, 640])
        lay.addWidget(split, 1)

        self.save_timer = QTimer(self, singleShot=True, interval=800)
        self.save_timer.timeout.connect(self.save)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.new_note)
        controller.scratchpad_text.connect(self.insert_text)
        controller.state_changed.connect(self._state)
        self.reload()

    def reload(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        sel = None
        for n in self.store.notes(self.search.text().strip()):
            title = n["title"] or "Untitled"
            when = datetime.fromtimestamp(n["updated_at"]).strftime("%d %b %H:%M")
            it = QListWidgetItem(("📌 " if n["pinned"] else "") + f"{title}\n{when}")
            it.setData(Qt.ItemDataRole.UserRole, n["id"])
            self.list.addItem(it)
            if n["id"] == self.current:
                sel = it
        self.list.blockSignals(False)
        if sel:
            self.list.setCurrentItem(sel)
        elif self.list.count() and self.current is None:
            self.list.setCurrentRow(0)

    def _open_item(self, item, _prev) -> None:
        if item:
            self.open_note(item.data(Qt.ItemDataRole.UserRole))

    def open_note(self, nid: int) -> None:
        self.save()
        n = self.store.note(nid)
        if not n:
            return
        self.current = nid
        self.editor.blockSignals(True)
        self.title.setText(n["title"])
        self.editor.setHtml(n["body"])
        self.editor.blockSignals(False)
        self.last_snapshot = 0.0

    def new_note(self) -> int:
        self.save()
        nid = self.store.add_note()
        self.current = nid
        self.search.clear()
        self.reload()
        self.open_note(nid)
        self.editor.setFocus()
        return nid

    def _changed(self, *_):
        if self.current is not None:
            self.save_timer.start()

    def save(self) -> None:
        if self.current is None:
            return
        import time

        now = time.time()
        snapshot = now - self.last_snapshot > SNAPSHOT_SECONDS
        body = self.editor.toHtml() if self.editor.toPlainText().strip() else ""
        title = self.title.text().strip() or self.editor.toPlainText().strip().split("\n")[0][:60]
        self.store.save_note(self.current, title, body, snapshot=snapshot)
        if snapshot:
            self.last_snapshot = now
        if self.save_timer.isActive():
            self.save_timer.stop()
        self._refresh_titles()

    def _refresh_titles(self) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == self.current:
                n = self.store.note(self.current)
                when = datetime.fromtimestamp(n["updated_at"]).strftime("%d %b %H:%M")
                it.setText(("📌 " if n["pinned"] else "") + f"{n['title'] or 'Untitled'}\n{when}")

    def insert_text(self, text: str) -> None:
        if self.current is None:
            self.new_note()
        cur = self.editor.textCursor()
        before = self.editor.toPlainText()[: cur.position()]
        if before and not before[-1].isspace():
            text = " " + text
        cur.insertText(text)
        self.editor.setTextCursor(cur)
        self.save()

    def _dictate(self) -> None:
        if self.ctl.state == "idle":
            if self.current is None:
                self.new_note()
            self.ctl.start("scratchpad", locked=True)
        elif self.ctl.state == "recording" and self.ctl.mode == "scratchpad":
            self.ctl.stop_and_process()

    def _state(self, state: str, mode: str) -> None:
        active = mode == "scratchpad" and state in ("recording", "locked")
        self.mic.setText("■ Stop" if active else "🎤 Dictate")

    def _fmt(self, fn) -> None:
        cur = self.editor.textCursor()
        fmt = cur.charFormat()
        fn(fmt)
        cur.mergeCharFormat(fmt)
        self.editor.mergeCurrentCharFormat(fmt)

    def _bold(self) -> None:
        from PySide6.QtGui import QFont

        self._fmt(lambda f: f.setFontWeight(QFont.Weight.Normal if f.fontWeight() > QFont.Weight.Normal else QFont.Weight.Bold))

    def _italic(self) -> None:
        self._fmt(lambda f: f.setFontItalic(not f.fontItalic()))

    def _list(self) -> None:
        from PySide6.QtGui import QTextListFormat

        cur = self.editor.textCursor()
        cur.createList(QTextListFormat.Style.ListDisc)

    def _menu(self, pos) -> None:
        it = self.list.itemAt(pos)
        if not it:
            return
        nid = it.data(Qt.ItemDataRole.UserRole)
        n = self.store.note(nid)
        m = QMenu(self)
        m.addAction("Unpin" if n["pinned"] else "Pin", lambda: (self.store.set_note_pinned(nid, not n["pinned"]), self.reload()))
        m.addAction("Delete", lambda: self._delete(nid))
        m.exec(self.list.mapToGlobal(pos))

    def _delete(self, nid: int) -> None:
        self.store.delete_note(nid)
        if self.current == nid:
            self.current = None
            self.editor.blockSignals(True)
            self.editor.clear()
            self.title.clear()
            self.editor.blockSignals(False)
        self.reload()

    def _versions_menu(self) -> None:
        if self.current is None:
            return
        self.save()
        m = QMenu(self)
        vs = self.store.note_versions(self.current)
        if not vs:
            m.addAction("No earlier versions").setEnabled(False)
        for v in vs:
            when = datetime.fromtimestamp(v["created_at"]).strftime("%d %b %H:%M:%S")
            m.addAction(f"Restore version from {when}", lambda body=v["body"]: self._restore(body))
        m.exec(self.versions.mapToGlobal(self.versions.rect().bottomLeft()))

    def _restore(self, body: str) -> None:
        self.last_snapshot = 0.0
        self.editor.setHtml(body)
        self.editor.moveCursor(QTextCursor.MoveOperation.End)
        self.save()
