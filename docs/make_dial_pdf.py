#!/usr/bin/env python3
"""Write print-at-100% letter dials (A–Z, 0–9 every 10°) into base-templates/."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "base-templates"
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


@dataclass(frozen=True)
class Style:
    """One printable layout. suffix is appended to dial-{n}in."""

    suffix: str
    caption: str
    big: bool
    spokes: bool
    box: bool


STYLES = (
    Style("", "spokes", False, True, False),
    Style("-nolines", "no spokes", False, False, False),
    Style("-big", "big letters, spokes", True, True, False),
    Style("-big-nolines", "big letters, no spokes", True, False, False),
    Style("-box", "aiming boxes", False, True, True),
    Style("-big-box", "big letters, aiming boxes", True, True, True),
)


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


def glyph_width(s: str, size: float) -> float:
    return H_BOLD[s] / 1000.0 * size


def text_at(x: float, y: float, s: str, size: float) -> str:
    w = glyph_width(s, size)
    return (
        f"BT /F1 {size:.2f} Tf {x - w / 2.0:.3f} {y - size * 0.35:.3f} Td "
        f"({pdf_escape(s)}) Tj ET\n"
    )


def radial_unit(i: float) -> tuple[float, float]:
    """Unit vector from center toward mark i (A = north, clockwise)."""
    std = math.radians(90.0 - i * 10.0)
    return math.cos(std), math.sin(std)


def stroke_rect(
    cx: float,
    cy: float,
    ux: float,
    uy: float,
    tx: float,
    ty: float,
    half_rad: float,
    half_tan: float,
) -> str:
    """Axis-aligned to the radial/tangential frame, not the page."""
    corners = []
    for sr, st in ((1.0, 1.0), (1.0, -1.0), (-1.0, -1.0), (-1.0, 1.0)):
        corners.append(
            (
                cx + ux * half_rad * sr + tx * half_tan * st,
                cy + uy * half_rad * sr + ty * half_tan * st,
            )
        )
    x0, y0 = corners[0]
    parts = [f"{x0:.3f} {y0:.3f} m"]
    for x, y in corners[1:]:
        parts.append(f"{x:.3f} {y:.3f} l")
    parts.append("h S")
    return " ".join(parts) + "\n"


def letter_sizes(inches: float, big: bool) -> tuple[float, float]:
    scale = inches / 7.0
    if big:
        cap = min(scale, 1.25)
        return 22.0 * cap, 26.0 * cap
    cap = min(scale, 1.15)
    return 13.0 * cap, 15.0 * cap


def build_stream(inches: float, page_w: float, page_h: float, style: Style) -> str:
    radius = inches * IN / 2.0
    # Leave room under the south letters for the caption.
    extra = 10.0 if style.big else 0.0
    cy_shift = (28.0 if inches <= 7 else 36.0) + extra
    cx, cy = page_w / 2.0, page_h / 2.0 + cy_shift
    body, ref = letter_sizes(inches, style.big)
    hub = 4.0 / 25.4 * IN
    # Square just inside the cut circle, on the wood, right before the letter.
    box_side = (0.20 if style.big else 0.16) * IN
    box_center_r = radius - box_side / 2.0 - 0.4

    parts: list[str] = []
    parts.append("1.4 w 0 0 0 RG\n")
    parts.append(circle(cx, cy, radius))

    for i, ch in enumerate(CHARS):
        size = ref if ch in REFS else body
        bar = glyph_width(ch, size)
        ux, uy = radial_unit(i)
        tx, ty = -uy, ux
        if style.spokes:
            if style.box:
                end_r = box_center_r - box_side / 2.0
            else:
                # End just outside the cut circle, immediately before the letter.
                end_r = radius + 0.05 * IN
            if ch in REFS:
                parts.append("1.1 w 0 0 0 RG\n")
            else:
                parts.append("0.55 w 0 0 0 RG\n")
            parts.append(f"{cx:.3f} {cy:.3f} m {cx + ux * end_r:.3f} {cy + uy * end_r:.3f} l S\n")
            if not style.box:
                parts.append("1.05 w 0 0 0 RG\n")
                x1, y1 = cx + ux * end_r, cy + uy * end_r
                parts.append(
                    f"{x1 - tx * bar / 2:.3f} {y1 - ty * bar / 2:.3f} m "
                    f"{x1 + tx * bar / 2:.3f} {y1 + ty * bar / 2:.3f} l S\n"
                )
        if style.box:
            bx, by = cx + ux * box_center_r, cy + uy * box_center_r
            parts.append("1.25 w 0 0 0 RG 1 1 1 rg\n")
            parts.append(
                stroke_rect(
                    bx, by, ux, uy, tx, ty, box_side / 2.0, box_side / 2.0
                ).replace("h S", "h B")
            )
            parts.append("0 0 0 RG 0 0 0 rg\n")

    parts.append("0.7 w 0 0 0 RG\n")
    parts.append(circle(cx, cy, hub))
    parts.append("0.45 w\n")
    parts.append(f"{cx - 8:.2f} {cy:.2f} m {cx + 8:.2f} {cy:.2f} l S\n")
    parts.append(f"{cx:.2f} {cy - 8:.2f} m {cx:.2f} {cy + 8:.2f} l S\n")

    parts.append("0 0 0 rg 0 0 0 RG\n")
    for i, ch in enumerate(CHARS):
        size = ref if ch in REFS else body
        letter_r = radius + 0.10 * IN + size * 0.48
        if style.box:
            # Keep the glyph clear of the aiming box on the rim.
            letter_r = max(letter_r, radius + 0.08 * IN + size * 0.55)
        x, y = polar(cx, cy, i, letter_r)
        parts.append(text_at(x, y, ch, size))

    parts.append("0 0 0 rg\n")
    cap = (
        f"{inches:g} in dial  -  {style.caption}  -  36 marks x 10 deg  -  "
        f"A at North  -  print at 100% (no fit-to-page)"
    )
    cap_size = 8.0
    cap_w = len(cap) * cap_size * 0.42
    south = radius + 0.10 * IN + ref * 0.95
    parts.append(
        f"BT /F1 {cap_size:.1f} Tf {cx - cap_w / 2.0:.2f} {cy - south - 18:.2f} Td "
        f"({pdf_escape(cap)}) Tj ET\n"
    )
    note = "Cut on the circle. Letters sit outside. Align the 8 mm center with the bearing."
    if style.box:
        note = (
            "Cut on the circle. Aim the needle into the small box before each letter. "
            "Align the 8 mm center with the bearing."
        )
    note_w = len(note) * 8.0 * 0.40
    parts.append(
        f"BT /F1 8 Tf {cx - note_w / 2.0:.2f} {cy - south - 30:.2f} Td "
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


def make(inches: float, style: Style) -> Path:
    # Letters sit outside the circle, so 8" and up need 11×17.
    page = LETTER if inches <= 7 else TABLOID
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"dial-{inches:g}in{style.suffix}.pdf"
    write_pdf(path, build_stream(inches, *page, style), *page)
    return path


if __name__ == "__main__":
    for size in (6, 7, 8, 9, 10):
        for style in STYLES:
            out = make(size, style)
            print(f"Wrote {out}")
