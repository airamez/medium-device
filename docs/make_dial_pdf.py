#!/usr/bin/env python3
"""Write print-at-100% letter dials (A–Z, 0–9 every 10°)."""

from __future__ import annotations

import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
IN = 72.0
LETTER = (612.0, 792.0)  # 8.5 x 11
TABLOID = (792.0, 1224.0)  # 11 x 17

CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
H_BOLD = {
    "A": 722, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778,
    "H": 722, "I": 278, "J": 500, "K": 667, "L": 556, "M": 833, "N": 722,
    "O": 778, "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722,
    "V": 667, "W": 944, "X": 667, "Y": 667, "Z": 611,
    "0": 556, "1": 556, "2": 556, "3": 556, "4": 556,
    "5": 556, "6": 556, "7": 556, "8": 556, "9": 556,
}
REFS = set("AEJNSW15")


def pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def circle(cx: float, cy: float, r: float) -> str:
    k = 0.5522847498 * r
    return (
        f"{cx + r:.3f} {cy:.3f} m\n"
        f"{cx + r:.3f} {cy + k:.3f} {cx + k:.3f} {cy + r:.3f} {cx:.3f} {cy + r:.3f} c\n"
        f"{cx - k:.3f} {cy + r:.3f} {cx - r:.3f} {cy + k:.3f} {cx - r:.3f} {cy:.3f} c\n"
        f"{cx - r:.3f} {cy - k:.3f} {cx - k:.3f} {cy - r:.3f} {cx:.3f} {cy - r:.3f} c\n"
        f"{cx + k:.3f} {cy - r:.3f} {cx + r:.3f} {cy - k:.3f} {cx + r:.3f} {cy:.3f} c\n"
        "s\n"
    )


def polar(cx: float, cy: float, i: float, r: float) -> tuple[float, float]:
    """A at north (top), then clockwise every 10°."""
    std = math.radians(90.0 - i * 10.0)
    return cx + r * math.cos(std), cy + r * math.sin(std)


def text_at(x: float, y: float, s: str, size: float) -> str:
    w = H_BOLD[s] / 1000.0 * size
    return (
        f"BT /F1 {size:.2f} Tf {x - w / 2.0:.3f} {y - size * 0.35:.3f} Td "
        f"({pdf_escape(s)}) Tj ET\n"
    )


def build_stream(inches: float, page_w: float, page_h: float) -> str:
    radius = inches * IN / 2.0
    cx, cy = page_w / 2.0, page_h / 2.0 + 18.0
    scale = inches / 7.0
    body = 14.0 * scale
    ref = 16.0 * scale
    tick = 0.18 * IN * min(scale, 1.15)
    tick_ref = 0.28 * IN * min(scale, 1.15)
    letter_r = radius - 0.42 * IN * min(scale, 1.2)

    parts: list[str] = []
    parts.append("1.5 w 0 0 0 RG\n")
    parts.append(circle(cx, cy, radius))

    parts.append("0.4 w 0.55 0.55 0.55 RG\n")
    parts.append(circle(cx, cy, letter_r - 0.18 * IN * min(scale, 1.2)))

    parts.append("0.6 w 0 0 0 RG\n")
    parts.append(circle(cx, cy, 4.0 / 25.4 * IN))
    parts.append("0.4 w\n")
    parts.append(f"{cx - 8:.2f} {cy:.2f} m {cx + 8:.2f} {cy:.2f} l S\n")
    parts.append(f"{cx:.2f} {cy - 8:.2f} m {cx:.2f} {cy + 8:.2f} l S\n")

    for i in range(36):
        ch = CHARS[i]
        x1, y1 = polar(cx, cy, i, radius)
        x0, y0 = polar(cx, cy, i, radius - (tick_ref if ch in REFS else tick))
        if ch in REFS:
            parts.append("1.2 w 0 0 0 RG\n")
        else:
            parts.append("0.6 w 0 0 0 RG\n")
        parts.append(f"{x0:.3f} {y0:.3f} m {x1:.3f} {y1:.3f} l S\n")

        gx0, gy0 = polar(cx, cy, i + 0.5, radius - 0.10 * IN)
        gx1, gy1 = polar(cx, cy, i + 0.5, radius)
        parts.append("0.3 w 0.6 0.6 0.6 RG\n")
        parts.append(f"{gx0:.3f} {gy0:.3f} m {gx1:.3f} {gy1:.3f} l S\n")

    parts.append("0 0 0 rg 0 0 0 RG\n")
    for i, ch in enumerate(CHARS):
        x, y = polar(cx, cy, i, letter_r)
        parts.append(text_at(x, y, ch, ref if ch in REFS else body))

    parts.append("0 0 0 rg\n")
    cap = f"{inches:g} in dial  -  36 marks x 10 deg  -  A at North  -  print at 100% (no fit-to-page)"
    cap_size = 8.0
    cap_w = len(cap) * cap_size * 0.42
    parts.append(
        f"BT /F1 {cap_size:.1f} Tf {cx - cap_w / 2.0:.2f} {cy - radius - 22:.2f} Td "
        f"({pdf_escape(cap)}) Tj ET\n"
    )
    note = "Cut on the outer circle. Align the 8 mm center mark with the bearing."
    note_w = len(note) * 8.0 * 0.40
    parts.append(
        f"BT /F1 8 Tf {cx - note_w / 2.0:.2f} {cy - radius - 34:.2f} Td "
        f"({pdf_escape(note)}) Tj ET\n"
    )
    return "".join(parts)


def write_pdf(path: Path, stream: str, page_w: float, page_h: float) -> None:
    stream_b = stream.encode("latin-1")
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R "
            + f"/MediaBox [0 0 {page_w:.2f} {page_h:.2f}] ".encode("ascii")
            + b"/Contents 4 0 R "
            + b"/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n" % len(stream_b) + stream_b + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii")
        out += obj
        out += b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (
        b"trailer\n"
        + f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
        + b"startxref\n"
        + f"{xref_pos}\n".encode("ascii")
        + b"%%EOF\n"
    )
    path.write_bytes(out)


def make(inches: float) -> Path:
    page = LETTER if inches <= 7.5 else TABLOID
    path = HERE / f"dial-{inches:g}in.pdf"
    write_pdf(path, build_stream(inches, *page), *page)
    return path


if __name__ == "__main__":
    for size in (6, 9, 10):
        out = make(size)
        print(f"Wrote {out}")
