"""Triangle-mesh geometry from STL files — pure Python, no heavy deps.

We deliberately avoid numpy/OpenCASCADE here so the geometric core stays
trivially importable and unit-testable. STL only carries a triangle soup, so
this module computes exactly the four quantities the costing funnel needs from
*any* solid:

    * axis-aligned bounding box  -> raw stock size
    * mesh volume                -> part weight + removed volume
    * surface area               -> finishing time + surface treatment area
    * a surface-complexity proxy -> 3-axis vs 5-axis heuristic

The volume is the classic signed-tetrahedron sum (Zhang & Chen); it is exact
for any closed, consistently-oriented mesh and degrades gracefully (we take the
magnitude) if the export is slightly non-watertight.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path

Vec = tuple[float, float, float]


@dataclass(frozen=True)
class MeshMetrics:
    """Everything the downstream engines need from the raw geometry."""

    triangles: int
    bbox_min: Vec
    bbox_max: Vec
    volume_mm3: float
    area_mm2: float

    @property
    def dims_mm(self) -> Vec:
        return (
            self.bbox_max[0] - self.bbox_min[0],
            self.bbox_max[1] - self.bbox_min[1],
            self.bbox_max[2] - self.bbox_min[2],
        )

    @property
    def bbox_volume_mm3(self) -> float:
        dx, dy, dz = self.dims_mm
        return dx * dy * dz

    @property
    def bbox_area_mm2(self) -> float:
        dx, dy, dz = self.dims_mm
        return 2.0 * (dx * dy + dy * dz + dz * dx)

    @property
    def complexity(self) -> float:
        """Surface-area ratio vs the bounding box, clamped to [0, 1].

        A plain block is ~1x its bbox area (proxy ~0); the more sculpted /
        pocketed / freeform the part, the larger its true area relative to the
        box, the closer this climbs to 1. The CAPP engine turns it into a
        finishing-time and 5-axis multiplier.
        """
        box = self.bbox_area_mm2
        if box <= 0:
            return 0.0
        ratio = self.area_mm2 / box
        return max(0.0, min(1.0, (ratio - 1.0) / 4.0))


def _tri_area(a: Vec, b: Vec, c: Vec) -> float:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    cx = uy * vz - uz * vy
    cy = uz * vx - ux * vz
    cz = ux * vy - uy * vx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def _signed_tetra_vol(a: Vec, b: Vec, c: Vec) -> float:
    # Signed volume of the tetrahedron (origin, a, b, c) = (a · (b × c)) / 6.
    cx = b[1] * c[2] - b[2] * c[1]
    cy = b[2] * c[0] - b[0] * c[2]
    cz = b[0] * c[1] - b[1] * c[0]
    return (a[0] * cx + a[1] * cy + a[2] * cz) / 6.0


def _metrics_from_triangles(tris: list[tuple[Vec, Vec, Vec]]) -> MeshMetrics:
    if not tris:
        raise GeometryError("mesh contains no triangles")

    inf = float("inf")
    mn = [inf, inf, inf]
    mx = [-inf, -inf, -inf]
    vol = 0.0
    area = 0.0
    for a, b, c in tris:
        for p in (a, b, c):
            for i in range(3):
                if p[i] < mn[i]:
                    mn[i] = p[i]
                if p[i] > mx[i]:
                    mx[i] = p[i]
        vol += _signed_tetra_vol(a, b, c)
        area += _tri_area(a, b, c)

    return MeshMetrics(
        triangles=len(tris),
        bbox_min=(mn[0], mn[1], mn[2]),
        bbox_max=(mx[0], mx[1], mx[2]),
        volume_mm3=abs(vol),
        area_mm2=area,
    )


class GeometryError(ValueError):
    """Raised when a mesh cannot be parsed or is degenerate."""


def _looks_binary(data: bytes) -> bool:
    # An ASCII STL starts with "solid"; but some binary exporters do too. The
    # reliable test is whether the declared triangle count matches the length.
    if len(data) < 84:
        return False
    n = struct.unpack_from("<I", data, 80)[0]
    return len(data) == 84 + n * 50


def _parse_binary(data: bytes) -> list[tuple[Vec, Vec, Vec]]:
    n = struct.unpack_from("<I", data, 80)[0]
    tris: list[tuple[Vec, Vec, Vec]] = []
    off = 84
    for _ in range(n):
        # 12 little-endian floats: normal(3) + v0(3) + v1(3) + v2(3), then 2-byte attr.
        vals = struct.unpack_from("<12f", data, off)
        v0 = (vals[3], vals[4], vals[5])
        v1 = (vals[6], vals[7], vals[8])
        v2 = (vals[9], vals[10], vals[11])
        tris.append((v0, v1, v2))
        off += 50
    return tris


def _parse_ascii(text: str) -> list[tuple[Vec, Vec, Vec]]:
    tris: list[tuple[Vec, Vec, Vec]] = []
    verts: list[Vec] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("vertex"):
            parts = line.split()
            verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            if len(verts) == 3:
                tris.append((verts[0], verts[1], verts[2]))
                verts = []
    return tris


def metrics_from_stl_bytes(data: bytes) -> MeshMetrics:
    """Parse an STL (binary or ASCII) and return its geometric metrics."""
    if _looks_binary(data):
        tris = _parse_binary(data)
    else:
        try:
            tris = _parse_ascii(data.decode("utf-8", errors="replace"))
        except (ValueError, IndexError) as exc:
            raise GeometryError(f"could not parse ASCII STL: {exc}") from exc
    if not tris:
        raise GeometryError("STL parsed but contained zero triangles")
    return _metrics_from_triangles(tris)


def metrics_from_stl_file(path: str | Path) -> MeshMetrics:
    return metrics_from_stl_bytes(Path(path).read_bytes())
