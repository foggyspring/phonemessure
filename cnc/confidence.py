"""Quote confidence score — how much to trust this automatic estimate.

Combines signals the engine already has into a 0–100 score + level + reasons,
so the customer knows when to expect a manual review. Auto-detected features,
freeform/undercut geometry, manual dimensions and the analytic (vs toolpath)
backend lower it; real-cycle-time calibration raises it.
"""
from __future__ import annotations


def score_quote(*, backend: str, complexity: float, holes_auto: bool, wall_auto: bool,
                undercut_frac: float, near_envelope: bool, has_mesh: bool,
                calibration_n: int, dim_suspect: bool = False,
                watertight: bool = True) -> dict:
    score = 90.0
    reasons: list[str] = []

    if not watertight:
        score -= 8; reasons.append("网格非水密，体积/料费估算可靠性下降")
    if dim_suspect:
        # implausible size ⇒ probable unit error ⇒ the quote is not trustworthy
        score -= 45
        reasons.append("尺寸异常，单位疑似有误（请核对）")
    if not has_mesh:
        score -= 15; reasons.append("手动输入尺寸，无三维模型")
    if backend == "analytic":
        score -= 10; reasons.append("解析法估算（刀路仿真更精确）")
    elif backend == "toolpath":
        score += 3
    if holes_auto:
        score -= 8; reasons.append("孔为自动识别，请核对")
    if wall_auto:
        score -= 4; reasons.append("壁厚为自动检测")
    if complexity > 0.6:
        score -= 10; reasons.append("曲面复杂度高")
    if undercut_frac > 0.2:
        score -= 10; reasons.append("存在倒扣/难达面")
    if near_envelope:
        score -= 5; reasons.append("接近机床行程上限")
    if calibration_n >= 3:
        score += 10; reasons.append(f"已用 {calibration_n} 个实测样本校准")

    score = max(30, min(98, round(score)))
    level = "high" if score >= 80 else "medium" if score >= 60 else "low"
    # Estimation uncertainty band by confidence level: a point price implies a
    # false precision, so we publish a ± range (reference only, not the billed
    # price). High-confidence quotes are tight; low-confidence ones are wide.
    band_pct = {"high": 0.08, "medium": 0.15, "low": 0.25}[level]
    return {"score": score, "level": level, "reasons": reasons, "band_pct": band_pct}
