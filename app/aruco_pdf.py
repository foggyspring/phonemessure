"""Printable calibration sheets — real ArUco markers via cv2.aruco.

Two PDFs are generated:

  1. /api/aruco-sheet.pdf
     A4 page with four DICT_4X4_50 markers (IDs 0/1/2/3) at known mm
     positions. Used by the in-app "Calibrate from sheet" step to recover
     a full planar homography image-pixels → mm-on-the-sheet.

  2. /api/charuco-sheet.pdf
     A4 Charuco board (DICT_4X4_50, 7×10 of 22 mm squares with 16.5 mm
     markers). Used once per camera to estimate camera intrinsics + lens
     distortion. After that, every measurement runs on an undistorted
     frame which is the single biggest free accuracy win.
"""
from __future__ import annotations

import io
from typing import BinaryIO

import numpy as np
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

try:
    import cv2  # type: ignore
    _CV2_OK = True
except Exception:
    cv2 = None  # type: ignore
    _CV2_OK = False

from .inference.intrinsics import (
    BOARD_SQUARES_X, BOARD_SQUARES_Y, BOARD_SQUARE_MM, BOARD_MARKER_MM,
    BOARD_DICT, make_board,
)
from .inference.aruco import (
    SHEET_MARKER_IDS, SHEET_W_MM, SHEET_H_MM, SHEET_DICT,
)


# Sheet layout (mm); ArUco markers are drawn centred on these positions.
MARGIN_MM = 25.0
RECT_W_MM = SHEET_W_MM   # 160
RECT_H_MM = SHEET_H_MM   # 247


def _require_cv2() -> None:
    if not _CV2_OK:
        raise RuntimeError(
            "opencv-contrib-python is required to generate ArUco/Charuco PDFs. "
            "pip install opencv-contrib-python"
        )


def _marker_png(dict_name: str, marker_id: int, pixels: int = 600) -> ImageReader:
    """Render a single ArUco marker into an in-memory PNG."""
    _require_cv2()
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dict_name))
    img = cv2.aruco.generateImageMarker(d, marker_id, pixels)
    pil = Image.fromarray(img)
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


def build_aruco_pdf(out: BinaryIO, marker_mm: float = 40.0) -> None:
    """4-corner sheet for homography calibration."""
    _require_cv2()
    c = canvas.Canvas(out, pagesize=A4)
    page_w, page_h = A4
    half = marker_mm / 2.0 * mm

    # PDF coords: origin bottom-left, Y up. We want:
    #   TL (id 0) at top-left of page
    #   TR (id 1) at top-right
    #   BR (id 2) at bottom-right
    #   BL (id 3) at bottom-left
    centres = {
        "TL": (MARGIN_MM,             page_h / mm - MARGIN_MM),
        "TR": (MARGIN_MM + RECT_W_MM, page_h / mm - MARGIN_MM),
        "BR": (MARGIN_MM + RECT_W_MM, page_h / mm - MARGIN_MM - RECT_H_MM),
        "BL": (MARGIN_MM,             page_h / mm - MARGIN_MM - RECT_H_MM),
    }

    for name, (cx_mm, cy_mm) in centres.items():
        marker_id = SHEET_MARKER_IDS[name]
        cx, cy = cx_mm * mm, cy_mm * mm
        img = _marker_png(SHEET_DICT, marker_id)
        c.drawImage(img, cx - half, cy - half, 2 * half, 2 * half,
                    preserveAspectRatio=True, mask=None)
        # tiny label below each marker
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(0, 0, 0)
        c.drawCentredString(cx, cy - half - 3 * mm, f"{name} (id {marker_id})")

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(page_w / 2, page_h - 12 * mm, "phonemessure — sheet (DICT_4X4_50)")
    c.setFont("Helvetica", 9)
    c.drawCentredString(
        page_w / 2, page_h - 18 * mm,
        f"Marker centres: {RECT_W_MM:.0f} × {RECT_H_MM:.0f} mm  |  Marker size: {marker_mm:.0f} mm",
    )
    c.drawCentredString(
        page_w / 2, page_h - 23 * mm,
        "Print at 100% scale. Lay flat in frame, all four markers fully visible.",
    )

    # 10 mm reference ticks
    c.setFont("Helvetica", 6)
    c.setLineWidth(0.4)
    tick_y_mm = 14
    for i in range(0, int(RECT_W_MM) + 1, 10):
        x = (MARGIN_MM + i) * mm
        h = 4 * mm if i % 50 == 0 else 2 * mm
        c.line(x, tick_y_mm * mm, x, (tick_y_mm + h / mm) * mm)
        if i % 50 == 0:
            c.drawString(x + 0.6 * mm, (tick_y_mm - 2.5) * mm, f"{i} mm")

    c.showPage()
    c.save()


def build_charuco_pdf(out: BinaryIO) -> None:
    """Charuco board for intrinsics calibration."""
    _require_cv2()
    board = make_board()
    # Render the board at 300 DPI on an image sized to its physical extent.
    board_w_mm = BOARD_SQUARES_X * BOARD_SQUARE_MM
    board_h_mm = BOARD_SQUARES_Y * BOARD_SQUARE_MM
    dpi = 300
    img_w = int(round(board_w_mm / 25.4 * dpi))
    img_h = int(round(board_h_mm / 25.4 * dpi))
    img = board.generateImage((img_w, img_h), marginSize=0, borderBits=1)
    pil = Image.fromarray(img)
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    buf.seek(0)
    reader = ImageReader(buf)

    c = canvas.Canvas(out, pagesize=A4)
    page_w, page_h = A4

    # Centre the board on the page
    bw_pt = board_w_mm * mm
    bh_pt = board_h_mm * mm
    x0 = (page_w - bw_pt) / 2
    y0 = (page_h - bh_pt) / 2 - 6 * mm  # leave room for header
    c.drawImage(reader, x0, y0, bw_pt, bh_pt)

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(page_w / 2, page_h - 12 * mm,
                        "phonemessure — Charuco board (intrinsics)")
    c.setFont("Helvetica", 9)
    c.drawCentredString(
        page_w / 2, page_h - 18 * mm,
        f"{BOARD_SQUARES_X}×{BOARD_SQUARES_Y}, square {BOARD_SQUARE_MM:.1f} mm, "
        f"marker {BOARD_MARKER_MM:.1f} mm, dict {BOARD_DICT}",
    )
    c.setFont("Helvetica", 8)
    c.drawCentredString(
        page_w / 2, page_h - 22.5 * mm,
        "Print at 100% scale. Capture 12-20 frames covering corners + tilts. "
        "Keep the board flat (glue to cardstock if possible).",
    )

    c.showPage()
    c.save()
