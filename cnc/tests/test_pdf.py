"""PDF rendering smoke test: a quote payload must produce a valid PDF."""
from __future__ import annotations

from cnc.geometry import Hole, metrics_from_stl_bytes
from cnc.quote import build_quote_pdf
from cnc.service import QuoteRequest, build_quote
from cnc.tests.fixtures import cube_stl


def test_build_quote_pdf_is_valid():
    metrics = metrics_from_stl_bytes(cube_stl(40.0))
    payload = build_quote(
        metrics,
        QuoteRequest(
            material="AL6061",
            quantity=10,
            finish="anodize_clear",
            tight_tolerance=True,
            holes=[Hole(diameter_mm=6.0, depth_mm=20.0, count=4, threaded=True)],
            part_name="unit-test-bracket",
        ),
    )
    pdf = build_quote_pdf(payload)
    assert pdf[:5] == b"%PDF-"          # valid PDF header
    assert len(pdf) > 2000              # has real content (tables + embedded font)
    assert b"%%EOF" in pdf[-1024:]      # properly terminated
