"""Timeline: time ruler, video thumbnails, audio waveform, draggable playhead, zoom and scroll."""
import math
import time
from array import array

from PySide6.QtCore import QLine, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygon
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollBar,
                               QSizePolicy, QSlider, QStyle, QVBoxLayout, QWidget)

from pedio.core.workers import PEAKS_PER_SECOND, ThumbnailWorker, WaveformWorker, thumb_step

RULER_H = 28
TRACK_H = 72
GAP = 4
VIEW_H = RULER_H + GAP + TRACK_H + GAP + TRACK_H
HEADER_W = 104
MAX_PPS = 300.0   # most zoomed-in: 300 pixels per second

BG = QColor("#1b1b1b")
RULER_BG = QColor("#2b2b2b")
TICK = QColor("#8a8a8a")
LABEL = QColor("#c8c8c8")
TRACK_BG = QColor("#262626")
VIDEO_BG = QColor("#33404d")
AUDIO_BG = QColor("#26332a")
WAVE = QColor("#58c26b")
WAVE_MUTED = QColor("#6c6c6c")
PLAYHEAD = QColor("#ff4d4d")

# (major tick every N seconds, number of minor divisions)
TICKS = [(0.1, 5), (0.2, 4), (0.5, 5), (1, 5), (2, 4), (5, 5), (10, 5), (15, 3),
         (30, 6), (60, 6), (120, 4), (300, 5), (600, 5), (900, 3), (1800, 6), (3600, 6)]


def ruler_label(t, with_tenths):
    total, frac = divmod(int(round(t * 10)), 10)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    text = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    return f"{text}.{frac}" if with_tenths else text


