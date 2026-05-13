"""YOLO-World wrapper for auto reference-object detection.

YOLO-World is open-vocabulary, so we can ask it to find "credit card" or
"coin" by text prompt rather than retraining on a custom class set. The
ckpt is ~50 MB and warms up in a few seconds on Apple-silicon MPS.

Once a reference is found, we map its bounding-box pixel size to its known
physical size to recover a mm-per-pixel scale. This is a coarse fallback
for users who don't have the calibration sheet on hand — homography from
the sheet is still the more accurate path.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from .runtime import select_device, checkpoints_dir


# Physical dimensions of known reference objects.
# `long_mm` is the longer side (or diameter for round things).
# `aspect` (long:short) is informational; we don't strictly need it for scale.
REFERENCES: Dict[str, Dict] = {
    "credit card":      {"long_mm": 85.60, "short_mm": 53.98, "round": False},
    "id card":          {"long_mm": 85.60, "short_mm": 53.98, "round": False},
    "a4 paper":         {"long_mm": 297.0, "short_mm": 210.0, "round": False},
    "letter paper":     {"long_mm": 279.4, "short_mm": 215.9, "round": False},
    "us quarter":       {"long_mm": 24.26, "short_mm": 24.26, "round": True},
    "us penny":         {"long_mm": 19.05, "short_mm": 19.05, "round": True},
    "us nickel":        {"long_mm": 21.21, "short_mm": 21.21, "round": True},
    "us dime":          {"long_mm": 17.91, "short_mm": 17.91, "round": True},
    "one yuan coin":    {"long_mm": 25.00, "short_mm": 25.00, "round": True},
    "five jiao coin":   {"long_mm": 20.50, "short_mm": 20.50, "round": True},
    "one jiao coin":    {"long_mm": 19.00, "short_mm": 19.00, "round": True},
    "one euro coin":    {"long_mm": 23.25, "short_mm": 23.25, "round": True},
    "two euro coin":    {"long_mm": 25.75, "short_mm": 25.75, "round": True},
}

DEFAULT_PROMPTS = ["credit card", "a4 paper", "us quarter", "one yuan coin"]


@dataclass
class Detection:
    label: str
    score: float
    box_xyxy: List[float]   # [x1, y1, x2, y2] in image pixels
    mm_per_px: Optional[float]


class _YoloHolder:
    """Lazy/singleton YOLO-World holder. Loading the model is slow, so we
    cache it process-wide and protect with a lock."""
    _model = None
    _last_prompts: tuple = ()
    _device: str = "cpu"
    _lock = threading.Lock()
    _err: Optional[str] = None

    @classmethod
    def get(cls, prompts: List[str]):
        with cls._lock:
            if cls._err:
                raise RuntimeError(cls._err)
            if cls._model is None:
                try:
                    from ultralytics import YOLOWorld  # type: ignore
                except ImportError as e:
                    cls._err = f"ultralytics not installed: {e}"
                    raise RuntimeError(cls._err)
                # Cache ckpt under our own checkpoints/ dir so it's predictable.
                ckpt = checkpoints_dir() / "yolov8s-worldv2.pt"
                cls._model = YOLOWorld(str(ckpt) if ckpt.exists() else "yolov8s-worldv2.pt")
                cls._device = select_device()
            prompt_key = tuple(sorted(set(prompts)))
            if prompt_key != cls._last_prompts:
                cls._model.set_classes(list(prompt_key))
                cls._last_prompts = prompt_key
            return cls._model


def warmup(prompts: Optional[List[str]] = None) -> Optional[str]:
    """Load the model and run one tiny forward pass. Returns an error
    message if the stack isn't installable, else None."""
    try:
        m = _YoloHolder.get(prompts or DEFAULT_PROMPTS)
        dummy = np.zeros((320, 320, 3), dtype=np.uint8)
        _ = m.predict(dummy, device=_YoloHolder._device, verbose=False, imgsz=320)
        return None
    except Exception as e:
        return str(e)


def detect(bgr: np.ndarray, prompts: Optional[List[str]] = None,
           conf: float = 0.10, max_imgsz: int = 640) -> List[Detection]:
    """Run YOLO-World on a single BGR frame, returning Detection list with
    a mm-per-pixel scale where the label matches one of our REFERENCES."""
    prompts = prompts or DEFAULT_PROMPTS
    model = _YoloHolder.get(prompts)
    h, w = bgr.shape[:2]
    res = model.predict(
        bgr, device=_YoloHolder._device, verbose=False,
        imgsz=max_imgsz, conf=conf,
    )
    out: List[Detection] = []
    if not res:
        return out
    r = res[0]
    names = r.names
    boxes = r.boxes
    if boxes is None or len(boxes) == 0:
        return out
    xyxy = boxes.xyxy.cpu().numpy()
    confs = boxes.conf.cpu().numpy()
    cls_ids = boxes.cls.cpu().numpy().astype(int)
    for (x1, y1, x2, y2), c, k in zip(xyxy, confs, cls_ids):
        label = names[int(k)].lower()
        bw, bh = float(x2 - x1), float(y2 - y1)
        ref = REFERENCES.get(label)
        mmpp = None
        if ref:
            if ref["round"]:
                # for round things use mean of width/height (≈ diameter)
                diam_px = (bw + bh) / 2.0
                if diam_px > 4:
                    mmpp = ref["long_mm"] / diam_px
            else:
                # for rectangles, match longer image-side to longer physical-side
                long_px = max(bw, bh)
                if long_px > 8:
                    mmpp = ref["long_mm"] / long_px
        out.append(Detection(
            label=label, score=float(c),
            box_xyxy=[float(x1), float(y1), float(x2), float(y2)],
            mm_per_px=mmpp,
        ))
    out.sort(key=lambda d: d.score, reverse=True)
    return out


def status_msg() -> Optional[str]:
    if _YoloHolder._err:
        return _YoloHolder._err
    try:
        import ultralytics  # noqa: F401
    except ImportError as e:
        return f"ultralytics not installed: {e}"
    return None
