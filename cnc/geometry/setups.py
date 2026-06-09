"""Estimate fixturing setups + undercut fraction from face normals.

On a 3-axis mill each fixturing orientation reaches the up-facing direction and
the perpendicular walls; the part must be re-fixtured ("flipped") to reach the
opposite face. We bin the surface area into the six ±axis directions and infer:

    * setup_dirs    – how many of the 6 principal directions carry real area
    * axes_flipped  – axes featured on BOTH sides (each forces a flip)
    * undercut_frac – area facing none of the 6 axes (≈ freeform / needs 5-axis)

Bounded + dependency-light (numpy only); returns None on failure so callers
fall back to the bbox heuristic.
"""
from __future__ import annotations

import io
import math

_ALIGN_DEG = 35.0          # normal within this of an axis ⇒ reachable from it
_SIGNIFICANT = 0.06        # area fraction to count a direction as "featured"


def analyze_setups(stl_bytes: bytes, *, max_faces: int = 200000) -> dict | None:
    try:
        import numpy as np
        import trimesh
    except ImportError:
        return None
    try:
        mesh = trimesh.load(io.BytesIO(stl_bytes), file_type="stl", process=True)
    except Exception:
        return None
    if not hasattr(mesh, "faces") or len(mesh.faces) < 4 or len(mesh.faces) > max_faces:
        return None
    try:
        n = np.asarray(mesh.face_normals, dtype=np.float64)
        a = np.asarray(mesh.area_faces, dtype=np.float64)
    except Exception:
        return None
    total = float(a.sum())
    if total <= 0:
        return None

    axes = np.eye(3)
    cos_t = math.cos(math.radians(_ALIGN_DEG))
    dir_area = {}                      # (axis, sign) -> area fraction
    aligned_any = np.zeros(len(n), dtype=bool)
    for ax in range(3):
        for sgn in (1.0, -1.0):
            d = axes[ax] * sgn
            m = (n @ d) > cos_t
            aligned_any |= m
            dir_area[(ax, sgn)] = float(a[m].sum()) / total

    setup_dirs = sum(1 for f in dir_area.values() if f > _SIGNIFICANT)
    axes_flipped = sum(
        1 for ax in range(3)
        if dir_area[(ax, 1.0)] > _SIGNIFICANT and dir_area[(ax, -1.0)] > _SIGNIFICANT
    )
    undercut_frac = float(a[~aligned_any].sum()) / total
    return {"setup_dirs": setup_dirs, "axes_flipped": axes_flipped,
            "undercut_frac": round(undercut_frac, 3)}


def estimate_setups_3axis(info: dict | None) -> int | None:
    """Map the mesh analysis to a 3-axis setup count (None if unavailable)."""
    if not info:
        return None
    # One flip reaches the opposite side and the four walls, so a plain prismatic
    # block is ~2 setups; extra setups come from undercuts / genuine multi-face
    # complexity, not merely from a box having six faces.
    base = 1 + (1 if info["axes_flipped"] >= 1 else 0)
    if info["undercut_frac"] > 0.15:
        base += 1
    if info["setup_dirs"] >= 5 and info["undercut_frac"] > 0.05:
        base += 1
    return max(1, min(4, base))
