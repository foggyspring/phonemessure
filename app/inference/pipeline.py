"""Per-session inference state.

A `SessionPipeline` owns the latest frame received for one session plus
the in-progress calibration collector. The WebSocket handler drains
frames into `latest_frame` (replace, don't queue) and lets the consumer
run inference whenever it's ready. This keeps latency low: if the model
takes 80 ms but new frames arrive every 100 ms, we never back up.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np

try:
    import cv2  # type: ignore
    _CV2_OK = True
except Exception:
    cv2 = None  # type: ignore
    _CV2_OK = False

from . import aruco as aruco_mod
from . import intrinsics as intr_mod
from . import yolo as yolo_mod


@dataclass
class SessionPipeline:
    sid: str
    latest_frame: Optional[np.ndarray] = None
    latest_frame_at: float = 0.0
    frame_count: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    intrinsics: Optional[intr_mod.Intrinsics] = None
    calib: Optional[intr_mod.CalibCollector] = None

    def push_jpeg(self, jpeg_bytes: bytes) -> bool:
        if not _CV2_OK:
            return False
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return False
        with self.lock:
            self.latest_frame = img
            self.latest_frame_at = time.time()
            self.frame_count += 1
        # Try to attach intrinsics if we haven't yet and one matches the size.
        if self.intrinsics is None:
            h, w = img.shape[:2]
            cand = intr_mod.load((w, h))
            if cand is not None:
                self.intrinsics = cand
        return True

    def _snapshot(self) -> Optional[np.ndarray]:
        with self.lock:
            f = self.latest_frame
        if f is None:
            return None
        return intr_mod.undistort(f, self.intrinsics)

    # ─── operations ─────────────────────────────────────────────

    def detect_aruco(self) -> dict:
        if (msg := aruco_mod.status_msg()):
            return {"ok": False, "reason": msg}
        f = self._snapshot()
        if f is None:
            return {"ok": False, "reason": "no frame yet"}
        pose = aruco_mod.detect_sheet(f)
        if pose is None:
            return {"ok": False, "reason": "could not see all four sheet markers"}
        return {"ok": True, "pose": pose.to_json(),
                "undistorted": self.intrinsics is not None}

    def detect_yolo(self, prompts=None) -> dict:
        if (msg := yolo_mod.status_msg()):
            return {"ok": False, "reason": msg}
        f = self._snapshot()
        if f is None:
            return {"ok": False, "reason": "no frame yet"}
        try:
            dets = yolo_mod.detect(f, prompts=prompts)
        except Exception as e:
            return {"ok": False, "reason": f"yolo failed: {e}"}
        return {
            "ok": True,
            "image_size": [int(f.shape[1]), int(f.shape[0])],
            "detections": [
                {"label": d.label, "score": d.score,
                 "box": d.box_xyxy, "mm_per_px": d.mm_per_px}
                for d in dets
            ],
            "undistorted": self.intrinsics is not None,
        }

    # ─── intrinsics flow ────────────────────────────────────────

    def calib_start(self) -> dict:
        if (msg := intr_mod.status_msg()):
            return {"ok": False, "reason": msg}
        self.calib = intr_mod.CalibCollector()
        self.intrinsics = None
        return {"ok": True, "captures": 0}

    def calib_capture(self) -> dict:
        if self.calib is None:
            self.calib = intr_mod.CalibCollector()
        with self.lock:
            f = self.latest_frame
        if f is None:
            return {"ok": False, "reason": "no frame yet"}
        return self.calib.add_frame(f)

    def calib_clear(self) -> dict:
        if self.calib is not None:
            self.calib.clear()
        return {"ok": True, "captures": 0}

    def calib_solve(self) -> dict:
        if self.calib is None:
            return {"ok": False, "reason": "calibration not started"}
        try:
            intr = self.calib.solve()
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        self.intrinsics = intr
        self.calib = None
        return {"ok": True, "rms": intr.rms,
                "image_size": list(intr.image_size),
                "saved": True}

    def intrinsics_status(self) -> dict:
        info: dict = {"have_intrinsics": self.intrinsics is not None}
        if self.intrinsics is not None:
            info["image_size"] = list(self.intrinsics.image_size)
            info["rms"] = self.intrinsics.rms
        return info


class _Registry:
    """sid -> SessionPipeline."""
    _by_sid: dict[str, SessionPipeline] = {}
    _lock = threading.Lock()

    @classmethod
    def get(cls, sid: str) -> SessionPipeline:
        with cls._lock:
            p = cls._by_sid.get(sid)
            if p is None:
                p = SessionPipeline(sid=sid)
                cls._by_sid[sid] = p
            return p

    @classmethod
    def drop(cls, sid: str) -> None:
        with cls._lock:
            cls._by_sid.pop(sid, None)


def get_pipeline(sid: str) -> SessionPipeline:
    return _Registry.get(sid)
