"""Transparent layer over the video: draws the image layers at the playhead and drags the selected one.

The export overlays the same pictures in ffmpeg (scale = frame width * layer.scale, top-left =
(W - w) * x, (H - h) * y), so what this widget shows is what the file gets.
"""
import os

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from pedio.core.clips import Project
from pedio.core.layers import layer_value


class ImageOverlay(QWidget):
    """Draws every active ImageLayer; press + drag the selected one to move it."""

    committed = Signal(float, float)   # new x, y (fractions of the free space)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = None
        self.position = 0.0
        self._drag = False
        self._ghost = None             # top-left in widget pixels while dragging
        self._grab = QPointF()
        self._pixmaps = {}
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
        if self.project is None or not self.project.image_layers or self.parentWidget() is None:
            self._drag = False
            self._ghost = None
            self.hide()
            return
        self.show()
        self.raise_()
        self.update()

    def _layer(self):
        if self.project is None:
            return None
        return self.project.image_by_id(self.project.selected_image_id)

    def _pixmap(self, path):
        pixmap = self._pixmaps.get(path)
        if pixmap is None:
            pixmap = QPixmap(path)
            self._pixmaps[path] = pixmap
        return pixmap if not pixmap.isNull() else None

    def _rect(self, layer):
        pixmap = self._pixmap(layer.path)
        if pixmap is None or self.width() <= 0 or self.height() <= 0:
            return None
        scale = layer_value(layer, "scale", self.position, layer.scale)
        width = max(2.0, self.width() * scale)
        height = width * pixmap.height() / max(pixmap.width(), 1)
        if self._ghost is not None and layer.id == self.project.selected_image_id:
            left, top = self._ghost.x(), self._ghost.y()
        else:
            x = layer_value(layer, "x", self.position, layer.x)
            y = layer_value(layer, "y", self.position, layer.y)
            left = (self.width() - width) * x
            top = (self.height() - height) * y
        return QRectF(left, top, width, height)

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _event):
        if self.project is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for layer in self.project.image_layers:
            if not (layer.start <= self.position <= layer.end):
                continue
            pixmap = self._pixmap(layer.path)
            rect = self._rect(layer)
            if pixmap is None or rect is None:
                continue
            painter.setOpacity(min(max(layer_value(layer, "opacity", self.position, layer.opacity), 0.0), 1.0))
            painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
            painter.setOpacity(1.0)
            if layer.id == self.project.selected_image_id:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(80, 160, 255, 220), 1, Qt.PenStyle.DashLine))
                painter.drawRect(rect)
        painter.end()

    # ------------------------------------------------------------------ mouse
    def _image_at(self, point):
        for layer in reversed(self.project.image_layers):
            rect = self._rect(layer)
            if rect is not None and layer.start <= self.position <= layer.end and rect.contains(point):
                return layer
        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.project is None:
            return
        layer = self._image_at(event.position())
        if layer is None:
            return
        self.project.select_image(layer.id)
        rect = self._rect(layer)
        self._drag = True
        self._grab = event.position() - rect.topLeft()
        self._ghost = QPointF(rect.topLeft())
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.update()

    def mouseMoveEvent(self, event):
        if self._drag:
            self._ghost = QPointF(event.position() - self._grab)
            self.update()
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._image_at(event.position()) is not None
                           else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._drag:
            return
        self._drag = False
        self.unsetCursor()
        layer = self._layer()
        rect = self._rect(layer) if layer is not None else None
        point = self._ghost
        self._ghost = None
        if rect is not None and point is not None:
            free_x = max(self.width() - rect.width(), 1e-6)
            free_y = max(self.height() - rect.height(), 1e-6)
            x = min(max(point.x() / free_x, 0.0), 1.0)
            y = min(max(point.y() / free_y, 0.0), 1.0)
            self.committed.emit(x, y)
        self.update()

    def leaveEvent(self, _event):
        if not self._drag:
            self.unsetCursor()
