from datetime import date, timedelta

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QProgressBar, QScrollArea, QVBoxLayout, QWidget, QGroupBox, QFormLayout,
)

from .. import stats as st
from .common import heading

WEEKS = 26
CATEGORY_LABEL = {"personal": "Personal", "work": "Work", "email": "Email", "other": "Other"}


class Card(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        self.value = QLabel("–")
        f = self.value.font()
        f.setPointSizeF(f.pointSizeF() * 2.2)
        f.setBold(True)
        self.value.setFont(f)
        lay.addWidget(self.value)
        t = QLabel(title)
        t.setEnabled(False)
        lay.addWidget(t)


class Heatmap(QWidget):
    CELL = 13
    GAP = 3

    def __init__(self):
        super().__init__()
        self.daily: dict[date, int] = {}
        self.setToolTip("Words per day. Bands: 1–249, 250–499, 500–749, 750+")

    def sizeHint(self) -> QSize:
        s = self.CELL + self.GAP
        return QSize(WEEKS * s + 4, 7 * s + 4)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = self.palette().color(QPalette.ColorRole.Highlight)
        base = self.palette().color(QPalette.ColorRole.Mid)
        today = date.today()
        start = today - timedelta(days=today.weekday()) - timedelta(weeks=WEEKS - 1)
        s = self.CELL + self.GAP
        for w in range(WEEKS):
            for d in range(7):
                day = start + timedelta(weeks=w, days=d)
                if day > today:
                    continue
                band = st.band(self.daily.get(day, 0))
                if band == 0:
                    c = QColor(base)
                    c.setAlpha(70)
                else:
                    c = QColor(accent)
                    c.setAlphaF(0.25 + 0.1875 * band)
                p.setBrush(c)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(QRectF(2 + w * s, 2 + d * s, self.CELL, self.CELL), 3, 3)


class InsightsPage(QWidget):
    def __init__(self, store, controller):
        super().__init__()
        self.store = store
        outer = QVBoxLayout(self)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.addWidget(heading("Insights", "Your dictation stats. WPM uses your last 100 dictations, excluding silence."))
        grid = QGridLayout()
        self.cards = {k: Card(t) for k, t in (
            ("wpm", "Words per minute"), ("words", "Total words"), ("count", "Dictations"),
            ("streak", "Current streak (days)"), ("longest", "Longest streak (days)"), ("edits", "AI edits & commands"),
        )}
        for i, c in enumerate(self.cards.values()):
            grid.addWidget(c, i // 3, i % 3)
        lay.addLayout(grid)
        hm = QGroupBox("Activity (last 26 weeks)")
        hl = QVBoxLayout(hm)
        self.heat = Heatmap()
        hl.addWidget(self.heat)
        lay.addWidget(hm)
        self.cat_box = QGroupBox("Words by category")
        self.cat_form = QFormLayout(self.cat_box)
        lay.addWidget(self.cat_box)
        self.app_box = QGroupBox("Top apps")
        self.app_form = QFormLayout(self.app_box)
        lay.addWidget(self.app_box)
        lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        controller.history_changed.connect(self.reload)
        self.reload()

    @staticmethod
    def _clear(form: QFormLayout) -> None:
        while form.rowCount():
            form.removeRow(0)

    def reload(self) -> None:
        s = st.compute(self.store.stats_rows())
        self.cards["wpm"].value.setText(f"{s.wpm:.0f}" if s.wpm else "–")
        self.cards["words"].value.setText(f"{s.total_words:,}")
        self.cards["count"].value.setText(f"{s.dictations:,}")
        self.cards["streak"].value.setText(str(s.current_streak))
        self.cards["longest"].value.setText(str(s.longest_streak))
        self.cards["edits"].value.setText(str(s.edits))
        self.heat.daily = s.daily_words
        self.heat.update()
        total = max(1, sum(s.by_category.values()))
        self._clear(self.cat_form)
        for cat in ("personal", "work", "email", "other"):
            bar = QProgressBar(maximum=total, value=s.by_category.get(cat, 0))
            bar.setFormat(f"{s.by_category.get(cat, 0):,} words")
            self.cat_form.addRow(CATEGORY_LABEL[cat], bar)
        self._clear(self.app_form)
        top = s.by_app.most_common(8)
        amax = max([n for _, n in top] or [1])
        for app, n in top:
            bar = QProgressBar(maximum=amax, value=n)
            bar.setFormat(f"{n:,} words")
            self.app_form.addRow(app, bar)
        if not top:
            self.app_form.addRow(QLabel("No data yet"))
