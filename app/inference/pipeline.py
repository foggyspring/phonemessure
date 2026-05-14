"""Per-session inference state.

A `SessionPipeline` owns:
  - `latest_frame`           : most recent frame, used by single-shot ops
                               (sharpness check, intrinsics capture, etc.)
  - `frame_buffer`           : a small ring of the most recent N frames,
                               used by multi-frame ArUco averaging
  - `intrinsics`             : loaded once we see a frame whose resolution
                               has a saved K + dist on disk
  - `calib`                  : in-progress charuco collector during the
                               intrinsics flow

Concurrency: the WebSocket task pushes frames at ~6 fps from one thread;
inference handlers run in `loop.run_in_executor`. The lock only protects
the buffer / latest pointer; the inference functions get an immutable
snapshot.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Tuple

import numpy as np

try:
    import cv2  # type: ignore
    _CV2_OK = True
except Exception:
    cv2 = None  # type: ignore
    _CV2_OK = False

from . import aruco as aruco_mod
from . import intrinsics as intr_mod
from . import refine as refine_mod
from . import yolo as yolo_mod


# How many frames to keep for averaging. At 6 fps this covers ~1.3 s.
FRAME_BUFFER_LEN = 8


@dataclass
class SessionPipeline:
    sid: str
    latest_frame: Optional[np.ndarray] = None
    latest_frame_at: float = 0.0
    frame_count: int = 0
    frame_buffer: Deque[Tuple[float, np.ndarray]] = field(
        default_factory=lambda: deque(maxlen=FRAME_BUFFER_LEN)
    )
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
            # Reset the averaging buffer when the resolution changes
            # (e.g. user clicks "Detect" which switches to hi-res mode).
            if self.frame_buffer and self.frame_buffer[-1][1].shape != img.shape:
                self.frame_buffer.clear()
            self.frame_buffer.append((time.time(), img))
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

    def _recent_undistorted(self, max_age_s: float = 1.5) -> List[np.ndarray]:
        with self.lock:
            now = time.time()
            same_shape = None
            picks: List[np.ndarray] = []
            # Walk newest → oldest, keep frames sharing the latest shape,
            # so a mid-stream resolution change doesn't poison the average.
            for ts, frame in reversed(self.frame_buffer):
                if now - ts > max_age_s:
                    break
                if same_shape is None:
                    same_shape = frame.shape
                elif frame.shape != same_shape:
                    break
                picks.append(frame)
        # newest first → reverse to chronological for any consumers who care
        picks.reverse()
        return [intr_mod.undistort(f, self.intrinsics) for f in picks]

    # ─── operations ─────────────────────────────────────────────

    def detect_aruco(self) -> dict:
        if (msg := aruco_mod.status_msg()):
            return {"ok": False, "reason": msg}
        frames = self._recent_undistorted()
        if not frames:
            return {"ok": False, "reason": "no frame yet"}
        pose = aruco_mod.detect_sheet_avg(frames)
        if pose is None:
            return {"ok": False, "reason": "could not see all four sheet markers"}
        return {
            "ok": True,
            "pose": pose.to_json(),
            "undistorted": self.intrinsics is not None,
        }

    def refine_point(self, x: float, y: float, radius: int = 20) -> dict:
        if (msg := aruco_mod.status_msg()):
            return {"ok": False, "reason": msg}
        f = self._snapshot()
        if f is None:
            return {"ok": False, "reason": "no frame yet"}
        h, wid = f.shape[:2]
        out = refine_mod.refine_point(f, x, y, radius=radius)
        out["image_size"] = [int(wid), int(h)]
        return out

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
        # Reject blurry captures early — they only poison the calibration.
        sharp = aruco_mod.sharpness(f)
        if sharp < 80.0:
            return {"ok": False, "reason": f"frame too blurry (sharpness={sharp:.0f}); hold steady"}
        res = self.calib.add_frame(f)
        res["sharpness"] = sharp
        return res

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
