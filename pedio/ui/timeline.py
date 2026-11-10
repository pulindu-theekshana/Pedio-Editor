"""Timeline: ruler, clips (video thumbnails + audio waveform), playhead, zoom and scroll,
clip selection, trim handles, drag to reorder, snapping.

The clips come from `pedio.core.clips.Project`. This file only draws them and turns mouse input into edits.
"""
import math
import os
import time
from array import array
from collections import namedtuple

from PySide6.QtCore import QLine, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap, QPolygon, QTransform
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollBar,
                               QSizePolicy, QSlider, QStyle, QVBoxLayout, QWidget)

from pedio.core.clips import MIN_CLIP
from pedio.core.workers import PEAKS_PER_SECOND, ThumbnailWorker, WaveformWorker, thumb_step

RULER_H = 28
TRACK_H = 72
GAP = 4
VIEW_H = RULER_H + GAP + TRACK_H + GAP + TRACK_H
HEADER_W = 104
MAX_PPS = 300.0   # most zoomed-in: 300 pixels per second
HANDLE_W = 8      # trim handle width in pixels (inside the clip edge)
SNAP_PX = 8       # snap when closer than this many pixels
DRAG_PX = 5       # the mouse must move this far before a click turns into a drag

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
SELECT = QColor("#ffcc33")   # selected clip outline + trim handles + snap line

# (major tick every N seconds, number of minor divisions)
TICKS = [(0.1, 5), (0.2, 4), (0.5, 5), (1, 5), (2, 4), (5, 5), (10, 5), (15, 3),
         (30, 6), (60, 6), (120, 4), (300, 5), (600, 5), (900, 3), (1800, 6), (3600, 6)]

# where a clip is drawn: timeline start/end (t0, t1) and which part of the source it shows
Slot = namedtuple("Slot", "clip t0 t1 src_in src_out")


def ruler_label(t, with_tenths):
    total, frac = divmod(int(round(t * 10)), 10)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    text = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    return f"{text}.{frac}" if with_tenths else text


def look_pixmap(pixmap, transform):
    """A thumbnail as the preview shows the clip: mirrored, turned, then cropped (same order as core/transform.py).
    The result is TRACK_H pixels tall."""
    image = pixmap.toImage()
    if transform.flip:
        image = image.mirrored(True, False)
    if transform.rotate:
        image = image.transformed(QTransform().rotate(transform.rotate), Qt.TransformationMode.SmoothTransformation)
    if transform.crop:
        x, y, w, h = transform.crop
        iw, ih = image.width(), image.height()
        image = image.copy(round(iw * x), round(ih * y), max(1, round(iw * w)), max(1, round(ih * h)))
    return QPixmap.fromImage(image.scaledToHeight(TRACK_H, Qt.TransformationMode.SmoothTransformation))


class Media:
    """What the timeline knows about one source file: thumbnails and audio peaks (shared by all its clips)."""

    def __init__(self, path, duration):
        self.path = path
        self.duration = duration
        self.looks = {}           # (thumbnail index, Transform) -> thumbnail after flip / rotate / crop
        self.thumbs = []
        self.thumbs_done = False
        self.thumb_step = thumb_step(duration)
        self.thumb_w = 0
        self.mins = array("h")
        self.maxs = array("h")
        self.audio_state = None   # None = still reading, True / False when finished


