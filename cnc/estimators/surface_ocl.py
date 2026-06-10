"""Surface-finishing refinement via opencamlib drop-cutter (optional).

The base toolpath estimator approximates top-surface finishing as a flat raster
(footprint_area / stepover). That under-counts sculpted/curved tops, whose real
3-axis ball-nose path is longer because it rides up and down the slopes.

opencamlib is a real CAM toolpath kernel: we drop a ball cutter onto the actual
triangle mesh along a set of probe scan-lines and measure how much longer the
true 3D path is than its flat XY projection — a "curvature factor" (>= 1). The
finishing estimate then becomes  raster_len_flat * curvature_factor, so a flat
lid stays ~1.0 while a dome/freeform top is correctly inflated.

Cost is bounded: a fixed number of probe lines regardless of the real stepover,
so big parts don't blow up. If opencamlib is missing this module reports
``available() == False`` and the caller keeps the flat raster.
"""
from __future__ import annotations

import math

_MAX_FACES = 300_000
# Probe density: the factor is a path-elongation RATIO, which converges fast —
# 32 lines @1.2mm sampling lands within ~1.6% of 64 @0.6mm (hemisphere/capsule
# benchmarks) at a quarter of the drop-cutter cost. It scales only the raster
# share of finishing, so the net price effect is well under 1%.
_PROBE_LINES = 32          # how many scan lines we actually drop-cutter
_MIN_SAMPLING = 1.2        # mm between sampled points along a line


def available() -> bool:
    try:
        import opencamlib  # noqa: F401
        return True
    except Exception:
        return False


def _build_surf(ocl, vertices, faces):
    s = ocl.STLSurf()
    P = ocl.Point
    add = s.addTriangle
    Tri = ocl.Triangle
    for f in faces:
        a, b, c = vertices[f[0]], vertices[f[1]], vertices[f[2]]
        add(Tri(P(float(a[0]), float(a[1]), float(a[2])),
                P(float(b[0]), float(b[1]), float(b[2])),
                P(float(c[0]), float(c[1]), float(c[2]))))
    return s


def curvature_factor(mesh, finish_d: float, stepover: float) -> tuple[float, dict]:
    """Return (factor>=1, info) measuring real surface-path elongation vs flat.

    Drops a ball cutter along `_PROBE_LINES` X-direction scan lines spread over
    the part footprint, then factor = Σ3D_segment / Σ2D_segment over on-surface
    points. 1.0 means perfectly flat; larger means sloped/curved.
    """
    try:
        import opencamlib as ocl
    except Exception as exc:  # pragma: no cover
        return 1.0, {"method": "flat", "reason": f"opencamlib unavailable: {exc}"}

    if len(mesh.faces) > _MAX_FACES:
        return 1.0, {"method": "flat", "reason": "mesh too large for OCL"}

    (x0, y0, z0), (x1, y1, z1) = mesh.bounds[0], mesh.bounds[1]
    if x1 - x0 < 1e-6 or y1 - y0 < 1e-6:
        return 1.0, {"method": "flat", "reason": "degenerate footprint"}

    try:
        surf = _build_surf(ocl, mesh.vertices, mesh.faces)
        cutter = ocl.BallCutter(max(finish_d, 0.5), 10.0)
        pdc = ocl.PathDropCutter()
        pdc.setSTL(surf)
        pdc.setCutter(cutter)
        sampling = max(_MIN_SAMPLING, (x1 - x0) / 200.0)
        pdc.setSampling(sampling)
        floor = z0 - 1.0
        pdc.setZ(floor)

        n_lines = min(_PROBE_LINES, max(4, int((y1 - y0) / max(stepover, 0.1))))
        path = ocl.Path()
        for i in range(n_lines):
            y = y0 + (i + 0.5) * (y1 - y0) / n_lines
            path.append(ocl.Line(ocl.Point(x0, y, z1 + 5.0),
                                 ocl.Point(x1, y, z1 + 5.0)))
        pdc.setPath(path)
        pdc.run()
        cl = pdc.getCLPoints()
    except Exception as exc:
        return 1.0, {"method": "flat", "reason": f"OCL run failed: {exc}"}

    # Sum 3D vs 2D over consecutive on-surface points (skip drops to the floor).
    eps = 1e-3
    d3 = d2 = 0.0
    prev = None
    on_pts = 0
    for p in cl:
        on = p.z > floor + 0.05            # actually touched the part surface
        if on and prev is not None:
            dx, dy, dz = p.x - prev[0], p.y - prev[1], p.z - prev[2]
            seg2 = math.hypot(dx, dy)
            if seg2 > eps and seg2 < (x1 - x0):   # guard line-to-line jumps
                d2 += seg2
                d3 += math.sqrt(seg2 * seg2 + dz * dz)
        prev = (p.x, p.y, p.z) if on else None
        on_pts += 1 if on else 0

    if d2 <= eps:
        return 1.0, {"method": "flat", "reason": "no on-surface path sampled"}
    factor = max(1.0, d3 / d2)
    return factor, {
        "method": "drop-cutter",
        "probe_lines": n_lines,
        "sampled_points": len(cl),
        "on_surface_points": on_pts,
        "factor": round(factor, 3),
    }
