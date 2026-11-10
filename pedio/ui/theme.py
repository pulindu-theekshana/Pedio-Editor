"""Dark theme (Step 12). One stylesheet for the whole window."""

DARK_QSS = """
QWidget { background:#202020; color:#d8d8d8; font-size:12px; }
QMainWindow, QDialog { background:#1b1b1b; }
QMenuBar, QMenu, QStatusBar { background:#262626; color:#d8d8d8; }
QMenu::item:selected, QMenuBar::item:selected { background:#3a4a5a; }
QPushButton { background:#333333; border:1px solid #4a4a4a; border-radius:4px; padding:4px 10px; }
QPushButton:hover { background:#3d3d3d; }
QPushButton:pressed, QPushButton:checked { background:#4a6078; border-color:#6f8fb0; }
QPushButton:disabled { color:#777777; background:#2a2a2a; }
QLineEdit, QPlainTextEdit, QTextEdit, QDoubleSpinBox, QSpinBox, QComboBox {
    background:#2a2a2a; border:1px solid #4a4a4a; border-radius:3px; padding:3px; selection-background-color:#4a6078;
}
QComboBox QAbstractItemView { background:#2a2a2a; color:#d8d8d8; selection-background-color:#4a6078; }
QSlider::groove:horizontal { height:5px; background:#3a3a3a; border-radius:2px; }
QSlider::handle:horizontal { width:14px; margin:-5px 0; background:#7fa8d0; border-radius:7px; }
QScrollBar:horizontal, QScrollBar:vertical { background:#222222; }
QScrollBar::handle:horizontal, QScrollBar::handle:vertical { background:#555555; border-radius:4px; }
QScrollBar::add-line, QScrollBar::sub-line { width:0; height:0; }
QListWidget, QTableWidget, QTreeWidget { background:#262626; border:1px solid #444444; }
QListWidget::item:selected { background:#4a6078; }
QSplitter::handle { background:#111111; }
QToolTip { background:#2a2a2a; color:#eeeeee; border:1px solid #555555; }
QStatusBar QLabel { background:transparent; }
"""


def apply_theme(app, dark=True):
    if dark:
        app.setStyleSheet(DARK_QSS)
    else:
        app.setStyleSheet("")
