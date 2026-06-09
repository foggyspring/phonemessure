"""Format dispatch for the geometry layer.

STL is handled natively by ``mesh.py`` (pure Python). STEP/IGES are B-rep
formats that need a real geometry kernel; we load OpenCASCADE *lazily* and only
if it is installed, so the whole system still runs (and quotes STL parts) in an
environment without OCCT. When OCCT is missing and a STEP/IGES file arrives, we
raise :class:`KernelUnavailable`, which the API turns into a 422 telling the
user to either install the kernel or fall back to manual dimension entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .mesh import GeometryError, MeshMetrics, Vec, metrics_from_stl_bytes

MESH_EXTS = {".stl"}
BREP_EXTS = {".step", ".stp", ".igs", ".iges"}
SUPPORTED_EXTS = MESH_EXTS | BREP_EXTS


@dataclass(frozen=True)
class ParseResult:
    metrics: MeshMetrics
    source_format: str           # "stl" | "step" | "iges"
    kernel: str                  # "mesh" | "occt"
    rendered_mesh_b64: str | None = None  # base64 STL the browser can preview


class KernelUnavailable(RuntimeError):
    """STEP/IGES supplied but no B-rep kernel (OpenCASCADE) is installed."""


def _ext(filename: str) -> str:
    return Path(filename).suffix.lower()


def parse_bytes(filename: str, data: bytes) -> ParseResult:
    """Parse an uploaded CAD file by extension into geometric metrics."""
    ext = _ext(filename)
    if ext in MESH_EXTS:
        return ParseResult(
            metrics=metrics_from_stl_bytes(data),
            source_format="stl",
            kernel="mesh",
            rendered_mesh_b64=None,  # frontend already has the STL bytes
        )
    if ext in BREP_EXTS:
        return _parse_brep(filename, data, ext)
    raise GeometryError(
        f"unsupported file type '{ext}'. Supported: "
        + ", ".join(sorted(SUPPORTED_EXTS))
    )


def _parse_brep(filename: str, data: bytes, ext: str) -> ParseResult:
    try:
        return _parse_brep_occt(filename, data, ext)
    except ImportError as exc:
        raise KernelUnavailable(
            "STEP/IGES parsing needs OpenCASCADE (pythonocc-core), which is not "
            "installed. Either `conda install -c conda-forge pythonocc-core`, "
            "upload an STL instead, or use the 'manual dimensions' fallback."
        ) from exc


def _parse_brep_occt(filename: str, data: bytes, ext: str) -> ParseResult:
    """Use OpenCASCADE to read a STEP/IGES solid and measure it.

    Imported lazily so the dependency is optional. Also tessellates the solid
    to an STL the browser can render, since Three.js cannot load B-rep directly.
    """
    import tempfile

    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.GProp import GProp_GProps
    from OCC.Extend.DataExchange import (
        read_iges_file,
        read_step_file,
        write_stl_file,
    )

    fmt = "iges" if ext in (".igs", ".iges") else "step"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    shape = read_iges_file(tmp_path) if fmt == "iges" else read_step_file(tmp_path)

    box = Bnd_Box()
    brepbndlib.Add(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()

    vprops = GProp_GProps()
    brepgprop.VolumeProperties(shape, vprops)
    volume_mm3 = abs(vprops.Mass())

    sprops = GProp_GProps()
    brepgprop.SurfaceProperties(shape, sprops)
    area_mm2 = sprops.Mass()

    bbox_min: Vec = (xmin, ymin, zmin)
    bbox_max: Vec = (xmax, ymax, zmax)
    metrics = MeshMetrics(
        triangles=0,
        bbox_min=bbox_min,
        bbox_max=bbox_max,
        volume_mm3=volume_mm3,
        area_mm2=area_mm2,
    )

    # Tessellate -> STL so the frontend can show a real preview.
    import base64

    BRepMesh_IncrementalMesh(shape, 0.3)
    stl_out = tmp_path + ".stl"
    write_stl_file(shape, stl_out)
    rendered = base64.b64encode(Path(stl_out).read_bytes()).decode("ascii")

    return ParseResult(
        metrics=metrics,
        source_format=fmt,
        kernel="occt",
        rendered_mesh_b64=rendered,
    )


def metrics_from_dims(
    length_mm: float, width_mm: float, height_mm: float, volume_mm3: float | None
) -> MeshMetrics:
    """Manual fallback: build metrics from user-typed bounding-box dimensions.

    Used when no kernel is available for a STEP file, or for a napkin quote.
    If the part volume is unknown we assume a conservative 55% of the bbox
    (a reasonable average machined-part fill) so the funnel still produces a
    defensible number rather than nothing.
    """
    if min(length_mm, width_mm, height_mm) <= 0:
        raise GeometryError("manual dimensions must all be positive")
    bbox = length_mm * width_mm * height_mm
    vol = volume_mm3 if volume_mm3 and volume_mm3 > 0 else 0.55 * bbox
    vol = min(vol, bbox)  # a part can't out-volume its own stock
    # Approximate area as the box area scaled up a touch for surface detail.
    area = 2.0 * (
        length_mm * width_mm + width_mm * height_mm + height_mm * length_mm
    ) * 1.15
    return MeshMetrics(
        triangles=0,
        bbox_min=(0.0, 0.0, 0.0),
        bbox_max=(length_mm, width_mm, height_mm),
        volume_mm3=vol,
        area_mm2=area,
    )
