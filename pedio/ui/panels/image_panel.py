"""Image panel: timeline range, position, size and opacity of the selected image layer."""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                               QPushButton, QSlider, QVBoxLayout, QWidget)

from pedio.core.clips import Project


def _slider_row(slider):
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(slider, 1)
    return box


class ImagePanel(QDialog):
    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Image")
        self.setMinimumWidth(380)
        self._updating = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._apply)

        self.title = QLabel()
        self.title.setStyleSheet("font-weight:bold;")
        self.start = QDoubleSpinBox()
        self.end = QDoubleSpinBox()
        for box in (self.start, self.end):
            box.setRange(0.0, 24 * 3600.0)
            box.setDecimals(2)
            box.setSingleStep(0.1)
            box.setSuffix(" s")
        self.x = QSlider(Qt.Orientation.Horizontal)
        self.y = QSlider(Qt.Orientation.Horizontal)
        self.scale = QSlider(Qt.Orientation.Horizontal)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        for s in (self.x, self.y, self.opacity):
            s.setRange(0, 100)
        self.scale.setRange(5, 200)
        self.scale_label = QLabel()
        self.opacity_label = QLabel()
        self.delete_btn = QPushButton("Delete image")
        self.up_btn = QPushButton("Move up (draw later)")
        self.down_btn = QPushButton("Move down (draw earlier)")

        form = QFormLayout()
        form.addRow("Start", self.start)
        form.addRow("End", self.end)
        form.addRow("X (free space)", _slider_row(self.x))
        form.addRow("Y (free space)", _slider_row(self.y))
        form.addRow("Size", _slider_row(self.scale))
        form.addRow("", self.scale_label)
        form.addRow("Opacity", _slider_row(self.opacity))
        form.addRow("", self.opacity_label)

        order = QHBoxLayout()
        order.addWidget(self.up_btn)
        order.addWidget(self.down_btn)
        root = QVBoxLayout(self)
        root.addWidget(self.title)
        root.addLayout(form)
        root.addLayout(order)
        root.addWidget(self.delete_btn)

        for w in (self.start, self.end, self.x, self.y, self.scale, self.opacity):
            w.valueChanged.connect(self._queue)
        self.delete_btn.clicked.connect(self._delete)
        self.up_btn.clicked.connect(lambda: self._move(1))
        self.down_btn.clicked.connect(lambda: self._move(-1))
        self.project.selection_changed.connect(self.refresh)
        self.project.changed.connect(self.refresh)
        self.refresh()

    def _layer(self):
        return self.project.image_by_id(self.project.selected_image_id)

    def refresh(self):
        layer = self._layer()
        self._updating = True
        for w in (self.start, self.end, self.x, self.y, self.scale, self.opacity, self.delete_btn,
                  self.up_btn, self.down_btn):
            w.setEnabled(layer is not None)
        if layer is None:
            self.title.setText("No image selected (Image > Add Image)")
        else:
            self.title.setText(f"Image layer  #{layer.id}  ({os.path.basename(layer.path)})")
            self.start.setValue(layer.start)
            self.end.setValue(layer.end)
            self.x.setValue(round(layer.x * 100))
            self.y.setValue(round(layer.y * 100))
            self.scale.setValue(round(layer.scale * 100))
            self.opacity.setValue(round(layer.opacity * 100))
        self._labels()
        self._updating = False

    def _labels(self):
        self.scale_label.setText(f"{self.scale.value()}% of the frame width")
        self.opacity_label.setText(f"{self.opacity.value()}%")

    def _queue(self, _value=None):
        self._labels()
        if not self._updating:
            self._timer.start()

    def _apply(self):
        layer = self._layer()
        if self._updating or layer is None:
            return
        start = self.start.value()
        end = max(self.end.value(), start + 0.1)
        self.project.update_image_layer(
            layer.id, start=start, end=end,
            x=self.x.value() / 100, y=self.y.value() / 100,
            scale=self.scale.value() / 100, opacity=self.opacity.value() / 100)

    def _delete(self):
        layer = self._layer()
        if layer is not None:
            self.project.remove_image_layer(layer.id)

    def _move(self, step):
        layer = self._layer()
        if layer is not None:
            index = self.project.image_index_of(layer.id)
            self.project.move_image_layer(layer.id, index + step)
