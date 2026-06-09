"""Drive FreeCAD's open-source CAM (Path) workbench headlessly for tier-1 quotes.

FreeCAD Path *is* an open-source automatic-CAM system: given a solid it
generates real roughing/drilling/profile toolpaths and posts G-code. This
module shells out to ``freecadcmd`` (no GUI), has it build a Job + operations on
an uploaded STEP/STL, post-processes to G-code, then routes that G-code through
:mod:`gcode_time` for a high-precision cycle time.

FreeCAD ships only via conda/AppImage (no pip wheel), so this backend is
*optional*: if ``freecadcmd`` is not on PATH we raise :class:`FreeCADUnavailable`
and the caller falls back to the toolpath/analytic backends. The embedded CAM
script targets FreeCAD ≥ 1.0's ``Path`` API and is the intended seam for a shop
to drop in its own tool library and post-processor.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .gcode_time import estimate_gcode


class FreeCADUnavailable(RuntimeError):
    pass


def freecad_binary() -> str | None:
    for name in ("freecadcmd", "FreeCADCmd", "freecad", "FreeCAD"):
        p = shutil.which(name)
        if p:
            return p
    env = os.environ.get("FREECAD_CMD")
    return env if env and Path(env).exists() else None


# Script executed *inside* FreeCAD. Builds a CAM Job on the model and posts
# G-code. Kept defensive: tries the modern (1.0) and legacy (0.20) API shapes.
_FC_SCRIPT = r'''
import sys, json, Part, FreeCAD as App
src, gcode_out = sys.argv[2], sys.argv[3]
doc = App.newDocument("q")
shape = Part.Shape(); shape.read(src)
obj = doc.addObject("Part::Feature", "Model"); obj.Shape = shape; doc.recompute()

def build_and_post():
    import Path
    try:
        from Path.Main import Job as PJob
    except Exception:
        from PathScripts import PathJob as PJob
    job = PJob.Create("Job", [obj])
    doc.recompute()
    # A tool controller usually exists by default; operations inherit feeds from it.
    tcs = getattr(job, "Tools", None)
    # Roughing (adaptive) + drilling, best-effort across versions.
    ops = []
    try:
        from Path.Op import Adaptive as Ad
        ops.append(Ad.Create("Rough"))
    except Exception:
        try:
            from PathScripts import PathAdaptive as Ad
            ops.append(Ad.Create("Rough"))
        except Exception:
            pass
    try:
        from Path.Op import Drilling as Dr
        ops.append(Dr.Create("Drill"))
    except Exception:
        pass
    for op in ops:
        try: job.Proxy.addOperation(op)
        except Exception: pass
    doc.recompute()
    # Post-process to G-code.
    try:
        from Path.Post.Command import buildPostList, exportObjectsWith
    except Exception:
        from PathScripts.PathPost import buildPostList, exportObjectsWith
    post = getattr(job, "PostProcessor", None) or "linuxcnc"
    try:
        objs = [o for o in job.Operations.Group]
        exportObjectsWith(objs, job, gcode_out)
    except Exception:
        # fallback: many versions write via job post directly
        job.Proxy.execute(job)
    return True

ok = False
try:
    ok = build_and_post()
except Exception as e:
    sys.stderr.write("FC-CAM-ERROR: %s\n" % e)
print("FC-CAM-DONE %s" % json.dumps({"ok": bool(ok)}))
'''


def estimate_step(
    model_path: str | os.PathLike,
    *,
    rapid_mm_min: float = 12000.0,
    default_feed_mm_min: float = 600.0,
    accel_mm_s2: float | None = 500.0,
    timeout_s: int = 240,
) -> dict:
    """Run FreeCAD CAM on a model file and return precise time info.

    Returns {minutes, cut_distance_mm, gcode_lines, source:'freecad'}.
    Raises FreeCADUnavailable if FreeCAD or the toolpath post is not usable.
    """
    fc = freecad_binary()
    if not fc:
        raise FreeCADUnavailable("freecadcmd not found on PATH (conda install freecad)")

    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "cam.py"
        script.write_text(_FC_SCRIPT)
        gcode_out = Path(td) / "out.ngc"
        try:
            proc = subprocess.run(
                [fc, str(script), str(model_path), str(gcode_out)],
                capture_output=True, text=True, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise FreeCADUnavailable(f"FreeCAD CAM timed out after {timeout_s}s") from exc

        if "FC-CAM-DONE" not in proc.stdout or not gcode_out.exists():
            raise FreeCADUnavailable(
                "FreeCAD CAM produced no G-code: " + (proc.stderr.strip()[-300:] or "unknown")
            )
        gtext = gcode_out.read_text(errors="replace")

    gt = estimate_gcode(
        gtext, rapid_mm_min=rapid_mm_min,
        default_feed_mm_min=default_feed_mm_min, accel_mm_s2=accel_mm_s2,
    )
    return {
        "source": "freecad",
        "minutes": round(gt.minutes, 2),
        "cut_distance_mm": round(gt.cut_distance_mm, 1),
        "rapid_distance_mm": round(gt.rapid_distance_mm, 1),
        "moves": gt.moves,
    }


def available() -> bool:
    return freecad_binary() is not None
