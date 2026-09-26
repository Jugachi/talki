from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLineEdit, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .common import heading, hint


class WordDialog(QDialog):
    def __init__(self, parent, word="", wrong="", starred=False):
        super().__init__(parent)
        self.setWindowTitle("Dictionary entry")
        form = QFormLayout(self)
        self.word = QLineEdit(word, maxLength=60)
        self.wrong = QLineEdit(wrong or "", maxLength=60, placeholderText="optional")
        self.star = QCheckBox("Starred")
        self.star.setChecked(starred)
        form.addRow("Word or phrase", self.word)
        form.addRow("Commonly misheard as", self.wrong)
        form.addRow(self.star)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)


class DictionaryPage(QWidget):
    def __init__(self, store):
        super().__init__()
        self.store = store
        lay = QVBoxLayout(self)
        lay.addWidget(heading(
            "Dictionary",
            "Names, jargon and acronyms Talki should spell your way. Words bias recognition and the AI "
            "cleanup; a \"misheard as\" form is always replaced with the correct word.",
        ))
        top = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Search…", clearButtonEnabled=True)
        self.search.textChanged.connect(self.reload)
        top.addWidget(self.search, 1)
        add = QPushButton("Add word")
        add.clicked.connect(self._add)
        top.addWidget(add)
        lay.addLayout(top)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["★", "Word", "Misheard as", "Uses", "Source"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.doubleClicked.connect(self._edit)
        lay.addWidget(self.table, 1)
        btns = QHBoxLayout()
        btns.addStretch(1)
        for label, fn in (("Star / unstar", self._star), ("Edit", self._edit), ("Delete", self._delete)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            btns.addWidget(b)
        lay.addLayout(btns)
        self.reload()

    def reload(self) -> None:
        q = self.search.text().strip().lower()
        self.entries = [e for e in self.store.dictionary() if not q or q in e.word.lower() or q in (e.wrong or "").lower()]
        self.table.setRowCount(len(self.entries))
        for i, e in enumerate(self.entries):
            for col, val in enumerate(("★" if e.starred else "", e.word, e.wrong or "", str(e.uses), e.source)):
                self.table.setItem(i, col, QTableWidgetItem(val))

    def _current(self):
        r = self.table.currentRow()
        return self.entries[r] if 0 <= r < len(self.entries) else None

    def _add(self) -> None:
        d = WordDialog(self)
        if d.exec() and d.word.text().strip():
            self.store.add_word(d.word.text(), d.wrong.text())
            self.reload()

    def _edit(self, *_):
        e = self._current()
        if not e:
            return
        d = WordDialog(self, e.word, e.wrong, e.starred)
        if d.exec() and d.word.text().strip():
            self.store.update_word(e.id, d.word.text(), d.wrong.text(), d.star.isChecked())
            self.reload()

    def _star(self) -> None:
        e = self._current()
        if e:
            self.store.update_word(e.id, e.word, e.wrong, not e.starred)
            self.reload()

    def _delete(self) -> None:
        e = self._current()
        if e:
            self.store.delete_word(e.id)
            self.reload()


class SnippetsPage(QWidget):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.current_id = None
        lay = QVBoxLayout(self)
        lay.addWidget(heading(
            "Snippets",
            "Say a trigger phrase and Talki inserts the full text. Matching ignores capitalization "
            "and works mid-sentence; the longest matching trigger wins.",
        ))
        body = QHBoxLayout()
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Trigger", "Expands to", "Uses"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.currentCellChanged.connect(self._select)
        body.addWidget(self.table, 3)
        ed = QVBoxLayout()
        form = QFormLayout()
        self.trigger = QLineEdit(maxLength=60, placeholderText="e.g. my calendar link")
        form.addRow("Trigger phrase", self.trigger)
        ed.addLayout(form)
        self.expansion = QPlainTextEdit(placeholderText="Text to insert (up to 4000 characters)")
        ed.addWidget(self.expansion, 1)
        eb = QHBoxLayout()
        for label, fn in (("New", self._new), ("Save", self._save), ("Delete", self._delete)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            eb.addWidget(b)
        ed.addLayout(eb)
        ed.addWidget(hint("Tip: internal punctuation in a trigger must be spoken, so keep triggers to plain words."))
        body.addLayout(ed, 2)
        lay.addLayout(body, 1)
        self.reload()

    def reload(self) -> None:
        self.snips = self.store.snippets()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.snips))
        for i, s in enumerate(self.snips):
            self.table.setItem(i, 0, QTableWidgetItem(s.trigger))
            self.table.setItem(i, 1, QTableWidgetItem(s.expansion.replace("\n", " ⏎ ")[:200]))
            self.table.setItem(i, 2, QTableWidgetItem(str(s.uses)))
        self.table.blockSignals(False)

    def _select(self, r, *_):
        if 0 <= r < len(self.snips):
            s = self.snips[r]
            self.current_id = s.id
            self.trigger.setText(s.trigger)
            self.expansion.setPlainText(s.expansion)

    def _new(self) -> None:
        self.current_id = None
        self.table.clearSelection()
        self.trigger.clear()
        self.expansion.clear()
        self.trigger.setFocus(Qt.FocusReason.OtherFocusReason)

    def _save(self) -> None:
        t, x = self.trigger.text().strip(), self.expansion.toPlainText()
        if not t or not x:
            return
        if self.current_id:
            self.store.update_snippet(self.current_id, t, x)
        else:
            self.store.add_snippet(t, x)
        self.reload()

    def _delete(self) -> None:
        if self.current_id:
            self.store.delete_snippet(self.current_id)
            self._new()
            self.reload()
