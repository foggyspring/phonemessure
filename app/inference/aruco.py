"""Real ArUco detection + plane homography.

Replaces the browser-side blob detector. Uses cv2.aruco with DICT_4X4_50
markers at the four corners of the printable calibration sheet. The four
marker centres form a known rectangle in millimetres on the sheet, so we
solve a 3×3 homography from image pixels → sheet millimetres. Once that
homography is known, every pixel that lies on the sheet's plane is
calibrated.

Marker IDs and sheet geometry are kept in sync with app/aruco_pdf.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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
    image_size: Tuple[int, int]            # (w, h)
    marker_centres: Dict[str, Tuple[float, float]]  # "TL" -> (x, y) in pixels
    H_image_to_mm: np.ndarray              # 3x3
    found_ids: List[int]

    def to_json(self) -> dict:
        return {
            "image_size": list(self.image_size),
            "marker_centres": {k: list(v) for k, v in self.marker_centres.items()},
            "H": self.H_image_to_mm.flatten().tolist(),
            "found_ids": self.found_ids,
        }


_detector_cache = {}


def _detector():
    if not _CV2_OK:
        raise RuntimeError("opencv-contrib-python not installed")
    if "sheet" not in _detector_cache:
        d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, SHEET_DICT))
        params = cv2.aruco.DetectorParameters()
        # Sub-pixel refinement gives ~0.5px better corners.
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
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


def detect_sheet(bgr: np.ndarray) -> Optional[ArucoPose]:
    """Return ArucoPose if all four sheet markers were detected, else None."""
    if not _CV2_OK:
        return None
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = _detector().detectMarkers(gray)
    if ids is None or len(ids) < 4:
        return None
    centres = _ordered_centres(corners, ids)
    if not all(k in centres for k in ("TL", "TR", "BR", "BL")):
        return None

    src = np.float32([centres["TL"], centres["TR"], centres["BR"], centres["BL"]])
    dst = np.float32([
        [0.0,        0.0],
        [SHEET_W_MM, 0.0],
        [SHEET_W_MM, SHEET_H_MM],
        [0.0,        SHEET_H_MM],
    ])
    H = cv2.getPerspectiveTransform(src, dst)
    return ArucoPose(
        image_size=(w, h),
        marker_centres=centres,
        H_image_to_mm=H,
        found_ids=sorted(int(i) for i in ids.flatten().tolist()),
    )


def status_msg() -> Optional[str]:
    if not _CV2_OK:
        return "opencv-contrib-python not installed"
    return None
