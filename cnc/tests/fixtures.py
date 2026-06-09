"""Synthetic STL generators so tests need no binary fixture files."""
from __future__ import annotations

import struct


def cube_stl(size_mm: float = 50.0) -> bytes:
    """A binary STL of an axis-aligned cube from origin to (size,size,size)."""
    s = size_mm
    v = [
        (0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0),  # bottom
        (0, 0, s), (s, 0, s), (s, s, s), (0, s, s),  # top
    ]
    # 12 triangles, outward winding.
    faces = [
        (0, 3, 2), (0, 2, 1),  # bottom (z=0, normal -z)
        (4, 5, 6), (4, 6, 7),  # top (z=s, +z)
        (0, 1, 5), (0, 5, 4),  # front (y=0, -y)
        (2, 3, 7), (2, 7, 6),  # back (y=s, +y)
        (1, 2, 6), (1, 6, 5),  # right (x=s, +x)
        (3, 0, 4), (3, 4, 7),  # left (x=0, -x)
    ]
    out = bytearray(b"\x00" * 80)
    out += struct.pack("<I", len(faces))
    for a, b, c in faces:
        out += struct.pack("<3f", 0.0, 0.0, 0.0)  # normal (ignored on read)
        for idx in (a, b, c):
            out += struct.pack("<3f", *v[idx])
        out += struct.pack("<H", 0)
    return bytes(out)
