"""Export the timeline to an MP4 file with ffmpeg. No widgets here: a pure command builder + a QThread worker.

The clip list is `Project.play_list()` = [Segment(path, start, length, transform, ...)], the same list the
player plays, so what you see in the preview is what you get in the file.
"""
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from fractions import Fraction

from PySide6.QtCore import QThread, Signal

from pedio.core.audio import AudioClip
from pedio.core.clips import Segment
from pedio.core.layers import TextLayer, build_ass
from pedio.core.transform import output_size, stream_rotation, video_filters
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
    size: tuple = None            # (width, height); None = size of the first clip (after its crop / rotate)
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
    if stream_rotation(video) % 180 == 90:
        width, height = height, width          # ffmpeg turns the picture upright before the filters
    fps = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate")) or DEFAULT_FPS
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return SourceInfo(width, height, fps, has_audio)


def probe_audio(path):
    """Check that a source file has an audio stream. Raises ExportError."""
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        raise ExportError("ffprobe was not found. Run setup.bat again.")
    cmd = [ffprobe, "-v", "error", "-show_streams", "-of", "json", path]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        streams = json.loads(out.stdout.decode("utf-8", "replace") or "{}").get("streams", [])
    except Exception:
        streams = []
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise ExportError(f"Could not read audio from {os.path.basename(path)}. Was the file moved or deleted?")


def _copy_audio_clips(audio_clips):
    """Accept AudioClip instances or their field tuples, and snapshot the result."""
    return [replace(clip) if isinstance(clip, AudioClip) else AudioClip(*clip) for clip in audio_clips]


def _active_audio_clips(audio_clips, duration):
    """Tracks that can contribute audible samples to an export of ``duration`` seconds."""
    return [clip for clip in audio_clips if not clip.mute and clip.volume > 0
            and clip.length > 0 and clip.start < duration]


def _extra_audio_filter(clip):
    """Volume + fade in / out for one AudioClip, before it is delayed and mixed."""
    chain = (f"aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
             f"atrim=duration={clip.length:.6f},asetpts=PTS-STARTPTS,volume={clip.volume:.6f}")
    if clip.fade_in > 0:
        chain += f",afade=t=in:st=0:d={min(clip.fade_in, clip.length):.6f}"
    if clip.fade_out > 0:
        start = max(clip.length - clip.fade_out, 0.0)
        chain += f",afade=t=out:st={start:.6f}:d={min(clip.fade_out, clip.length):.6f}"
    return chain


def _segment_fades(seg):
    """Video fade from / to black at the ends of one segment (its PTS starts at 0)."""
    out = ""
    if seg.fade_in > 0:
        out += f",fade=t=in:st=0:d={min(seg.fade_in, seg.length):.6f}"
    if seg.fade_out > 0:
        out += f",fade=t=out:st={max(seg.length - seg.fade_out, 0.0):.6f}:d={min(seg.fade_out, seg.length):.6f}"
    return out


def _segment_audio_fades(seg):
    """The same fades for the clip's own sound (so the sound does not jump at the cut)."""
    out = ""
    if seg.fade_in > 0:
        out += f",afade=t=in:st=0:d={min(seg.fade_in, seg.length):.6f}"
    if seg.fade_out > 0:
        out += f",afade=t=out:st={max(seg.length - seg.fade_out, 0.0):.6f}:d={min(seg.fade_out, seg.length):.6f}"
    return out


