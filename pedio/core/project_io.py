"""Save / open a Pedio project as one JSON file (`.pedio`).

The file stores plain data only (paths, times, transforms, keyframes). The media files themselves are
not copied, so a project refers to the videos / sounds / images where they are on disk.

The same dictionary form is used by the undo history (`core/history.py`), so every new effect only has
to be added in ONE place: here.
"""
import json
import os

from pedio.core.audio import AudioClip, reserve_ids as reserve_audio_ids
from pedio.core.clips import Clip, reserve_ids as reserve_clip_ids
from pedio.core.keyframes import anim_from_dict, anim_to_dict
from pedio.core.layers import ImageLayer, TextLayer, reserve_ids as reserve_layer_ids
from pedio.core.transform import Transform, clean_crop

VERSION = 2


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _transform(data):
    data = data or {}
    crop = data.get("crop")
    if isinstance(crop, (list, tuple)) and len(crop) == 4:
        crop = clean_crop(tuple(_number(v) for v in crop))
    else:
        crop = None
    rotate = int(_number(data.get("rotate", 0))) % 360
    rotate = round(rotate / 90) * 90 % 360
    return Transform(rotate, bool(data.get("flip", False)), crop)


def _transform_dict(transform):
    return {"rotate": transform.rotate, "flip": transform.flip,
            "crop": list(transform.crop) if transform.crop else None}


# ---------------------------------------------------------------------- project -> dict
def project_to_dict(project):
    """The whole edit as JSON-ready data. Used by save and by the undo history."""
    data = {
        "app": "Pedio Editor",
        "version": VERSION,
        "clips": [],
        "audio_clips": [],
        "text_layers": [],
        "image_layers": [],
        "selected_id": int(project.selected_id),
        "selected_audio_id": int(project.selected_audio_id),
        "selected_text_id": int(project.selected_text_id),
        "selected_image_id": int(project.selected_image_id),
    }
    for c in project.clips:
        data["clips"].append({
            "path": c.path,
            "src_duration": c.src_duration,
            "src_in": c.src_in,
            "src_out": c.src_out,
            "id": c.id,
            "size": list(c.size or (0, 0)),
            "meta_rotation": c.meta_rotation,
            "transform": _transform_dict(c.transform),
            "fade_in": c.fade_in,
            "fade_out": c.fade_out,
            "speed": c.speed,
            "anim": anim_to_dict(c.anim),
        })
    for c in project.audio_clips:
        data["audio_clips"].append({
            "path": c.path, "start": c.start, "src_in": c.src_in, "src_out": c.src_out,
            "volume": c.volume, "mute": c.mute, "fade_in": c.fade_in, "fade_out": c.fade_out,
            "id": c.id, "anim": anim_to_dict(c.anim),
        })
    for layer in project.text_layers:
        data["text_layers"].append({
            "text": layer.text, "start": layer.start, "end": layer.end,
            "x": layer.x, "y": layer.y, "size": layer.size, "color": layer.color,
            "outline": layer.outline, "outline_color": layer.outline_color,
            "shadow": layer.shadow, "shadow_color": layer.shadow_color,
            "opacity": layer.opacity, "anim": anim_to_dict(layer.anim), "id": layer.id,
        })
    for layer in project.image_layers:
        data["image_layers"].append({
            "path": layer.path, "start": layer.start, "end": layer.end,
            "x": layer.x, "y": layer.y, "scale": layer.scale, "opacity": layer.opacity,
            "anim": anim_to_dict(layer.anim), "id": layer.id,
        })
    return data


# ---------------------------------------------------------------------- dict -> project
def _make_clip(d):
    kwargs = dict(
        path=str(d.get("path", "")),
        src_duration=max(_number(d.get("src_duration")), 0.0),
        src_in=max(_number(d.get("src_in")), 0.0),
        src_out=max(_number(d.get("src_out")), 0.0),
        size=tuple(int(v) for v in (d.get("size") or (0, 0))[:2]),
        meta_rotation=int(_number(d.get("meta_rotation"))),
        transform=_transform(d.get("transform")),
        fade_in=max(_number(d.get("fade_in")), 0.0),
        fade_out=max(_number(d.get("fade_out")), 0.0),
        speed=min(max(_number(d.get("speed"), 1.0), 0.25), 4.0),
        anim=anim_from_dict(d.get("anim")),
    )
    if d.get("id"):
        kwargs["id"] = int(d["id"])
    return Clip(**kwargs)


