"""Automatic minimum wall-thickness from a triangle mesh (shot-ray method).

No OpenCASCADE / rtree / embree: a self-contained vectorised Möller–Trumbore
ray caster shoots a ray from each sampled face inward along -normal and measures
the distance to the opposite surface — the local solid thickness. A low
percentile of those distances is the min wall thickness, which the DFM/cost
layers use to auto-flag thin walls even when the user declared nothing.

Bounded by design (face cap + sample cap + time budget); returns None when it
can't produce a trustworthy estimate, in which case callers simply skip it.
"""
from __future__ import annotations

import time

from .mesh import _looks_binary, _parse_ascii, _parse_binary


def _first_hit_distance(o, d, v0, e1, e2, np):
    """First positive ray/triangle intersection distance over all faces (vectorised)."""
    eps = 1e-9
    h = np.cross(d, e2)
    a = np.einsum("ij,ij->i", e1, h)
    mask = np.abs(a) > eps
    inv = np.zeros_like(a)
    inv[mask] = 1.0 / a[mask]
    s = o - v0
    u = inv * np.einsum("ij,ij->i", s, h)
    q = np.cross(s, e1)
    v = inv * (q @ d)
    t = inv * np.einsum("ij,ij->i", e2, q)
    hit = mask & (u >= -1e-6) & (u <= 1 + 1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6) & (t > 1e-6)
    ts = t[hit]
    return float(ts.min()) if ts.size else float("inf")


def estimate_min_wall_mm(
    stl_bytes: bytes, *, max_faces: int = 60000, max_samples: int = 320,
    time_budget_s: float = 2.5, percentile: float = 2.0,
) -> float | None:
    try:
        import numpy as np
    except ImportError:
        return None
    try:
        tris = (_parse_binary(stl_bytes) if _looks_binary(stl_bytes)
                else _parse_ascii(stl_bytes.decode("utf-8", errors="replace")))
    except (ValueError, IndexError):
        return None
    if len(tris) < 4:
        return None
    V = np.asarray(tris, dtype=np.float64)        # (F,3,3)
    F = V.shape[0]
    if F > max_faces:                             # too big → stay bounded, skip
        return None
    v0, v1, v2 = V[:, 0, :], V[:, 1, :], V[:, 2, :]
    e1, e2 = v1 - v0, v2 - v0
    n = np.cross(e1, e2)
    norm = np.linalg.norm(n, axis=1)
    good = norm > 1e-12
    if good.sum() < 4:
        return None
    normals = np.zeros_like(n)
    normals[good] = n[good] / norm[good, None]
    centers = (v0 + v1 + v2) / 3.0
    areas = 0.5 * norm

    idx = np.flatnonzero(good)
    if idx.size > max_samples:
        p = areas[idx] / areas[idx].sum()         # area-weighted sampling
        idx = np.random.default_rng(0).choice(idx, size=max_samples, replace=False, p=p)

    pts = V.reshape(-1, 3)
    diag = float(np.linalg.norm(pts.max(0) - pts.min(0)))
    if diag <= 0:
        return None
    eps = max(diag * 1e-5, 1e-4)
    origins = centers[idx] - normals[idx] * eps   # just inside the surface
    dirs = -normals[idx]                          # shoot into the solid

    thick: list[float] = []
    t0 = time.time()
    for o, d in zip(origins, dirs):
        if time.time() - t0 > time_budget_s:
            break
        dist = _first_hit_distance(o, d, v0, e1, e2, np)
        if dist != float("inf") and dist > eps * 3:
            thick.append(dist)
    if len(thick) < 10:
        return None
    return round(float(np.percentile(np.asarray(thick), percentile)), 3)
