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
    """samples: [{material, backend, estimated_min, actual_min}, ...] → factors.

    Returns a flat, JSON-serialisable dict keyed by "material|backend",
    "material", and "_global" (most specific first when resolved). A key needs
    >= MIN_SAMPLES to appear.
    """
    by_mb: dict[str, list[float]] = defaultdict(list)
    by_mat: dict[str, list[float]] = defaultdict(list)
    all_ratios: list[float] = []
    for s in samples:
        est = float(s.get("estimated_min", 0) or 0)
        act = float(s.get("actual_min", 0) or 0)
        if est > 0 and act > 0:
            r = act / est
            mat = str(s.get("material", ""))
            be = s.get("backend")
            by_mat[mat].append(r)
            all_ratios.append(r)
            if be:
                by_mb[f"{mat}|{be}"].append(r)

    out: dict[str, dict] = {}
    if all_ratios:
        out["_global"] = {"factor": round(_clamp(median(all_ratios)), 3), "n": len(all_ratios)}
    for key, ratios in {**by_mat, **by_mb}.items():
        if len(ratios) >= MIN_SAMPLES:
            out[key] = {"factor": round(_clamp(median(ratios)), 3), "n": len(ratios)}
    return out


def factor_for(factors: dict | None, material_key: str,
               backend: str | None = None) -> tuple[float, int]:
    """Resolve (factor, sample_count): material+backend → material → global → 1.0."""
    if not factors:
        return 1.0, 0
    keys = ([f"{material_key}|{backend}"] if backend else []) + [material_key, "_global"]
    for key in keys:
        if key in factors:
            return factors[key]["factor"], factors[key]["n"]
    return 1.0, 0
