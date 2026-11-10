"""Fade in / fade out at the ends of the selected video clip (0.1 - 2 s). Video to / from black,
the clip's own sound fades too."""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QGridLayout, QLabel, QPushButton, QSlider,
                               QVBoxLayout)

from pedio.core.clips import Project


class FadePanel(QDialog):
    """Modeless panel for the selected video clip (Project.clips)."""

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Fade in / out")
        self.setMinimumWidth(340)
        self._updating = False
        self._clip_id = 0

        self.name = QLabel()
        self.name.setWordWrap(True)
        self.name.setStyleSheet("font-weight:bold;")

        self.fade_in = QSlider(Qt.Orientation.Horizontal)
        self.fade_out = QSlider(Qt.Orientation.Horizontal)
        for s in (self.fade_in, self.fade_out):
            s.setRange(0, 20)               # 0.0 .. 2.0 seconds, steps of 0.1
        self.fade_in_label = QLabel("0.0 s")
        self.fade_out_label = QLabel("0.0 s")
        self.clear_btn = QPushButton("No fade")

        grid = QGridLayout()
        grid.addWidget(QLabel("Fade in"), 0, 0)
        grid.addWidget(self.fade_in, 0, 1)
        grid.addWidget(self.fade_in_label, 0, 2)
        grid.addWidget(QLabel("Fade out"), 1, 0)
        grid.addWidget(self.fade_out, 1, 1)
        grid.addWidget(self.fade_out_label, 1, 2)

        root = QVBoxLayout(self)
        root.addWidget(self.name)
        root.addLayout(grid)
        root.addWidget(self.clear_btn)

        self.fade_in.valueChanged.connect(self._apply)
        self.fade_out.valueChanged.connect(self._apply)
        self.clear_btn.clicked.connect(self._clear)
        self.project.selection_changed.connect(self.refresh)
        self.project.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        clip = self.project.clips[self.project.index_of(self.project.selected_id)] \
            if self.project.index_of(self.project.selected_id) >= 0 else None
        self._clip_id = clip.id if clip else 0
        self._updating = True
        for w in (self.fade_in, self.fade_out, self.clear_btn):
            w.setEnabled(clip is not None)
        if clip is None:
            self.name.setText("Select a video clip on the timeline")
            self.fade_in.setValue(0)
            self.fade_out.setValue(0)
        else:
            self.name.setText(f"{os.path.basename(clip.path)}   ({clip.length:.1f} s)")
            self.fade_in.setValue(round(clip.fade_in * 10))
            self.fade_out.setValue(round(clip.fade_out * 10))
        self._update_labels()
        self._updating = False

    def _update_labels(self):
        self.fade_in_label.setText(f"{self.fade_in.value() / 10:.1f} s")
        self.fade_out_label.setText(f"{self.fade_out.value() / 10:.1f} s")

    def _apply(self, _value=None):
        if self._updating or not self._clip_id:
            return
        self.project.set_fades(self._clip_id, self.fade_in.value() / 10, self.fade_out.value() / 10)
        self._update_labels()

    def _clear(self):
        if not self._updating and self._clip_id:
            self.project.set_fades(self._clip_id, 0.0, 0.0)
