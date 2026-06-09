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

    @property
    def total_holes(self) -> int:
        return sum(h.count for h in self.holes)

    @property
    def threaded_holes(self) -> int:
        return sum(h.count for h in self.holes if h.threaded)

    @property
    def distinct_drill_sizes(self) -> int:
        return len({round(h.diameter_mm, 1) for h in self.holes}) if self.holes else 0


# Geometry thresholds — deliberately conservative, tunable shop constants.
_SLENDER_RATIO = 8.0     # longest/shortest bbox edge above this = floppy/whippy
_THIN_WALL_MM = 1.0      # below this = deformation risk
_FREEFORM_COMPLEXITY = 0.45  # surface-area proxy above this = sculpted/freeform


def analyze(
    metrics: MeshMetrics,
    holes: list[Hole] | None = None,
    tight_tolerance: bool = False,
    requires_5axis: bool = False,
    min_wall_mm: float | None = None,
) -> FeatureSet:
    holes = holes or []
    warnings: list[str] = []

    dims = sorted(metrics.dims_mm)
    if dims[0] > 0 and dims[2] / dims[0] > _SLENDER_RATIO:
        warnings.append(
            f"细长比偏大 ({dims[2]/dims[0]:.0f}:1)，加工易振动/变形，可能需要额外支撑或降速。"
        )

    if metrics.complexity > _FREEFORM_COMPLEXITY:
        warnings.append(
            "检测到大量非平面/自由曲面，建议球头刀精铣或五轴联动，系数已自动上调。"
        )
        # Heavy freeform geometry strongly implies multi-axis work.
        if not requires_5axis and metrics.complexity > 0.7:
            requires_5axis = True
            warnings.append("曲面复杂度很高，已自动按五轴联动估算。")

    if min_wall_mm is not None and 0 < min_wall_mm < _THIN_WALL_MM:
        warnings.append(
            f"最小壁厚 {min_wall_mm:.2f}mm < {_THIN_WALL_MM:.0f}mm，存在加工变形风险，难度系数上调。"
        )

    for h in holes:
        if h.is_deep:
            warnings.append(
                f"Ø{h.diameter_mm:g} 深 {h.depth_mm:g}mm 深径比 >4，需啄钻(peck)，工时上调。"
            )
        if h.threaded and h.diameter_mm < 2.0:
            warnings.append(f"Ø{h.diameter_mm:g} 螺纹孔过小，攻丝易断丝，建议确认螺距。")

    # The universal CNC reminder from the brief: inside corners can't be sharp.
    warnings.append("提示：CNC 内壁转角受刀具直径限制必然带 R 角；若需绝对直角需 EDM 清角，成本另计。")

    return FeatureSet(
        metrics=metrics,
        holes=holes,
        tight_tolerance=tight_tolerance,
        requires_5axis=requires_5axis,
        min_wall_mm=min_wall_mm,
        warnings=warnings,
    )
