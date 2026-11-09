"""How the picture of one clip is changed: mirror, rotate, crop. No widgets here.

`Transform` is plain data kept on every `Clip`. `video_filters()` is the ONE place that turns it into
ffmpeg filters: the player (mpv `vf=lavfi=[...]`) and the export (`-filter_complex`) both use it,
so the preview and the exported file show the same picture.

Order: the picture is mirrored first (`flip`), then turned `rotate` degrees clockwise, then cropped.
`crop` is a box in the picture AFTER mirror + rotate (= the picture as you see it on the preview),
written as fractions of its width / height: (x, y, w, h), each 0..1, origin top-left.
"""
import math
from dataclasses import dataclass, replace

MIN_CROP = 0.05    # smallest crop box side, as a fraction of the picture

# Aspect ratio presets for the crop box and Video > Aspect Ratio: name -> (width, height)
ASPECT_PRESETS = {
    "16:9": (16, 9), "9:16": (9, 16), "4:3": (4, 3), "3:4": (3, 4),
    "1:1": (1, 1), "4:5": (4, 5), "21:9": (21, 9),
}

_TURN = {90: ["transpose=1"], 180: ["hflip", "vflip"], 270: ["transpose=2"]}   # clockwise turns


@dataclass(frozen=True)
class Transform:
    rotate: int = 0        # 0, 90, 180, 270 degrees clockwise (applied after `flip`)
    flip: bool = False     # mirror left-right (applied first)
    crop: tuple = None     # (x, y, w, h) fractions of the picture after flip + rotate; None = no crop

    @property
    def is_default(self):
        return self.rotate == 0 and not self.flip and self.crop is None

    # ---- changes as the user sees them on the preview. They return a new Transform.
    def turned(self, quarter_turns=1):
        """Turn the picture 90 degrees clockwise (x times). The crop box turns with the picture."""
        t = self
        for _ in range(quarter_turns % 4):
            t = replace(t, rotate=(t.rotate + 90) % 360, crop=_turn_box(t.crop))
        return t

    def mirrored(self, horizontal=True):
        """Mirror what is on the screen: left-right (horizontal) or top-bottom. The crop box mirrors too."""
        if horizontal:      # H after (flip, rotate r) = (flip toggled, rotate -r)
            return replace(self, flip=not self.flip, rotate=(-self.rotate) % 360, crop=_mirror_box(self.crop, True))
        # V = turn 180 + H, so (flip toggled, rotate 180 - r)
        return replace(self, flip=not self.flip, rotate=(180 - self.rotate) % 360, crop=_mirror_box(self.crop, False))

    def with_crop(self, rect):
        return replace(self, crop=clean_crop(rect))


def _turn_box(box):
    if box is None:
        return None
    x, y, w, h = box
    return (round(1 - y - h, 4), x, h, w)


def _mirror_box(box, horizontal):
    if box is None:
        return None
    x, y, w, h = box
    return (round(1 - x - w, 4), y, w, h) if horizontal else (x, round(1 - y - h, 4), w, h)


def clean_crop(rect):
    """A crop box that is safe to store: inside the picture, not tiny, 4 decimals.
    None (or a box that covers the whole picture) means "no crop"."""
    if rect is None:
        return None
    x, y, w, h = (min(max(float(v), 0.0), 1.0) for v in rect)
    w, h = max(w, MIN_CROP), max(h, MIN_CROP)
    x, y = min(x, 1.0 - w), min(y, 1.0 - h)
    x, y, w, h = round(x, 4), round(y, 4), round(w, 4), round(h, 4)
    w, h = round(min(w, 1.0 - x), 4), round(min(h, 1.0 - y), 4)
    if x < 0.001 and y < 0.001 and w > 0.999 and h > 0.999:
        return None
    return (x, y, w, h)


def centered_crop(width, height, ratio):
    """The biggest crop box with this ratio (w, h) that fits a picture of width x height pixels, centered."""
    frame, want = width / height, ratio[0] / ratio[1]
    w, h = (1.0, frame / want) if want >= frame else (want / frame, 1.0)
    return (round((1 - w) / 2, 4), round((1 - h) / 2, 4), round(w, 4), round(h, 4))


# ---------------------------------------------------------------------- filters
def video_filters(transform, meta_rotation=0, with_crop=True):
    """ffmpeg filters (a list) for this transform.

    meta_rotation: the turn a phone video asks for in its metadata (clockwise degrees). ffmpeg applies it
    by itself, so the export passes 0. mpv hands the raw picture to the filters (and then turns it by
    the metadata), so the player passes the real value and sets `video-rotate=no`.
    with_crop=False gives the picture before the crop (what the crop box is drawn on)."""
    out = list(_TURN.get(meta_rotation % 360, []))
    if transform.flip:
        out.append("hflip")
    out += _TURN.get(transform.rotate % 360, [])
    if with_crop and transform.crop:
        x, y, w, h = transform.crop
        # sizes and positions are even numbers (yuv420p); only * / and floor() here, so no commas to escape
        out.append(f"crop=w=floor(iw*{w:.4f}/2)*2:h=floor(ih*{h:.4f}/2)*2"
                   f":x=floor(iw*{x:.4f}/2)*2:y=floor(ih*{y:.4f}/2)*2")
    return out


def output_size(width, height, transform):
    """Size of the picture after the transform (same maths as the filters above).
    (width, height) = the picture as a player shows it, phone rotation already applied."""
    if transform.rotate % 180 == 90:
        width, height = height, width
    if transform.crop:
        _x, _y, w, h = transform.crop
        width, height = math.floor(width * w / 2) * 2, math.floor(height * h / 2) * 2
    return width, height


def stream_rotation(stream):
    """Clockwise degrees (0, 90, 180, 270) a player must turn this video stream to show it upright.
    `stream` is one entry of ffprobe's JSON. Phone videos store the picture sideways and say so here."""
    degrees = 0
    try:
        degrees = int(float(stream.get("tags", {}).get("rotate", 0)))          # old tag: clockwise
    except (TypeError, ValueError):
        pass
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            try:
                degrees = -int(float(side["rotation"]))                         # new side data: counterclockwise
            except (TypeError, ValueError):
                pass
    return round(degrees / 90) % 4 * 90
