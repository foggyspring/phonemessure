"""Printable calibration sheet generator.

A4 page with four solid-black fiducial squares at known mm positions plus a
crosshair and ruler ticks for visual sanity check.

We use plain square fiducials (not full ArUco) because:
  * detection in the browser stays tiny (no opencv.js / no library bundle)
  * 4 corner squares with a known rectangle geometry are enough for a
    full planar homography
  * each marker gets a printed numeric ID so the user can still verify
    orientation by eye
"""
from __future__ import annotations

from typing import BinaryIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


# Spec for the layout (mm). These numbers are also baked into the JS
# calibration code — change them in both places together.
MARGIN_MM = 25.0           # distance from page edge to marker center
RECT_W_MM = A4[0] / mm - 2 * MARGIN_MM  # ~160 mm
RECT_H_MM = A4[1] / mm - 2 * MARGIN_MM  # ~247 mm


def build_aruco_pdf(out: BinaryIO, marker_mm: float = 40.0) -> None:
    c = canvas.Canvas(out, pagesize=A4)
    page_w, page_h = A4
    half = marker_mm / 2.0 * mm

    centers_mm = [
        (MARGIN_MM, MARGIN_MM),                           # bottom-left in PDF coords
        (MARGIN_MM + RECT_W_MM, MARGIN_MM),               # bottom-right
        (MARGIN_MM + RECT_W_MM, MARGIN_MM + RECT_H_MM),   # top-right
        (MARGIN_MM, MARGIN_MM + RECT_H_MM),               # top-left
    ]
    labels = ["BL 1", "BR 2", "TR 3", "TL 4"]

    # Markers
    c.setFillColorRGB(0, 0, 0)
    for (cx_mm, cy_mm), label in zip(centers_mm, labels):
        cx, cy = cx_mm * mm, cy_mm * mm
        c.rect(cx - half, cy - half, 2 * half, 2 * half, stroke=0, fill=1)
        # white inset square with the label, makes orientation visible
        inset = half * 0.55
        c.setFillColorRGB(1, 1, 1)
        c.rect(cx - inset, cy - inset, 2 * inset, 2 * inset, stroke=0, fill=1)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", marker_mm * 0.35 * mm / mm)
        c.drawCentredString(cx, cy - marker_mm * 0.12 * mm, label)

    # Title + instructions
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(page_w / 2, page_h - 12 * mm, "phonemessure calibration sheet")
    c.setFont("Helvetica", 9)
    c.drawCentredString(
        page_w / 2,
        page_h - 18 * mm,
        f"Marker rectangle: {RECT_W_MM:.1f} x {RECT_H_MM:.1f} mm (centers)  "
        f"|  Marker size: {marker_mm:.0f} mm",
    )
    c.drawCentredString(
        page_w / 2,
        page_h - 23 * mm,
        "Print at 100% scale (no fit-to-page). Lay flat in frame. All four markers must be visible.",
    )

    # 10 mm reference ticks along the bottom
    tick_y = 10 * mm
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.4)
    for i in range(0, int(RECT_W_MM) + 1, 10):
        x = (MARGIN_MM + i) * mm
        h = 4 * mm if i % 50 == 0 else 2 * mm
        c.line(x, tick_y, x, tick_y + h)
    c.setFont("Helvetica", 7)
    c.drawString(MARGIN_MM * mm, tick_y - 3 * mm, "0 mm")
    c.drawString((MARGIN_MM + 100) * mm - 5 * mm, tick_y - 3 * mm, "100 mm")

    c.showPage()
    c.save()
