"""App settings that are worth keeping: dark theme and hardware decoding."""
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QVBoxLayout)


class SettingsDialog(QDialog):
    def __init__(self, dark=True, hwdec=True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(360)
        self.dark = QCheckBox("Dark theme")
        self.dark.setChecked(bool(dark))
        self.hwdec = QCheckBox("Hardware decoding (faster preview)")
        self.hwdec.setChecked(bool(hwdec))
        note = QLabel("If the preview shows a black picture or an error, turn hardware decoding off.")
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow(self.dark)
        form.addRow(self.hwdec)
        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(note)
        root.addWidget(buttons)