# ---------------------------------------------------------------------- command
def build_command(ffmpeg, clips, sources, out_path, settings, audio_clips=(), text_layers=(), ass_name=None):
    """The ffmpeg command line. clips = [Segment] (3-tuples work too), sources = {path: SourceInfo}.

    Every clip is cut with input seeking (-ss / -t), mirrored / rotated / cropped (video_filters, the same
    filters the preview uses), brought to one size / frame rate / audio format, then joined with the
    concat filter. A clip without sound gets silence of the same length.
    The output size is the size of the first clip after its transform; the other clips are fitted into it.
    Extra audio clips are placed at their timeline start and mixed over the video's original sound.
    Text layers are drawn by the `ass` filter from a file the worker writes (`ass_name`, relative to its cwd);
    the same ASS file is shown by the preview.
    """
    clips = [Segment(*c) for c in clips]
    audio_clips = _active_audio_clips(_copy_audio_clips(audio_clips), sum(c.length for c in clips))
    first = sources[clips[0].path]
    width, height = settings.size or output_size(first.width, first.height, clips[0].transform)
    width, height = width - width % 2, height - height % 2          # yuv420p needs even numbers
    fps = settings.fps or first.fps

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    parts, joined = [], ""
    for i, seg in enumerate(clips):
        path, length = seg.path, seg.length
        cmd += ["-ss", f"{seg.start:.6f}", "-t", f"{length:.6f}", "-i", path]
        # ffmpeg turns phone videos upright by itself, so meta_rotation stays 0 here (the player has to do it)
        look = "".join(f + "," for f in video_filters(seg.transform))
        fade = _segment_fades(seg)
        parts.append(
            f"[{i}:v:0]{look}scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"fps={fps.numerator}/{fps.denominator},format=yuv420p,setpts=PTS-STARTPTS{fade}[v{i}]")
        if sources[path].has_audio:
            audio = f"[{i}:a:0]aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,apad"
        else:
            audio = f"anullsrc=r={AUDIO_RATE}:cl=stereo"
        parts.append(f"{audio},atrim=duration={length:.6f},asetpts=PTS-STARTPTS{_segment_audio_fades(seg)}[a{i}]")
        joined += f"[v{i}][a{i}]"
    parts.append(f"{joined}concat=n={len(clips)}:v=1:a=1[v][basea]")
    video_out = "[v]"
    if ass_name and text_layers:
        parts.append(f"[v]ass={ass_name}[vtext]")
        video_out = "[vtext]"

    if audio_clips:
        mixed = "[basea]"
        for i, clip in enumerate(audio_clips, start=len(clips)):
            cmd += ["-ss", f"{clip.src_in:.6f}", "-t", f"{clip.length:.6f}", "-i", clip.path]
            delay_ms = max(0, round(clip.start * 1000))
            parts.append(f"[{i}:a:0]{_extra_audio_filter(clip)},adelay={delay_ms}:all=1[extra{i}]")
            mixed += f"[extra{i}]"
        parts.append(f"{mixed}amix=inputs={len(audio_clips) + 1}:duration=first:normalize=0[a]")
    else:
        parts.append("[basea]anull[a]")

    cmd += [
        "-filter_complex", ";".join(parts), "-map", video_out, "-map", "[a]",
        "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", settings.audio_bitrate,
        "-movflags", "+faststart", "-f", "mp4",
        "-progress", "pipe:1", "-nostats", out_path,
    ]
    return cmd


def build_audio_mix_command(ffmpeg, clips, sources, out_path, audio_clips=()):
    """Audio-only mix of the whole timeline (video sound + extra audio clips), for the preview player."""
    clips = [Segment(*c) for c in clips]
    total = sum(c.length for c in clips)
    audio_clips = _active_audio_clips(_copy_audio_clips(audio_clips), total)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    parts, joined = [], ""
    for i, seg in enumerate(clips):
        cmd += ["-ss", f"{seg.start:.6f}", "-t", f"{seg.length:.6f}", "-i", seg.path]
        if sources[seg.path].has_audio:
            audio = f"[{i}:a:0]aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,apad"
        else:
            audio = f"anullsrc=r={AUDIO_RATE}:cl=stereo"
        parts.append(f"{audio},atrim=duration={seg.length:.6f},asetpts=PTS-STARTPTS"
                     f"{_segment_audio_fades(seg)}[a{i}]")
        joined += f"[a{i}]"
    parts.append(f"{joined}concat=n={len(clips)}:v=0:a=1[basea]")
    if audio_clips:
        mixed = "[basea]"
        for i, clip in enumerate(audio_clips, start=len(clips)):
            cmd += ["-ss", f"{clip.src_in:.6f}", "-t", f"{clip.length:.6f}", "-i", clip.path]
            delay_ms = max(0, round(clip.start * 1000))
            parts.append(f"[{i}:a:0]{_extra_audio_filter(clip)},adelay={delay_ms}:all=1[extra{i}]")
            mixed += f"[extra{i}]"
        parts.append(f"{mixed}amix=inputs={len(audio_clips) + 1}:duration=first:normalize=0[a]")
    else:
        parts.append("[basea]anull[a]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[a]", "-c:a", "aac", "-b:a", "160k", "-f", "mp4", out_path]
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

    def __init__(self, clips, out_path, settings=None, parent=None, audio_clips=None, text_layers=None):
        super().__init__(parent)
        self.clips = [Segment(*c) for c in clips]     # copy: later edits do not change a running export
        self.audio_clips = _copy_audio_clips(audio_clips or [])
        self.text_layers = [layer.copy() if isinstance(layer, TextLayer) else TextLayer(*layer)
                            for layer in (text_layers or [])]
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
        part = os.path.abspath(self.out_path) + ".part"
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
        for seg in self.clips:
            if seg.path not in sources:
                if self._stop:
                    return self._cancel(part)
                sources[seg.path] = probe_source(seg.path)
        total = sum(seg.length for seg in self.clips)
        audio_clips = _active_audio_clips(self.audio_clips, total)
        for clip in audio_clips:
            if clip.path in sources:
                if not sources[clip.path].has_audio:
                    raise ExportError(f"Could not read audio from {os.path.basename(clip.path)}.")
            else:
                if self._stop:
                    return self._cancel(part)
                probe_audio(clip.path)

        ass_dir = None
        ass_name = None
        if self.text_layers:
            # A bare file name (relative to ffmpeg's cwd) keeps ':' and '\' of Windows paths out of the
            # filter graph. The preview shows the same kind of ASS file.
            ass_dir = tempfile.mkdtemp(prefix="pedio_text_")
            ass_name = "text.ass"
            with open(os.path.join(ass_dir, ass_name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(build_ass(self.text_layers))
        try:
            cmd = build_command(ffmpeg, self.clips, sources, part, self.settings, audio_clips,
                                self.text_layers, ass_name)
            with tempfile.TemporaryFile() as errors:  # a file, so ffmpeg can never block on a full pipe
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=errors, stdin=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW,
                    cwd=ass_dir)
                if self._stop:
                    self._proc.kill()
                for line in self._proc.stdout:
                    t = _out_time(line)
                    if t is not None and total > 0:
                        self.progress.emit(min(max(t / total, 0.0), 1.0))
                code = self._proc.wait()
                errors.seek(0)
                message = errors.read().decode("utf-8", "replace").strip()
        finally:
            if ass_dir:
                shutil.rmtree(ass_dir, ignore_errors=True)

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


class AudioMixWorker(QThread):
    """Builds the preview's audio track (video sound + audio clips) as a small temporary file.
    `ready` gives back the generation number, so an old job's result can be ignored."""

    ready = Signal(int, str)      # generation, path
    failed = Signal(int, str)     # generation, message

    def __init__(self, clips, audio_clips, out_path, generation, parent=None):
        super().__init__(parent)
        self.clips = [Segment(*c) for c in clips]
        self.audio_clips = _copy_audio_clips(audio_clips or [])
        self.out_path = out_path
        self.generation = generation
        self._proc = None

    def stop(self):
        proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    def run(self):
        try:
            ffmpeg = find_tool("ffmpeg")
            if not ffmpeg:
                raise ExportError("ffmpeg was not found. Run setup.bat again.")
            if not self.clips:
                raise ExportError("There is nothing to mix.")
            sources = {}
            for seg in self.clips:
                if seg.path not in sources:
                    sources[seg.path] = probe_source(seg.path)
            cmd = build_audio_mix_command(ffmpeg, self.clips, sources, self.out_path, self.audio_clips)
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW)
            _out, err = self._proc.communicate()
            if self._proc.returncode != 0:
                message = (err or b"").decode("utf-8", "replace").strip().splitlines()
                raise ExportError(message[-1] if message else "Could not build the preview audio.")
            self.ready.emit(self.generation, self.out_path)
        except Exception as e:
            try:
                os.remove(self.out_path)
            except OSError:
                pass
            self.failed.emit(self.generation, str(e))
