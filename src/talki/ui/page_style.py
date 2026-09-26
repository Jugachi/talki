from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QListWidget,
    QPlainTextEdit, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from .common import heading, hint

STYLE_INFO = {
    "formal": ("Formal", "Caps + full punctuation"),
    "casual": ("Casual", "Caps + lighter punctuation"),
    "very_casual": ("Very casual", "no caps + less punctuation"),
    "excited": ("Excited!", "More exclamations!"),
}
CATEGORY_INFO = {
    "personal": ("Personal messages", "Signal, WhatsApp, Telegram, Discord…", ("formal", "casual", "very_casual")),
    "work": ("Work messages", "Slack, Teams, Mattermost…", ("formal", "casual", "excited")),
    "email": ("Email", "Thunderbird, KMail, Gmail in the browser…", ("formal", "casual", "excited")),
    "other": ("Everything else", "Editors, AI chats, documents…", ("formal", "casual", "excited")),
}
LEVELS = {
    "none": "None: raw transcript",
    "light": "Light: remove fillers, fix grammar",
    "medium": "Medium: edit for clarity and concision",
    "high": "High: rewrite into polished prose",
}


class StylePage(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        lay = QVBoxLayout(self)
        lay.addWidget(heading(
            "Style",
            "How your words are written, per kind of app. Styles apply to English dictation.",
        ))

        cleanup = QGroupBox("Auto cleanup")
        cf = QFormLayout(cleanup)
        self.level = QComboBox()
        for k, v in LEVELS.items():
            self.level.addItem(v, k)
        self.level.setCurrentIndex(list(LEVELS).index(cfg.get("cleanup.level")))
        self.level.currentIndexChanged.connect(lambda: cfg.set("cleanup.level", self.level.currentData()))
        cf.addRow("Cleanup level", self.level)
        for key, label in (
            ("cleanup.smart_formatting", "Smart formatting (punctuation, lists, spoken commands like \"new line\")"),
            ("cleanup.drop_period_in_messages", "Drop the final period in short chat messages"),
            ("cleanup.press_enter_command", "Ending with \"press enter\" sends the message"),
            ("cleanup.ide_features", "Developer mode in IDEs (identifier spelling, \"at file dot py\" → @file.py)"),
        ):
            cb = QCheckBox(label)
            cb.setChecked(cfg.get(key))
            cb.toggled.connect(lambda v, k=key: cfg.set(k, v))
            cf.addRow(cb)
        lay.addWidget(cleanup)

        for cat, (title, apps, allowed) in CATEGORY_INFO.items():
            box = QGroupBox(title)
            bl = QVBoxLayout(box)
            bl.addWidget(hint(apps))
            rl = QHBoxLayout()
            group = QButtonGroup(box)
            for s in allowed:
                name, desc = STYLE_INFO[s]
                rb = QRadioButton(f"{name}  ·  {desc}")
                rb.setChecked(cfg.get("styles").get(cat) == s)
                rb.toggled.connect(lambda on, c=cat, s=s: on and self._set_style(c, s))
                group.addButton(rb)
                rl.addWidget(rb)
            rl.addStretch(1)
            bl.addLayout(rl)
            lay.addWidget(box)
        lay.addWidget(hint(
            "Apps are matched by window class, and browser tabs by window title. Edit "
            "\"app_categories\" and \"title_categories\" in ~/.config/talki/config.json to add your own."
        ))
        lay.addStretch(1)

    def _set_style(self, cat: str, style: str) -> None:
        styles = self.cfg.get("styles")
        styles[cat] = style
        self.cfg.set("styles", styles)


class TransformsPage(QWidget):
    MAX = 9

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        lay = QVBoxLayout(self)
        lay.addWidget(heading(
            "Transforms",
            "Select text in any app and press Ctrl+Meta+<slot> to rewrite it with the local AI. "
            "Up to 9 transforms; the slot number is the position in this list.",
        ))
        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        body.addWidget(self.list, 1)
        ed = QVBoxLayout()
        form = QFormLayout()
        self.name = QLineEdit(maxLength=40)
        form.addRow("Name", self.name)
        ed.addLayout(form)
        self.instruction = QPlainTextEdit(placeholderText="Instruction, e.g. \"Make this sound friendlier and shorter.\"")
        ed.addWidget(self.instruction, 1)
        eb = QHBoxLayout()
        for label, fn in (("New", self._new), ("Save", self._save), ("Delete", self._delete),
                          ("Move up", lambda: self._move(-1)), ("Move down", lambda: self._move(1))):
            b = QPushButton(label)
            b.clicked.connect(fn)
            eb.addWidget(b)
        ed.addLayout(eb)
        body.addLayout(ed, 2)
        lay.addLayout(body, 1)
        self.reload()

    def reload(self, select: int = 0) -> None:
        self.items = self.cfg.get("transforms")
        self.list.clear()
        for i, t in enumerate(self.items, 1):
            self.list.addItem(f"{i}. {t['name']}")
        if self.items:
            self.list.setCurrentRow(min(select, len(self.items) - 1))

    def _select(self, r: int) -> None:
        if 0 <= r < len(self.items):
            self.name.setText(self.items[r]["name"])
            self.instruction.setPlainText(self.items[r]["instruction"])

    def _new(self) -> None:
        self.list.setCurrentRow(-1)
        self.name.clear()
        self.instruction.clear()

    def _save(self) -> None:
        t = {"name": self.name.text().strip(), "instruction": self.instruction.toPlainText().strip()}
        if not t["name"] or not t["instruction"]:
            return
        r = self.list.currentRow()
        if 0 <= r < len(self.items):
            self.items[r] = t
        elif len(self.items) < self.MAX:
            self.items.append(t)
            r = len(self.items) - 1
        self.cfg.set("transforms", self.items)
        self.reload(r)

    def _delete(self) -> None:
        r = self.list.currentRow()
        if 0 <= r < len(self.items):
            del self.items[r]
            self.cfg.set("transforms", self.items)
            self.reload(r)

    def _move(self, d: int) -> None:
        r = self.list.currentRow()
        n = r + d
        if 0 <= r < len(self.items) and 0 <= n < len(self.items):
            self.items[r], self.items[n] = self.items[n], self.items[r]
            self.cfg.set("transforms", self.items)
            self.reload(n)
