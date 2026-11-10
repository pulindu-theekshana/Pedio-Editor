"""Speed panel: play the selected video clip slower or faster (0.25x .. 4x)."""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QLabel,
                               QPushButton, QSlider, QVBoxLayout)

from pedio.core.clips import MAX_SPEED, MIN_SPEED, Project

PRESETS = [("0.25x (very slow)", 0.25), ("0.5x (slow)", 0.5), ("0.75x", 0.75),
           ("1x (normal)", 1.0), ("1.25x", 1.25), ("1.5x", 1.5), ("2x (fast)", 2.0),
           ("4x (very fast)", 4.0)]


class SpeedPanel(QDialog):
    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Clip speed")
        self.setMinimumWidth(340)
        self._updating = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._apply)

        self.name = QLabel()
        self.preset = QComboBox()
        for label, value in PRESETS:
            self.preset.addItem(label, value)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(MIN_SPEED, MAX_SPEED)
        self.spin.setDecimals(2)
        self.spin.setSingleStep(0.05)
        self.spin.setSuffix("x")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(int(MIN_SPEED * 100), int(MAX_SPEED * 100))
        self.length = QLabel()

        form = QFormLayout()
        form.addRow("", self.name)
        form.addRow("Preset", self.preset)
        form.addRow("Speed", self.spin)
        form.addRow("", self.slider)
        form.addRow("New length", self.length)
        root = QVBoxLayout(self)
        root.addLayout(form)

        self.preset.currentIndexChanged.connect(self._pick_preset)
        self.spin.valueChanged.connect(self._from_spin)
        self.slider.valueChanged.connect(self._from_slider)
        self.project.selection_changed.connect(self.refresh)
        self.project.changed.connect(self.refresh)
        self.refresh()

    def _clip(self):
        return self.project.clips[self.project.index_of(self.project.selected_id)] \
            if self.project.index_of(self.project.selected_id) >= 0 else None

    def refresh(self):
        clip = self._clip()
        self._updating = True
        for w in (self.preset, self.spin, self.slider):
            w.setEnabled(clip is not None)
        if clip is None:
            self.name.setText("Select a video clip on the timeline")
            self.length.setText("")
        else:
            self.name.setText(f"{os.path.basename(clip.path)}   ({clip.length:.2f} s)")
            self.spin.setValue(clip.speed)
            self.slider.setValue(round(clip.speed * 100))
            self.preset.setCurrentIndex(min(range(len(PRESETS)),
                                            key=lambda i: abs(PRESETS[i][1] - clip.speed)))
            self.length.setText(f"{clip.source_length / clip.speed:.2f} s")
        self._updating = False

    def _pick_preset(self, index):
        if not self._updating and index >= 0:
            self.spin.setValue(PRESETS[index][1])

    def _from_spin(self, value):
        if self._updating:
            return
        self._updating = True
        self.slider.setValue(round(value * 100))
        self._updating = False
        self._timer.start()

    def _from_slider(self, value):
        if self._updating:
            return
        self._updating = True
        self.spin.setValue(value / 100)
        self._updating = False
        self._timer.start()

    def _apply(self):
        clip = self._clip()
        if clip is not None:
            self.project.set_speed(clip.id, self.spin.value())
