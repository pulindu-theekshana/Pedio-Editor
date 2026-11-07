from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout,
                               QPushButton, QStackedWidget, QButtonGroup)

CATEGORIES = {
    "Video":   ["Import", "Trim Start", "Trim End", "Split", "Delete", "Crop", "Aspect Ratio", "Rotate", "Flip", "Speed"],
    "Audio":   ["Add Audio", "Extract Audio", "Volume", "Mute", "Fade In/Out"],
    "Effects": ["Fade In", "Fade Out", "Transition"],
    "Text":    ["Add Text", "Edit Text"],
    "Export":  ["Export Video", "Export Settings"],
}


class BottomToolbar(QWidget):
    tool_clicked = Signal(str, str)  # category, tool

    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # tools row (changes with the selected category)
        self.stack = QStackedWidget()
        self.stack.setFixedHeight(46)
        root.addWidget(self.stack)

        # category row
        cat_row = QHBoxLayout()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, (cat, tools) in enumerate(CATEGORIES.items()):
            btn = QPushButton(cat)
            btn.setCheckable(True)
            btn.setMinimumHeight(32)
            self.group.addButton(btn, i)
            cat_row.addWidget(btn)
            self.stack.addWidget(self._make_tools_page(cat, tools))
        self.group.idClicked.connect(self.stack.setCurrentIndex)
        self.group.button(0).setChecked(True)
        root.addLayout(cat_row)

    def _make_tools_page(self, cat, tools):
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        for t in tools:
            b = QPushButton(t)
            b.setMinimumHeight(40)
            b.clicked.connect(lambda _=False, c=cat, t=t: self.tool_clicked.emit(c, t))
            lay.addWidget(b)
        lay.addStretch()
        return page
