"""Background ffmpeg jobs for the timeline: video thumbnails and audio waveform peaks."""
import glob
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from array import array

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from pedio.core.transform import stream_rotation
from pedio.paths import find_tool

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # no black console flash on Windows

PEAK_RATE = 8000                          # audio is decoded to 8 kHz mono just for the picture
PEAK_BUCKET = 40                          # samples per peak
PEAKS_PER_SECOND = PEAK_RATE // PEAK_BUCKET   # = 200


def thumb_step(duration, target=150):
    """Seconds between thumbnails: about `target` thumbnails for the whole video, at least 0.5 s apart."""
    return max(0.5, math.ceil(duration / target * 2) / 2)


class ProbeWorker(QThread):
    """Asks ffprobe for the length, picture size and phone rotation of a file, and checks that it has a video stream."""

    done = Signal(str, float, int, int, int)   # path, duration (s), width, height (as a player shows it), clockwise turn from metadata
    failed = Signal(str, str)                  # path, message

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        ffprobe = find_tool("ffprobe")
        if not ffprobe:
            self.failed.emit(self.path, "ffprobe was not found. Run setup.bat again.")
            return
        cmd = [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", self.path]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
            info = json.loads(out.stdout.decode("utf-8", "replace") or "{}")
            streams = info.get("streams", [])
            duration = float(info.get("format", {}).get("duration", 0))
        except Exception:
            streams, duration = [], 0.0
        video = next((s for s in streams if s.get("codec_type") == "video"
                      and s.get("width") and not s.get("disposition", {}).get("attached_pic")), None)
        name = os.path.basename(self.path)
        if video is None:
            self.failed.emit(self.path, f"Could not open {name}. It may not be a valid video.")
        elif duration <= 0:
            self.failed.emit(self.path, f"Could not read the length of {name}.")
        else:
            width, height = int(video["width"]), int(video["height"])
            rotation = stream_rotation(video)
            if rotation % 180 == 90:
                width, height = height, width      # the picture as a player shows it
            self.done.emit(self.path, duration, width, height, rotation)


class FrameWorker(QThread):
    """One picture of a clip at `t` seconds with the given filters (mirror / rotate, no crop), at most 720 px tall.
    The crop box is drawn on this picture."""

    frame_ready = Signal(QImage)
    failed = Signal(str)

    def __init__(self, path, t, filters, parent=None):
        super().__init__(parent)
        self.path = path
        self.t = t
        self.filters = list(filters)

    def run(self):
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            self.failed.emit("ffmpeg was not found. Run setup.bat again.")
            return
        vf = ",".join(self.filters + ["scale=-2:if(gt(ih\\,720)\\,720\\,ih)"])   # a backslash escapes a comma for ffmpeg
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{max(self.t, 0.0):.3f}",
               "-i", self.path, "-frames:v", "1", "-vf", vf, "-f", "image2pipe", "-c:v", "png", "pipe:1"]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
            image = QImage.fromData(out.stdout, "PNG")
        except Exception:
            image = QImage()
        if image.isNull():
            self.failed.emit("Could not read a picture from this clip.")
        else:
            self.frame_ready.emit(image)


class ThumbnailWorker(QThread):
    """Makes one small picture every `step` seconds. Thumbnail n shows the video at n * step seconds."""

    thumb_ready = Signal(int, QImage)

    def __init__(self, path, duration, step, height, parent=None):
        super().__init__(parent)
        self.path = path
        self.step = step
        self.height = height
        self.count = int(duration // step) + 1
        self._stop = False
        self._proc = None

    def stop(self):
        self._stop = True
        proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    def run(self):
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            return
        tmp = tempfile.mkdtemp(prefix="pedio_thumbs_")
        try:
            # Keyframes only is much faster; if that gives (almost) nothing, decode every frame.
            for keyframes_only in (True, False):
                sent = self._extract(ffmpeg, tmp, keyframes_only)
                if self._stop or sent >= min(2, self.count):
                    break
                for name in glob.glob(os.path.join(tmp, "*.jpg")):
                    os.remove(name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _extract(self, ffmpeg, tmp, keyframes_only):
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if keyframes_only:
            cmd += ["-skip_frame", "nokey"]
        cmd += [
            "-i", self.path, "-an", "-sn",
            "-vf", f"fps=1/{self.step:.4f},scale=-2:{self.height}",
            "-frames:v", str(self.count), "-q:v", "5",
            os.path.join(tmp, "t_%05d.jpg"),
        ]
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        sent = set()
        while True:
            running = self._proc.poll() is None
            self._send_new(tmp, sent, final=not running)
            if not running or self._stop:
                break
            time.sleep(0.2)
        return len(sent)

    def _send_new(self, tmp, sent, final):
        names = sorted(glob.glob(os.path.join(tmp, "t_*.jpg")))
        for i, name in enumerate(names):
            index = int(os.path.basename(name)[2:7]) - 1
            if index in sent:
                continue
            if not final and i == len(names) - 1:
                break  # the newest file may still be written by ffmpeg
            image = QImage(name)
            if not image.isNull():
                sent.add(index)
                self.thumb_ready.emit(index, image)


class WaveformWorker(QThread):
    """Decodes the audio and sends (min, max) peaks, PEAKS_PER_SECOND per second, as raw int16 bytes."""

    chunk_ready = Signal(bytes, bytes)   # mins, maxs  (array('h').tobytes())
    audio_done = Signal(bool)            # True if the file has an audio track

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self._stop = False
        self._proc = None

    def stop(self):
        self._stop = True
        proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    def run(self):
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            self.audio_done.emit(False)
            return
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", self.path,
            "-vn", "-map", "0:a:0?", "-ac", "1", "-ar", str(PEAK_RATE), "-f", "s16le", "-",
        ]
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        bucket_bytes = PEAK_BUCKET * 2
        total = 0
        rest = b""
        while not self._stop:
            data = self._proc.stdout.read(bucket_bytes * 1000)  # ~5 seconds of audio
            if not data:
                break
            data = rest + data
            usable = len(data) // bucket_bytes * bucket_bytes
            rest = data[usable:]
            total += self._send(data[:usable])
        if not self._stop:
            if len(rest) >= 4:  # the last, shorter bucket
                total += self._send(rest[:len(rest) // 2 * 2])
            self.audio_done.emit(total > 0)
        self._proc.stdout.close()
        self._proc.wait()

    def _send(self, raw):
        samples = array("h")
        samples.frombytes(raw)
        mins, maxs = array("h"), array("h")
        for i in range(0, len(samples), PEAK_BUCKET):
            part = samples[i:i + PEAK_BUCKET]
            mins.append(min(part))
            maxs.append(max(part))
        if len(mins):
            self.chunk_ready.emit(mins.tobytes(), maxs.tobytes())
        return len(mins)
