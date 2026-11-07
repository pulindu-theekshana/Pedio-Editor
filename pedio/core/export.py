"""Export the timeline to an MP4 file with ffmpeg. No widgets here: a pure command builder + a QThread worker.

The clip list is `Project.play_list()` = [(path, start, length)], the same list the player plays,
so what you see in the preview is what you get in the file.
"""
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from fractions import Fraction

from PySide6.QtCore import QThread, Signal

from pedio.core.workers import CREATE_NO_WINDOW
from pedio.paths import find_tool

AUDIO_RATE = 48000
DEFAULT_FPS = Fraction(30)


class ExportError(Exception):
    """A problem we can explain to the user in one sentence."""


@dataclass
class ExportSettings:
    """Later steps (crop, export settings) add their options here."""
    crf: int = 20                 # x264 quality: lower = better + bigger file
    preset: str = "veryfast"      # x264 speed / size trade-off
    audio_bitrate: str = "192k"
    size: tuple = None            # (width, height); None = size of the first clip
    fps: Fraction = None          # None = frame rate of the first clip


@dataclass
class SourceInfo:
    """What the export needs to know about one source file."""
    width: int
    height: int
    fps: Fraction
    has_audio: bool


# ---------------------------------------------------------------------- probing
def _fraction(text):
    try:
        value = Fraction(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def _rotation(stream):
    """Rotation in degrees from the file's metadata (phone videos): old 'rotate' tag or newer side data."""
    degrees = 0
    try:
        degrees = int(float(stream.get("tags", {}).get("rotate", 0)))
    except (TypeError, ValueError):
        pass
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            try:
                degrees = int(float(side["rotation"]))
            except (TypeError, ValueError):
                pass
    return degrees


def probe_source(path):
    """Size, frame rate and audio of a file (ffprobe). Raises ExportError."""
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        raise ExportError("ffprobe was not found. Run setup.bat again.")
    cmd = [ffprobe, "-v", "error", "-show_streams", "-of", "json", path]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        streams = json.loads(out.stdout.decode("utf-8", "replace") or "{}").get("streams", [])
    except Exception:
        streams = []
    name = os.path.basename(path)
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and s.get("width") and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise ExportError(f"Could not read {name}. Was the file moved or deleted?")
    width, height = int(video["width"]), int(video["height"])
    if abs(_rotation(video)) % 180 == 90:
        width, height = height, width          # ffmpeg turns the picture upright before the filters
    fps = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate")) or DEFAULT_FPS
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return SourceInfo(width, height, fps, has_audio)


# ---------------------------------------------------------------------- command
def build_command(ffmpeg, clips, sources, out_path, settings):
    """The ffmpeg command line. clips = [(path, start, length)], sources = {path: SourceInfo}.

    Every clip is cut with input seeking (-ss / -t), brought to one size / frame rate / audio format,
    then joined with the concat filter. A clip without sound gets silence of the same length.
    """
    first = sources[clips[0][0]]
    width, height = settings.size or (first.width, first.height)
    width, height = width - width % 2, height - height % 2          # yuv420p needs even numbers
    fps = settings.fps or first.fps

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    parts, joined = [], ""
    for i, (path, start, length) in enumerate(clips):
        cmd += ["-ss", f"{start:.6f}", "-t", f"{length:.6f}", "-i", path]
        parts.append(
            f"[{i}:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"fps={fps.numerator}/{fps.denominator},format=yuv420p,setpts=PTS-STARTPTS[v{i}]")
        if sources[path].has_audio:
            audio = f"[{i}:a:0]aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,apad"
        else:
            audio = f"anullsrc=r={AUDIO_RATE}:cl=stereo"
        parts.append(f"{audio},atrim=duration={length:.6f},asetpts=PTS-STARTPTS[a{i}]")
        joined += f"[v{i}][a{i}]"
    parts.append(f"{joined}concat=n={len(clips)}:v=1:a=1[v][a]")

    cmd += [
        "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", settings.audio_bitrate,
        "-movflags", "+faststart", "-f", "mp4",
        "-progress", "pipe:1", "-nostats", out_path,
    ]
    return cmd


def _out_time(line):
    """Seconds from an ffmpeg '-progress' line, or None. (out_time_ms is really microseconds.)"""
    key, _, value = line.strip().partition("=")
    if key in ("out_time_us", "out_time_ms"):
        try:
            return int(value) / 1_000_000
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------- worker
class ExportWorker(QThread):
    """Runs the export in the background. The file is written as `<name>.part` and renamed when it is complete,
    so a cancelled or failed export never leaves a broken file (or destroys an older one)."""

    progress = Signal(float)     # 0.0 .. 1.0
    done = Signal(str)           # path of the finished file
    failed = Signal(str)         # message for the user
    cancelled = Signal()

    def __init__(self, clips, out_path, settings=None, parent=None):
        super().__init__(parent)
        self.clips = list(clips)                      # copy: later edits do not change a running export
        self.out_path = out_path
        self.settings = settings or ExportSettings()
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
        part = self.out_path + ".part"
        try:
            self._export(part)
        except ExportError as e:
            self._remove(part)
            self.failed.emit(str(e))
        except Exception as e:
            self._remove(part)
            self.failed.emit(f"Export failed: {e}")

    def _export(self, part):
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            raise ExportError("ffmpeg was not found. Run setup.bat again.")
        if not self.clips:
            raise ExportError("There is nothing to export.")
        sources = {}
        for path, _, _ in self.clips:
            if path not in sources:
                if self._stop:
                    return self._cancel(part)
                sources[path] = probe_source(path)
        total = sum(length for _, _, length in self.clips)

        cmd = build_command(ffmpeg, self.clips, sources, part, self.settings)
        with tempfile.TemporaryFile() as errors:      # a file, so ffmpeg can never block on a full pipe
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=errors, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
            if self._stop:
                self._proc.kill()
            for line in self._proc.stdout:
                t = _out_time(line)
                if t is not None and total > 0:
                    self.progress.emit(min(max(t / total, 0.0), 1.0))
            code = self._proc.wait()
            errors.seek(0)
            message = errors.read().decode("utf-8", "replace").strip()

        if self._stop:
            return self._cancel(part)
        if code != 0:
            lines = [ln for ln in message.splitlines() if ln.strip()]
            raise ExportError("ffmpeg could not export the video.\n" + "\n".join(lines[-4:]))
        os.replace(part, self.out_path)
        self.progress.emit(1.0)
        self.done.emit(self.out_path)

    def _cancel(self, part):
        self._remove(part)
        self.cancelled.emit()

    @staticmethod
    def _remove(path):
        try:
            os.remove(path)
        except OSError:
            pass
