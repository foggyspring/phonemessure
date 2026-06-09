"""G-code time integrator: exact on known programs."""
from __future__ import annotations

from cnc.estimators.gcode_time import estimate_gcode


def test_single_feed_move_no_accel():
    # G1 X100 at F600 (mm/min) with no accel => 100/600 min = 10 s.
    g = "G21\nG90\nG1 X100 F600\n"
    t = estimate_gcode(g, accel_mm_s2=None)
    assert abs(t.cut_distance_mm - 100.0) < 1e-6
    assert abs(t.seconds - 10.0) < 1e-6
    assert t.moves == 1


def test_rapid_vs_cut_split():
    g = "G21 G90\nG0 X50\nG1 X150 F1000\n"
    t = estimate_gcode(g, rapid_mm_min=6000, accel_mm_s2=None)
    assert abs(t.rapid_distance_mm - 50.0) < 1e-6   # 50mm rapid
    assert abs(t.cut_distance_mm - 100.0) < 1e-6    # 100mm cut
    assert abs(t.rapid_seconds - 0.5) < 1e-6        # 50/6000 min = 0.5s
    assert abs(t.cut_seconds - 6.0) < 1e-6          # 100/1000 min = 6s


def test_imperial_units_g20():
    # G20 inch: X1 = 25.4 mm.
    g = "G20 G90\nG1 X1 F60\n"   # F60 in/min = 1524 mm/min
    t = estimate_gcode(g, accel_mm_s2=None)
    assert abs(t.cut_distance_mm - 25.4) < 1e-4


def test_accel_slows_short_segments():
    # With accel, a short move can't reach feed -> takes longer than dist/feed.
    g = "G21 G90\nG1 X2 F6000\n"   # ideal: 2/6000 min = 0.02s
    fast = estimate_gcode(g, accel_mm_s2=None).seconds
    slow = estimate_gcode(g, accel_mm_s2=300).seconds
    assert slow > fast


def test_arc_length_quarter_circle():
    # G2 quarter circle radius 10 from (10,0) to (0,10), centre (0,0).
    g = "G21 G90\nG0 X10 Y0\nG3 X0 Y10 I-10 J0 F1000\n"
    t = estimate_gcode(g, accel_mm_s2=None)
    import math
    assert abs(t.cut_distance_mm - (math.pi / 2 * 10)) < 0.2
