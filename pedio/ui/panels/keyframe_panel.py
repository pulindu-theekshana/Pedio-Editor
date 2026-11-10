"""Keyframe panel: animate x / y / size / scale / opacity / volume at the playhead.

The same keyframes are used by the preview (player filters, overlay widgets, ASS) and by the export
(ffmpeg expressions, ASS), see core/keyframes.py.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout)

from pedio.core.clips import Project
from pedio.core.keyframes import EASINGS, value_at
from pedio.core.layers import layer_value

PROPS = {
    "text": [("x", 0.0, 1.0), ("y", 0.0, 1.0), ("size", 8.0, 400.0), ("opacity", 0.0, 1.0)],
    "image": [("x", 0.0, 1.0), ("y", 0.0, 1.0), ("scale", 0.05, 2.0), ("opacity", 0.0, 1.0)],
    "audio": [("volume", 0.0, 2.0)],
}


class KeyframePanel(QDialog):
    def __init__(self, project: Project, position_fn, parent=None):
        super().__init__(parent)
        self.project = project
        self.position_fn = position_fn
        self._updating = False
        self._clipboard = None          # (kind, anim) copied from another layer
        self.setWindowTitle("Keyframes")
        self.setMinimumWidth(430)

        self.title = QLabel()
        self.title.setStyleSheet("font-weight:bold;")
        self.property = QComboBox()
        self.value = QDoubleSpinBox()
        self.value.setDecimals(3)
        self.value.setSingleStep(0.05)
        self.easing = QComboBox()
        for name in EASINGS:
            self.easing.addItem(name, name)
        self.add_btn = QPushButton("Add / update key at playhead")
        self.delete_btn = QPushButton("Delete selected key")
        self.clear_btn = QPushButton("Clear this property")
        self.copy_btn = QPushButton("Copy all keyframes")
        self.paste_btn = QPushButton("Paste all keyframes")
        self.list = QListWidget()
        self.list.setMinimumHeight(150)

        form = QFormLayout()
        form.addRow("Property", self.property)
        form.addRow("Value", self.value)
        form.addRow("Easing", self.easing)

        buttons = QHBoxLayout()
        buttons.addWidget(self.add_btn)
        buttons.addWidget(self.delete_btn)
        buttons.addWidget(self.clear_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.copy_btn)
        buttons.addWidget(self.paste_btn)

        root = QVBoxLayout(self)
        root.addWidget(self.title)
        root.addLayout(form)
        root.addLayout(buttons)
        root.addWidget(self.list)

        self.property.currentIndexChanged.connect(self.refresh)
        self.list.itemSelectionChanged.connect(self._selected)
        self.add_btn.clicked.connect(self._add)
        self.delete_btn.clicked.connect(self._delete)
        self.clear_btn.clicked.connect(self._clear)
        self.copy_btn.clicked.connect(self._copy)
        self.paste_btn.clicked.connect(self._paste)
        self.project.selection_changed.connect(self.refresh)
        self.project.changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------ target
    def _target(self):
        """(kind, item) for the current selection (text, then image, then audio)."""
        project = self.project
        layer = project.text_by_id(project.selected_text_id)
        if layer is not None:
            return "text", layer
        layer = project.image_by_id(project.selected_image_id)
        if layer is not None:
            return "image", layer
        layer = project.audio_by_id(project.selected_audio_id)
        if layer is not None:
            return "audio", layer
        return None, None

    def _prop(self):
        return self.property.currentData()

    # ------------------------------------------------------------------ drawing
    def refresh(self):
        kind, target = self._target()
        self._updating = True
        self.property.blockSignals(True)
        self.property.clear()
        if target is not None:
            for name, lo, hi in PROPS[kind]:
                self.property.addItem(name, name)
        self.property.blockSignals(False)
        enabled = target is not None
        for w in (self.property, self.value, self.easing, self.add_btn, self.delete_btn,
                  self.clear_btn, self.list):
            w.setEnabled(enabled)
        self.copy_btn.setEnabled(enabled)
        self.paste_btn.setEnabled(enabled and self._clipboard is not None
                                  and self._clipboard[0] == kind)
        if target is None:
            self.title.setText("Select a text, image or audio layer first")
            self.list.clear()
            self._updating = False
            return
        self.title.setText(f"{kind.title()} #{target.id}: keyframes at the playhead")
        points = (target.anim or {}).get(self._prop(), [])
        self.list.clear()
        for point in points:
            item = QListWidgetItem(f"{point.t:8.2f} s    {point.value:.3f}    ({point.easing})")
            item.setData(Qt.ItemDataRole.UserRole, point.t)
            self.list.addItem(item)
        t = self.position_fn()
        lo, hi = next((lo, hi) for name, lo, hi in PROPS[kind] if name == self._prop())
        self.value.setRange(lo, hi)
        self.value.setValue(value_at(points, t, getattr(target, self._prop())))
        self._updating = False

    def _selected(self):
        if self._updating:
            return
        kind, target = self._target()
        item = self.list.currentItem()
        if target is None or item is None:
            return
        t = item.data(Qt.ItemDataRole.UserRole)
        point = next((p for p in (target.anim or {}).get(self._prop(), []) if abs(p.t - t) < 1e-6), None)
        if point is None:
            return
        self._updating = True
        self.value.setValue(point.value)
        index = self.easing.findData(point.easing)
        self.easing.setCurrentIndex(max(index, 0))
        self._updating = False

    # ------------------------------------------------------------------ edits
    def _add(self):
        kind, target = self._target()
        if target is not None:
            self.project.set_keyframe(kind, target.id, self._prop(), self.position_fn(),
                                      self.value.value(), self.easing.currentData())
            self.refresh()

    def _delete(self):
        kind, target = self._target()
        item = self.list.currentItem()
        if target is not None and item is not None:
            self.project.remove_keyframe(kind, target.id, self._prop(),
                                         item.data(Qt.ItemDataRole.UserRole))
            self.refresh()

    def _clear(self):
        kind, target = self._target()
        if target is None:
            return
        points = (target.anim or {}).get(self._prop(), [])
        for point in list(points):
            self.project.remove_keyframe(kind, target.id, self._prop(), point.t)
        self.project.changed.emit()
        self.refresh()

    def _copy(self):
        kind, target = self._target()
        if target is not None:
            self._clipboard = (kind, {key: list(points) for key, points in (target.anim or {}).items()})
            self.paste_btn.setEnabled(False)

    def _paste(self):
        kind, target = self._target()
        if target is None or self._clipboard is None or self._clipboard[0] != kind:
            return
        target.anim = {key: list(points) for key, points in self._clipboard[1].items()}
        self.project.changed.emit()
        self.refresh()