class TimelineView(QWidget):
    """Draws the ruler and both tracks, and the playhead on top."""

    seek_requested = Signal(float, bool)   # seconds, exact
    view_changed = Signal()                # zoom or scroll changed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, VIEW_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(VIEW_H)

        self.duration = 0.0
        self.pps = 1.0            # pixels per second (zoom)
        self.scroll_px = 0.0      # how far the timeline is scrolled, in pixels
        self.position = 0.0       # playhead, seconds
        self.fit_mode = True      # zoom follows the window width until the user zooms in
        self.muted = False
        self._dragging = False
        self._ignore_until = 0.0
        self._cache = None
        self._cache_dirty = True
        self._font = QFont()
        self._font.setPointSize(8)

        self._soon = QTimer(self)             # batches repaints while data is streaming in
        self._soon.setSingleShot(True)
        self._soon.setInterval(100)
        self._soon.timeout.connect(self._invalidate)

        self.clear_media()

    # ------------------------------------------------------------------ data
    def clear_media(self):
        self.duration = 0.0
        self.position = 0.0
        self.scroll_px = 0.0
        self.pps = 1.0
        self.fit_mode = True
        self.thumbs = []
        self.thumbs_done = False
        self.thumb_step = 1.0
        self.thumb_w = 0
        self.mins = array("h")
        self.maxs = array("h")
        self.audio_state = None   # None = still reading, True / False when finished
        self._invalidate()
        self.view_changed.emit()

    def set_duration(self, duration):
        self.duration = float(duration)
        self.position = 0.0
        self.scroll_px = 0.0
        self.fit_mode = True
        self.pps = self.fit_pps()
        self._invalidate()
        self.view_changed.emit()

    def update_duration(self, duration):
        """The duration was refined after loading: keep the zoom, fix the limits."""
        self.duration = float(duration)
        if self.fit_mode:
            self.pps = self.fit_pps()
        self._clamp_scroll()
        self._invalidate()
        self.view_changed.emit()

    def set_thumb_step(self, step):
        self.thumb_step = step
        self.thumbs = []
        self.thumbs_done = False
        self.thumb_w = 0

    def set_thumbs_done(self):
        """No more thumbnails are coming: the end of the video reuses the last one (after the last keyframe)."""
        self.thumbs_done = True
        self._invalidate()

    def add_thumbnail(self, index, image):
        pixmap = QPixmap.fromImage(image)
        while len(self.thumbs) <= index:
            self.thumbs.append(None)
        self.thumbs[index] = pixmap
        if not self.thumb_w and pixmap.height():
            self.thumb_w = max(24, round(pixmap.width() * TRACK_H / pixmap.height()))
        self._invalidate_soon()

    def add_peaks(self, mins, maxs):
        part = array("h")
        part.frombytes(mins)
        self.mins.extend(part)
        part = array("h")
        part.frombytes(maxs)
        self.maxs.extend(part)
        self._invalidate_soon()

    def set_audio_done(self, has_audio):
        self.audio_state = bool(has_audio)
        self._invalidate()

    def set_muted(self, muted):
        self.muted = muted
        self._invalidate()

    # ------------------------------------------------------------------ zoom / scroll
    def fit_pps(self):
        return max(self.width(), 1) / self.duration if self.duration > 0 else 1.0

    def max_pps(self):
        return max(MAX_PPS, self.fit_pps())

    def time_to_x(self, t):
        return t * self.pps - self.scroll_px

    def x_to_time(self, x):
        if self.duration <= 0:
            return 0.0
        return min(max((x + self.scroll_px) / self.pps, 0.0), self.duration)

    def _clamp_scroll(self):
        limit = max(0.0, self.duration * self.pps - self.width())
        self.scroll_px = min(max(self.scroll_px, 0.0), limit)

    def set_zoom(self, pps, anchor_x=None):
        if self.duration <= 0:
            return
        lo = self.fit_pps()
        pps = min(max(pps, lo), self.max_pps())
        if anchor_x is None:  # keep the playhead where it is on screen, or else the middle of the view
            px = self.time_to_x(self.position)
            anchor_x = px if 0 <= px <= self.width() else self.width() / 2
        anchor_time = (self.scroll_px + anchor_x) / self.pps
        self.pps = pps
        self.fit_mode = pps <= lo * 1.0001
        self.scroll_px = anchor_time * pps - anchor_x
        self._clamp_scroll()
        self._invalidate()
        self.view_changed.emit()

    def zoom_fit(self):
        self.set_zoom(self.fit_pps())
        self.set_scroll(0)

    def set_scroll(self, px):
        old = self.scroll_px
        self.scroll_px = px
        self._clamp_scroll()
        if self.scroll_px != old:
            self._invalidate()
            self.view_changed.emit()

    def resizeEvent(self, event):
        if self.duration > 0:
            if self.fit_mode:
                self.pps = self.fit_pps()
            else:
                self.pps = max(self.pps, self.fit_pps())
            self._clamp_scroll()
            self.view_changed.emit()
        self._invalidate()
        super().resizeEvent(event)

    # ------------------------------------------------------------------ playhead
    def set_position(self, t):
        """Called with the player's position while playing/seeking."""
        if self._dragging or time.monotonic() < self._ignore_until:
            return
        old = self.position
        t = min(max(t, 0.0), self.duration) if self.duration > 0 else 0.0
        if t == old:
            return
        self.position = t
        if self.duration > 0:  # keep the playhead on screen while playing
            x_old, x_new = self.time_to_x(old), self.time_to_x(t)
            if abs(t - old) > 1.0 and not 0 <= x_new <= self.width():
                self.set_scroll(t * self.pps - self.width() * 0.3)       # jumped somewhere off screen
            elif 0 <= x_old <= self.width() and x_new > self.width() - 20 and t > old:
                self.set_scroll(t * self.pps - self.width() * 0.1)       # reached the right edge
        self._update_playhead(old, t)

    def _update_playhead(self, old, new):
        xs = sorted((int(self.time_to_x(old)), int(self.time_to_x(new))))
        self.update(QRect(xs[0] - 8, 0, xs[1] - xs[0] + 17, self.height()))

    def _seek_to_x(self, x, exact):
        t = self.x_to_time(x)
        old, self.position = self.position, t
        self._update_playhead(old, t)
        self.seek_requested.emit(t, exact)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.duration > 0:
            self._dragging = True
            self._seek_to_x(event.position().x(), exact=False)

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._seek_to_x(event.position().x(), exact=False)

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._seek_to_x(event.position().x(), exact=True)
            self._dragging = False
            self._ignore_until = time.monotonic() + 0.15  # let the player catch up before following it again

    def wheelEvent(self, event):
        if self.duration <= 0:
            return
        steps = (event.angleDelta().y() or event.angleDelta().x()) / 120
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.pps * (1.25 ** steps), event.position().x())
        else:
            self.set_scroll(self.scroll_px - steps * 80)
        event.accept()

    # ------------------------------------------------------------------ painting
    def _invalidate(self):
        self._cache_dirty = True
        self.update()

    def _invalidate_soon(self):
        if not self._soon.isActive():
            self._soon.start()

    def _ensure_cache(self):
        w, h = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        size = QSize(int(w * dpr), int(h * dpr))
        if self._cache is None or self._cache_dirty or self._cache.size() != size:
            pixmap = QPixmap(size)
            pixmap.setDevicePixelRatio(dpr)
            pixmap.fill(BG)
            painter = QPainter(pixmap)
            self._paint_content(painter, w)
            painter.end()
            self._cache = pixmap
            self._cache_dirty = False

    def paintEvent(self, event):
        self._ensure_cache()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        if self.duration > 0:
            x = int(round(self.time_to_x(self.position)))
            if -10 <= x <= self.width() + 10:
                p.setPen(QPen(PLAYHEAD, 2))
                p.drawLine(x, 0, x, self.height())
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(PLAYHEAD)
                p.drawPolygon(QPolygon([QPoint(x - 7, 0), QPoint(x + 7, 0), QPoint(x, 12)]))

    def _paint_content(self, p, w):
        video_y = RULER_H + GAP
        audio_y = video_y + TRACK_H + GAP
        p.setFont(self._font)
        p.fillRect(0, 0, w, RULER_H, RULER_BG)
        p.fillRect(0, video_y, w, TRACK_H, TRACK_BG)
        p.fillRect(0, audio_y, w, TRACK_H, TRACK_BG)
        if self.duration <= 0:
            p.setPen(LABEL)
            p.drawText(QRect(0, video_y, w, TRACK_H * 2 + GAP), Qt.AlignmentFlag.AlignCenter,
                       "Import a video to see the timeline")
            return
        x_end = int(min(w, self.duration * self.pps - self.scroll_px))
        self._paint_ruler(p, w)
        if x_end > 0:
            self._paint_video(p, video_y, x_end)
            self._paint_audio(p, audio_y, x_end)

    def _paint_ruler(self, p, w):
        major, divisions = next(((m, d) for m, d in TICKS if m * self.pps >= 90), TICKS[-1])
        minor = major / divisions
        k = max(0, int(math.floor(self.scroll_px / self.pps / minor)))
        p.setPen(TICK)
        while True:
            t = k * minor
            x = t * self.pps - self.scroll_px
            if x > w or t > self.duration + 1e-6:
                break
            is_major = k % divisions == 0
            xi = int(x)
            p.setPen(TICK)
            p.drawLine(xi, RULER_H - (12 if is_major else 6), xi, RULER_H)
            if is_major:
                p.setPen(LABEL)
                p.drawText(xi + 3, 12, ruler_label(t, major < 1))
            k += 1

    def _paint_video(self, p, y, x_end):
        p.fillRect(0, y, x_end, TRACK_H, VIDEO_BG)
        tile = self.thumb_w or 128
        i = int(self.scroll_px // tile)
        while True:
            x = i * tile - self.scroll_px
            if x >= x_end:
                break
            if self.thumbs:
                index = int(round(i * tile / self.pps / self.thumb_step))
                if index >= len(self.thumbs):   # not made (yet): reuse the last one only when finished
                    index = len(self.thumbs) - 1 if self.thumbs_done else -1
                pixmap = self.thumbs[index] if index >= 0 else None
                if pixmap is not None:
                    width = int(min(tile, x_end - x, pixmap.width()))
                    p.drawPixmap(int(x), y, pixmap, 0, 0, width, TRACK_H)
            if x > 0:
                p.setPen(QColor(0, 0, 0, 140))
                p.drawLine(int(x), y, int(x), y + TRACK_H)
            i += 1

    def _paint_audio(self, p, y, x_end):
        p.fillRect(0, y, x_end, TRACK_H, AUDIO_BG)
        area = QRect(0, y, x_end, TRACK_H)
        if self.audio_state is False:
            p.setPen(LABEL)
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "This video has no audio")
            return
        count = len(self.mins)
        if count == 0:
            p.setPen(LABEL)
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "Reading audio ...")
            return
        mid = y + TRACK_H / 2
        scale = (TRACK_H / 2 - 4) / 32768
        per_px = PEAKS_PER_SECOND / self.pps   # how many peaks one pixel column covers
        lines = []
        for x in range(x_end):
            first = int((self.scroll_px + x) * per_px)
            if first >= count:
                break
            last = max(first + 1, int((self.scroll_px + x + 1) * per_px))
            top = int(mid - max(self.maxs[first:last]) * scale)
            bottom = int(mid - min(self.mins[first:last]) * scale)
            lines.append(QLine(x, top, x, max(bottom, top + 1)))
        p.setPen(QPen(WAVE_MUTED if self.muted else WAVE, 1))
        p.drawLines(lines)


