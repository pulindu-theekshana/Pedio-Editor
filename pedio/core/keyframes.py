"""Keyframes: a list of (time, value) points for one animated property.

Times are timeline seconds (the same clock as the playhead and the text / image / audio layers).
Every effect keeps its plain value on the layer (`x`, `size`, `volume` ...); an `anim` dict on the
layer maps a property name to a list of Keyframes. With no keyframes the plain value is used, so
projects made before keyframes still load and export exactly as before.

`value_at()` is the ONE place that turns keyframes into a value at time t; the preview (Qt / mpv / ASS)
and the export (ffmpeg) both read it, so they cannot drift apart.
"""
from dataclasses import dataclass

EASINGS = ("linear", "hold", "ease")


@dataclass
class Keyframe:
    t: float
    value: float
    easing: str = "linear"     # how to go from THIS keyframe to the next one


def _clean_easing(easing):
    return easing if easing in EASINGS else "linear"


def value_at(keyframes, t, default):
    """The value of one property at timeline time t. Empty = default; before / after the ends = the end value."""
    if not keyframes:
        return default
    points = sorted(keyframes, key=lambda k: k.t)
    if t <= points[0].t:
        return points[0].value
    if t >= points[-1].t:
        return points[-1].value
    for a, b in zip(points, points[1:]):
        if a.t <= t <= b.t:
            span = b.t - a.t
            if span <= 1e-9 or a.easing == "hold":
                return a.value
            u = (t - a.t) / span
            if a.easing == "ease":
                u = u * u * (3.0 - 2.0 * u)     # smoothstep
            return a.value + (b.value - a.value) * u
    return points[-1].value


def set_keyframe(keyframes, t, value, easing="linear"):
    """Add or replace the keyframe at time t. Returns the list sorted by time."""
    t = max(float(t), 0.0)
    value = float(value)
    easing = _clean_easing(easing)
    out = [k for k in keyframes if abs(k.t - t) > 1e-6]
    out.append(Keyframe(t, value, easing))
    out.sort(key=lambda k: k.t)
    return out


def remove_keyframe(keyframes, t):
    """Remove the keyframe at time t (within a millisecond). Returns the new list."""
    return [k for k in keyframes if abs(k.t - float(t)) > 0.001]


def nearest_keyframe(keyframes, t, tolerance=0.02):
    """The keyframe close to t, or None (used by the panel to update the point under the playhead)."""
    for k in keyframes:
        if abs(k.t - float(t)) <= tolerance:
            return k
    return None


def key_times(anim):
    """Every time used by any property of one layer / clip, sorted."""
    out = set()
    for points in (anim or {}).values():
        out.update(round(k.t, 6) for k in points)
    return sorted(out)


def property_times(anim, prop):
    return sorted(k.t for k in (anim or {}).get(prop, []))


def copy_anim(anim):
    """A deep enough copy for undo snapshots."""
    return {prop: [Keyframe(k.t, k.value, k.easing) for k in points]
            for prop, points in (anim or {}).items() if points}


def anim_to_dict(anim):
    return {prop: [{"t": k.t, "value": k.value, "easing": k.easing} for k in points]
            for prop, points in (anim or {}).items() if points}


def anim_from_dict(data):
    out = {}
    for prop, points in (data or {}).items():
        if not isinstance(points, list):
            continue
        out[str(prop)] = [Keyframe(float(p.get("t", 0.0)), float(p.get("value", 0.0)),
                                   _clean_easing(p.get("easing", "linear")))
                          for p in points if isinstance(p, dict)]
    return out


def expression(keyframes, start, end, default, shift=0.0, var="t"):
    """A piecewise-linear ffmpeg expression of this track between start and end (timeline seconds).
    `shift` is added to the filter's local time (e.g. an audio clip delayed to its timeline start).
    `var` is the filter's time variable (`t` for most filters, `T` for geq)."""
    points = sorted((k for k in (keyframes or []) if start - 1e-6 <= k.t <= end + 1e-6),
                    key=lambda k: k.t)
    if not points:
        return f"{float(default):.6f}"
    if len(points) == 1:
        return f"{points[0].value:.6f}"
    out = f"{points[-1].value:.6f}"
    for a, b in reversed(list(zip(points, points[1:]))):
        ta, tb = a.t + shift, b.t + shift
        span = max(tb - ta, 1e-6)
        if a.easing == "hold":
            part = f"{a.value:.6f}"
        else:
            part = f"({a.value:.6f}+({b.value - a.value:.6f})*({var}-{ta:.6f})/{span:.6f})"
        out = f"if(lt({var},{tb:.6f}),{part},{out})"
    if points[0].t + shift > 0:
        out = f"if(lt({var},{points[0].t + shift:.6f}),{points[0].value:.6f},{out})"
    return out
