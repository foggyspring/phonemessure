"""Camera intrinsics — Charuco-board calibration + undistort.

Workflow:
  1. User prints the charuco board PDF (/api/charuco-sheet.pdf) on A4.
  2. They open the "Calibrate intrinsics" panel and capture ~12-20 frames
     from many angles. Each capture is fed here.
  3. When they hit "Solve", we run cv2.aruco.calibrateCameraCharuco and
     persist K + dist_coeffs to ~/.phonemessure/intrinsics-<image_w>x<h>.json.
  4. Subsequent frames at the same resolution get undistorted before any
     measurement.

Intrinsics are stored *per image resolution* because the K matrix scales
with resolution. The phone's "cover-cropped" overlay frame is always sent
at one consistent size for a given session, so this is fine.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

try:
    import cv2  # type: ignore
    _CV2_OK = True
    _CV2_ERR = None
except Exception as e:  # pragma: no cover
    cv2 = None  # type: ignore
    _CV2_OK = False
    _CV2_ERR = str(e)

from .runtime import state_dir


# Charuco board geometry — kept in sync with app/aruco_pdf.py.
BOARD_SQUARES_X = 7
BOARD_SQUARES_Y = 10
BOARD_SQUARE_MM = 22.0
BOARD_MARKER_MM = 16.5
BOARD_DICT = "DICT_4X4_50"


def _dict_get(name: str):
    if not _CV2_OK:
        raise RuntimeError(_CV2_ERR or "opencv-contrib-python not installed")
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


def make_board():
    """Build the Charuco board object the calibrator and PDF share."""
    d = _dict_get(BOARD_DICT)
    return cv2.aruco.CharucoBoard(
        (BOARD_SQUARES_X, BOARD_SQUARES_Y),
        BOARD_SQUARE_MM / 1000.0,   # OpenCV wants metres
        BOARD_MARKER_MM / 1000.0,
        d,
    )


@dataclass
class Intrinsics:
    image_size: Tuple[int, int]       # (w, h)
    K: np.ndarray                     # 3x3
    dist: np.ndarray                  # (5,) typically
    rms: float                        # reprojection error from calibration

    def to_json(self) -> dict:
        return {
            "image_size": list(self.image_size),
            "K": self.K.tolist(),
            "dist": self.dist.flatten().tolist(),
            "rms": float(self.rms),
        }

    @classmethod
    def from_json(cls, obj: dict) -> "Intrinsics":
        return cls(
            image_size=tuple(obj["image_size"]),  # type: ignore[arg-type]
            K=np.array(obj["K"], dtype=np.float64),
            dist=np.array(obj["dist"], dtype=np.float64),
            rms=float(obj.get("rms", 0.0)),
        )


def _path_for(image_size: Tuple[int, int]) -> Path:
    w, h = image_size
    return state_dir() / f"intrinsics-{w}x{h}.json"


def load(image_size: Tuple[int, int]) -> Optional[Intrinsics]:
    p = _path_for(image_size)
    if not p.exists():
        return None
    try:
        return Intrinsics.from_json(json.loads(p.read_text("utf-8")))
    except Exception:
        return None


def save(intr: Intrinsics) -> Path:
    p = _path_for(intr.image_size)
    p.write_text(json.dumps(intr.to_json(), indent=2), encoding="utf-8")
    return p


@dataclass
class CalibCollector:
    """Accumulates per-frame Charuco detections until the user solves."""
    board: "cv2.aruco.CharucoBoard" = field(default_factory=make_board)
    detector: "cv2.aruco.CharucoDetector" = field(init=False)
    all_corners: List[np.ndarray] = field(default_factory=list)
    all_ids: List[np.ndarray] = field(default_factory=list)
    image_size: Optional[Tuple[int, int]] = None

    def __post_init__(self) -> None:
        if not _CV2_OK:
            raise RuntimeError(_CV2_ERR or "opencv-contrib-python not installed")
        self.detector = cv2.aruco.CharucoDetector(self.board)

    def add_frame(self, bgr: np.ndarray) -> dict:
        """Detect corners in this frame; remember them if we found enough."""
        h, w = bgr.shape[:2]
        if self.image_size is None:
            self.image_size = (w, h)
        elif self.image_size != (w, h):
            return {"ok": False, "reason": "frame size changed"}

        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        ch_corners, ch_ids, _, _ = self.detector.detectBoard(gray)
        n = 0 if ch_corners is None else len(ch_corners)
        if ch_corners is None or n < 6:
            return {"ok": False, "n": int(n)}
        self.all_corners.append(ch_corners)
        self.all_ids.append(ch_ids)
        return {"ok": True, "n": int(n), "captures": len(self.all_corners)}

    def clear(self) -> None:
        self.all_corners.clear()
        self.all_ids.clear()
        self.image_size = None

    def solve(self) -> Intrinsics:
        if not self.all_corners or self.image_size is None:
            raise RuntimeError("no captures yet — capture some frames first")
        if len(self.all_corners) < 5:
            raise RuntimeError(f"need ≥5 captures, have {len(self.all_corners)}")
        flags = cv2.CALIB_RATIONAL_MODEL if False else 0  # default 5-coeff model
        rms, K, dist, _rvecs, _tvecs = cv2.aruco.calibrateCameraCharuco(
            charucoCorners=self.all_corners,
            charucoIds=self.all_ids,
            board=self.board,
            imageSize=self.image_size,
            cameraMatrix=None,
            distCoeffs=None,
            flags=flags,
        )
        intr = Intrinsics(image_size=self.image_size, K=K, dist=dist, rms=float(rms))
        save(intr)
        return intr


def undistort(bgr: np.ndarray, intr: Optional[Intrinsics]) -> np.ndarray:
    """Return an undistorted copy if we have matching intrinsics, else the
    input untouched."""
    if intr is None or not _CV2_OK:
        return bgr
    h, w = bgr.shape[:2]
    if (w, h) != intr.image_size:
        return bgr
    return cv2.undistort(bgr, intr.K, intr.dist, None, intr.K)


def status_msg() -> Optional[str]:
    """Return human-readable error if OpenCV isn't usable, else None."""
    return None if _CV2_OK else (_CV2_ERR or "opencv-contrib-python not installed")