def _make_audio(d):
    kwargs = dict(
        path=str(d.get("path", "")), start=max(_number(d.get("start")), 0.0),
        src_in=max(_number(d.get("src_in")), 0.0), src_out=max(_number(d.get("src_out")), 0.0),
        volume=min(max(_number(d.get("volume"), 1.0), 0.0), 2.0),
        mute=bool(d.get("mute", False)),
        fade_in=max(_number(d.get("fade_in")), 0.0),
        fade_out=max(_number(d.get("fade_out")), 0.0),
        anim=anim_from_dict(d.get("anim")),
    )
    if d.get("id"):
        kwargs["id"] = int(d["id"])
    return AudioClip(**kwargs)


def _make_text(d):
    kwargs = dict(
        text=str(d.get("text", "Text")), start=max(_number(d.get("start")), 0.0),
        end=max(_number(d.get("end"), 3.0), 0.0), x=_number(d.get("x"), 0.5),
        y=_number(d.get("y"), 0.5), size=int(_number(d.get("size"), 64)),
        color=str(d.get("color", "#ffffff")), outline=int(_number(d.get("outline"), 3)),
        outline_color=str(d.get("outline_color", "#000000")),
        shadow=int(_number(d.get("shadow"), 2)),
        shadow_color=str(d.get("shadow_color", "#000000")),
        opacity=min(max(_number(d.get("opacity"), 1.0), 0.0), 1.0),
        anim=anim_from_dict(d.get("anim")),
    )
    if d.get("id"):
        kwargs["id"] = int(d["id"])
    return TextLayer(**kwargs)


def _make_image(d):
    kwargs = dict(
        path=str(d.get("path", "")), start=max(_number(d.get("start")), 0.0),
        end=max(_number(d.get("end"), 3.0), 0.0), x=_number(d.get("x"), 0.5),
        y=_number(d.get("y"), 0.5), scale=min(max(_number(d.get("scale"), 0.5), 0.05), 2.0),
        opacity=min(max(_number(d.get("opacity"), 1.0), 0.0), 1.0),
        anim=anim_from_dict(d.get("anim")),
    )
    if d.get("id"):
        kwargs["id"] = int(d["id"])
    return ImageLayer(**kwargs)


def apply_project_dict(project, data):
    """Replace the edit in `project` with this dictionary and tell the UI."""
    clips = [_make_clip(d) for d in data.get("clips", []) if isinstance(d, dict)]
    audio = [_make_audio(d) for d in data.get("audio_clips", []) if isinstance(d, dict)]
    texts = [_make_text(d) for d in data.get("text_layers", []) if isinstance(d, dict)]
    images = [_make_image(d) for d in data.get("image_layers", []) if isinstance(d, dict)]
    project.clips = clips
    project.audio_clips = audio
    project.text_layers = texts
    project.image_layers = images
    all_ids = [c.id for c in clips] + [c.id for c in audio] + [c.id for c in texts] + [c.id for c in images]
    top = max(all_ids or [0])
    reserve_clip_ids(top)
    reserve_audio_ids(top)
    reserve_layer_ids(top)
    project.selected_id = int(data.get("selected_id", 0) or 0)
    project.selected_audio_id = int(data.get("selected_audio_id", 0) or 0)
    project.selected_text_id = int(data.get("selected_text_id", 0) or 0)
    project.selected_image_id = int(data.get("selected_image_id", 0) or 0)
    if project.text_by_id(project.selected_text_id) is None:
        project.selected_text_id = 0
    if project.image_by_id(project.selected_image_id) is None:
        project.selected_image_id = 0
    if project.audio_by_id(project.selected_audio_id) is None:
        project.selected_audio_id = 0
    if project.index_of(project.selected_id) < 0:
        project.selected_id = 0
    project.changed.emit()
    project.selection_changed.emit()
    return project


# ---------------------------------------------------------------------- files
def save_project(project, path):
    """Write the edit to `path` atomically (`<name>.part`, then rename)."""
    data = project_to_dict(project)
    part = path + ".part"
    with open(part, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(part, path)


def read_project(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or "clips" not in data:
        raise ValueError("This is not a Pedio project file.")
    return data


def open_project(project, path):
    apply_project_dict(project, read_project(path))
    return project