class TimelinePanel(QWidget):
    """Zoom controls + track headers (with the audio mute button) + the timeline + a scroll bar."""

    def __init__(self, player, parent=None):
        super().__init__(parent)
        self.player = player
        self._path = ""
        self._started = False
        self._thumb_worker = None
        self._wave_worker = None

        style = self.style()
        self._icon_volume = style.standardIcon(QStyle.StandardPixmap.SP_MediaVolume)
        self._icon_muted = style.standardIcon(QStyle.StandardPixmap.SP_MediaVolumeMuted)

        self.view = TimelineView()

        # --- zoom row
        self.zoom_out_btn = QPushButton("-")
        self.zoom_in_btn = QPushButton("+")
        self.fit_btn = QPushButton("Fit")
        for b in (self.zoom_out_btn, self.zoom_in_btn):
            b.setFixedWidth(30)
        self.fit_btn.setFixedWidth(46)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(0, 1000)
        self.zoom_slider.setFixedWidth(180)
        zoom_row = QHBoxLayout()
        zoom_row.addStretch(1)
        zoom_row.addWidget(QLabel("Zoom"))
        zoom_row.addWidget(self.zoom_out_btn)
        zoom_row.addWidget(self.zoom_slider)
        zoom_row.addWidget(self.zoom_in_btn)
        zoom_row.addWidget(self.fit_btn)

        # --- track headers, lined up with the tracks in the view
        header = QWidget()
        header.setFixedSize(HEADER_W, VIEW_H)
        hl = QVBoxLayout(header)
        hl.setContentsMargins(0, 0, 6, 0)
        hl.setSpacing(0)
        hl.addSpacing(RULER_H + GAP)
        video_head = self._track_header("Video")
        hl.addWidget(video_head)
        hl.addSpacing(GAP)
        audio_head = self._track_header("Audio")
        self.mute_btn = QPushButton(" Mute")
        self.mute_btn.setCheckable(True)
        self.mute_btn.setIcon(self._icon_volume)
        self.mute_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        audio_head.layout().addWidget(self.mute_btn)
        hl.addWidget(audio_head)

        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(header)
        body.addWidget(self.view, 1)

        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        scroll_row = QHBoxLayout()
        scroll_row.setSpacing(0)
        scroll_row.addSpacing(HEADER_W)
        scroll_row.addWidget(self.scrollbar, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 4)
        root.setSpacing(2)
        root.addLayout(zoom_row)
        root.addLayout(body)
        root.addLayout(scroll_row)
        root.addStretch(1)

        for w in (self.zoom_out_btn, self.zoom_in_btn, self.fit_btn, self.zoom_slider, self.scrollbar):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # --- wiring
        self.zoom_out_btn.clicked.connect(lambda: self.view.set_zoom(self.view.pps / 1.5))
        self.zoom_in_btn.clicked.connect(lambda: self.view.set_zoom(self.view.pps * 1.5))
        self.fit_btn.clicked.connect(self.view.zoom_fit)
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider)
        self.scrollbar.valueChanged.connect(self.view.set_scroll)
        self.view.view_changed.connect(self._sync_controls)
        self.view.seek_requested.connect(player.seek_to)
        self.mute_btn.clicked.connect(lambda checked: player.set_muted(checked))

        player.file_loaded.connect(self._on_file_loaded)
        player.duration_changed.connect(self._on_duration)
        player.position_changed.connect(self.view.set_position)
        player.load_failed.connect(self.clear)
        player.muted_changed.connect(self._sync_mute)

        self._sync_mute(player.is_muted())
        self._sync_controls()

    @staticmethod
    def _track_header(text):
        frame = QFrame()
        frame.setFixedHeight(TRACK_H)
        frame.setStyleSheet("QFrame { background:#2f2f2f; border-radius:4px; } QLabel { color:#ddd; }")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(8, 6, 8, 6)
        label = QLabel(text)
        label.setStyleSheet("font-weight:bold;")
        lay.addWidget(label)
        lay.addStretch(1)
        return frame

    # ------------------------------------------------------------------ loading a video
    def _on_file_loaded(self, path):
        self._stop_workers()
        self.view.clear_media()
        self._path = path
        self._started = False
        self._maybe_start(self.player.duration())

    def _on_duration(self, duration):
        if duration <= 0 or not self._path:
            return
        if not self._started:
            self._maybe_start(duration)
        elif abs(duration - self.view.duration) > 0.05:
            self.view.update_duration(duration)

    def _maybe_start(self, duration):
        if duration <= 0 or not self._path or self._started:
            return
        self._started = True
        self.view.set_duration(duration)
        step = thumb_step(duration)
        self.view.set_thumb_step(step)
        self._thumb_worker = ThumbnailWorker(self._path, duration, step, TRACK_H, self)
        self._thumb_worker.thumb_ready.connect(self._on_thumb)
        self._thumb_worker.finished.connect(self._on_thumbs_finished)
        self._wave_worker = WaveformWorker(self._path, self)
        self._wave_worker.chunk_ready.connect(self._on_peaks)
        self._wave_worker.audio_done.connect(self._on_audio_done)
        self._thumb_worker.start()
        self._wave_worker.start()

    # signals from a worker that was already replaced are ignored (self.sender() is the worker)
    def _on_thumb(self, index, image):
        if self.sender() is self._thumb_worker:
            self.view.add_thumbnail(index, image)

    def _on_thumbs_finished(self):
        if self.sender() is self._thumb_worker:
            self.view.set_thumbs_done()

    def _on_peaks(self, mins, maxs):
        if self.sender() is self._wave_worker:
            self.view.add_peaks(mins, maxs)

    def _on_audio_done(self, has_audio):
        if self.sender() is self._wave_worker:
            self.view.set_audio_done(has_audio)

    def clear(self, *_):
        self._stop_workers()
        self._path = ""
        self._started = False
        self.view.clear_media()

    def _stop_workers(self):
        for worker in (self._thumb_worker, self._wave_worker):
            if worker is not None:
                worker.stop()
                if worker.wait(3000):
                    worker.deleteLater()
        self._thumb_worker = self._wave_worker = None

    def shutdown(self):
        """Call before the window closes."""
        self._stop_workers()

    # ------------------------------------------------------------------ controls
    def _sync_mute(self, muted):
        self.mute_btn.setChecked(muted)
        self.mute_btn.setIcon(self._icon_muted if muted else self._icon_volume)
        self.view.set_muted(muted)

    def _zoom_range(self):
        return self.view.fit_pps(), self.view.max_pps()

    def _on_zoom_slider(self, value):
        lo, hi = self._zoom_range()
        if self.view.duration > 0 and hi > lo:
            self.view.set_zoom(lo * (hi / lo) ** (value / 1000))

    def _sync_controls(self):
        v = self.view
        has_media = v.duration > 0
        lo, hi = self._zoom_range()

        self.scrollbar.blockSignals(True)
        self.scrollbar.setRange(0, max(0, int(v.duration * v.pps - v.width())))
        self.scrollbar.setPageStep(max(v.width(), 1))
        self.scrollbar.setSingleStep(40)
        self.scrollbar.setValue(int(v.scroll_px))
        self.scrollbar.blockSignals(False)
        self.scrollbar.setEnabled(has_media and self.scrollbar.maximum() > 0)

        self.zoom_slider.blockSignals(True)
        if has_media and hi > lo:
            self.zoom_slider.setValue(int(1000 * math.log(max(v.pps, lo) / lo) / math.log(hi / lo)))
        else:
            self.zoom_slider.setValue(0)
        self.zoom_slider.blockSignals(False)

        can_zoom = has_media and hi > lo
        for w in (self.zoom_slider, self.zoom_in_btn, self.zoom_out_btn, self.fit_btn):
            w.setEnabled(can_zoom)
