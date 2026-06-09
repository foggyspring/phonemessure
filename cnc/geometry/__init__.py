"""3D geometry parsing engine — bounding box, volume, area, features."""
from .features import FeatureSet, Hole, analyze
from .mesh import GeometryError, MeshMetrics, metrics_from_stl_bytes, metrics_from_stl_file
from .parser import (
    KernelUnavailable,
    ParseResult,
    metrics_from_dims,
    parse_bytes,
)

__all__ = [
    "GeometryError",
    "MeshMetrics",
    "metrics_from_stl_bytes",
    "metrics_from_stl_file",
    "KernelUnavailable",
    "ParseResult",
    "parse_bytes",
    "metrics_from_dims",
    "FeatureSet",
    "Hole",
    "analyze",
]
