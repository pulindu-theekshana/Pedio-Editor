"""Video preview: embedded mpv + play/pause, seek bar, time display, volume and mute.

The player does not open files itself: it plays an edit list (`Project.play_list()`: Segment(path, start, length,
transform, ...), the clips on the timeline) as one mpv "edl://" playlist, so playback runs across clips.

Mirror / rotate / crop (core/transform.py) are mpv video filters, `vf=lavfi=[...]`, the same filters the export
uses. mpv has ONE filter chain for the whole playlist, so the player switches it when the playhead moves into a
clip with another transform (`_sync_look`). Seeking and pausing show the right picture; during live playback the
first few frames after a cut between two differently transformed clips can still show the old look.
"""
import bisect

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSlider,
                               QStackedLayout, QStyle, QVBoxLayout, QWidget)

from pedio.core.transform import video_filters

VIDEO_EXTS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v",
    ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".3gp", ".ogv",
}

AUDIO_EXTS = {
    ".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg", ".oga", ".opus", ".wma", ".aiff", ".aif",
}

END_FILE_ERROR = 4  # mpv "end-file" reason: the file could not be played


def fmt_time(seconds):
    """12.34 -> '00:12.3', 3725.5 -> '1:02:05.5'"""
    tenths = int(max(seconds or 0.0, 0.0) * 10)
    total, frac = divmod(tenths, 10)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}.{frac}" if h else f"{m:02d}:{s:02d}.{frac}"


def edl_url(segments):
    """[(path, start, length)] -> mpv 'edl://' URL. %N% gives the byte length of the path, so any characters are safe."""
    parts = []
    for path, start, length in segments:
        parts.append(f"%{len(path.encode('utf-8'))}%{path},{start:.3f},{length:.3f}")
    return "edl://" + ";".join(parts)


def merge_sources(segments):
    """[Segment] -> [(path, start, length)] for mpv. Pieces that continue each other in the same file are one
    piece (the transform does not matter here), so changing a transform never reloads the playlist."""
    out = []
    for s in segments:
        if out and out[-1][0] == s.path and abs(out[-1][1] + out[-1][2] - s.start) < 1e-6:
            path, start, length = out[-1]
            out[-1] = (path, start, length + s.length)
        else:
            out.append((s.path, s.start, s.length))
    return [(p, round(st, 3), round(n, 3)) for p, st, n in out]


