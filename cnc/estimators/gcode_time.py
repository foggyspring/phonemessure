"""G-code cycle-time estimator.

Reusable across any CAM output (FreeCAD Path, HeeksCNC, LinuxCNC, hand G-code):
parse the program, walk the moves, and integrate time = distance / feed for cut
moves and distance / rapid for G0, with an optional trapezoidal accel model so
short segments (which never reach commanded feed) are not under-estimated.

Supports the common modal subset: G20/G21 units, G90/G91, G0/G1 linear,
G2/G3 arcs (approximated by chord+radius arc length), F feedrate, and X/Y/Z/I/J.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

_TOKEN = re.compile(r"([A-Za-z])\s*([-+]?[0-9]*\.?[0-9]+)")


@dataclass
class GcodeTime:
    seconds: float
    cut_seconds: float
    rapid_seconds: float
    cut_distance_mm: float
    rapid_distance_mm: float
    moves: int

    @property
    def minutes(self) -> float:
        return self.seconds / 60.0


def _seg_time(dist: float, feed_mm_min: float, accel_mm_s2: float | None) -> float:
    """Time for one linear segment at a target feed, optional trapezoidal accel.

    Without accel: t = dist / feed. With accel we assume start/end at rest
    (conservative per-segment) using a trapezoidal/triangular profile.
    """
    feed_mm_s = max(feed_mm_min, 1e-6) / 60.0
    if dist <= 0:
        return 0.0
    if not accel_mm_s2 or accel_mm_s2 <= 0:
        return dist / feed_mm_s
    d_accel = feed_mm_s ** 2 / accel_mm_s2          # distance to reach feed then brake
    if dist >= d_accel:                              # trapezoid: accel + cruise + decel
        t_ramp = 2.0 * feed_mm_s / accel_mm_s2
        d_cruise = dist - d_accel
        return t_ramp + d_cruise / feed_mm_s
    # triangle: never reaches commanded feed
    v_peak = math.sqrt(dist * accel_mm_s2)
    return 2.0 * v_peak / accel_mm_s2


def estimate_gcode(
    text: str,
    *,
    rapid_mm_min: float = 12000.0,
    default_feed_mm_min: float = 600.0,
    accel_mm_s2: float | None = 500.0,
    arc_segments: int = 24,
) -> GcodeTime:
    units = 1.0          # mm
    absolute = True
    feed = default_feed_mm_min
    x = y = z = 0.0
    cut_t = rapid_t = 0.0
    cut_d = rapid_d = 0.0
    moves = 0

    for raw in text.splitlines():
        line = raw.split(";", 1)[0]
        line = re.sub(r"\(.*?\)", "", line).strip()
        if not line:
            continue
        words = _TOKEN.findall(line)
        if not words:
            continue
        wd = {}
        gcodes = []
        for letter, val in words:
            L = letter.upper()
            v = float(val)
            if L == "G":
                gcodes.append(int(v))
            else:
                wd[L] = v

        for g in gcodes:
            if g == 20: units = 25.4
            elif g == 21: units = 1.0
            elif g == 90: absolute = True
            elif g == 91: absolute = False
        if "F" in wd:
            feed = wd["F"] * units

        motion = None
        for g in gcodes:
            if g in (0, 1, 2, 3):
                motion = g
        if motion is None and not any(k in wd for k in "XYZ"):
            continue
        if motion is None:
            continue

        nx = (wd.get("X", x / units) * units) if absolute else x + wd.get("X", 0.0) * units
        ny = (wd.get("Y", y / units) * units) if absolute else y + wd.get("Y", 0.0) * units
        nz = (wd.get("Z", z / units) * units) if absolute else z + wd.get("Z", 0.0) * units

        if motion in (2, 3):
            # Arc length from I/J centre offsets (XY plane) + helical Z.
            i = wd.get("I", 0.0) * units
            j = wd.get("J", 0.0) * units
            cx, cy = x + i, y + j
            r = math.hypot(i, j)
            a0 = math.atan2(y - cy, x - cx)
            a1 = math.atan2(ny - cy, nx - cx)
            da = a1 - a0
            if motion == 2:   # CW
                if da >= 0: da -= 2 * math.pi
            else:             # CCW
                if da <= 0: da += 2 * math.pi
            arc = abs(da) * r
            dist = math.hypot(arc, nz - z)
        else:
            dist = math.sqrt((nx - x) ** 2 + (ny - y) ** 2 + (nz - z) ** 2)

        if motion == 0:
            rapid_t += _seg_time(dist, rapid_mm_min, accel_mm_s2)
            rapid_d += dist
        else:
            cut_t += _seg_time(dist, feed, accel_mm_s2)
            cut_d += dist
        moves += 1
        x, y, z = nx, ny, nz

    return GcodeTime(
        seconds=cut_t + rapid_t,
        cut_seconds=cut_t,
        rapid_seconds=rapid_t,
        cut_distance_mm=cut_d,
        rapid_distance_mm=rapid_d,
        moves=moves,
    )
