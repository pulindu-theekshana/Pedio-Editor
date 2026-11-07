"""Video preview: embedded mpv + play/pause, seek bar, time display, volume and mute."""
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSlider,
                               QStackedLayout, QStyle, QVBoxLayout, QWidget)

VIDEO_EXTS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v",
    ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".3gp", ".ogv",
}

END_FILE_ERROR = 4  # mpv "end-file" reason: the file could not be played


def fmt_time(seconds):
    """12.34 -> '00:12.3', 3725.5 -> '1:02:05.5'"""
    tenths = int(max(seconds or 0.0, 0.0) * 10)
    total, frac = divmod(tenths, 10)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}.{frac}" if h else f"{m:02d}:{s:02d}.{frac}"


class SeekSlider(QSlider):
    """Slider that jumps to the clicked position and keeps following the mouse while pressed."""

    def _value_at(self, event):
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), int(event.position().x()), self.width())

    def _drag_to(self, event):
        value = self._value_at(event)
        self.setValue(value)
        self.sliderMoved.emit(value)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.setSliderDown(True)  # emits sliderPressed
            self._drag_to(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self._drag_to(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self._drag_to(event)
            self.setSliderDown(False)  # emits sliderReleased


class PlayerWidget(QWidget):
    # Public signals (the timeline etc. will use these in later steps)
    file_loaded = Signal(str)        # path of the loaded video
    load_failed = Signal(str)        # message
    position_changed = Signal(float)  # seconds
    duration_changed = Signal(float)  # seconds
    playing_changed = Signal(bool)
    muted_changed = Signal(bool)

    # mpv calls us from its own thread; these signals hand the data over to the Qt thread
    _mpv_time = Signal(object)
    _mpv_duration = Signal(object)
    _mpv_pause = Signal(object)
    _mpv_loaded = Signal(str)
    _mpv_end = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = None       # mpv is created on the first import
        self._has_file = False
        self._duration = 0.0
        self._muted = False
        self._last_path = ""

        style = self.style()
        self._icon_play = style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        self._icon_pause = style.standardIcon(QStyle.StandardPixmap.SP_MediaPause)
        self._icon_volume = style.standardIcon(QStyle.StandardPixmap.SP_MediaVolume)
        self._icon_muted = style.standardIcon(QStyle.StandardPixmap.SP_MediaVolumeMuted)

        # --- video area: a hint page, and the native window mpv draws into
        self.hint = QLabel("Drop a video here\nor click  Video > Import  (Ctrl+O)")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setStyleSheet("background:#000; color:#aaa; font-size:18px;")
        self.hint.setMinimumSize(320, 180)

        self.video = QWidget()
        self.video.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.video.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        palette = self.video.palette()
        palette.setColor(QPalette.ColorRole.Window, Qt.GlobalColor.black)
        self.video.setPalette(palette)
        self.video.setAutoFillBackground(True)
        self.video.setMinimumSize(320, 180)

        self.stack = QStackedLayout()
        self.stack.addWidget(self.hint)
        self.stack.addWidget(self.video)

        # --- controls
        self.play_btn = QPushButton()
        self.play_btn.setIcon(self._icon_play)
        self.play_btn.setFixedWidth(40)
        self.play_btn.setEnabled(False)

        self.seek = SeekSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.setEnabled(False)

        self.time_label = QLabel(f"{fmt_time(0)} / {fmt_time(0)}")
        self.time_label.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")

        self.mute_btn = QPushButton()
        self.mute_btn.setIcon(self._icon_volume)
        self.mute_btn.setFixedWidth(40)

        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(100)
        self.volume.setFixedWidth(100)

        for w in (self.play_btn, self.seek, self.mute_btn, self.volume):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        controls = QHBoxLayout()
        controls.setContentsMargins(8, 4, 8, 4)
        controls.setSpacing(10)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.seek, 1)
        controls.addWidget(self.time_label)
        controls.addWidget(self.mute_btn)
        controls.addWidget(self.volume)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(self.stack, 1)
        root.addLayout(controls)

        # --- wiring
        self.play_btn.clicked.connect(self.toggle_play)
        self.mute_btn.clicked.connect(self.toggle_mute)
        self.volume.valueChanged.connect(self._on_volume)
        self.seek.sliderMoved.connect(self._on_seek_moved)
        self.seek.sliderReleased.connect(self._on_seek_released)
        self._mpv_time.connect(self._on_time)
        self._mpv_duration.connect(self._on_duration)
        self._mpv_pause.connect(self._on_pause)
        self._mpv_loaded.connect(self._on_loaded)
        self._mpv_end.connect(self._on_end)

        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self.toggle_play)

    # ------------------------------------------------------------------ mpv setup
    def _ensure_player(self):
        if self.player is not None:
            return True
        try:
            import mpv  # needs mpv-2.dll (see paths.py)

            self.stack.setCurrentWidget(self.video)
            wid = str(int(self.video.winId()))
            options = dict(
                wid=wid,
                idle="yes",             # stay alive with no file loaded
                keep_open="yes",        # stop on the last frame instead of closing
                osc="no",
                osd_level=0,
                input_default_bindings="no",
                input_vo_keyboard="no",
                loglevel="error",
                log_handler=lambda level, prefix, text: print(f"[mpv {level}] {prefix}: {text.strip()}"),
            )
            nice_to_have = dict(hwdec="auto-safe", drag_and_drop="no")
            try:
                self.player = mpv.MPV(**options, **nice_to_have)
            except Exception:  # an option this libmpv build does not know
                self.player = mpv.MPV(**options)
        except Exception as e:
            self.player = None
            self.stack.setCurrentWidget(self.hint)
            self.load_failed.emit(f"Could not start the video player: {e}")
            return False

        p = self.player
        p.volume = self.volume.value()
        p.mute = self._muted
        p.observe_property("time-pos", lambda _name, v: self._mpv_time.emit(v))
        p.observe_property("duration", lambda _name, v: self._mpv_duration.emit(v))
        p.observe_property("pause", lambda _name, v: self._mpv_pause.emit(bool(v)))

        @p.event_callback("file-loaded")
        def _file_loaded(_event):
            self._mpv_loaded.emit(str(p.path or ""))

        @p.event_callback("end-file")
        def _end_file(event):
            self._mpv_end.emit(int(event.data.reason))

        return True

    def shutdown(self):
        """Call before the window closes."""
        if self.player is not None:
            player, self.player = self.player, None
            try:
                player.terminate()
            except Exception:
                pass

    # ------------------------------------------------------------------ public API
    def load(self, path):
        if not self._ensure_player():
            return
        self._last_path = path
        self.player.pause = True  # show the first frame, wait for Play
        self.player.play(path)

    def toggle_play(self):
        if self.player is None or not self._has_file:
            return
        if self.player.pause and self.player.eof_reached:  # finished: start again from the beginning
            self.seek_to(0.0)
        self.player.pause = not self.player.pause

    def seek_to(self, seconds, exact=True):
        if self.player is None or not self._has_file:
            return
        mode = "absolute+exact" if exact else "absolute+keyframes"
        self.player.command("seek", f"{max(seconds, 0.0):.3f}", mode)

    def toggle_mute(self):
        self.set_muted(not self._muted)

    def set_muted(self, muted):
        if muted == self._muted:
            return
        self._muted = muted
        self.mute_btn.setIcon(self._icon_muted if muted else self._icon_volume)
        if self.player is not None:
            self.player.mute = muted
        self.muted_changed.emit(muted)

    def is_muted(self):
        return self._muted

    def position(self):
        return self.seek.value() / 1000.0

    def duration(self):
        return self._duration

    # ------------------------------------------------------------------ slots (Qt thread)
    def _on_volume(self, value):
        if self.player is not None:
            self.player.volume = value

    def _on_seek_moved(self, value):
        self.time_label.setText(f"{fmt_time(value / 1000)} / {fmt_time(self._duration)}")
        self.seek_to(value / 1000, exact=False)  # fast, keyframe-accurate while dragging

    def _on_seek_released(self):
        self.seek_to(self.seek.value() / 1000, exact=True)  # exact position when the mouse is released

    def _on_time(self, t):
        if t is None:
            return
        if not self.seek.isSliderDown():
            self.seek.setValue(int(t * 1000))
            self.time_label.setText(f"{fmt_time(t)} / {fmt_time(self._duration)}")
        self.position_changed.emit(float(t))

    def _on_duration(self, d):
        self._duration = float(d or 0.0)
        self.seek.setRange(0, int(self._duration * 1000))
        self.time_label.setText(f"{fmt_time(self.position())} / {fmt_time(self._duration)}")
        self.duration_changed.emit(self._duration)

    def _on_pause(self, paused):
        self.play_btn.setIcon(self._icon_play if paused else self._icon_pause)
        self.playing_changed.emit(not paused)

    def _on_loaded(self, path):
        self._has_file = True
        self.play_btn.setEnabled(True)
        self.seek.setEnabled(True)
        self.stack.setCurrentWidget(self.video)
        self.file_loaded.emit(path or self._last_path)

    def _on_end(self, reason):
        if reason == END_FILE_ERROR:
            # mpv has already dropped any previous video, so go back to the empty state
            self._has_file = False
            self._duration = 0.0
            self.play_btn.setEnabled(False)
            self.seek.setEnabled(False)
            self.seek.setRange(0, 0)
            self.time_label.setText(f"{fmt_time(0)} / {fmt_time(0)}")
            self.stack.setCurrentWidget(self.hint)
            name = os.path.basename(self._last_path) or "that file"
            self.load_failed.emit(f"Could not open {name}. It may not be a valid video.")