class TimelineView(QWidget):
    """Draws the ruler, the clips on both tracks and the playhead; handles the mouse."""

    seek_requested = Signal(float, bool)   # seconds, exact
    view_changed = Signal()                # zoom or scroll changed

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setMinimumSize(200, VIEW_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(VIEW_H)
        self.setMouseTracking(True)

        self.media = {}           # source path -> Media
        self.pps = 1.0            # pixels per second (zoom)
        self.scroll_px = 0.0      # how far the timeline is scrolled, in pixels
        self.position = 0.0       # playhead, seconds
        self.fit_mode = True      # zoom follows the window width until the user zooms in
        self.muted = False
        self.snap_enabled = True

        self._mode = None         # None | "scrub" | "press" | "reorder" | "trim"
        self._press_x = 0.0
        self._press_index = -1    # index of the clip being pressed / dragged / trimmed
        self._grab_dt = 0.0       # reorder: seconds from the clip start to where it was grabbed
        self._drop_index = -1     # reorder: where the clip would land
        self._ghost = None        # reorder: (x, width) of the clip following the mouse
        self._trim = None         # trim: [clip id, "l" | "r", new source in / out]
        self._snap_t = None       # time of the snap line being shown
        self._ignore_until = 0.0
        self._cache = None
        self._cache_dirty = True
        self._font = QFont()
        self._font.setPointSize(8)
        self._fm = QFontMetrics(self._font)

        self._soon = QTimer(self)             # batches repaints while data is streaming in
        self._soon.setSingleShot(True)
        self._soon.setInterval(100)
        self._soon.timeout.connect(self._invalidate)

        project.changed.connect(self._on_project_changed)
        project.selection_changed.connect(self._invalidate)

    @property
    def duration(self):
        return self.project.total()

    # ------------------------------------------------------------------ data
    def _on_project_changed(self):
        total = self.duration
        if total <= 0:
            self.position = 0.0
            self.scroll_px = 0.0
            self.pps = 1.0
            self.fit_mode = True
        else:
            self.pps = self.fit_pps() if self.fit_mode else max(self.pps, self.fit_pps())
            self.position = min(self.position, total)
            self._clamp_scroll()
        self._invalidate()
        self.view_changed.emit()

    def add_media(self, path, duration):
        media = Media(path, duration)
        self.media[path] = media
        return media

    def add_thumbnail(self, path, index, image):
        media = self.media.get(path)
        if media is None:
            return
        pixmap = QPixmap.fromImage(image)
        while len(media.thumbs) <= index:
            media.thumbs.append(None)
        media.thumbs[index] = pixmap
        if not media.thumb_w and pixmap.height():
            media.thumb_w = max(24, round(pixmap.width() * TRACK_H / pixmap.height()))
        self._invalidate_soon()

    def set_thumbs_done(self, path):
        """No more thumbnails are coming: the end of the video reuses the last one (after the last keyframe)."""
        media = self.media.get(path)
        if media is not None:
            media.thumbs_done = True
            self._invalidate()

    def add_peaks(self, path, mins, maxs):
        media = self.media.get(path)
        if media is None:
            return
        part = array("h")
        part.frombytes(mins)
        media.mins.extend(part)
        part = array("h")
        part.frombytes(maxs)
        media.maxs.extend(part)
        self._invalidate_soon()

    def set_audio_done(self, path, has_audio):
        media = self.media.get(path)
        if media is not None:
            media.audio_state = bool(has_audio)
            self._invalidate()

    def set_muted(self, muted):
        self.muted = muted
        self._invalidate()

    def set_snap(self, on):
        self.snap_enabled = bool(on)

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
        if self._mode is not None or time.monotonic() < self._ignore_until:
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

    def set_playhead(self, t):
        """Move the playhead (and the player) to t, e.g. after an edit."""
        t = min(max(t, 0.0), self.duration)
        old, self.position = self.position, t
        self._ignore_until = time.monotonic() + 0.3
        if not 0 <= self.time_to_x(t) <= self.width():
            self.set_scroll(t * self.pps - self.width() * 0.3)
        self._update_playhead(old, t)
        self.seek_requested.emit(t, True)

    def _update_playhead(self, old, new):
        xs = sorted((int(self.time_to_x(old)), int(self.time_to_x(new))))
        self.update(QRect(xs[0] - 8, 0, xs[1] - xs[0] + 17, self.height()))

    def _seek_to_x(self, x, exact, snap=False):
        t = self.x_to_time(x)
        if snap:
            t = min(self._snap(t, playhead=False), self.duration)
        old, self.position = self.position, t
        self._update_playhead(old, t)
        self.update()
        self.seek_requested.emit(t, exact)

    # ------------------------------------------------------------------ snapping
    def _snap(self, t, limit=None, playhead=True):
        """Pull t onto the playhead or a clip edge when it is close (SNAP_PX). Remembers the line to draw.
        `limit`: clip edges after this time are ignored (they move while a right trim handle is dragged).
        The playhead never moves, so it is always a target."""
        self._snap_t = None
        if not self.snap_enabled or self.pps <= 0:
            return t
        best, dist = None, SNAP_PX / self.pps
        points = [c for c in self.project.boundaries() if limit is None or c <= limit + 1e-9]
        if playhead:
            points.append(self.position)
        for c in points:
            d = abs(c - t)
            if d < dist:
                best, dist = c, d
        if best is None:
            return t
        self._snap_t = best
        return best

    # ------------------------------------------------------------------ layout
    @staticmethod
    def _pack(clips):
        slots, t = [], 0.0
        for c in clips:
            slots.append(Slot(c, t, t + c.length, c.src_in, c.src_out))
            t += c.length
        return slots

    def _layout(self):
        """Where every clip is drawn right now, including the preview of a reorder or a trim."""
        clips = self.project.clips
        if self._mode == "reorder" and 0 <= self._press_index < len(clips) and self._drop_index >= 0:
            dragged = clips[self._press_index]
            others = [c for c in clips if c is not dragged]
            k = self._drop_index
            return self._pack(others[:k] + [dragged] + others[k:])
        slots = self._pack(clips)
        if self._mode == "trim" and self._trim:
            clip_id, side, value = self._trim
            for i, s in enumerate(slots):
                if s.clip.id != clip_id:
                    continue
                if side == "l":   # the left edge follows the mouse, the right edge stays (the gap closes on release)
                    slots[i] = s._replace(t0=s.t0 + (value - s.clip.src_in), src_in=value)
                else:             # the right edge follows the mouse, the clips after it move along
                    delta = value - s.clip.src_out
                    slots[i] = s._replace(t1=s.t1 + delta, src_out=value)
                    for k in range(i + 1, len(slots)):
                        o = slots[k]
                        slots[k] = o._replace(t0=o.t0 + delta, t1=o.t1 + delta)
                break
        return slots

    def _hit(self, x):
        """('l' | 'r' | 'body', index) for the clip under x (left / right trim handle, or the middle), else (None, -1)."""
        for i, s in enumerate(self._pack(self.project.clips)):
            x0, x1 = self.time_to_x(s.t0), self.time_to_x(s.t1)
            if x0 <= x < x1:
                handle = min(HANDLE_W, (x1 - x0) / 3)
                if x < x0 + handle:
                    return "l", i
                if x >= x1 - handle:
                    return "r", i
                return "body", i
        return None, -1

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.duration <= 0:
            return
        x, y = event.position().x(), event.position().y()
        if y < RULER_H:                       # ruler: drag the playhead
            self._mode = "scrub"
            self._seek_to_x(x, exact=False, snap=True)
            return
        part, i = self._hit(x)
        if i < 0:                             # empty space: deselect
            self.project.select(0)
            return
        clip = self.project.clips[i]
        self.project.select(clip.id)
        self._press_index = i
        if part in ("l", "r"):
            self._mode = "trim"
            self._trim = [clip.id, part, clip.src_in if part == "l" else clip.src_out]
        else:
            self._mode = "press"              # a click selects + moves the playhead; a drag reorders
            self._press_x = x
            self._grab_dt = self.x_to_time(x) - self.project.starts()[i]
            self._seek_to_x(x, exact=True)

    def mouseMoveEvent(self, event):
        x, y = event.position().x(), event.position().y()
        if self._mode is None:
            part, _ = self._hit(x) if y >= RULER_H else (None, -1)
            if part in ("l", "r"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.unsetCursor()
            return
        if x < 24:                            # near the edge while dragging: scroll
            self.set_scroll(self.scroll_px - 14)
        elif x > self.width() - 24:
            self.set_scroll(self.scroll_px + 14)
        if self._mode == "scrub":
            self._seek_to_x(x, exact=False, snap=True)
        elif self._mode == "press":
            if abs(x - self._press_x) >= DRAG_PX:
                self._mode = "reorder"
                self._drop_index = self._press_index
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self._reorder_to(x)
        elif self._mode == "reorder":
            self._reorder_to(x)
        elif self._mode == "trim":
            self._trim_to(x)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._mode is None:
            return
        mode, self._mode = self._mode, None
        x = event.position().x()
        if mode == "scrub":
            self._seek_to_x(x, exact=True, snap=True)
        elif mode == "reorder":
            self._finish_reorder()
        elif mode == "trim":
            self._finish_trim()
        self._snap_t = None
        self._ghost = None
        self._trim = None
        self._drop_index = -1
        self._press_index = -1
        self._ignore_until = time.monotonic() + 0.15  # let the player catch up before following it again
        self.unsetCursor()
        self._invalidate()

    def _reorder_to(self, x):
        clips = self.project.clips
        dragged = clips[self._press_index]
        start = (x + self.scroll_px) / self.pps - self._grab_dt     # where the dragged clip would start
        centre = start + dragged.length / 2
        k, pos = 0, 0.0
        for c in clips:
            if c is dragged:
                continue
            if pos + c.length / 2 < centre:
                k += 1
            pos += c.length
        self._ghost = (self.time_to_x(start), dragged.length * self.pps)
        if k != self._drop_index:
            self._drop_index = k
            self._invalidate()
        else:
            self.update()

    def _finish_reorder(self):
        clip = self.project.clips[self._press_index]
        if self._drop_index != self._press_index and self.project.move(clip.id, self._drop_index):
            start = self.project.starts()[self.project.index_of(clip.id)]
            self.set_playhead(start)

    def _trim_to(self, x):
        clip_id, side, _ = self._trim
        i = self.project.index_of(clip_id)
        clip = self.project.clips[i]
        start = self.project.starts()[i]
        t = (x + self.scroll_px) / self.pps                       # time under the mouse
        t = self._snap(t, limit=start + clip.length if side == "r" else None)
        value = clip.src_in + (t - start)
        if side == "l":
            value = min(max(value, 0.0), clip.src_out - MIN_CLIP)
        else:
            value = min(max(value, clip.src_in + MIN_CLIP), clip.src_duration)
        self._trim[2] = value
        self._invalidate()

    def _finish_trim(self):
        clip_id, side, value = self._trim
        if side == "l":
            self.project.trim_start(clip_id, value)
        else:
            self.project.trim_end(clip_id, value)

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
        if self._ghost:   # the clip being dragged, following the mouse
            gx, gw = self._ghost
            ghost = QRect(int(gx), RULER_H + GAP, max(int(gw), 2), TRACK_H * 2 + GAP)
            p.fillRect(ghost, QColor(255, 255, 255, 50))
            p.setPen(QPen(QColor(255, 255, 255, 170), 1))
            p.drawRect(ghost)
        if self._snap_t is not None:
            sx = int(round(self.time_to_x(self._snap_t)))
            p.setPen(QPen(SELECT, 1, Qt.PenStyle.DashLine))
            p.drawLine(sx, 0, sx, self.height())
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
        if not self.project.clips:
            p.setPen(LABEL)
            p.drawText(QRect(0, video_y, w, TRACK_H * 2 + GAP), Qt.AlignmentFlag.AlignCenter,
                       "Import a video to see the timeline")
            return
        self._paint_ruler(p, w)
        slots = self._layout()
        active = self._trim[0] if self._mode == "trim" and self._trim else 0
        slots = [s for s in slots if s.clip.id != active] + [s for s in slots if s.clip.id == active]  # trimmed clip on top
        for s in slots:
            x0, x1 = self.time_to_x(s.t0), self.time_to_x(s.t1)
            if x1 < 0 or x0 > w:
                continue
            self._paint_video_clip(p, video_y, s, x0, x1, w)
            self._paint_audio_clip(p, audio_y, s, x0, x1, w)
            self._paint_frame(p, s, x0, x1, w, video_y, audio_y)

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

    @staticmethod
    def _look(media, index, transform):
        """media.thumbs[index] after the clip's flip / rotate / crop (cached per picture + transform)."""
        key = (index, transform)
        pixmap = media.looks.get(key)
        if pixmap is None:
            if len(media.looks) > 800:               # old looks of earlier edits
                media.looks.clear()
            pixmap = media.looks[key] = look_pixmap(media.thumbs[index], transform)
        return pixmap

    def _paint_video_clip(self, p, y, s, x0, x1, w):
        left, right = max(x0, 0.0), min(x1, float(w))
        if right <= left:
            return
        p.fillRect(QRect(int(left), y, int(right) - int(left), TRACK_H), VIDEO_BG)
        media = self.media.get(s.clip.path)
        if media is None or not media.thumbs:
            return
        transform = s.clip.transform                # flip / rotate / crop: the thumbnails show what the preview shows
        tile = media.thumb_w or 128
        if not transform.is_default:
            first = next((n for n, pm in enumerate(media.thumbs) if pm is not None), None)
            if first is None:
                return
            tile = self._look(media, first, transform).width()
        i = max(0, int((left - x0) // tile))
        while True:
            x = x0 + i * tile                       # tiles start at the left edge of the clip
            if x >= right:
                break
            index = int(round((s.src_in + i * tile / self.pps) / media.thumb_step))
            if index >= len(media.thumbs):          # not made (yet): reuse the last one only when finished
                index = len(media.thumbs) - 1 if media.thumbs_done else -1
            pixmap = media.thumbs[index] if index >= 0 else None
            if pixmap is not None and not transform.is_default:
                pixmap = self._look(media, index, transform)
            if pixmap is not None:
                dx0, dx1 = max(x, left), min(x + tile, right, x + pixmap.width())
                if dx1 > dx0:
                    p.drawPixmap(int(dx0), y, pixmap, int(dx0 - x), 0, int(dx1 - dx0), TRACK_H)
            i += 1

    def _paint_audio_clip(self, p, y, s, x0, x1, w):
        left, right = max(x0, 0.0), min(x1, float(w))
        if right <= left:
            return
        area = QRect(int(left), y, int(right) - int(left), TRACK_H)
        p.fillRect(area, AUDIO_BG)
        media = self.media.get(s.clip.path)
        if media is None:
            return
        if media.audio_state is False:
            p.setPen(LABEL)
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "No audio")
            return
        count = len(media.mins)
        if count == 0:
            p.setPen(LABEL)
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "Reading audio ...")
            return
        mid = y + TRACK_H / 2
        scale = (TRACK_H / 2 - 4) / 32768
        lines = []
        for x in range(int(left), int(right)):
            first = int((s.src_in + (x - x0) / self.pps) * PEAKS_PER_SECOND)
            if first < 0:
                continue
            if first >= count:
                break
            last = max(first + 1, int((s.src_in + (x + 1 - x0) / self.pps) * PEAKS_PER_SECOND))
            top = int(mid - max(media.maxs[first:last]) * scale)
            bottom = int(mid - min(media.mins[first:last]) * scale)
            lines.append(QLine(x, top, x, max(bottom, top + 1)))
        p.setPen(QPen(WAVE_MUTED if self.muted else WAVE, 1))
        p.drawLines(lines)

    def _paint_frame(self, p, s, x0, x1, w, vy, ay):
        """Clip border, name + length, and (for the selected clip) the outline and trim handles."""
        xi0, xi1 = int(round(x0)), int(round(x1))
        p.setPen(QColor(0, 0, 0, 200))
        for y in (vy, ay):
            p.drawLine(xi0, y, xi0, y + TRACK_H)
        avail = int(min(x1, w) - max(x0, 0)) - 8
        if avail > 30:
            text = f"{os.path.basename(s.clip.path)}   {s.t1 - s.t0:.1f}s"
            text = self._fm.elidedText(text, Qt.TextElideMode.ElideRight, avail)
            lx = int(max(x0, 0)) + 4
            p.fillRect(QRect(lx, vy + 3, self._fm.horizontalAdvance(text) + 8, 15), QColor(0, 0, 0, 150))
            p.setPen(LABEL)
            p.drawText(QRect(lx + 4, vy + 3, avail, 15),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        if s.clip.id != self.project.selected_id:
            return
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(SELECT, 2))
        for y in (vy, ay):
            p.drawRect(QRect(xi0 + 1, y + 1, xi1 - xi0 - 2, TRACK_H - 2))
        hw = int(min(HANDLE_W, (x1 - x0) / 3))
        for hx in (xi0, xi1 - hw):
            p.fillRect(QRect(hx, vy, hw, TRACK_H), SELECT)
            p.setPen(QColor(70, 50, 0))
            mid = vy + TRACK_H // 2
            p.drawLine(hx + hw // 2, mid - 8, hx + hw // 2, mid + 8)


class TimelinePanel(QWidget):
    """Zoom controls + track headers (with the audio mute button) + the timeline + a scroll bar."""

    def __init__(self, player, project, parent=None):
        super().__init__(parent)
        self.player = player
        self.project = project
        self._workers = {}   # worker -> source path it works on

        style = self.style()
        self._icon_volume = style.standardIcon(QStyle.StandardPixmap.SP_MediaVolume)
        self._icon_muted = style.standardIcon(QStyle.StandardPixmap.SP_MediaVolumeMuted)

        self.view = TimelineView(project)

        # --- zoom row
        self.snap_btn = QPushButton("Snap")
        self.snap_btn.setCheckable(True)
        self.snap_btn.setChecked(True)
        self.snap_btn.setToolTip("Clip edges and the trim handles snap to the playhead and to each other")
        self.zoom_out_btn = QPushButton("-")
        self.zoom_in_btn = QPushButton("+")
        self.fit_btn = QPushButton("Fit")
        for b in (self.zoom_out_btn, self.zoom_in_btn):
            b.setFixedWidth(30)
        self.fit_btn.setFixedWidth(46)
        self.snap_btn.setFixedWidth(56)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(0, 1000)
        self.zoom_slider.setFixedWidth(180)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(self.snap_btn)
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

        for w in (self.snap_btn, self.zoom_out_btn, self.zoom_in_btn, self.fit_btn,
                  self.zoom_slider, self.scrollbar):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # --- wiring
        self.snap_btn.toggled.connect(self.view.set_snap)
        self.zoom_out_btn.clicked.connect(lambda: self.view.set_zoom(self.view.pps / 1.5))
        self.zoom_in_btn.clicked.connect(lambda: self.view.set_zoom(self.view.pps * 1.5))
        self.fit_btn.clicked.connect(self.view.zoom_fit)
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider)
        self.scrollbar.valueChanged.connect(self.view.set_scroll)
        self.view.view_changed.connect(self._sync_controls)
        self.view.seek_requested.connect(player.seek_to)
        self.mute_btn.clicked.connect(lambda checked: player.set_muted(checked))

        player.position_changed.connect(self.view.set_position)
        player.muted_changed.connect(self._sync_mute)
        project.changed.connect(self._start_new_media)

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

    # ------------------------------------------------------------------ public helpers
    def position(self):
        """Playhead time in seconds."""
        return self.view.position

    def set_playhead(self, t):
        self.view.set_playhead(t)

    # ------------------------------------------------------------------ thumbnails + waveform per source file
    def _start_new_media(self):
        for clip in self.project.clips:
            if clip.path not in self.view.media:
                self._start_media(clip.path, clip.src_duration)

    def _start_media(self, path, duration):
        media = self.view.add_media(path, duration)
        thumbs = ThumbnailWorker(path, duration, media.thumb_step, TRACK_H, self)
        wave = WaveformWorker(path, self)
        self._workers[thumbs] = path
        self._workers[wave] = path
        thumbs.thumb_ready.connect(self._on_thumb)
        thumbs.finished.connect(self._on_thumbs_finished)
        wave.chunk_ready.connect(self._on_peaks)
        wave.audio_done.connect(self._on_audio_done)
        thumbs.start()
        wave.start()

    # the sender is the worker; it tells us which file the data belongs to
    def _on_thumb(self, index, image):
        path = self._workers.get(self.sender())
        if path:
            self.view.add_thumbnail(path, index, image)

    def _on_thumbs_finished(self):
        path = self._workers.get(self.sender())
        if path:
            self.view.set_thumbs_done(path)

    def _on_peaks(self, mins, maxs):
        path = self._workers.get(self.sender())
        if path:
            self.view.add_peaks(path, mins, maxs)

    def _on_audio_done(self, has_audio):
        path = self._workers.get(self.sender())
        if path:
            self.view.set_audio_done(path, has_audio)

    def shutdown(self):
        """Call before the window closes."""
        workers = list(self._workers)
        for worker in workers:
            worker.stop()
        for worker in workers:
            worker.wait(3000)

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
