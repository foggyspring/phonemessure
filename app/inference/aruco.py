"""Real ArUco detection + plane homography.

Replaces the browser-side blob detector. Uses cv2.aruco with DICT_4X4_50
markers at the four corners of the printable calibration sheet. The four
marker centres form a known rectangle in millimetres on the sheet, so we
solve a 3×3 homography from image pixels → sheet millimetres. Once that
homography is known, every pixel that lies on the sheet's plane is
calibrated.

Marker IDs and sheet geometry are kept in sync with app/aruco_pdf.py.

Multi-frame averaging (`detect_sheet_avg`) is the precision lever: running
ArUco on N successive frames and averaging the per-marker centroids drops
the corner localization noise as 1/√N, which directly halves the homography
error when N=4 and a phone is held reasonably steady.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import cv2  # type: ignore
    _CV2_OK = True
except Exception:
    cv2 = None  # type: ignore
    _CV2_OK = False


# Sheet geometry (must match aruco_pdf.SHEET_*)
SHEET_MARKER_IDS = {"TL": 0, "TR": 1, "BR": 2, "BL": 3}
SHEET_W_MM = 160.0   # marker-centre to marker-centre, long axis
SHEET_H_MM = 247.0   # short axis
SHEET_DICT = "DICT_4X4_50"


@dataclass
class ArucoPose:
    image_size: Tuple[int, int]                       # (w, h)
    marker_centres: Dict[str, Tuple[float, float]]    # "TL" -> (x, y) in pixels
    H_image_to_mm: np.ndarray                         # 3x3
    found_ids: List[int]
    # Quality metrics — populated by multi-frame averager; single-frame
    # detection leaves frames_used=1 and stds zero.
    frames_used: int = 1
    corner_std_px: Dict[str, float] = None  # type: ignore[assignment]
    sharpness: float = 0.0                  # Laplacian variance of last frame

    def to_json(self) -> dict:
        out = {
            "image_size": list(self.image_size),
            "marker_centres": {k: list(v) for k, v in self.marker_centres.items()},
            "H": self.H_image_to_mm.flatten().tolist(),
            "found_ids": self.found_ids,
            "frames_used": int(self.frames_used),
            "sharpness": float(self.sharpness),
        }
        if self.corner_std_px is not None:
            out["corner_std_px"] = {k: float(v) for k, v in self.corner_std_px.items()}
        return out


_detector_cache: Dict[str, "cv2.aruco.ArucoDetector"] = {}


def _detector():
    if not _CV2_OK:
        raise RuntimeError("opencv-contrib-python not installed")
    if "sheet" not in _detector_cache:
        d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, SHEET_DICT))
        params = cv2.aruco.DetectorParameters()
        # Sub-pixel refinement gives ~0.5px better corners. The contour
        # method is slightly slower but more robust against motion blur
        # than the default refinement.
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        params.cornerRefinementWinSize = 5
        params.cornerRefinementMaxIterations = 50
        params.cornerRefinementMinAccuracy = 0.01
        _detector_cache["sheet"] = cv2.aruco.ArucoDetector(d, params)
    return _detector_cache["sheet"]


def _ordered_centres(corners_list, ids) -> Dict[str, Tuple[float, float]]:
    """corners_list[i] is shape (1, 4, 2). Returns {"TL": (x,y), ...}."""
    by_id: Dict[int, Tuple[float, float]] = {}
    for c, i in zip(corners_list, ids.flatten().tolist()):
        pts = c.reshape(-1, 2)
        cx = float(pts[:, 0].mean())
        cy = float(pts[:, 1].mean())
        by_id[int(i)] = (cx, cy)
    out: Dict[str, Tuple[float, float]] = {}
    for name, mid in SHEET_MARKER_IDS.items():
        if mid in by_id:
            out[name] = by_id[mid]
    return out


def _detect_one(bgr: np.ndarray) -> Optional[Dict[str, Tuple[float, float]]]:
    if not _CV2_OK:
        return None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = _detector().detectMarkers(gray)
    if ids is None or len(ids) < 4:
        return None
    centres = _ordered_centres(corners, ids)
    if not all(k in centres for k in ("TL", "TR", "BR", "BL")):
        return None
    return centres


def _homography(centres: Dict[str, Tuple[float, float]]) -> np.ndarray:
    src = np.float32([centres["TL"], centres["TR"], centres["BR"], centres["BL"]])
    dst = np.float32([
        [0.0,        0.0],
        [SHEET_W_MM, 0.0],
        [SHEET_W_MM, SHEET_H_MM],
        [0.0,        SHEET_H_MM],
    ])
    return cv2.getPerspectiveTransform(src, dst)


def detect_sheet(bgr: np.ndarray) -> Optional[ArucoPose]:
    """Single-frame detection."""
    centres = _detect_one(bgr)
    if centres is None:
        return None
    H = _homography(centres)
    h, w = bgr.shape[:2]
    return ArucoPose(
        image_size=(w, h),
        marker_centres=centres,
        H_image_to_mm=H,
        found_ids=[SHEET_MARKER_IDS[k] for k in centres],
        corner_std_px={k: 0.0 for k in centres},
        sharpness=sharpness(bgr),
    )


def detect_sheet_avg(frames: Sequence[np.ndarray], reject_px: float = 3.0) -> Optional[ArucoPose]:
    """Multi-frame averaging.

    1. detect on every frame, keep the ones with all 4 markers,
    2. for each marker take the median over frames,
    3. discard any frame whose marker centre deviates >reject_px from median
       (rejects motion blur / partial occlusion frames),
    4. average remaining frames' marker positions, return pose + per-marker
       std-dev so the UI can show "this was steady" vs "you were shaking".
    """
    if not frames:
        return None
    per_frame: List[Dict[str, Tuple[float, float]]] = []
    for f in frames:
        c = _detect_one(f)
        if c is not None:
            per_frame.append(c)
    if len(per_frame) == 0:
        return None
    if len(per_frame) == 1:
        return detect_sheet(frames[-1])

    # collect per-marker arrays
    stacked: Dict[str, np.ndarray] = {}
    for k in ("TL", "TR", "BR", "BL"):
        stacked[k] = np.array([cf[k] for cf in per_frame], dtype=np.float64)  # (N, 2)

    # median rejection
    keep = np.ones(len(per_frame), dtype=bool)
    for k, arr in stacked.items():
        med = np.median(arr, axis=0)
        d = np.linalg.norm(arr - med, axis=1)
        keep &= d < reject_px
    if keep.sum() < 1:
        # all frames disagreed wildly; fall back to most recent
        return detect_sheet(frames[-1])

    means: Dict[str, Tuple[float, float]] = {}
    stds: Dict[str, float] = {}
    for k, arr in stacked.items():
        kept = arr[keep]
        m = kept.mean(axis=0)
        means[k] = (float(m[0]), float(m[1]))
        stds[k] = float(np.linalg.norm(kept.std(axis=0)))

    H = _homography(means)
    h, w = frames[-1].shape[:2]
    return ArucoPose(
        image_size=(w, h),
        marker_centres=means,
        H_image_to_mm=H,
        found_ids=[SHEET_MARKER_IDS[k] for k in means],
        frames_used=int(keep.sum()),
        corner_std_px=stds,
        sharpness=sharpness(frames[-1]),
    )


def sharpness(bgr: np.ndarray) -> float:
    """Laplacian variance — proxy for focus / motion blur.

    Rough thresholds we use in the UI:
      < 50    : blurry, reject
      50-150  : marginal, warn
      > 150   : sharp
    """
    if not _CV2_OK:
        return 0.0
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def status_msg() -> Optional[str]:
    if not _CV2_OK:
        return "opencv-contrib-python not installed"
    return None
