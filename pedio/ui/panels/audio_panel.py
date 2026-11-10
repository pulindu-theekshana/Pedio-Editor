"""Small panel for the selected audio clip: volume, mute and fade in / out at its ends."""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QGridLayout, QLabel, QSlider,
                               QVBoxLayout)

from pedio.core.clips import Project


class AudioPanel(QDialog):
    """Modeless panel. It edits the audio clip selected on the timeline (Project.audio_clips)."""

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Audio clip")
        self.setMinimumWidth(340)
        self._updating = False
        self._clip_id = 0

        self.name = QLabel()
        self.name.setWordWrap(True)
        self.name.setStyleSheet("font-weight:bold;")

        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 200)
        self.volume_label = QLabel("100%")
        self.mute = QCheckBox("Mute this sound")
        self.fade_in = QSlider(Qt.Orientation.Horizontal)
        self.fade_out = QSlider(Qt.Orientation.Horizontal)
        for s in (self.fade_in, self.fade_out):
            s.setRange(0, 20)                 # 0.0 .. 2.0 seconds, steps of 0.1
        self.fade_in_label = QLabel("0.0 s")
        self.fade_out_label = QLabel("0.0 s")

        grid = QGridLayout()
        grid.addWidget(QLabel("Volume"), 0, 0)
        grid.addWidget(self.volume, 0, 1)
        grid.addWidget(self.volume_label, 0, 2)
        grid.addWidget(self.mute, 1, 0, 1, 3)
        grid.addWidget(QLabel("Fade in"), 2, 0)
        grid.addWidget(self.fade_in, 2, 1)
        grid.addWidget(self.fade_in_label, 2, 2)
        grid.addWidget(QLabel("Fade out"), 3, 0)
        grid.addWidget(self.fade_out, 3, 1)
        grid.addWidget(self.fade_out_label, 3, 2)

        root = QVBoxLayout(self)
        root.addWidget(self.name)
        root.addLayout(grid)

        self.volume.valueChanged.connect(self._apply)
        self.mute.toggled.connect(self._apply)
        self.fade_in.valueChanged.connect(self._apply)
        self.fade_out.valueChanged.connect(self._apply)
        self.project.selection_changed.connect(self.refresh)
        self.project.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        clip = self.project.audio_by_id(self.project.selected_audio_id)
        self._clip_id = clip.id if clip else 0
        self._updating = True
        for w in (self.volume, self.mute, self.fade_in, self.fade_out):
            w.setEnabled(clip is not None)
        if clip is None:
            self.name.setText("Select an audio clip on the timeline")
            self.volume.setValue(100)
            self.mute.setChecked(False)
            self.fade_in.setValue(0)
            self.fade_out.setValue(0)
        else:
            self.name.setText(f"{os.path.basename(clip.path)}   ({clip.length:.1f} s)")
            self.volume.setValue(round(clip.volume * 100))
            self.mute.setChecked(clip.mute)
            self.fade_in.setValue(round(clip.fade_in * 10))
            self.fade_out.setValue(round(clip.fade_out * 10))
        self._update_labels()
        self._updating = False

    def _update_labels(self):
        self.volume_label.setText(f"{self.volume.value()}%")
        self.fade_in_label.setText(f"{self.fade_in.value() / 10:.1f} s")
        self.fade_out_label.setText(f"{self.fade_out.value() / 10:.1f} s")

    def _apply(self, _value=None):
        if self._updating or not self._clip_id:
            return
        clip = self.project.audio_by_id(self._clip_id)
        if clip is None:
            return
        self.project.set_audio_volume(clip.id, self.volume.value() / 100)
        self.project.set_audio_mute(clip.id, self.mute.isChecked())
        self.project.set_audio_fades(clip.id, self.fade_in.value() / 10, self.fade_out.value() / 10)
        self._update_labels()
