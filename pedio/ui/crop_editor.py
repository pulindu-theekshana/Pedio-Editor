"""Crop editor: one picture of the clip with a draggable crop box. Pure Qt (no mpv), shown in place of the preview.

CropView   = the picture + the box (drag the box to move it, drag the handles to resize; optional aspect ratio lock).
CropEditor = CropView + the buttons (aspect ratio list, Reset, Cancel, Done) + the background job that gets the picture.
The box is stored as fractions of the picture (x, y, w, h), the same numbers as `Transform.crop`.
"""
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from pedio.core.transform import ASPECT_PRESETS, MIN_CROP, clean_crop, video_filters
from pedio.core.workers import FrameWorker

MARGIN = 14       # px around the picture
GRAB = 11         # px: how close to a handle the mouse must be
YELLOW = QColor("#f5c518")

_CURSORS = {
    "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
    "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
    "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
    "move": Qt.CursorShape.SizeAllCursor,
}


class CropView(QWidget):
    changed = Signal()     # the box moved or was resized

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 180)
        self.setMouseTracking(True)
        self._image = None
        self._box = [0.0, 0.0, 1.0, 1.0]   # x0, y0, x1, y1 (fractions of the picture)
        self._ratio = None                 # (w, h) in pixels to keep while resizing, or None = free
        self._full_size = None             # real size of the picture in pixels (for the size label)
        self._drag = None                  # (handle, mouse point as fractions, box when the drag began)
        self._message = ""

    # ---- data
    def set_image(self, image):
        self._image = image
        self._message = ""
        self.update()

    def clear(self, message="Loading the picture ..."):
        self._image = None
        self._message = message
        self._drag = None
        self.set_ratio(None)
        self.set_rect(None)

    def set_full_size(self, size):
        self._full_size = tuple(size) if size and size[0] > 0 and size[1] > 0 else None

    def image_ratio(self):
        """(w, h) of the picture, for the "Original" ratio."""
        if self._image is not None:
            return (self._image.width(), self._image.height())
        return self._full_size

    def set_rect(self, rect):
        """rect = (x, y, w, h) fractions, or None for the whole picture."""
        if rect is None:
            self._box = [0.0, 0.0, 1.0, 1.0]
        else:
            x, y, w, h = rect
            self._box = [x, y, x + w, y + h]
        self.update()

    def crop_rect(self):      # not "rect": that name belongs to QWidget (the widget's own rectangle)
        """The box as (x, y, w, h) fractions, or None if it covers the whole picture."""
        x0, y0, x1, y1 = self._box
        return clean_crop((x0, y0, x1 - x0, y1 - y0))

    def set_ratio(self, ratio):
        """Lock the box to this (w, h) ratio (None = free). The box becomes the biggest one with that ratio,
        around the middle of the old box."""
        self._ratio = tuple(ratio) if ratio else None
        if self._ratio is None:
            return
        k = self._k()
        if k is None:
            self._ratio = None
            return
        x0, y0, x1, y1 = self._box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        w, h = (1.0, 1.0 / k) if k >= 1.0 else (k, 1.0)
        x0 = min(max(cx - w / 2, 0.0), 1.0 - w)
        y0 = min(max(cy - h / 2, 0.0), 1.0 - h)
        self._box = [x0, y0, x0 + w, y0 + h]
        self.update()
        self.changed.emit()

    def _k(self):
        """Width / height of the box in fractions when the ratio is locked."""
        size = self.image_ratio()
        if self._ratio is None or not size:
            return None
        return (self._ratio[0] / self._ratio[1]) / (size[0] / size[1])

    # ---- geometry
    def _picture_rect(self):
        if self._image is None:
            return QRectF()
        area = QRectF(self.rect()).adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)
        scale = min(area.width() / self._image.width(), area.height() / self._image.height())
        w, h = self._image.width() * scale, self._image.height() * scale
        return QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)

    def _box_rect(self):
        pr = self._picture_rect()
        x0, y0, x1, y1 = self._box
        return QRectF(pr.x() + x0 * pr.width(), pr.y() + y0 * pr.height(), (x1 - x0) * pr.width(), (y1 - y0) * pr.height())

    def _norm(self, point):
        pr = self._picture_rect()
        return ((point.x() - pr.x()) / pr.width(), (point.y() - pr.y()) / pr.height())

    def _handles(self):
        """{name: point on the widget}. Edge handles only when the ratio is free."""
        b = self._box_rect()
        pts = {"tl": b.topLeft(), "tr": b.topRight(), "bl": b.bottomLeft(), "br": b.bottomRight()}
        if self._ratio is None:
            pts.update({"t": QPointF(b.center().x(), b.top()), "b": QPointF(b.center().x(), b.bottom()),
                        "l": QPointF(b.left(), b.center().y()), "r": QPointF(b.right(), b.center().y())})
        return pts

    def _hit(self, point):
        if self._image is None:
            return None
        for name, pt in self._handles().items():
            if abs(point.x() - pt.x()) <= GRAB and abs(point.y() - pt.y()) <= GRAB:
                return name
        return "move" if self._box_rect().contains(point) else None

    # ---- mouse
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        handle = self._hit(event.position())
        if handle:
            self._drag = (handle, self._norm(event.position()), tuple(self._box))

    def mouseMoveEvent(self, event):
        if self._drag is None:
            handle = self._hit(event.position())
            self.setCursor(_CURSORS[handle] if handle else Qt.CursorShape.ArrowCursor)
            return
        handle, start, box0 = self._drag
        nx, ny = self._norm(event.position())
        x0, y0, x1, y1 = box0
        if handle == "move":
            w, h = x1 - x0, y1 - y0
            x0 = min(max(x0 + nx - start[0], 0.0), 1.0 - w)
            y0 = min(max(y0 + ny - start[1], 0.0), 1.0 - h)
            self._box = [x0, y0, x0 + w, y0 + h]
        else:
            nx, ny = min(max(nx, 0.0), 1.0), min(max(ny, 0.0), 1.0)
            k = self._k()
            if k is None:
                if "l" in handle:
                    x0 = min(nx, x1 - MIN_CROP)
                if "r" in handle:
                    x1 = max(nx, x0 + MIN_CROP)
                if "t" in handle:
                    y0 = min(ny, y1 - MIN_CROP)
                if "b" in handle:
                    y1 = max(ny, y0 + MIN_CROP)
                self._box = [x0, y0, x1, y1]
            else:
                self._box = self._resize_locked(handle, box0, nx, ny, k)
        self.update()
        self.changed.emit()

    @staticmethod
    def _resize_locked(handle, box0, nx, ny, k):
        """Corner drag with a locked ratio: the opposite corner stays, the box follows the mouse."""
        x0, y0, x1, y1 = box0
        ax = x1 if "l" in handle else x0
        ay = y1 if "t" in handle else y0
        left, up = "l" in handle, "t" in handle
        w = max(abs(nx - ax), abs(ny - ay) * k)
        w = min(w, ax if left else 1.0 - ax, (ay if up else 1.0 - ay) * k)
        w = max(w, MIN_CROP, MIN_CROP * k)
        h = w / k
        return [ax - w if left else ax, ay - h if up else ay, ax if left else ax + w, ay if up else ay + h]

    def mouseReleaseEvent(self, event):
        self._drag = None

    # ---- painting
    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000"))
        if self._image is None:
            p.setPen(QColor("#aaa"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            return
        pr = self._picture_rect()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(pr, self._image)
        box = self._box_rect()
        shade = QColor(0, 0, 0, 150)
        p.fillRect(QRectF(pr.left(), pr.top(), pr.width(), box.top() - pr.top()), shade)
        p.fillRect(QRectF(pr.left(), box.bottom(), pr.width(), pr.bottom() - box.bottom()), shade)
        p.fillRect(QRectF(pr.left(), box.top(), box.left() - pr.left(), box.height()), shade)
        p.fillRect(QRectF(box.right(), box.top(), pr.right() - box.right(), box.height()), shade)

        p.setPen(QPen(QColor(255, 255, 255, 110), 1))          # thirds
        for i in (1, 2):
            p.drawLine(QPointF(box.left() + box.width() * i / 3, box.top()), QPointF(box.left() + box.width() * i / 3, box.bottom()))
            p.drawLine(QPointF(box.left(), box.top() + box.height() * i / 3), QPointF(box.right(), box.top() + box.height() * i / 3))
        p.setPen(QPen(YELLOW, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(box)
        p.setBrush(YELLOW)
        p.setPen(Qt.PenStyle.NoPen)
        for pt in self._handles().values():
            p.drawRect(QRectF(pt.x() - 5, pt.y() - 5, 10, 10))

        w, h = self._box_pixels()
        label = f"{w} x {h}"
        metrics = p.fontMetrics()
        tag = QRectF(box.left() + 6, box.top() + 6, metrics.horizontalAdvance(label) + 12, metrics.height() + 6)
        if tag.right() < box.right() and tag.bottom() < box.bottom():
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(tag, 4, 4)
            p.setPen(QColor("#fff"))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, label)

    def _box_pixels(self):
        """Size of the box in real pixels (same rounding as the crop filter: even numbers)."""
        size = self._full_size or (self._image.width(), self._image.height())
        x0, y0, x1, y1 = self._box
        return math.floor(size[0] * (x1 - x0) / 2) * 2, math.floor(size[1] * (y1 - y0) / 2) * 2


class CropEditor(QWidget):
    applied = Signal(object)    # the box (x, y, w, h) or None = no crop
    cancelled = Signal()

    RATIOS = [("Free", None), ("Original", "original")] + list(ASPECT_PRESETS.items())

    def __init__(self, parent=None):
        super().__init__(parent)
        self.clip_id = 0
        self._worker = None

        self.view = CropView()
        self.ratio_box = QComboBox()
        for name, _ in self.RATIOS:
            self.ratio_box.addItem(name)
        self.reset_btn = QPushButton("Reset")
        self.cancel_btn = QPushButton("Cancel")
        self.done_btn = QPushButton("Done")
        self.done_btn.setDefault(True)
        hint = QLabel("Drag the box to move it, drag the yellow handles to resize.")
        hint.setStyleSheet("color:#888;")

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        bar.addWidget(QLabel("Aspect ratio:"))
        bar.addWidget(self.ratio_box)
        bar.addWidget(self.reset_btn)
        bar.addSpacing(12)
        bar.addWidget(hint, 1)
        bar.addWidget(self.cancel_btn)
        bar.addWidget(self.done_btn)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.view, 1)
        root.addLayout(bar)

        self.ratio_box.activated.connect(self._on_ratio)
        self.reset_btn.clicked.connect(self._reset)
        self.cancel_btn.clicked.connect(self.cancelled)
        self.done_btn.clicked.connect(self._done)

    def start(self, clip, t):
        """Open the editor for this clip; `t` = seconds into the source file for the picture."""
        self.clip_id = clip.id
        self.view.clear()
        self.view.set_full_size(clip.picture_size)
        self.ratio_box.setCurrentIndex(0)
        self.done_btn.setEnabled(False)
        self.reset_btn.setEnabled(False)
        self.ratio_box.setEnabled(False)
        self._start_crop = clip.transform.crop
        worker = FrameWorker(clip.path, t, video_filters(clip.transform, with_crop=False), self)
        worker.frame_ready.connect(self._on_frame)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def shutdown(self):
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.wait(5000)

    def _on_frame(self, image):
        if self.sender() is not self._worker:      # an old job (the editor was closed and opened again)
            return
        self.view.set_image(image)
        self.view.set_rect(self._start_crop)
        for w in (self.done_btn, self.reset_btn, self.ratio_box):
            w.setEnabled(True)

    def _on_failed(self, message):
        if self.sender() is not self._worker:
            return
        self.view.clear(message)

    def _on_ratio(self, index):
        value = self.RATIOS[index][1]
        self.view.set_ratio(self.view.image_ratio() if value == "original" else value)

    def _reset(self):
        self.ratio_box.setCurrentIndex(0)
        self.view.set_ratio(None)
        self.view.set_rect(None)

    def _done(self):
        self.applied.emit(self.view.crop_rect())
