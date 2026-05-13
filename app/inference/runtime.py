"""Device selection + checkpoint paths.

On Apple silicon we want MPS; everywhere else we fall back to CPU.  We never
auto-pick CUDA because the target setup is a 16 GB MBP with no discrete GPU
— pretending we have CUDA would just OOM later.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path


def select_device() -> str:
    """Return a torch-style device string: "mps" or "cpu"."""
    try:
        import torch  # type: ignore
    except ImportError:
        return "cpu"
    if (
        platform.system() == "Darwin"
        and platform.machine() == "arm64"
        and getattr(torch.backends, "mps", None)
        and torch.backends.mps.is_available()
        and torch.backends.mps.is_built()
        # PYTORCH_ENABLE_MPS_FALLBACK lets ops missing on MPS silently fall
        # back to CPU instead of crashing the pipeline.
    ):
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        return "mps"
    return "cpu"


def checkpoints_dir() -> Path:
    """Where downloaded checkpoints live. Override via PHONEMESSURE_CKPT_DIR."""
    env = os.environ.get("PHONEMESSURE_CKPT_DIR")
    if env:
        p = Path(env).expanduser()
    else:
        p = Path(__file__).resolve().parents[2] / "checkpoints"
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_dir() -> Path:
    """Where per-machine state (camera intrinsics, etc.) is persisted."""
    p = Path(os.environ.get("PHONEMESSURE_STATE_DIR", "")).expanduser() if os.environ.get("PHONEMESSURE_STATE_DIR") else Path.home() / ".phonemessure"
    p.mkdir(parents=True, exist_ok=True)
    return p
