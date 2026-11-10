"""Text panel: write the text and set its size, colours, outline, shadow and timeline range."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

from pedio.core.clips import Project
from pedio.core.layers import TextLayer, normalize_color


def _color_button(initial="#ffffff"):
    button = QPushButton()
    button.setFixedSize(40, 24)
    _paint_swatch(button, initial)
    return button


def _paint_swatch(button, color):
    button.setProperty("color", color)
    button.setStyleSheet(f"background:{color}; border:1px solid #555;")


def _slider_row(slider):
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(slider, 1)
    return box


class TextPanel(QDialog):
    """Modeless panel for the selected text layer (Project.text_layers)."""

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Text")
        self.setMinimumWidth(380)
        self._updating = False
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(250)
        self._apply_timer.timeout.connect(self._apply)

        self.title = QLabel()
        self.title.setStyleSheet("font-weight:bold;")
        self.text = QPlainTextEdit()
        self.text.setFixedHeight(64)
        self.size = QSlider(Qt.Orientation.Horizontal)
        self.size.setRange(8, 200)
        self.size_label = QLabel()
        self.outline = QSlider(Qt.Orientation.Horizontal)
        self.outline.setRange(0, 20)
        self.outline_label = QLabel()
        self.shadow = QSlider(Qt.Orientation.Horizontal)
        self.shadow.setRange(0, 20)
        self.shadow_label = QLabel()
        self.color = _color_button("#ffffff")
        self.outline_color = _color_button("#000000")
        self.shadow_color = _color_button("#000000")
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity_label = QLabel()
        self.start = QDoubleSpinBox()
        self.end = QDoubleSpinBox()
        for box in (self.start, self.end):
            box.setRange(0.0, 24 * 3600.0)
            box.setDecimals(2)
            box.setSingleStep(0.1)
            box.setSuffix(" s")
        self.x = QSlider(Qt.Orientation.Horizontal)
        self.y = QSlider(Qt.Orientation.Horizontal)
        for s in (self.x, self.y):
            s.setRange(0, 100)
        self.delete_btn = QPushButton("Delete text")

        form = QFormLayout()
        form.addRow("Text", self.text)
        form.addRow("Size", _slider_row(self.size))
        form.addRow("", self.size_label)
        form.addRow("Colour", self.color)
        form.addRow("Outline", _slider_row(self.outline))
        form.addRow("", self.outline_label)
        form.addRow("Outline colour", self.outline_color)
        form.addRow("Shadow", _slider_row(self.shadow))
        form.addRow("", self.shadow_label)
        form.addRow("Shadow colour", self.shadow_color)
        form.addRow("Opacity", _slider_row(self.opacity))
        form.addRow("", self.opacity_label)
        form.addRow("Start", self.start)
        form.addRow("End", self.end)
        form.addRow("X", self.x)
        form.addRow("Y", self.y)

        root = QVBoxLayout(self)
        root.addWidget(self.title)
        root.addLayout(form)
        root.addWidget(self.delete_btn)

        self.text.textChanged.connect(self._queue_apply)
        self.size.valueChanged.connect(self._queue_apply)
        self.outline.valueChanged.connect(self._queue_apply)
        self.shadow.valueChanged.connect(self._queue_apply)
        self.opacity.valueChanged.connect(self._queue_apply)
        self.start.valueChanged.connect(self._queue_apply)
        self.end.valueChanged.connect(self._queue_apply)
        self.x.valueChanged.connect(self._queue_apply)
        self.y.valueChanged.connect(self._queue_apply)
        self.color.clicked.connect(lambda: self._pick_color("color"))
        self.outline_color.clicked.connect(lambda: self._pick_color("outline_color"))
        self.shadow_color.clicked.connect(lambda: self._pick_color("shadow_color"))
        self.delete_btn.clicked.connect(self._delete)
        self.project.selection_changed.connect(self.refresh)
        self.project.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        layer = self.project.text_by_id(self.project.selected_text_id)
        self._updating = True
        for w in (self.text, self.size, self.outline, self.shadow, self.opacity,
                  self.start, self.end, self.x, self.y, self.color, self.outline_color,
                  self.shadow_color, self.delete_btn):
            w.setEnabled(layer is not None)
        if layer is None:
            self.title.setText("No text selected (Text > Add Text)")
        else:
            self.title.setText(f"Text layer  #{layer.id}")
            self.text.setPlainText(layer.text)
            self.size.setValue(int(layer.size))
            self.outline.setValue(int(layer.outline))
            self.shadow.setValue(int(layer.shadow))
            self.opacity.setValue(round(layer.opacity * 100))
            _paint_swatch(self.color, layer.color)
            _paint_swatch(self.outline_color, layer.outline_color)
            _paint_swatch(self.shadow_color, layer.shadow_color)
            self.start.setValue(layer.start)
            self.end.setValue(layer.end)
            self.x.setValue(round(layer.x * 100))
            self.y.setValue(round(layer.y * 100))
        self._update_labels()
        self._updating = False

    def _update_labels(self):
        self.size_label.setText(f"{self.size.value()} px (on a 1920 x 1080 canvas)")
        self.outline_label.setText(f"{self.outline.value()} px")
        self.shadow_label.setText(f"{self.shadow.value()} px")
        self.opacity_label.setText(f"{self.opacity.value()}%")

    def _queue_apply(self, _value=None):
        self._update_labels()
        if not self._updating:
            self._apply_timer.start()

    def _apply(self):
        layer = self.project.text_by_id(self.project.selected_text_id)
        if self._updating or layer is None:
            return
        start = self.start.value()
        end = max(self.end.value(), start + 0.1)
        self.project.update_text_layer(
            layer.id,
            text=self.text.toPlainText(),
            size=self.size.value(),
            color=self.color.property("color"),
            outline=self.outline.value(),
            outline_color=self.outline_color.property("color"),
            shadow=self.shadow.value(),
            shadow_color=self.shadow_color.property("color"),
            opacity=self.opacity.value() / 100,
            start=start,
            end=end,
            x=self.x.value() / 100,
            y=self.y.value() / 100,
        )

    def _pick_color(self, field):
        layer = self.project.text_by_id(self.project.selected_text_id)
        if layer is None:
            return
        start = getattr(layer, field)
        color = QColorDialog.getColor(QColor(start), self, "Pick a colour")
        if color.isValid():
            hex_color = normalize_color(color.name())
            button = getattr(self, field)
            _paint_swatch(button, hex_color)
            self._queue_apply()

    def _delete(self):
        layer = self.project.text_by_id(self.project.selected_text_id)
        if layer is not None:
            self.project.remove_text_layer(layer.id)
