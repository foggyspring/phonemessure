"""Automatic cylindrical-hole recognition from a triangle mesh.

No BREP/OpenCASCADE: groups the mesh into smoothly-connected surface patches,
fits a cylinder to each (axis = the direction the face normals are
perpendicular to; radius/centre by an algebraic circle fit), and keeps the
patches that are genuinely concave, round and well-covered — i.e. holes, not
fillets or bosses. Returns grouped {diameter_mm, depth_mm, count, through}.

Conservative by design (good-circle + concavity + angular-coverage gates) so it
rarely false-positives, and bounded (face cap) so it stays fast. Auto-detected
holes feed drilling cost + DFM only when the customer declared none; declared
holes always win.
"""
from __future__ import annotations

import io
import math

_SMOOTH_ANGLE_DEG = 40.0
_MIN_FACES = 6
_MAX_FACES = 80000


def detect_holes(stl_bytes: bytes, *, max_faces: int = _MAX_FACES) -> list[dict]:
    try:
        import numpy as np
        import trimesh
        from trimesh.graph import connected_component_labels
    except ImportError:
        return []
    try:
        mesh = trimesh.load(io.BytesIO(stl_bytes), file_type="stl", process=True)
    except Exception:
        return []
    if not hasattr(mesh, "faces") or len(mesh.faces) < 8 or len(mesh.faces) > max_faces:
        return []

    try:
        N = np.asarray(mesh.face_normals, dtype=np.float64)
        C = np.asarray(mesh.triangles_center, dtype=np.float64)
        adj = np.asarray(mesh.face_adjacency)
        ang = np.asarray(mesh.face_adjacency_angles)
    except Exception:
        return []
    if adj.size == 0:
        return []

    smooth = adj[ang < math.radians(_SMOOTH_ANGLE_DEG)]
    labels = connected_component_labels(smooth, node_count=len(mesh.faces))
    found: list[tuple[float, float, bool]] = []

    for lab in np.unique(labels):
        idx = np.flatnonzero(labels == lab)
        if idx.size < _MIN_FACES:
            continue
        n, c = N[idx], C[idx]
        # axis ⟂ all normals → smallest-eigenvalue eigenvector of Σ nnᵀ.
        w, v = np.linalg.eigh(n.T @ n)
        if w[2] <= 0 or w[0] / w[2] > 0.06 or w[1] / w[2] < 0.15:
            continue                       # planar (rank 1) or spherical → not a cylinder
        axis = v[:, 0]
        b1, b2 = v[:, 1], v[:, 2]
        x, y = c @ b1, c @ b2
        # algebraic (Kåsa) circle fit in the cross-section plane
        A = np.c_[2 * x, 2 * y, np.ones_like(x)]
        try:
            sol, *_ = np.linalg.lstsq(A, x * x + y * y, rcond=None)
        except np.linalg.LinAlgError:
            continue
        cx, cy = sol[0], sol[1]
        r2 = sol[2] + cx * cx + cy * cy
        if r2 <= 0:
            continue
        r = math.sqrt(r2)
        if r < 0.3 or r > 200.0:
            continue
        rho = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
        if np.mean(np.abs(rho - r)) > 0.15 * r:
            continue                       # not round enough
        # concavity: do the wall normals point toward the axis (hole) or away (boss)?
        radial = np.c_[x - cx, y - cy]
        radial /= (np.linalg.norm(radial, axis=1, keepdims=True) + 1e-9)
        npl = np.c_[n @ b1, n @ b2]
        npl /= (np.linalg.norm(npl, axis=1, keepdims=True) + 1e-9)
        if np.mean(-(radial * npl).sum(1)) < 0.5:
            continue                       # convex → boss/stud, not a hole
        # angular coverage — a real hole wraps most of the way round
        th = np.sort(np.arctan2(y - cy, x - cx))
        gaps = np.diff(np.r_[th, th[0] + 2 * math.pi])
        if (2 * math.pi - gaps.max()) < math.radians(200):
            continue                       # fillet / partial arc, not a bore
        verts = mesh.vertices[np.unique(mesh.faces[idx])]
        depth = float(np.ptp(verts @ axis))
        # through if the bore spans the part along its axis
        part_extent = float(np.ptp(mesh.vertices @ axis))
        through = depth > 0.85 * part_extent
        if depth < 0.5:
            continue
        found.append((2 * r, depth, through))

    # group near-identical holes → {diameter, depth, count, through}
    groups: dict[tuple, dict] = {}
    for dia, depth, through in found:
        key = (round(dia, 1), round(depth, 1))
        g = groups.setdefault(key, {"diameter_mm": round(dia, 2), "depth_mm": round(depth, 2),
                                     "count": 0, "through": through})
        g["count"] += 1
        g["through"] = g["through"] and through
    return sorted(groups.values(), key=lambda h: h["diameter_mm"])
