"""Pre-fetch ML checkpoints into ./checkpoints/.

Run this once on a new machine so the first measurement request doesn't pay
the model-download cost mid-flight. Ultralytics caches by filename; if a
matching pt is already in checkpoints/ we don't re-download.

    python -m scripts.fetch_models           # everything we use
    python -m scripts.fetch_models --yolo    # just YOLO-World
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def fetch_yolo() -> None:
    try:
        from ultralytics import YOLOWorld  # type: ignore
    except ImportError:
        print("ultralytics is not installed. pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)
    # Loading the class with a name triggers the download into the ultralytics cache
    # and our local checkpoints/ directory on first instantiation.
    from app.inference.runtime import checkpoints_dir
    ckpt = checkpoints_dir() / "yolov8s-worldv2.pt"
    if ckpt.exists():
        print(f"YOLO-World already cached at {ckpt} ({ckpt.stat().st_size // 1024} KiB)")
        return
    print("Downloading YOLO-World ckpt (~50 MB)…")
    # First instantiation will resolve and download into ultralytics' default cache.
    m = YOLOWorld("yolov8s-worldv2.pt")
    # Then move the file into our checkpoints/ so it's predictable.
    src = Path(m.ckpt_path) if hasattr(m, "ckpt_path") and m.ckpt_path else None
    if src and src.exists():
        ckpt.write_bytes(src.read_bytes())
        print(f"  saved to {ckpt}")
    else:
        print("  ultralytics handled the download; future runs will use its cache")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--yolo", action="store_true", help="only fetch YOLO-World")
    args = p.parse_args()

    if args.yolo or not any([args.yolo]):
        fetch_yolo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
