from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QStackedWidget, QVBoxLayout, QWidget

from .page_history import HistoryPage
from .page_insights import InsightsPage
from .page_notes import NotesPage
from .page_settings import SettingsPage
from .page_style import StylePage, TransformsPage
from .page_words import DictionaryPage, SnippetsPage

PAGES = [
    ("history", "Home", "view-history"),
    ("dictionary", "Dictionary", "accessories-dictionary"),
    ("snippets", "Snippets", "insert-text"),
    ("style", "Style", "format-text-bold"),
    ("transforms", "Transforms", "tools-wizard"),
    ("notes", "Scratchpad", "document-edit"),
    ("insights", "Insights", "office-chart-bar"),
    ("settings", "Settings", "configure"),
]


class HubWindow(QWidget):
    def __init__(self, cfg, store, controller, app):
        super().__init__()
        self.setWindowTitle("Talki")
        self.setWindowIcon(QIcon.fromTheme("audio-input-microphone"))
        self.resize(1100, 720)
        lay = QHBoxLayout(self)
        side = QVBoxLayout()
        brand = QLabel("Talki")
        f = brand.font()
        f.setPointSizeF(f.pointSizeF() * 1.8)
        f.setBold(True)
        brand.setFont(f)
        side.addWidget(brand)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setEnabled(False)
        self.status.setMaximumWidth(190)
        side.addWidget(self.status)
        self.nav = QListWidget()
        self.nav.setFixedWidth(190)
        self.nav.setIconSize(QSize(20, 20))
        self.nav.setFrameShape(QListWidget.Shape.NoFrame)
        side.addWidget(self.nav, 1)
        lay.addLayout(side)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)

        self.pages = {
            "history": HistoryPage(store, controller),
            "dictionary": DictionaryPage(store),
            "snippets": SnippetsPage(store),
            "style": StylePage(cfg),
            "transforms": TransformsPage(cfg),
            "notes": NotesPage(store, controller),
            "insights": InsightsPage(store, controller),
            "settings": SettingsPage(cfg, controller, app),
        }
        for key, label, icon in PAGES:
            it = QListWidgetItem(QIcon.fromTheme(icon), label)
            it.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(it)
            self.stack.addWidget(self.pages[key])
        self.nav.currentRowChanged.connect(self._switch)
        self.nav.setCurrentRow(0)
        controller.model_status.connect(self.status.setText)

    def _switch(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        page = self.stack.currentWidget()
        if hasattr(page, "reload") and page not in (self.pages["notes"], self.pages["transforms"]):
            page.reload()

    def show_page(self, key: str) -> None:
        idx = [k for k, _, _ in PAGES].index(key)
        self.nav.setCurrentRow(idx)
        self.present()

    def present(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, ev):
        self.pages["notes"].save()
        self.hide()
        ev.ignore()
