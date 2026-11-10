"""The audio lane: sound clips placed on the timeline next to the video. No widgets here.

An AudioClip is plain data: a part of a sound file (src_in .. src_out), where it starts on the timeline
(`start`), a volume and a mute flag. The ffmpeg filter for one clip is built in ONE place here
(`clip_filter`), so the export and, later, the preview mix the sound the same way.
Later fades (Step 7d) become two more fields on AudioClip and two more filters in `clip_filter`.
"""
from dataclasses import dataclass, field, replace
from itertools import count

AUDIO_RATE = 48000      # every sound is brought to this rate (and stereo) before it is mixed
MAX_VOLUME = 2.0        # 1.0 = original loudness, 2.0 = twice as loud
MIN_AUDIO = 0.1         # shortest audio clip allowed, in seconds

_next_id = count(1)     # audio ids are unique inside the audio lane only (video clips have their own ids)


@dataclass
class AudioClip:
    path: str                 # any file with a sound stream (MP3, WAV, AAC ... or a video)
    src_duration: float       # length of the whole source file
    src_in: float             # where the clip starts inside the source file
    src_out: float            # where the clip ends inside the source file
    start: float = 0.0        # where the clip starts on the timeline, in seconds
    volume: float = 1.0       # 0 .. MAX_VOLUME
    mute: bool = False
    id: int = field(default_factory=lambda: next(_next_id))

    @property
    def length(self):
        return self.src_out - self.src_in

    @property
    def end(self):
        """Where the clip ends on the timeline."""
        return self.start + self.length


def audible(clips, total):
    """The clips that are heard in an edit that is `total` seconds long: not muted, not starting after the end.
    A clip that runs past the end is cut there. Returns new AudioClip objects (the originals are not changed)."""
    out = []
    for c in clips:
        room = total - c.start
        if c.mute or c.volume <= 0 or room < 0.001:
            continue
        length = min(c.length, room)
        out.append(replace(c, src_out=c.src_in + length))
    return out


def clip_filter(clip, input_index, label):
    """ffmpeg filter chain for one audio clip: `[input_index:a:0]...[label]`.
    Brings the sound to the common format, sets the volume, and delays it to its place on the timeline.
    The caller cuts the part (src_in / length) with `-ss / -t` on the input."""
    chain = [f"aresample={AUDIO_RATE}", "aformat=sample_fmts=fltp:channel_layouts=stereo",
             "asetpts=PTS-STARTPTS"]
    if abs(clip.volume - 1.0) > 1e-3:
        chain.append(f"volume={clip.volume:.4f}")
    delay = round(clip.start * 1000)          # adelay takes milliseconds, one value per channel (stereo)
    if delay > 0:
        chain.append(f"adelay={delay}|{delay}")
    return f"[{input_index}:a:0]" + ",".join(chain) + f"[{label}]"
