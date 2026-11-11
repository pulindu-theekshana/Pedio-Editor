"""Export settings: container, quality, resolution, frame rate and audio bitrate."""
from fractions import Fraction

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QVBoxLayout)

from pedio.core.export import CONTAINERS, ExportSettings

QUALITY = [("Best (CRF 16)", 16, "slow"), ("High (CRF 19)", 19, "medium"),
           ("Balanced (CRF 20)", 20, "veryfast"), ("Small file (CRF 28)", 28, "veryfast")]
RESOLUTIONS = [("Original", None), ("1920 x 1080", (1920, 1080)), ("1280 x 720", (1280, 720)),
               ("854 x 480", (854, 480)), ("1080 x 1920 (vertical)", (1080, 1920)),
               ("1080 x 1080 (square)", (1080, 1080))]
FPS = [("Original", None), ("24 fps", Fraction(24)), ("25 fps", Fraction(25)),
       ("30 fps", Fraction(30)), ("50 fps", Fraction(50)), ("60 fps", Fraction(60))]


class ExportSettingsDialog(QDialog):
    def __init__(self, settings: ExportSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export settings")
        self.setMinimumWidth(380)

        self.format = QComboBox()
        for name, value in (("MP4 (H.264 + AAC)", "mp4"), ("MKV (H.264 + AAC)", "mkv"),
                            ("WebM (VP9 + Opus)", "webm")):
            self.format.addItem(name, value)
        self.quality = QComboBox()
        for label, crf, preset in QUALITY:
            self.quality.addItem(label, (crf, preset))
        self.resolution = QComboBox()
        for label, size in RESOLUTIONS:
            self.resolution.addItem(label, size)
        self.fps = QComboBox()
        for label, fps in FPS:
            self.fps.addItem(label, fps)
        self.audio = QComboBox()
        for bitrate in ("128k", "192k", "256k", "320k"):
            self.audio.addItem(bitrate, bitrate)
        self.note = QLabel("The file is written as <name>.mp4.part and renamed when finished.")
        self.note.setWordWrap(True)

        self._select(self.format, settings.format)
        self._select(self.quality, next((item for item in QUALITY if item[1] == settings.crf),
                                        QUALITY[2])[1:])
        self._select(self.resolution, settings.size)
        self._select(self.fps, settings.fps)
        self._select(self.audio, settings.audio_bitrate)

        form = QFormLayout()
        form.addRow("Format", self.format)
        form.addRow("Quality", self.quality)
        form.addRow("Size", self.resolution)
        form.addRow("Frame rate", self.fps)
        form.addRow("Audio", self.audio)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(self.note)
        root.addWidget(buttons)

    @staticmethod
    def _select(combo, value):
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return
        combo.setCurrentIndex(0)

    def values(self):
        crf, preset = self.quality.currentData()
        fmt = self.format.currentData()
        if fmt not in CONTAINERS:
            fmt = "mp4"
        return ExportSettings(crf=crf, preset=preset, audio_bitrate=self.audio.currentData(),
                              size=self.resolution.currentData(), fps=self.fps.currentData(),
                              format=fmt)