def look_schedule(segments):
    """[(end_time, (transform, meta_rotation, fade_in, fade_out, start, end))] in timeline seconds:
    which look is on screen until end_time."""
    out, t = [], 0.0
    for s in segments:
        start = t
        t += s.length
        out.append((t, (s.transform, s.meta_rotation, s.fade_in, s.fade_out, start, t)))
    return out


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
        self._segments = []          # the last edit list given to set_edit_list
        self._audio_mix = ""         # external audio file (whole timeline mix) that mpv should play
        self._subtitles = ""         # generated ASS file with the timeline's text layers
        self._sources = []           # what mpv has loaded: [(path, start, length)]
        self._looks = []             # [(end_time, (transform, meta_rotation))]: how each part of the timeline is shown
        self._applied_look = None    # the look mpv's filters are set to now
        self._seek_after_load = None  # timeline position to restore once the new list is loaded
        self._resume = False         # start playing again after the reload
        self._settling = False       # ignore mpv's time updates until the position is restored
        self._generation = 0         # counts reloads, so an old settle timer does not end a newer one

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
            # "-copy": the GPU decodes, the frames are copied back to normal memory. Needed for the vf=lavfi filters
            # (rotate / flip / crop): with plain "auto-safe" the frames stay on the GPU (d3d11) and the filter fails.
            nice_to_have = dict(hwdec="auto-copy-safe", drag_and_drop="no")
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
    def set_edit_list(self, segments, position=0.0):
        """Play these Segments one after another. After a reload the player goes back to `position` (timeline
        seconds) and keeps playing if it was. If only a transform changed (rotate / flip / crop) nothing is
        reloaded: the video filters are changed on the fly."""
        segments = list(segments)
        self._segments = segments
        sources = merge_sources(segments)
        self._looks = look_schedule(segments)
        if sources == self._sources:
            self._sync_look(position)
            return
        self._sources = sources
        if not sources:
            self._unload()
            return
        if not self._ensure_player():
            self._sources = []
            return
        if self._audio_mix:     # the whole-timeline sound mix beats the files' own sound (it includes it)
            try:
                self.player.audio_file = self._audio_mix
            except Exception as e:
                print(f"[player] could not set the audio mix: {e}")
        if self._subtitles:     # the text layers, drawn by mpv like a normal external subtitle
            try:
                self.player.sub_file = self._subtitles
            except Exception as e:
                print(f"[player] could not set the text layers: {e}")
        total = sum(n for _p, _s, n in sources)
        if self._settling:  # reloaded again before the last reload finished: keep its "resume" choice
            was_playing = self._resume
        else:
            was_playing = self._has_file and not self.player.pause and not self.player.eof_reached
        self._seek_after_load = min(max(position, 0.0), max(total - 0.04, 0.0))
        self._resume = bool(was_playing)
        self._settling = True
        self._generation += 1
        self._last_path = sources[0][0]
        self._sync_look(self._seek_after_load)  # the filters are global: set them for where we will be
        self.player.pause = True  # show the first frame, wait for Play
        self.player.play(edl_url(sources))

    def pause(self):
        if self.player is not None and self._has_file:
            self.player.pause = True

    def refresh(self):
        """Draw the current picture again (after the video widget was hidden and shown)."""
        if self.player is not None and self._has_file and self._seek_after_load is None:
            self.player.command("seek", f"{self.position():.3f}", "absolute+exact")

    def set_audio_mix(self, path):
        """Use this file as the whole timeline's sound (an ffmpeg mix of the video sound + audio clips).
        Reloads the playlist at the current position so mpv switches tracks cleanly."""
        path = path or ""
        if path == self._audio_mix:
            return
        self._audio_mix = path
        if self.player is None or not self._sources or not self._segments:
            return
        segments, position = list(self._segments), self.position()
        self._sources = []                 # force set_edit_list to load the list again
        self.set_edit_list(segments, position)

    def audio_mix_path(self):
        return self._audio_mix

    def set_subtitles(self, path):
        """Draw these text layers (an ASS file written by the text panel) over the video. Reloads at the current
        position when the file actually changed."""
        path = path or ""
        if path == self._subtitles:
            return
        self._subtitles = path
        if self.player is None or not self._sources or not self._segments:
            return
        segments, position = list(self._segments), self.position()
        self._sources = []                 # force set_edit_list to load the list again
        self.set_edit_list(segments, position)

    def _look_at(self, t):
        ends = [end for end, _look in self._looks]
        i = min(bisect.bisect_right(ends, t), len(self._looks) - 1)
        return self._looks[i][1]

    def _sync_look(self, t):
        """Set mpv's video filters to the look of the clip at timeline time t (if they are not already)."""
        if self.player is None or not self._looks:
            return
        look = self._look_at(t)
        if look == self._applied_look:
            return
        self._applied_look = look
        transform, meta_rotation, fade_in, fade_out, start, end = look
        need = (not transform.is_default) or fade_in > 0 or fade_out > 0
        # no transform and no fade: mpv shows the picture as before (it turns phone videos by itself).
        # with a transform or a fade: the filters do everything, including the phone rotation.
        filters = video_filters(transform, meta_rotation) if need else []
        if fade_in > 0:
            filters.append(f"fade=t=in:st={start:.3f}:d={min(fade_in, end - start):.3f}")
        if fade_out > 0:
            at = max(end - fade_out, start)
            filters.append(f"fade=t=out:st={at:.3f}:d={min(fade_out, end - start):.3f}")
        try:
            self.player.video_rotate = "no" if filters else "0"
            if filters:
                self.player.command("vf", "set", "lavfi=[" + ",".join(filters) + "]")
            else:
                self.player.command("vf", "clr", "")
        except Exception as e:
            print(f"[player] could not set the video filters: {e}")

    def _unload(self):
        self._applied_look = None
        self._seek_after_load = None
        self._resume = False
        self._settling = False
        if self.player is not None:
            try:
                self.player.command("stop")
            except Exception:
                pass
        self._reset_state()

    def _reset_state(self):
        """Back to the empty look: nothing loaded."""
        self._has_file = False
        self._duration = 0.0
        self.play_btn.setEnabled(False)
        self.seek.setEnabled(False)
        self.seek.setRange(0, 0)
        self.time_label.setText(f"{fmt_time(0)} / {fmt_time(0)}")
        self.stack.setCurrentWidget(self.hint)

    def toggle_play(self):
        if self.player is None or not self._has_file:
            return
        if self.player.pause and self.player.eof_reached:  # finished: start again from the beginning
            self.seek_to(0.0)
        self.player.pause = not self.player.pause

    def seek_to(self, seconds, exact=True):
        if self.player is None or not self._has_file:
            return
        self._sync_look(max(seconds, 0.0))     # show the target with the look of the clip it is in
        if self._seek_after_load is not None:  # a reload is still running: remember the target instead
            self._seek_after_load = max(seconds, 0.0)
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
        if t is None or self._settling:
            return
        self._sync_look(t)
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
        if self._seek_after_load is not None:  # a reload: go back to where we were
            t, self._seek_after_load = self._seek_after_load, None
            self._sync_look(t)
            if t > 0.0 and self.player is not None:
                self.player.command("seek", f"{t:.3f}", "absolute+exact")
            QTimer.singleShot(200, lambda g=self._generation: self._end_settle(g))
        self.file_loaded.emit(path or self._last_path)

    def _end_settle(self, generation):
        if generation != self._generation:
            return
        self._settling = False
        if self.player is None:
            return
        if self._resume:
            self._resume = False
            self.player.pause = False
        self._on_time(self.player.time_pos)

    def _on_end(self, reason):
        if reason == END_FILE_ERROR:
            # mpv has already dropped the previous list, so go back to the empty state
            self._seek_after_load = None
            self._resume = False
            self._settling = False
            self._sources = []    # so the next edit loads the list again
            self._applied_look = None
            self._reset_state()
            self.load_failed.emit("Could not play the timeline. One of the clips may not be a valid video.")
