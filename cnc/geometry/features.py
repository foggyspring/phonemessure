"""Feature & manufacturability analysis.

Two kinds of signal feed the CAPP engine:

  1. *Geometry-derived* (from the mesh/B-rep metrics): bounding box, fill ratio,
     slenderness, surface complexity. These are robust and automatic.

  2. *User-declared* (from the UI): holes, threads, tight tolerances. As the
     brief itself warns, a STEP file usually carries no PMI/tolerance data and
     thread call-outs are ambiguous, so we let the operator declare them rather
     than silently under-quote.

The output is a :class:`FeatureSet` the CAPP engine consumes, plus a list of
DFM warnings the frontend surfaces (sharp internal corners need an R, thin
walls risk deformation, deep holes need pecking, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .mesh import MeshMetrics


@dataclass
class Hole:
    diameter_mm: float
    depth_mm: float
    count: int = 1
    threaded: bool = False

    @property
    def is_deep(self) -> bool:
        # Depth-to-diameter > 4 needs peck-drilling / risks tool deflection.
        return self.diameter_mm > 0 and self.depth_mm / self.diameter_mm > 4.0


@dataclass
class FeatureSet:
    metrics: MeshMetrics
    holes: list[Hole] = field(default_factory=list)
    tight_tolerance: bool = False
    requires_5axis: bool = False
    min_wall_mm: float | None = None
    warnings: list[str] = field(default_factory=list)
    setup_dirs: int | None = None        # distinct machining directions (mesh)
    setup_count: int | None = None       # mesh-derived 3-axis setup count
    undercut_frac: float = 0.0           # area fraction not facing any ±axis
    tolerance: dict | None = None        # resolved tolerance class params
    surface: dict | None = None          # resolved surface-roughness class

    @property
    def total_holes(self) -> int:
        return sum(h.count for h in self.holes)

    @property
    def threaded_holes(self) -> int:
        return sum(h.count for h in self.holes if h.threaded)

    @property
    def distinct_drill_sizes(self) -> int:
        return len({round(h.diameter_mm, 1) for h in self.holes}) if self.holes else 0


# Surface-area proxy above this = sculpted/freeform (drives 5-axis promotion).
# Manufacturability *messaging* thresholds live in cnc/dfm.py — the single DFM
# rule engine; this module only derives plan-affecting facts.
_FREEFORM_COMPLEXITY = 0.45


def analyze(
    metrics: MeshMetrics,
    holes: list[Hole] | None = None,
    tight_tolerance: bool = False,
    requires_5axis: bool = False,
    min_wall_mm: float | None = None,
) -> FeatureSet:
    holes = holes or []

    # Heavy freeform geometry strongly implies multi-axis work — this changes
    # the machine/plan, so it stays here (not in the advisory DFM layer).
    if not requires_5axis and metrics.complexity > 0.7:
        requires_5axis = True

    return FeatureSet(
        metrics=metrics,
        holes=holes,
        tight_tolerance=tight_tolerance,
        requires_5axis=requires_5axis,
        min_wall_mm=min_wall_mm,
    )
