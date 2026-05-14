"""Sub-pixel point refinement ("snap to nearest edge").

When the user is dragging an endpoint, the laptop runs Sobel on a small ROI
around the touch position and snaps the point to the strongest local edge.
This removes the dominant error term in the precision budget — the finger
tap — turning ~0.5 px tap uncertainty (with magnifier) into ~0.05 px edge
localization.

Trade-off: we always snap *towards* the strongest gradient in a 41×41 window,
which can mislead if the user is deliberately aiming at a soft transition.
Hence the gradient-magnitude floor: below that, we report `refined=False`
and the client leaves the point where the user put it.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

try:
    import cv2  # type: ignore
    _CV2_OK = True
except Exception:
    cv2 = None  # type: ignore
    _CV2_OK = False


DEFAULT_RADIUS = 20         # window is (2R+1)² px in detection-frame coords
GRADIENT_FLOOR = 60.0       # |∇I| below this is treated as "no edge here"


def _parabolic_peak(c, l, r):
    """Sub-pixel peak of a parabola through (-1,l), (0,c), (1,r).

    Returns the offset δ in (-0.5, 0.5). If the curvature is flat, returns 0."""
    denom = (l - 2 * c + r)
    if abs(denom) < 1e-6:
        return 0.0
    return 0.5 * (l - r) / denom


def refine_point(bgr: np.ndarray, x: float, y: float,
                 radius: int = DEFAULT_RADIUS) -> dict:
    """Snap (x, y) to the strongest nearby edge.

    `bgr` is a full frame in detection-frame coordinates (the same space
    as the homography we return from ArUco detection). `x`, `y` are in
    the same coordinate space.

    Returns:
        {"ok": True/False, "x": float, "y": float, "refined": bool,
         "gradient": float, "reason": optional str}
    """
    if not _CV2_OK:
        return {"ok": False, "reason": "opencv unavailable"}
    h, w = bgr.shape[:2]
    xi, yi = int(round(x)), int(round(y))
    if not (0 <= xi < w and 0 <= yi < h):
        return {"ok": False, "reason": "point outside frame"}

    x0 = max(0, xi - radius)
    y0 = max(0, yi - radius)
    x1 = min(w, xi + radius + 1)
    y1 = min(h, yi + radius + 1)
    if x1 - x0 < 5 or y1 - y0 < 5:
        return {"ok": True, "x": x, "y": y, "refined": False, "gradient": 0.0,
                "reason": "ROI too small at frame border"}

    roi = bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)

    # Weight by distance from touch (soft Gaussian) so we don't grab a
    # stronger edge 20 px away when there's a reasonable edge 2 px away.
    rh, rw = mag.shape
    cx = x - x0
    cy = y - y0
    ys, xs = np.mgrid[0:rh, 0:rw]
    sigma = max(4.0, radius / 3.0)
    weight = np.exp(-((xs - cx) ** 2 + (ys - cy) ** 2) / (2 * sigma * sigma))
    scored = (mag * weight).astype(np.float32)

    py, px = np.unravel_index(int(scored.argmax()), scored.shape)
    g_at_peak = float(mag[py, px])
    if g_at_peak < GRADIENT_FLOOR:
        return {"ok": True, "x": float(x), "y": float(y),
                "refined": False, "gradient": g_at_peak,
                "reason": "no edge strong enough"}

    # Sub-pixel parabolic fit on the gradient magnitude (not the weighted
    # one — peak position should reflect the actual image edge).
    if 0 < px < rw - 1:
        px += _parabolic_peak(mag[py, px], mag[py, px - 1], mag[py, px + 1])
    if 0 < py < rh - 1:
        py += _parabolic_peak(mag[py, px if isinstance(px, int) else int(round(px))],
                              mag[py - 1, int(round(px))],
                              mag[py + 1, int(round(px))])
    return {
        "ok": True,
        "x": float(x0 + px),
        "y": float(y0 + py),
        "refined": True,
        "gradient": g_at_peak,
    }
