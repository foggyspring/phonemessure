"""Time-estimate calibration: learn correction factors from real cycle times.

A shop records (estimated_min, actual_min) pairs as parts come off the machine.
This module turns those into a robust per-material correction factor that scales
future estimates toward reality — the systematic way to shrink the estimator's
±error without hand-tuning feeds/speeds.

Robustness: a material needs at least ``MIN_SAMPLES`` before its own factor is
trusted; otherwise the global factor (all materials) applies, then 1.0. Factors
use the median ratio (outlier-resistant) and are clamped so a few bad records
can't wildly distort a quote.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median

MIN_SAMPLES = 3
CLAMP = (0.33, 3.0)


def _clamp(x: float) -> float:
    return max(CLAMP[0], min(CLAMP[1], x))


def compute_time_factors(samples: list[dict]) -> dict:
    """samples: [{material, estimated_min, actual_min}, ...] → factors.

    Returns {material: {"factor": f, "n": n}, "_global": {"factor", "n"}}.
    """
    by_mat: dict[str, list[float]] = defaultdict(list)
    all_ratios: list[float] = []
    for s in samples:
        est = float(s.get("estimated_min", 0) or 0)
        act = float(s.get("actual_min", 0) or 0)
        if est > 0 and act > 0:
            r = act / est
            by_mat[str(s.get("material", ""))].append(r)
            all_ratios.append(r)

    out: dict[str, dict] = {}
    if all_ratios:
        out["_global"] = {"factor": round(_clamp(median(all_ratios)), 3), "n": len(all_ratios)}
    for mat, ratios in by_mat.items():
        if len(ratios) >= MIN_SAMPLES:
            out[mat] = {"factor": round(_clamp(median(ratios)), 3), "n": len(ratios)}
    return out


def factor_for(factors: dict | None, material_key: str) -> tuple[float, int]:
    """Resolve (factor, sample_count) for a material: own → global → 1.0."""
    if not factors:
        return 1.0, 0
    if material_key in factors:
        f = factors[material_key]
        return f["factor"], f["n"]
    g = factors.get("_global")
    if g:
        return g["factor"], g["n"]
    return 1.0, 0
