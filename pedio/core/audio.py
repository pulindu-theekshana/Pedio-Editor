"""Audio clips placed on the edit timeline. No widgets or ffmpeg code live here."""
from dataclasses import dataclass, field
from itertools import count

from pedio.core.keyframes import value_at

_next_id = count(1)


def reserve_ids(max_id):
    global _next_id
    _next_id = count(int(max_id) + 1)


@dataclass
class AudioClip:
    """One source-audio range placed at ``start`` seconds on the timeline."""
    path: str
    start: float
    src_in: float
    src_out: float
    volume: float = 1.0
    mute: bool = False
    fade_in: float = 0.0     # seconds of fade in at the clip start (0 .. 2)
    fade_out: float = 0.0    # seconds of fade out at the clip end (0 .. 2)
    id: int = field(default_factory=lambda: next(_next_id))
    anim: dict = field(default_factory=dict)   # property -> [Keyframe] (volume; times are timeline seconds)

    @property
    def length(self):
        return self.src_out - self.src_in

    @property
    def end(self):
        return self.start + self.length

    def volume_at(self, t):
        return value_at(self.anim.get("volume"), t, self.volume)
