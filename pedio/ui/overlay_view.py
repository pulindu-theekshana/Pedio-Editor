"""Transparent layer over the video: drag the selected text layer to a new place.

mpv draws the text itself (from the generated ASS file). This overlay only paints a ghost while the mouse drags,
and tells the project the new position when the mouse is released, so mpv is not reloaded for every pixel.
"""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from pedio.core.clips import Project
from pedio.core.layers import CANVAS_H, TextLayer, layer_value


class TextOverlay(QWidget):
    """Active only while a text layer is selected: press inside the text and drag."""

    committed = Signal(float, float)   # new x, y (fractions)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = None
        self.position = 0.0
        self._layer_id = 0
        self._drag = False
        self._ghost = None            # (x, y) centre in widget pixels while dragging
        self._grab = QPointF()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.hide()

    # ------------------------------------------------------------------ state
    def set_project(self, project: Project):
        self.project = project
        project.selection_changed.connect(self.refresh)
        project.changed.connect(self.refresh)
        self.refresh()

    def set_position(self, t):
        t = float(t)
        if abs(t - self.position) < 1e-6:
            return
        self.position = t
        if self.isVisible():
            self.update()

    def refresh(self):
        layer = self._layer()
        if layer is None or self.parentWidget() is None:
            self._layer_id = 0
            self._drag = False
            self._ghost = None
            self.hide()
            return
        self._layer_id = layer.id
        self.show()
        self.raise_()
        self.update()

    def _layer(self):
        if self.project is None:
            return None
        return self.project.text_by_id(self.project.selected_text_id)

    # ------------------------------------------------------------------ geometry
    def _font(self, layer: TextLayer):
        font = QFont("Arial")
        pixel = max(int(layer.size * self.height() / CANVAS_H), 4)
        font.setPixelSize(pixel)
        return font

    def _centre(self, layer):
        if self._ghost is not None:
            return self._ghost
        x = layer_value(layer, "x", self.position, layer.x)
        y = layer_value(layer, "y", self.position, layer.y)
        return QPointF(x * self.width(), y * self.height())

    def _text_rect(self, layer):
        font = self._font(layer)
        metrics = QFontMetricsF(font)
        lines = str(layer.text).split("\n") or [""]
        width = max(metrics.horizontalAdvance(line) for line in lines)
        height = metrics.lineSpacing() * len(lines)
        centre = self._centre(layer)
        return QRectF(centre.x() - width / 2, centre.y() - height / 2, width, height)

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event):
        layer = self._layer()
        if layer is None or not str(layer.text).strip():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setOpacity(min(max(layer_value(layer, "opacity", self.position, layer.opacity), 0.0), 1.0))
        self._paint_text(painter, layer, self._centre(layer))
        painter.setOpacity(1.0)
        if self._drag:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(80, 160, 255, 220), 1, Qt.PenStyle.DashLine))
            painter.drawRect(self._text_rect(layer).adjusted(-10, -10, 10, 10))
        painter.end()

    def _paint_text(self, painter, layer, centre):
        font = self._font(layer)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        lines = str(layer.text).split("\n") or [""]
        scale = self.height() / CANVAS_H
        outline = layer.outline * scale
        path = QPainterPath()
        top = centre.y() - metrics.lineSpacing() * len(lines) / 2
        for i, line in enumerate(lines):
            width = metrics.horizontalAdvance(line)
            # addText() takes the baseline, so go one ascent down from the line's top
            path.addText(QPointF(centre.x() - width / 2, top + metrics.ascent() + i * metrics.lineSpacing()),
                         font, line)
        if outline > 0:
            painter.setPen(QPen(QColor(layer.outline_color), outline * 2,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(layer.color))
        painter.drawPath(path)

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        layer = self._layer()
        if layer is None:
            return
        rect = self._text_rect(layer).adjusted(-14, -14, 14, 14)
        if rect.contains(event.position()):
            self._drag = True
            centre = self._centre(layer)
            self._grab = event.position() - centre
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
        else:
            self.project.select_text(0)

    def mouseMoveEvent(self, event):
        layer = self._layer()
        if layer is None:
            return
        if self._drag:
            centre = event.position() - self._grab
            self._ghost = QPointF(centre)
            self.update()
        elif self._text_rect(layer).adjusted(-14, -14, 14, 14).contains(event.position()):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._drag:
            return
        self._drag = False
        self.unsetCursor()
        centre = self._ghost or self._centre(self._layer())
        self._ghost = None
        if self.width() > 0 and self.height() > 0:
            self.committed.emit(centre.x() / self.width(), centre.y() / self.height())
        self.update()

    def leaveEvent(self, _event):
        if not self._drag:
            self.unsetCursor()
