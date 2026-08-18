#!/usr/bin/env python3
"""Read angles from the Nano and map a 360° circle to A–Z and 0–9.

Once: --calibrate walks every letter and saves the raw angles (handles
uneven marks and a warped sensor). Each session: confirm A, J, S, 1 so a
moved base can be aligned to that saved map.

Examples:
  python capture.py --calibrate        # save all 36 letter positions
  python capture.py                    # confirm A J S 1, then type
  python capture.py --debug            # live pointer angle
  python capture.py --diagnostic       # Enter + type the printed letter; log mapping
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import serial
except ImportError:
    sys.exit(
        "pyserial is not installed.\n"
        "Arch:  sudo pacman -S python-pyserial\n"
        "venv:  source .venv/bin/activate && pip install pyserial"
    )

CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"  # 36 symbols
SECTOR_DEG = 360.0 / len(CHARS)  # 10° between sticker centers
# Four printed-dial cardinals (A at north, clockwise).
CAL_REFS = ("A", "J", "S", "1")
CARDINAL_MIN_SEP_DEG = 25.0  # A/J/S/1 are ~90°; 31.5 and 31.4 is the same tap
CARDINAL_SAVED_TOL_DEG = 30.0  # session tap vs A-shifted saved mark
COMPASS_STEP_TOL_DEG = 20.0
# ~10° per mark: letter in the middle, 3.5° space between letter edges.
GAP_BETWEEN_DEG = 3.5
LETTER_DEG = SECTOR_DEG - GAP_BETWEEN_DEG  # 6.5°
HALF_LETTER_DEG = LETTER_DEG / 2.0
GAP_SIDE_DEG = GAP_BETWEEN_DEG / 2.0
DEFAULT_DELAY_S = 1.0
DEFAULT_WRAP_COLS = 60
DEFAULT_STILL_TOL_DEG = 2.0  # parked if the live angle stays within this of the lock
CAL_MIN_STEP_DEG = 2.0  # two consecutive --calibrate taps closer than this are the same mark
CAL_REWIND = 2  # on a too-close tap, recapture this many previous letters as well
STILL_SAMPLES = 4  # same letter this many times = stopped
SPACE_STILL_SAMPLES = 25  # gaps need a real pause; skip letter-to-letter transit
START_MOVE_DEG = 5.0  # after ready, ignore the parked letter until the needle moves
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def backup_dir() -> Path:
    return CONFIG_PATH.parent / "config-backups"


def guess_port() -> str | None:
    candidates = sorted(
        glob.glob("/dev/ttyUSB*")
        + glob.glob("/dev/ttyACM*")
        + glob.glob("/dev/cu.usbserial*")
        + glob.glob("/dev/cu.wchusbserial*")
    )
    return candidates[0] if candidates else None


def parse_angle(line: str) -> float | None:
    line = line.strip()
    if not line.startswith("a="):
        return None
    value = line[2:].strip()
    if value == "ERR":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def int_deg(angle: float) -> int:
    """Drop decimals. 23.6 / 23.7 / 23.8 all become 23."""
    return int(angle) % 360


def int_circular_delta(a: int, b: int) -> int:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def still_on_mark(ang_i: int, ref_i: int) -> bool:
    """True if the integer reading is still in the same 6° letter (or 4° gap)."""
    return int_circular_delta(ang_i, ref_i) <= int(HALF_LETTER_DEG)  # 3°


def still_on_angle(angle: float, ref: float, tol_deg: float) -> bool:
    """Parked if the live (decimal) angle is within tol of the lock.

    10.9 and 11.0 are 0.1° apart — still stopped. A 10° step is not.
    """
    return circular_delta(angle, ref) <= tol_deg


def circular_nudge(ref: float, angle: float, weight: float = 0.25) -> float:
    """Slide the lock a little toward the new sample so 10.9/11.0 cluster."""
    wr = 1.0 - weight
    s = wr * math.sin(math.radians(ref)) + weight * math.sin(math.radians(angle))
    c = wr * math.cos(math.radians(ref)) + weight * math.cos(math.radians(angle))
    return math.degrees(math.atan2(s, c)) % 360.0


def circular_delta(a: float, b: float) -> float:
    d = abs(b - a) % 360.0
    return min(d, 360.0 - d)


def directed_delta(start: float, end: float, invert: bool) -> float:
    """Travel from start to end in the letter-order direction (0–360)."""
    if invert:
        return (start - end) % 360.0
    return (end - start) % 360.0


def circular_mean(angles: list[float]) -> float:
    s = sum(math.sin(math.radians(a)) for a in angles)
    c = sum(math.cos(math.radians(a)) for a in angles)
    return math.degrees(math.atan2(s, c)) % 360.0


def circular_range(angles: list[float]) -> float:
    """Smallest arc that covers every sample (handles 359.8 / 0.2)."""
    if len(angles) < 2:
        return 0.0
    ordered = sorted(a % 360.0 for a in angles)
    gaps = [ordered[i + 1] - ordered[i] for i in range(len(ordered) - 1)]
    gaps.append(ordered[0] + 360.0 - ordered[-1])
    return 360.0 - max(gaps)


class RestWindow:
    """The needle is stopped when delay_s of samples all fit in still_tol_deg.

    That is the right test: 10.9 and 11.0 are 0.1° apart (stopped).
    Sliding 10° to the next letter opens a range bigger than the tolerance.
    """

    def __init__(self, hold_s: float, tol_deg: float) -> None:
        self.hold_s = hold_s
        self.tol_deg = tol_deg
        self.samples: list[tuple[float, float]] = []

    def add(self, now: float, angle: float) -> None:
        self.samples.append((now, angle))
        # Keep a little extra so the window can actually reach hold_s
        # (discrete samples are always slightly newer than now - hold_s).
        cut = now - self.hold_s - 0.15
        self.samples = [(t, a) for t, a in self.samples if t >= cut]

    def clear(self) -> None:
        self.samples.clear()

    def ready(self, now: float) -> bool:
        if len(self.samples) < 2:
            return False
        covered = now - self.samples[0][0]
        if covered < self.hold_s * 0.85:
            return False
        return circular_range([a for _, a in self.samples]) <= self.tol_deg

    def mean(self) -> float:
        return circular_mean([a for _, a in self.samples])


class LetterHold:
    """Type when the decoded letter stays the same for hold_s.

    Analog jitter of a few degrees is still the same letter on a 10° map.
    RestWindow's 2° band treated that jitter as 'still moving' and never typed.
    """

    def __init__(self, hold_s: float) -> None:
        self.hold_s = hold_s
        self.char: str | None = None
        self.t0: float | None = None

    def update(self, now: float, char: str) -> bool:
        if char != self.char or self.t0 is None:
            self.char = char
            self.t0 = now
            return False
        return (now - self.t0) >= self.hold_s * 0.85

    def held_s(self, now: float) -> float:
        if self.t0 is None:
            return 0.0
        return max(0.0, now - self.t0)

    def clear(self) -> None:
        self.char = None
        self.t0 = None


def char_dial(ch: str) -> float:
    """Dial degrees at the center of a letter sticker (A = 0°, B = 10°, …)."""
    return CHARS.index(ch) * SECTOR_DEG


def make_point(ch: str, angle: float) -> dict:
    return {"char": ch, "angle": float(angle), "dial": char_dial(ch)}


def signed_circular_shift(old: float, new: float) -> float:
    """Smallest signed turn from old to new, in (-180, 180]."""
    return (new - old + 180.0) % 360.0 - 180.0


def shift_points_to_new_a(points: list[dict], new_a: float) -> tuple[list[dict], float]:
    """Rotate every saved mark so A sits on new_a. Relative gaps stay the same.

    Returns (shifted points, signed shift in degrees).
    """
    old_a = next((p for p in points if p["char"] == "A"), None)
    if old_a is None:
        raise ValueError("No saved A mark")
    shift = signed_circular_shift(old_a["angle"], new_a)
    shifted = [make_point(p["char"], (p["angle"] + shift) % 360.0) for p in points]
    return shifted, shift


def offset_angle(angle: float, session_a: float, saved_a: float) -> float:
    """Map a live reading into the saved calibration frame using this session's A."""
    return (angle - session_a + saved_a) % 360.0


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {
            "offset": 0.0,
            "invert": False,
            "points": [],
            "delay_s": DEFAULT_DELAY_S,
            "wrap_cols": DEFAULT_WRAP_COLS,
            "still_tol_deg": DEFAULT_STILL_TOL_DEG,
        }
    data = json.loads(CONFIG_PATH.read_text())
    points = []
    for item in data.get("points") or []:
        ch = str(item.get("char", "")).upper()
        if ch not in CHARS:
            continue
        points.append(make_point(ch, item["angle"]))
    by_char = {p["char"]: p for p in points}
    points = [by_char[c] for c in CHARS if c in by_char]
    offset = float(data.get("offset", points[0]["angle"] if points else 0.0))
    delay_s = float(data.get("delay_s", DEFAULT_DELAY_S))
    if delay_s <= 0:
        delay_s = DEFAULT_DELAY_S
    wrap_cols = int(data.get("wrap_cols", DEFAULT_WRAP_COLS))
    if wrap_cols < 10:
        wrap_cols = DEFAULT_WRAP_COLS
    still_tol_deg = float(data.get("still_tol_deg", DEFAULT_STILL_TOL_DEG))
    if still_tol_deg <= 0:
        still_tol_deg = DEFAULT_STILL_TOL_DEG
    return {
        "offset": offset,
        "invert": bool(data.get("invert", False)),
        "points": points,
        "delay_s": delay_s,
        "wrap_cols": wrap_cols,
        "still_tol_deg": still_tol_deg,
    }


def save_config(
    points: list[dict],
    invert: bool = False,
    delay_s: float | None = None,
    wrap_cols: int | None = None,
    still_tol_deg: float | None = None,
) -> None:
    cfg = load_config()
    if delay_s is None:
        delay_s = cfg["delay_s"]
    if wrap_cols is None:
        wrap_cols = cfg["wrap_cols"]
    if wrap_cols < 10:
        wrap_cols = DEFAULT_WRAP_COLS
    if still_tol_deg is None:
        still_tol_deg = cfg["still_tol_deg"]
    if still_tol_deg <= 0:
        still_tol_deg = DEFAULT_STILL_TOL_DEG
    a = next(
        (p["angle"] for p in points if p["char"] == "A"),
        points[0]["angle"] if points else 0.0,
    )
    payload = {
        "offset": round(float(a), 3),
        "invert": invert,
        "delay_s": round(float(delay_s), 3),
        "wrap_cols": int(wrap_cols),
        "still_tol_deg": round(float(still_tol_deg), 3),
        "points": [
            {"char": p["char"], "angle": round(p["angle"], 3)} for p in points
        ],
    }
    CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def list_config_backups() -> list[Path]:
    folder = backup_dir()
    if not folder.is_dir():
        return []
    return sorted(folder.glob("config_*.json"))


def backup_config() -> Path | None:
    """Copy the current config.json aside. None if there is nothing to save."""
    if not CONFIG_PATH.exists() or CONFIG_PATH.stat().st_size == 0:
        return None
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y_%m_%d_%H_%M")
    dest = folder / f"config_{stamp}.json"
    n = 2
    while dest.exists():
        dest = folder / f"config_{stamp}_{n}.json"
        n += 1
    dest.write_bytes(CONFIG_PATH.read_bytes())
    return dest


def restore_config(name: str) -> Path:
    """Copy a backup over config.json. `name` is a file, stem, or 'latest'."""
    backups = list_config_backups()
    if not backups:
        raise FileNotFoundError("No backups in " + str(backup_dir()))
    if name in ("latest", "last"):
        src = backups[-1]
    else:
        key = Path(name).name
        src = None
        for item in backups:
            if item.name == key or item.stem == key or item.name == key + ".json":
                src = item
                break
        if src is None:
            raise FileNotFoundError(f"No backup named {name}")
    backup_config()
    CONFIG_PATH.write_bytes(src.read_bytes())
    return src


def cmd_restore(name: str) -> int:
    """List or restore a --calibrate backup. Does not need the Nano."""
    if name in ("list", ""):
        backups = list_config_backups()
        if not backups:
            print(f"No backups in {backup_dir()}")
            return 1
        print(f"Backups in {backup_dir()}:")
        for item in backups:
            print(f"  {item.name}")
        print("\nRestore one:")
        print(f"  python capture.py --restore latest")
        print(f"  python capture.py --restore {backups[-1].name}")
        return 0
    try:
        src = restore_config(name)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    print(f"Restored {src.name} → {CONFIG_PATH.name}")
    print("The previous live file was copied into config-backups/ first.")
    return 0


def detect_invert(points: list[dict]) -> bool:
    """True if raw angles decrease while letters go A → B → C."""
    if len(points) < 2:
        return False
    votes_inv = 0
    votes_fwd = 0
    for i, a in enumerate(points):
        b = points[(i + 1) % len(points)]
        dial_span = (b["dial"] - a["dial"]) % 360.0
        cw = (b["angle"] - a["angle"]) % 360.0
        ccw = (a["angle"] - b["angle"]) % 360.0
        if abs(ccw - dial_span) < abs(cw - dial_span):
            votes_inv += 1
        else:
            votes_fwd += 1
    return votes_inv > votes_fwd


def fit_offset(points: list[dict], invert: bool) -> float:
    """One global origin from the 8 marks. The printed dial is exact 10°.

    Piecewise interpolation treated hand-point error as real warping and
    shoved in-between letters into the 2° space bands.
    """
    if not points:
        return 0.0
    sign = -1.0 if invert else 1.0
    implied = [(p["angle"] - sign * p["dial"]) % 360.0 for p in points]
    return circular_mean(implied)


def points_consistent(
    points: list[dict], invert: bool, tol_deg: float = COMPASS_STEP_TOL_DEG
) -> bool:
    """True if marks go around the circle in letter order with a real gap.

    Handmade stickers are not exactly 10° apart. Interpolation needs order and
    a non-zero step, not a perfect 10° grid. A leftover wrap (e.g. 18° instead
    of 10°) is just slack from slightly short steps — that is usable.
    """
    del tol_deg  # kept so older callers/tests still pass the argument
    if len(points) < 2:
        return True
    n = len(points)
    for i in range(n):
        a = points[i]
        b = points[(i + 1) % n]
        got = directed_delta(a["angle"], b["angle"], invert)
        # < 2°: same spot twice. > 180°: went the long way = out of order.
        if got < 2.0 or got > 180.0:
            return False
    return True


def raw_to_dial(angle: float, points: list[dict], invert: bool) -> float:
    """Map a raw sensor angle onto the dial using piecewise interpolation."""
    if len(points) < 2:
        offset = points[0]["angle"] if points else 0.0
        dial = (angle - offset) % 360.0
        return (360.0 - dial) % 360.0 if invert else dial

    n = len(points)
    for i in range(n):
        a = points[i]
        b = points[(i + 1) % n]
        span = directed_delta(a["angle"], b["angle"], invert)
        if span < 1e-6:
            continue
        pos = directed_delta(a["angle"], angle, invert)
        if pos < span:
            t = pos / span
            dial_span = (b["dial"] - a["dial"]) % 360.0
            return (a["dial"] + t * dial_span) % 360.0

    nearest = min(points, key=lambda p: circular_delta(p["angle"], angle))
    return nearest["dial"]


def is_full_cal(points: list[dict]) -> bool:
    have = {p["char"] for p in points}
    return all(ch in have for ch in CHARS)


def ordered_refs(
    points: list[dict], names: tuple[str, ...] = CAL_REFS
) -> list[dict]:
    by = {p["char"]: p for p in points}
    missing = [n for n in names if n not in by]
    if missing:
        raise ValueError(f"Missing calibration marks: {', '.join(missing)}")
    return [by[n] for n in names]


def step_from(start: float, distance: float, invert: bool) -> float:
    """Travel `distance` from start in the letter-order direction."""
    if invert:
        return (start - distance) % 360.0
    return (start + distance) % 360.0


def map_through_refs(
    angle: float,
    src_refs: list[dict],
    dst_refs: list[dict],
    invert: bool,
) -> float:
    """Piecewise-map an angle from the src A/J/S/1 frame into the dst frame.

    Relative placement inside each quadrant is preserved as a fraction of
    that quadrant, so an off-center B stays off-center after the base moves.
    """
    if len(src_refs) != len(dst_refs) or len(src_refs) < 2:
        raise ValueError("Need matching A/J/S/1 refs")
    n = len(src_refs)
    for i in range(n):
        a = src_refs[i]
        b = src_refs[(i + 1) % n]
        span = directed_delta(a["angle"], b["angle"], invert)
        if span < 1e-6:
            continue
        pos = directed_delta(a["angle"], angle, invert)
        if pos < span or i == n - 1:
            t = pos / span
            da = dst_refs[i]
            db = dst_refs[(i + 1) % n]
            dst_span = directed_delta(da["angle"], db["angle"], invert)
            return step_from(da["angle"], t * dst_span, invert)
    nearest_i = min(
        range(n), key=lambda i: circular_delta(src_refs[i]["angle"], angle)
    )
    return dst_refs[nearest_i]["angle"] % 360.0


def align_cal_to_session(
    saved: list[dict],
    session_refs: list[dict],
    invert: bool,
) -> list[dict]:
    """Put the 36 saved marks into this session's sensor frame using A/J/S/1."""
    src = ordered_refs(saved)
    dst = ordered_refs(session_refs)
    return [
        make_point(p["char"], map_through_refs(p["angle"], src, dst, invert))
        for p in saved
    ]


def nearest_cal_char(angle: float, points: list[dict]) -> str:
    """Closest saved mark in raw sensor space. No 10° grid assumed."""
    if not points:
        return "?"
    return min(points, key=lambda p: circular_delta(p["angle"], angle))["char"]


def cal_char_or_space(
    angle: float, points: list[dict], gap_deg: float = GAP_BETWEEN_DEG
) -> str:
    """Nearest saved mark, or space if the needle sits in the 3–4° gap."""
    if not points:
        return "?"
    by = {p["char"]: p for p in points}
    ordered = [by[ch] for ch in CHARS if ch in by]
    if len(ordered) < 2:
        return nearest_cal_char(angle, points)
    n = len(ordered)
    i = min(range(n), key=lambda k: circular_delta(ordered[k]["angle"], angle))
    nearest = ordered[i]
    prev = ordered[(i - 1) % n]
    nxt = ordered[(i + 1) % n]
    d_near = circular_delta(nearest["angle"], angle)
    toward_prev = circular_delta(angle, prev["angle"])
    toward_next = circular_delta(angle, nxt["angle"])
    span = circular_delta(
        nearest["angle"],
        prev["angle"] if toward_prev < toward_next else nxt["angle"],
    )
    half = max(0.2, (span - gap_deg) / 2.0)
    if d_near <= half:
        return nearest["char"]
    return " "


def monotonic_cal_points(
    points: list[dict], invert: bool, min_step: float = 0.3
) -> tuple[list[dict], list[str]]:
    """Keep marks that go A→B→C… around the circle. Drop wrap-arounds.

    A real --calibrate file once stored 4–9 on top of A–C (9 sat between
    A and B). Nearest-neighbor then typed 9 on A and B on C.
    """
    by = {p["char"]: p for p in points}
    if "A" not in by:
        return [], [p["char"] for p in points]
    a_ang = by["A"]["angle"]
    kept = [by["A"]]
    dropped: list[str] = []
    last_pos = 0.0
    for ch in CHARS[1:]:
        if ch not in by:
            continue
        ang = by[ch]["angle"]
        pos = directed_delta(a_ang, ang, invert)
        if pos <= last_pos + min_step or pos >= 360.0 - min_step:
            dropped.append(ch)
            continue
        if any(circular_delta(ang, k["angle"]) < CAL_MIN_STEP_DEG for k in kept):
            dropped.append(ch)
            continue
        kept.append(by[ch])
        last_pos = pos
    return kept, dropped


def cal_mark_error(
    points: list[dict], angle: float, invert: bool
) -> str | None:
    """Why this tap cannot be the next letter, or None if it is fine."""
    if not points:
        return None
    a_ang = points[0]["angle"]
    last = points[-1]
    last_pos = (
        0.0
        if last["char"] == "A"
        else directed_delta(a_ang, last["angle"], invert)
    )
    pos = directed_delta(a_ang, angle, invert)
    if pos <= last_pos + 0.3:
        return f"not past {last['char']} — go clockwise toward the next tick"
    if pos >= 360.0 - CAL_MIN_STEP_DEG:
        return "that's back on A"
    for p in points[:-1]:
        if circular_delta(angle, p["angle"]) < CAL_MIN_STEP_DEG:
            return f"on top of {p['char']}"
    return None


def session_cardinal_warning(
    saved: list[dict], session_refs: list[dict]
) -> str | None:
    """A moved a lot but J/S/1 did not (or the reverse): likely the wrong letter."""
    try:
        saved_r = {p["char"]: p["angle"] for p in ordered_refs(saved)}
        sess_r = {p["char"]: p["angle"] for p in ordered_refs(session_refs)}
    except ValueError:
        return None
    shifts = {
        ch: signed_circular_shift(saved_r[ch], sess_r[ch]) for ch in CAL_REFS
    }
    others = [shifts[ch] for ch in ("J", "S", "1")]
    mid = sorted(others)[1]
    if abs(shifts["A"] - mid) > 8.0:
        return (
            f"A moved {shifts['A']:+.1f}° from the saved map but J/S/1 "
            f"moved about {mid:+.1f}°. Point at printed A (top), not C."
        )
    return None


def a_shift_expected(saved: list[dict], session_a: float) -> list[dict]:
    """Where the saved map would sit if only A moved (pure rotation)."""
    shifted, _shift = shift_points_to_new_a(saved, session_a)
    return shifted


def offset_to_dial(angle: float, offset: float, invert: bool) -> float:
    """A-centered dial degrees. invert=True if the chip counts opposite paper."""
    dial = (angle - offset) % 360.0
    if invert:
        dial = (360.0 - dial) % 360.0
    return dial


def locate_segment(angle: float, points: list[dict], invert: bool) -> dict:
    """Which calibrated arc contains this raw angle (same walk as raw_to_dial)."""
    if len(points) < 2:
        p = points[0] if points else {"char": "?", "angle": 0.0, "dial": 0.0}
        return {
            "from": p["char"],
            "to": p["char"],
            "from_raw": float(p["angle"]),
            "to_raw": float(p["angle"]),
            "from_dial": float(p["dial"]),
            "to_dial": float(p["dial"]),
            "raw_span": 0.0,
            "dial_span": 0.0,
            "pos": 0.0,
            "t": 0.0,
        }
    n = len(points)
    for i in range(n):
        a = points[i]
        b = points[(i + 1) % n]
        span = directed_delta(a["angle"], b["angle"], invert)
        if span < 1e-6:
            continue
        pos = directed_delta(a["angle"], angle, invert)
        if pos < span:
            dial_span = (b["dial"] - a["dial"]) % 360.0
            return {
                "from": a["char"],
                "to": b["char"],
                "from_raw": float(a["angle"]),
                "to_raw": float(b["angle"]),
                "from_dial": float(a["dial"]),
                "to_dial": float(b["dial"]),
                "raw_span": span,
                "dial_span": dial_span,
                "pos": pos,
                "t": pos / span,
            }
    nearest = min(points, key=lambda p: circular_delta(p["angle"], angle))
    return {
        "from": nearest["char"],
        "to": nearest["char"],
        "from_raw": float(nearest["angle"]),
        "to_raw": float(nearest["angle"]),
        "from_dial": float(nearest["dial"]),
        "to_dial": float(nearest["dial"]),
        "raw_span": 0.0,
        "dial_span": 0.0,
        "pos": 0.0,
        "t": 0.0,
    }


def cal_spans(points: list[dict], invert: bool) -> list[dict]:
    """Raw travel between consecutive cal marks (each printed cardinal is 90°)."""
    if len(points) < 2:
        return []
    out = []
    n = len(points)
    for i in range(n):
        a = points[i]
        b = points[(i + 1) % n]
        out.append(
            {
                "from": a["char"],
                "to": b["char"],
                "raw_span": directed_delta(a["angle"], b["angle"], invert),
                "dial_span": (b["dial"] - a["dial"]) % 360.0,
            }
        )
    return out


def parse_true_char(text: str) -> str | None:
    """Accept A–Z / 0–9 (any case). Empty or junk → None."""
    t = text.strip().upper()
    # `"" in CHARS` is True in Python — require exactly one symbol.
    if len(t) == 1 and t in CHARS:
        return t
    return None


def _mapper_view(pred: str, dial: float, true_dial: float, **extra) -> dict:
    view = {
        "pred": pred,
        "dial": round(dial, 3),
        "err_deg": round(signed_from_center(true_dial, dial), 3),
    }
    view.update(extra)
    return view


def diagnostic_sample(
    angle: float,
    true_char: str,
    points: list[dict],
    invert: bool,
    cal_points: list[dict] | None = None,
) -> dict:
    """Compare every mapper we have against the letter the user is pointing at.

    piecewise — current live mapper (interpolate A/J/S/1).
    global    — one offset fitted to all four marks (printed 10° grid).
    a_only    — A is origin, ignore J/S/1.
    flipped   — same piecewise walk with invert reversed.
    """
    true_char = true_char.upper()
    if true_char not in CHARS:
        raise ValueError(f"true_char must be one of {CHARS}")
    true_dial = char_dial(true_char)
    a_offset = next(
        (p["angle"] for p in points if p["char"] == "A"),
        points[0]["angle"] if points else 0.0,
    )
    global_offset = fit_offset(points, invert) if points else a_offset

    dial_pw = raw_to_dial(angle, points, invert) if points else offset_to_dial(
        angle, a_offset, invert
    )
    dial_g = offset_to_dial(angle, global_offset, invert)
    dial_a = offset_to_dial(angle, a_offset, invert)
    dial_flip = raw_to_dial(angle, points, not invert) if points else offset_to_dial(
        angle, a_offset, not invert
    )

    rec = {
        "raw": round(float(angle), 3),
        "true": true_char,
        "true_dial": true_dial,
        "invert": invert,
        "points": [
            {
                "char": p["char"],
                "angle": round(float(p["angle"]), 3),
                "dial": float(p["dial"]),
            }
            for p in points
        ],
        "segment": locate_segment(angle, points, invert),
        "piecewise": _mapper_view(dial_to_char(dial_pw), dial_pw, true_dial),
        "global": _mapper_view(
            dial_to_char(dial_g),
            dial_g,
            true_dial,
            offset=round(global_offset, 3),
        ),
        "a_only": _mapper_view(
            dial_to_char(dial_a),
            dial_a,
            true_dial,
            offset=round(a_offset, 3),
        ),
        "flipped": _mapper_view(dial_to_char(dial_flip), dial_flip, true_dial),
    }
    if cal_points:
        pred_cal = nearest_cal_char(angle, cal_points)
        rec["cal36"] = _mapper_view(pred_cal, char_dial(pred_cal), true_dial)
    return rec


def format_diagnostic_header(points: list[dict], invert: bool) -> str:
    point_txt = " ".join(f"{p['char']}={p['angle']:.2f}" for p in points)
    lines = [
        f"# invert={invert}",
        f"# points {point_txt}",
        "# cal spans (printed cardinals are 90° of dial):",
    ]
    for sp in cal_spans(points, invert):
        warn = ""
        if abs(sp["raw_span"] - 90.0) > 15.0:
            warn = "  ** WARPED (not ~90°) **"
        lines.append(
            f"#   {sp['from']}→{sp['to']}  raw={sp['raw_span']:.2f}°  "
            f"dial={sp['dial_span']:.1f}°{warn}"
        )
    lines.append(
        "# columns: raw sensor, printed letter you typed, then three mappers "
        "(piecewise / global-fit / A-only) and invert-flipped piecewise"
    )
    return "\n".join(lines) + "\n"


def format_diagnostic_block(sample: int, rec: dict, when: datetime) -> str:
    seg = rec["segment"]
    pw, gl, ao, fl = rec["piecewise"], rec["global"], rec["a_only"], rec["flipped"]
    lines = [
        f"# sample {sample}  {when.isoformat(timespec='seconds')}",
        (
            f"raw={rec['raw']:.3f}  true={rec['true']}  "
            f"true_dial={rec['true_dial']:.1f}  invert={rec['invert']}"
        ),
        (
            f"segment {seg['from']}→{seg['to']}  "
            f"t={seg['t']:.3f}  pos={seg['pos']:.3f}  "
            f"raw_span={seg['raw_span']:.3f}  "
            f"{seg['from']}={seg['from_raw']:.3f}  {seg['to']}={seg['to_raw']:.3f}"
        ),
        (
            f"piecewise  pred={pw['pred']}  dial={pw['dial']:.3f}  "
            f"err={pw['err_deg']:+.3f}°"
        ),
        (
            f"global     pred={gl['pred']}  dial={gl['dial']:.3f}  "
            f"err={gl['err_deg']:+.3f}°  offset={gl.get('offset', 0):.3f}"
        ),
        (
            f"a_only     pred={ao['pred']}  dial={ao['dial']:.3f}  "
            f"err={ao['err_deg']:+.3f}°  A={ao.get('offset', 0):.3f}"
        ),
        (
            f"flipped    pred={fl['pred']}  dial={fl['dial']:.3f}  "
            f"err={fl['err_deg']:+.3f}°"
        ),
    ]
    if "cal36" in rec:
        c36 = rec["cal36"]
        lines.append(
            f"cal36      pred={c36['pred']}  dial={c36['dial']:.3f}  "
            f"err={c36['err_deg']:+.3f}°"
        )
    lines.append(json.dumps(rec, sort_keys=True))
    return "\n".join(lines) + "\n"


def format_diagnostic_summary(records: list[dict]) -> str:
    if not records:
        return "No samples logged.\n"
    names = ("piecewise", "global", "a_only", "flipped")
    if any("cal36" in r for r in records):
        names = names + ("cal36",)
    lines = [f"Samples: {len(records)}"]
    for name in names:
        errs = [r[name]["err_deg"] for r in records]
        hits = sum(1 for r in records if r[name]["pred"] == r["true"])
        mean = sum(errs) / len(errs)
        absmean = sum(abs(e) for e in errs) / len(errs)
        lines.append(
            f"  {name:<10}  correct {hits}/{len(records)}  "
            f"mean err {mean:+.2f}°  mean |err| {absmean:.2f}°"
        )
    best = min(
        names,
        key=lambda n: sum(abs(r[n]["err_deg"]) for r in records),
    )
    lines.append(f"Lowest |error|: {best}")
    return "\n".join(lines) + "\n"


def nearest_letter_idx(dial: float) -> int:
    return int((dial + SECTOR_DEG / 2.0) // SECTOR_DEG) % len(CHARS)


def signed_from_center(center: float, dial: float) -> float:
    """Signed offset from center, in (-180, 180]."""
    return (dial - center + 180.0) % 360.0 - 180.0


def signed_turn(start: float, end: float) -> float:
    """Smallest signed turn from start to end, in (-180, 180]."""
    return (end - start + 180.0) % 360.0 - 180.0


def invert_from_clockwise_move(a_angle: float, toward_b: float) -> bool:
    """Paper is clockwise A→B. Invert if the sensor went the other way."""
    return signed_turn(a_angle, toward_b) < 0


def match_char(angle: float, a_offset: float, invert: bool = False) -> str:
    """Map a sensor angle to A–Z / 0–9, or space in the gap."""
    return dial_to_char(offset_to_dial(angle, a_offset, invert))


def dial_to_char(dial: float, gap_deg: float = GAP_BETWEEN_DEG) -> str:
    """Letter if inside the 6.5° window around a 10° center, else space."""
    idx = nearest_letter_idx(dial)
    center = idx * SECTOR_DEG
    half = (SECTOR_DEG - gap_deg) / 2.0
    if abs(signed_from_center(center, dial)) <= half:
        return CHARS[idx]
    return " "


def session_a_offset(points: list[dict], fallback: float = 0.0) -> float:
    return next((p["angle"] for p in points if p["char"] == "A"), fallback)


def live_char(
    angle: float,
    points: list[dict],
    invert: bool,
    offset: float = 0.0,
) -> str:
    """What typing uses: aligned saved mark, or space in the gap."""
    if len(points) >= 2:
        return cal_char_or_space(angle, points)
    return match_char(angle, session_a_offset(points, offset), invert)


def angle_to_char(
    angle: float,
    offset: float = 0.0,
    invert: bool = False,
    points: list[dict] | None = None,
) -> str:
    """Piecewise interpolation when points are given (diagnostic / tests only)."""
    if points:
        dial = raw_to_dial(angle, points, invert)
    else:
        return match_char(angle, offset, invert)
    return dial_to_char(dial)


def log_glyph(char: str) -> str:
    return "SP" if char == " " else char


_SPEAK_WORDS = {
    " ": "space",
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
}
_speak_lock = threading.Lock()
_speak_engine = None
_speak_warned = False


def speak_word(char: str) -> str:
    return _SPEAK_WORDS.get(char, char)


def _espeak_cmd() -> list[str] | None:
    for name in ("espeak-ng", "espeak", "spd-say"):
        path = shutil.which(name)
        if path:
            if name == "spd-say":
                return [path, "-w"]
            return [path, "-s", "170"]
    return None


def _speak_blocking(word: str) -> None:
    global _speak_engine, _speak_warned
    try:
        import pyttsx3

        if _speak_engine is None:
            _speak_engine = pyttsx3.init()
            _speak_engine.setProperty("rate", 170)
        _speak_engine.say(word)
        _speak_engine.runAndWait()
        return
    except Exception:
        pass
    cmd = _espeak_cmd()
    if cmd:
        try:
            subprocess.run(
                cmd + [word],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass
    if not _speak_warned:
        _speak_warned = True
        print(
            "\nNo speech engine. Arch:  sudo pacman -S espeak-ng\n"
            "or:  pip install pyttsx3\n",
            flush=True,
        )


def speak_glyph(char: str) -> None:
    """Say the typed character in a background thread (does not block serial)."""
    word = speak_word(char)
    if not _speak_lock.acquire(blocking=False):
        return

    def run() -> None:
        try:
            _speak_blocking(word)
        finally:
            _speak_lock.release()

    threading.Thread(target=run, daemon=True).start()


def allow_emit(char: str, typed: list[str]) -> bool:
    del char, typed
    return True


def still_needed(char: str) -> int:
    del char
    return STILL_SAMPLES


def typing_unlocked(
    parked_char: str | None,
    parked_angle: float | None,
    seen: str,
    angle: float,
    min_deg: float = START_MOVE_DEG,
) -> bool:
    """After the ready tap, typing starts only once the needle leaves that letter.

    Sitting on 1 (the last confirm) must not type 1. A tiny wobble into the
    next gap also stays locked. A real move to another letter unlocks.
    """
    if parked_char is None or parked_angle is None:
        return False
    if seen == parked_char:
        return False
    return circular_delta(parked_angle, angle) >= min_deg


def should_wrap_line(line: str, char: str, wrap_cols: int) -> bool:
    """New line after wrap_cols characters."""
    del char
    return len(line) >= wrap_cols


def open_serial(port: str, baud: int) -> serial.Serial:
    try:
        return serial.Serial(port, baud, timeout=1)
    except serial.SerialException as exc:
        sys.exit(
            f"Cannot open {port}: {exc}\n"
            "If Permission denied: sudo usermod -aG uucp $USER  (then re-login)"
        )


def read_one_angle(ser: serial.Serial, timeout_s: float = 5.0) -> float:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        angle = parse_angle(raw.decode("utf-8", errors="replace"))
        if angle is not None:
            return angle
    sys.exit("No angle from the Nano. Is analog firmware uploaded and USB connected?")


def _decode_angle(raw: bytes) -> float | None:
    if not raw:
        return None
    return parse_angle(raw.decode("utf-8", errors="replace"))


def drain_serial(ser: serial.Serial, seconds: float = 0.15) -> None:
    """Drop stale USB samples so the next tap is not last letter's angle."""
    try:
        ser.reset_input_buffer()
    except Exception:
        pass
    end = time.time() + seconds
    while time.time() < end:
        raw = ser.readline()
        if not raw:
            break


def expected_session_ref(saved: list[dict], session_a: float, ch: str) -> float:
    """Where this cardinal should sit if only A moved since --calibrate."""
    shifted = a_shift_expected(saved, session_a)
    return next(p["angle"] for p in shifted if p["char"] == ch)


def cardinal_tap_error(
    angle: float,
    prev_angle: float | None,
    expected: float | None = None,
    min_sep: float = CARDINAL_MIN_SEP_DEG,
    saved_tol: float = CARDINAL_SAVED_TOL_DEG,
) -> str | None:
    if prev_angle is not None and circular_delta(prev_angle, angle) < min_sep:
        return (
            f"only {circular_delta(prev_angle, angle):.1f}° from the last tap "
            f"— move to this letter"
        )
    if expected is not None and circular_delta(angle, expected) > saved_tol:
        return f"saved mark is {expected:.0f}°, this tap is {angle:.0f}°"
    return None


def read_stable_angle(ser: serial.Serial, duration_s: float = 0.45) -> float:
    """Average *fresh* samples. The USB buffer often still holds old angles."""
    ser.reset_input_buffer()
    toss_until = time.time() + 0.12
    while time.time() < toss_until:
        raw = ser.readline()
        if not raw:
            break
    angles: list[float] = []
    end = time.time() + duration_s
    while time.time() < end:
        angle = _decode_angle(ser.readline())
        if angle is not None:
            angles.append(angle)
    if not angles:
        return read_one_angle(ser)
    return circular_mean(angles)


def _glyph(char: str) -> str:
    return "space" if char == " " else char


def confirm_angle(
    ser: serial.Serial,
    prompt: str = "",
    label_fn=None,
    live_label: str | None = None,
    end_line: bool = True,
) -> float:
    """Show the live angle and lock the value at the moment of the keypress.

    Do not average after the tap — the needle often springs back then, which
    used to shift the whole A offset and throw every letter off.
    """
    if prompt:
        print(prompt, flush=True)
    if live_label:
        print(f"{live_label}  …     ", end="", flush=True)
    recent: list[float] = []
    fd = sys.stdin.fileno()
    old_timeout = ser.timeout
    # timeout is set below; drain after that so readline cannot block forever
    try:
        import select
        import termios
        import tty
    except ImportError:
        try:
            input("  Enter...")
        except EOFError:
            print()
            sys.exit("Cancelled.")
        return read_stable_angle(ser)

    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ser.timeout = 0.05
        drain_serial(ser)
        while True:
            angle = _decode_angle(ser.readline())
            if angle is not None:
                recent.append(angle)
                if len(recent) > 25:
                    recent = recent[-25:]
                extra = ""
                if label_fn is not None:
                    extra = f"  {_glyph(label_fn(angle))}"
                if live_label:
                    line = f"{live_label}  {angle:5.1f}°{extra}"
                else:
                    line = f"  {angle:5.1f}°{extra}"
                print(f"\r{line}     ", end="", flush=True)
            ready, _, _ = select.select([sys.stdin], [], [], 0.0)
            if ready:
                sys.stdin.read(1)
                if end_line:
                    print()
                break
    except (KeyboardInterrupt, EOFError):
        print()
        raise
    finally:
        ser.timeout = old_timeout
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    if recent:
        # Last few samples only — a long average still includes the approach
        # and any leftover USB frames from the previous letter.
        return circular_mean(recent[-3:])
    return read_stable_angle(ser)


def cmd_span(ser: serial.Serial, seconds: float) -> int:
    print(f"Turn the magnet slowly through a FULL circle for {seconds:.0f} seconds.")
    print("Keep it 1–3 mm above the chip, disc flat and centered.\n")
    lo = 360.0
    hi = 0.0
    n = 0
    end = time.time() + seconds
    try:
        while time.time() < end:
            raw = ser.readline()
            if not raw:
                continue
            angle = parse_angle(raw.decode("utf-8", errors="replace"))
            if angle is None:
                continue
            lo = min(lo, angle)
            hi = max(hi, angle)
            n += 1
            left = max(0.0, end - time.time())
            print(f"\r  now a={angle:7.2f}   min={lo:7.2f}  max={hi:7.2f}  left={left:4.1f}s   ", end="", flush=True)
    except KeyboardInterrupt:
        print()
    print()
    span = hi - lo
    print(f"Samples: {n}")
    print(f"Min a={lo:.2f}°   Max a={hi:.2f}°   Span={span:.2f}°")
    if n < 10:
        print("Not enough samples. Is the firmware uploading angles?")
        return 1
    if span >= 300:
        print("Good: almost a full circle.")
    elif span >= 180:
        print("Partial circle. Letters will be cramped. Improve magnet (diametric, flatter, 1–3 mm).")
    else:
        print("Too small for 36 characters.")
        print("The board is not seeing rotation as 0–360°.")
        print("Usual causes:")
        print("  - magnet is axial (fridge type), not diametric")
        print("  - magnet too far, too close, or off-center")
        print("  - disc not parallel to the chip")
        print("  - analog OUT only twitching; I2C would be better if we can get it working")
        print("  - DIR pin left floating (tie AS5600 DIR to GND)")
    return 0


def expected_step_deg(prev: str, cur: str) -> float:
    return (char_dial(cur) - char_dial(prev)) % 360.0


def calibrate_rewind(i: int, n_saved: int, back: int = CAL_REWIND) -> int:
    """Index to restart from when this tap sits on the last saved mark.

    Drops `back` previous letters (or fewer at the start) so S/T get
    recaptured when U lands on T.
    """
    return max(0, i - min(back, n_saved))


def calibrate_close_action(
    i: int, n_saved: int, already: set[int], back: int = CAL_REWIND
) -> tuple[str, int]:
    """First too-close at this letter: rewind. Second time, or during that
    redo window: accept. Stops Q←S←U cascades in a compressed sensor sector.
    """
    if i in already:
        return ("accept", i)
    redo_from = calibrate_rewind(i, n_saved, back)
    already.update(range(redo_from, i + 1))
    return ("rewind", redo_from)


def format_cal_step(prev: float, angle: float, invert: bool) -> str:
    """Letter-order step, or the short arc if the needle went the other way."""
    step = directed_delta(prev, angle, invert)
    if step <= 180.0:
        return f"  ({step:+.1f}°)"
    return f"  ({circular_delta(prev, angle):.1f}° other way)"


def cmd_calibrate(ser: serial.Serial, force_invert: bool) -> int:
    """Walk A–Z then 0–9 and save every raw angle to config.json."""
    print(
        "Point at each printed letter, going clockwise A → B → C … 8 → 9.\n"
        "Tap space on the tick. 36 marks. This is the map used every session.\n"
        "If a tap lands on the last mark, the previous two letters are\n"
        "redone once. If they are still close, the reading is saved — that\n"
        "sector of the chip is compressed, not a missed tap.\n"
    )
    points: list[dict] = []
    invert_guess = False
    already_rewound: set[int] = set()
    i = 0
    while i < len(CHARS):
        ch = CHARS[i]
        try:
            angle = confirm_angle(ser, live_label=ch, end_line=False)
        except EOFError:
            print()
            sys.exit("Cancelled. Nothing saved.")
        extra = ""
        if points:
            invert_for_check = invert_guess
            if len(points) >= 2:
                invert_for_check = invert_from_clockwise_move(
                    points[0]["angle"], points[1]["angle"]
                )
            err = cal_mark_error(points, angle, invert_for_check)
            if err and "not past" in err and circular_delta(
                points[-1]["angle"], angle
            ) >= CAL_MIN_STEP_DEG:
                print(f"\r{ch}  {angle:5.1f}°  {err} — tap {ch} again     ")
                continue
            if err and err.startswith("on top of"):
                print(f"\r{ch}  {angle:5.1f}°  {err} — tap {ch} again     ")
                continue
            if err and err.startswith("that's back"):
                print(f"\r{ch}  {angle:5.1f}°  {err} — tap {ch} again     ")
                continue
        if points and circular_delta(points[-1]["angle"], angle) < CAL_MIN_STEP_DEG:
            action, redo_from = calibrate_close_action(
                i, len(points), already_rewound
            )
            if action == "rewind":
                dropped = [p["char"] for p in points[redo_from:]] + [ch]
                points = points[:redo_from]
                invert_guess = (
                    invert_from_clockwise_move(
                        points[0]["angle"], points[1]["angle"]
                    )
                    if len(points) >= 2
                    else False
                )
                print(
                    f"\r{ch}  {angle:5.1f}°  too close to {CHARS[i - 1]} — "
                    f"redo {' '.join(dropped)}                    "
                )
                i = redo_from
                continue
            gap = circular_delta(points[-1]["angle"], angle)
            extra = f"  (close to {points[-1]['char']}, {gap:.1f}° — saved)"
        elif points:
            extra = format_cal_step(points[-1]["angle"], angle, invert_guess)
        print(f"\r{ch}  {angle:5.1f}°{extra}")
        points.append(make_point(ch, angle))
        if len(points) == 2:
            invert_guess = invert_from_clockwise_move(
                points[0]["angle"], points[1]["angle"]
            )
        i += 1

    invert = True if force_invert else detect_invert(points)
    bak = backup_config()
    save_config(points, invert)
    print(f"\nSaved {len(points)} marks in {CONFIG_PATH.name}  invert={invert}")
    if bak:
        print(f"Previous settings kept as {bak.parent.name}/{bak.name}")
        print("Restore:  python capture.py --restore latest")
    for sp in cal_spans(ordered_refs(points), invert):
        note = ""
        if abs(sp["raw_span"] - 90.0) > 15.0:
            note = "  (uneven — that is why we save every letter)"
        print(f"  {sp['from']}→{sp['to']}  {sp['raw_span']:.1f}° raw{note}")
    print("\nEach session now only confirms A, J, S, 1:")
    print("  python capture.py")
    return 0


def print_session_confirm(
    saved: list[dict], session_refs: list[dict], invert: bool
) -> None:
    """Show how A/J/S/1 sit versus a pure A-shift of the saved map."""
    session_a = session_a_offset(session_refs)
    expected = {p["char"]: p for p in a_shift_expected(saved, session_a)}
    shift = signed_circular_shift(
        session_a_offset(saved), session_a
    )
    print(f"A moved {shift:+.1f}° from the saved map. invert={invert}")
    sess_by = {p["char"]: p for p in session_refs}
    for ch in CAL_REFS:
        got = sess_by[ch]["angle"]
        if ch == "A":
            print(f"  A  {got:6.1f}°")
            continue
        exp = expected[ch]["angle"]
        delta = signed_circular_shift(exp, got)
        note = ""
        if abs(delta) > 12.0:
            note = "  (will stretch this quadrant)"
        print(f"  {ch}  {got:6.1f}°  A-shift {exp:6.1f}°  Δ {delta:+5.1f}°{note}")
    print()


def calibrate_four(
    ser: serial.Serial,
    force_invert: bool,
    wait_go: bool = True,
    invert_hint: bool | None = None,
    saved: list[dict] | None = None,
) -> tuple[list[dict], bool, float | None]:
    """A J S 1 on the printed sheet (north, east, south, west)."""
    print("Confirm A, J, S, 1 (top, right, bottom, left). Tap space on each.")
    print("Wait until the live angle is near the saved mark, then tap.\n")
    points: list[dict] = []
    for ch in CAL_REFS:
        expected = None
        if saved and points:
            expected = expected_session_ref(saved, points[0]["angle"], ch)
        hint = f"need {expected:.0f}°" if expected is not None else None

        def live_need(_angle: float, _hint: str | None = hint) -> str:
            return _hint or ""

        while True:
            try:
                angle = confirm_angle(
                    ser,
                    live_label=ch,
                    label_fn=live_need if hint else None,
                    end_line=False,
                )
            except EOFError:
                print()
                sys.exit("Cancelled.")
            prev = points[-1]["angle"] if points else None
            err = cardinal_tap_error(angle, prev, expected)
            if err:
                near = ""
                if saved:
                    near = f" (on {nearest_cal_char(angle, saved)})"
                print(f"\r{ch}  {angle:5.1f}°  {err}{near} — again          ")
                continue
            print(f"\r{ch}  {angle:5.1f}°                    ")
            points.append(make_point(ch, angle))
            break
    if force_invert:
        invert = True
    elif invert_hint is not None:
        invert = invert_hint
    else:
        invert = detect_invert(points)
    go_angle: float | None = None
    if wait_go:
        print(
            "\nTap space when ready. Capture does not start on this letter\n"
            "(usually 1). After space, MOVE the needle — then hold a letter.\n"
        )
        try:
            go_angle = confirm_angle(
                ser,
                live_label="ready",
                label_fn=lambda _a: "tap space, then move",
            )
        except EOFError:
            print()
            sys.exit("Cancelled.")
    else:
        print(f"\ninvert={invert}")
        for sp in cal_spans(points, invert):
            note = ""
            if abs(sp["raw_span"] - 90.0) > 15.0:
                note = "  (not ~90°)"
            print(f"  {sp['from']}→{sp['to']}  {sp['raw_span']:.1f}° raw{note}")
        print()
    return points, invert, go_angle


def cmd_letters(
    ser: serial.Serial,
    delay_s: float | None,
    still_tol_deg: float | None,
    move_deg: float,
    invert: bool,
    log_dir: Path,
    wrap_cols: int | None = None,
    sound: bool = False,
) -> int:
    del move_deg
    cfg = load_config()
    if wrap_cols is None:
        wrap_cols = cfg["wrap_cols"]
    elif wrap_cols != cfg["wrap_cols"]:
        save_config(cfg["points"], invert or cfg["invert"], cfg["delay_s"], wrap_cols)
        print(f"Saved wrap_cols={wrap_cols} in config.json")
    if delay_s is None:
        delay_s = cfg["delay_s"]
    elif abs(delay_s - cfg["delay_s"]) > 1e-9:
        save_config(cfg["points"], invert or cfg["invert"], delay_s, wrap_cols)
        print(f"Saved delay of {delay_s:.2f}s in config.json")
    if still_tol_deg is None:
        still_tol_deg = cfg["still_tol_deg"]
    elif abs(still_tol_deg - cfg["still_tol_deg"]) > 1e-9:
        save_config(
            cfg["points"], invert or cfg["invert"], delay_s, wrap_cols, still_tol_deg
        )
        print(f"Saved still_tol_deg={still_tol_deg:.2f} in config.json")

    raw_saved = cfg["points"]
    if not is_full_cal(raw_saved):
        print(
            "No 36-letter calibration in config.json.\n"
            "Point at every letter once, then this program only needs A, J, S, 1:\n"
            "  python capture.py --calibrate"
        )
        return 1

    invert_map = True if invert else cfg["invert"]
    saved, dropped = monotonic_cal_points(raw_saved, invert_map)
    print()
    print(f"Loaded {len(saved)}-letter map from {CONFIG_PATH.name}.")
    if dropped:
        print(
            f"Ignoring out-of-order marks: {', '.join(dropped)}\n"
            "Those sat on top of other letters (that is why A typed as 9).\n"
            "Run --calibrate again to recapture them."
        )
    have = {p["char"] for p in saved}
    if any(ch not in have for ch in CAL_REFS):
        print("Need usable A, J, S, 1 in the map. Run --calibrate.")
        return 1

    session_refs, invert, go_angle = calibrate_four(
        ser,
        invert,
        wait_go=True,
        invert_hint=None if invert else invert_map,
        saved=saved,
    )
    aligned = align_cal_to_session(saved, session_refs, invert)
    print_session_confirm(saved, session_refs, invert)
    warn = session_cardinal_warning(saved, session_refs)
    if warn:
        print(warn)
        print()

    log_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now()
    stamp = started.strftime("%Y_%m_%d_%H_%M")
    txt_name = f"Session_{stamp}.txt"
    log_name = f"Session_{stamp}.log"
    txt_path = log_dir / txt_name
    log_path = log_dir / log_name
    ref_txt = " ".join(f"{p['char']}={p['angle']:.2f}" for p in session_refs)
    header = (
        f"# session start {started.isoformat(timespec='seconds')}\n"
        f"# invert={invert} mapper=cal36 delay_s={delay_s} "
        f"wrap_cols={wrap_cols} still_tol_deg={still_tol_deg}\n"
        f"# session {ref_txt}\n"
    )
    txt_path.write_text(header)
    log_path.write_text(header)

    print(f"Logging {txt_name} / {log_name}")
    extra = " Speech on." if sound else ""
    print(
        f"Move the needle first — the letter under it now is ignored. "
        f"Then hold one letter for {delay_s:.1f}s to type "
        f"(space in the {GAP_BETWEEN_DEG:.1f}° gaps). "
        f"Wrap at {wrap_cols}.{extra}\n"
    )

    hold = LetterHold(delay_s)
    last_emitted: str | None = None
    must_leave = False
    parked_angle = go_angle
    parked_char = (
        live_char(go_angle, aligned, invert) if go_angle is not None else None
    )
    typing_started = parked_char is None
    typed: list[str] = []
    line = ""

    def show(
        angle: float,
        seen: str,
        held_s: float = 0.0,
        armed: bool = False,
        waiting: bool = False,
    ) -> None:
        # Only the current wrap-line is redrawn, so the terminal never wraps this.
        if waiting:
            timer = "move"
        elif armed:
            timer = " ok "
        else:
            timer = f"{held_s:3.1f}s"
        print(
            f"\r{angle:5.1f}° {_glyph(seen):<5}{timer}| {line}\033[K",
            end="",
            flush=True,
        )

    def write_out(text: str, ang_i: int, glyph: str) -> None:
        for item in (txt_path,):
            with item.open("a") as fh:
                fh.write(text)
                fh.flush()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with log_path.open("a") as fh:
            fh.write(f"{ts}  {log_glyph(glyph)}  angle={ang_i}\n")
            fh.flush()

    try:
        while True:
            raw = ser.readline()
            if not raw:
                continue
            angle = parse_angle(raw.decode("utf-8", errors="replace"))
            if angle is None:
                continue

            now = time.time()
            char = live_char(angle, aligned, invert)
            if not typing_started:
                if parked_char is None:
                    parked_char = char
                    parked_angle = angle
                if not typing_unlocked(parked_char, parked_angle, char, angle):
                    show(angle, char, waiting=True)
                    continue
                typing_started = True
                hold.clear()

            show(angle, char, hold.held_s(now), armed=must_leave)

            if must_leave:
                if char == last_emitted:
                    continue
                must_leave = False
                hold.clear()
                hold.update(now, char)
                continue

            if not hold.update(now, char):
                continue
            if not allow_emit(char, typed):
                continue

            line += char
            typed.append(char)
            show(angle, char, hold.held_s(now), armed=True)
            write_out(char, int_deg(angle), char)
            if sound:
                speak_glyph(char)
            last_emitted = char
            must_leave = True
            hold.clear()
            if should_wrap_line(line, char, wrap_cols):
                print()
                line = ""
                typed.append("\n")
                write_out("\n", int_deg(angle), char)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        return 0


def cmd_diagnostic(ser: serial.Serial, force_invert: bool, log_dir: Path) -> int:
    """Point, press Enter, type the printed letter, log every mapper's answer.

    This is how we debug O-reads-as-P without guessing. The log keeps raw
    angle, the four cal marks, piecewise/global/A-only/flipped predictions,
    and signed error vs the letter you said you were on.
    """
    cfg = load_config()
    raw_saved = cfg["points"]
    invert_map = True if force_invert else cfg["invert"]
    saved, dropped = (
        monotonic_cal_points(raw_saved, invert_map)
        if raw_saved
        else ([], [])
    )
    session_refs, invert, _ = calibrate_four(
        ser,
        force_invert,
        wait_go=False,
        invert_hint=None if force_invert else (invert_map if saved else None),
        saved=saved if saved else None,
    )
    cal_points = None
    if saved and all(ch in {p["char"] for p in saved} for ch in CAL_REFS):
        invert = invert_map
        cal_points = align_cal_to_session(saved, session_refs, invert)
        print_session_confirm(saved, session_refs, invert)
        if dropped:
            print(f"Ignoring out-of-order marks: {', '.join(dropped)}\n")
        warn = session_cardinal_warning(saved, session_refs)
        if warn:
            print(warn)
            print()
    else:
        print("No usable 36-letter map in config.json — logging piecewise / A-only only.\n")

    log_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now()
    stamp = started.strftime("%Y_%m_%d_%H_%M")
    log_path = log_dir / f"Diagnostic_{stamp}.log"
    header = (
        f"# diagnostic start {started.isoformat(timespec='seconds')}\n"
        f"# mapper={'cal36' if cal_points else 'none'}\n"
        f"{format_diagnostic_header(session_refs, invert)}"
    )
    log_path.write_text(header)

    a_offset = session_a_offset(session_refs)
    print(f"Logging {log_path.name}")
    print(
        "Point at a printed letter. Press Enter to lock the angle,\n"
        "then type that letter (A–Z or 0–9) and Enter again.\n"
        "Empty line skips. Ctrl+C stops and prints a summary.\n"
    )

    records: list[dict] = []
    n = 0

    def live_label(angle: float) -> str:
        if cal_points:
            live = nearest_cal_char(angle, cal_points)
            return f"type={live}"
        live = match_char(angle, a_offset, invert)
        pw = angle_to_char(angle, invert=invert, points=session_refs)
        return f"type={live}  piecewise={pw}"

    try:
        while True:
            try:
                angle = confirm_angle(
                    ser,
                    live_label="raw",
                    label_fn=live_label,
                    end_line=True,
                )
            except EOFError:
                print()
                break
            try:
                typed = input("  printed letter (A–Z / 0–9): ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            true_char = parse_true_char(typed)
            if true_char is None:
                print("  skipped (need one of A–Z or 0–9)\n")
                continue
            n += 1
            rec = diagnostic_sample(
                angle, true_char, session_refs, invert, cal_points=cal_points
            )
            records.append(rec)
            when = datetime.now()
            with log_path.open("a") as fh:
                fh.write(format_diagnostic_block(n, rec, when))
                fh.flush()
            shown = rec.get("cal36") or rec["a_only"]
            label = "cal36" if "cal36" in rec else "A-only"
            print(
                f"  #{n}  raw={rec['raw']:.2f}°  you={true_char}  "
                f"{label}={shown['pred']} ({shown['err_deg']:+.1f}°)  "
                f"→ {log_path.name}\n"
            )
    except KeyboardInterrupt:
        print()

    summary = format_diagnostic_summary(records)
    with log_path.open("a") as fh:
        fh.write("# summary\n")
        for line in summary.strip().splitlines():
            fh.write(f"# {line}\n")
    print(summary, end="")
    print(f"Wrote {log_path}")
    return 0


def cmd_debug(ser: serial.Serial) -> int:
    """Live pointer angle only — rotate the base until A sits at north."""
    print("Pointer angle. Rotate the base until A is where you want north. Ctrl+C to stop.\n")
    try:
        while True:
            angle = _decode_angle(ser.readline())
            if angle is None:
                continue
            print(f"\r  {angle:6.1f}°   ", end="", flush=True)
    except KeyboardInterrupt:
        print()
        return 0


def cmd_stream(ser: serial.Serial, change_pct: float, show_all: bool) -> int:
    min_deg = 360.0 * (change_pct / 100.0)
    if show_all:
        print("Printing every sample (--all).")
    else:
        print(
            f"New line only if angle moves {change_pct:.0f}% ({min_deg:.0f}°). "
            "Status every 2s. Use --all or run without flags to type letters."
        )
    print("Ctrl+C to stop.\n")

    last_printed: float | None = None
    last_status = 0.0
    try:
        while True:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            angle = parse_angle(line)
            if angle is None:
                print(line)
                continue
            if last_printed is None:
                print(f"a={angle:.2f}    (start)")
                last_printed = angle
                last_status = time.time()
                continue
            delta = circular_delta(last_printed, angle)
            if show_all or delta >= min_deg:
                print(f"a={angle:.2f}    (moved {delta:.2f}°)")
                last_printed = angle
                last_status = time.time()
                continue
            now = time.time()
            if now - last_status >= 2.0:
                print(
                    f"  now a={angle:.2f}  moved {delta:.2f}°  "
                    f"(need {min_deg:.0f}° for a new line)"
                )
                last_status = now
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="360° printed dial → A–Z and 0–9 (36-letter map + A/J/S/1 confirm)"
    )
    parser.add_argument("--port", help="Serial port, e.g. /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--change-pct",
        type=float,
        default=10.0,
        help="For raw stream: print when angle moves this %% of a turn",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="Point at all 36 letters and save their sensor angles to config.json",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show only the live pointer angle (use this to rotate the base so A is north)",
    )
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help=(
            "After A/J/S/1: press Enter on a letter, type the printed character, "
            "append raw/predicted/error details to logs/Diagnostic_*.log"
        ),
    )
    parser.add_argument("--all", action="store_true", help="Print every raw angle")
    parser.add_argument(
        "--span",
        action="store_true",
        help="Measure min/max angle during a full turn (checks if 36 letters are possible)",
    )
    parser.add_argument(
        "--span-seconds",
        type=float,
        default=12.0,
        help="How long --span records (default 12)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help=(
            "Seconds the needle must hold on a letter before it is selected "
            f"(saved as delay_s in config.json; default {DEFAULT_DELAY_S})"
        ),
    )
    parser.add_argument(
        "--wrap",
        type=int,
        default=None,
        dest="wrap_cols",
        help=(
            "Start a new line after this many characters "
            f"(saved as wrap_cols in config.json; default {DEFAULT_WRAP_COLS})"
        ),
    )
    parser.add_argument(
        "--still-tol",
        type=float,
        default=None,
        dest="still_tol_deg",
        help=(
            "Degrees the needle may wobble and still count as stopped "
            f"(saved as still_tol_deg in config.json; default {DEFAULT_STILL_TOL_DEG})"
        ),
    )
    parser.add_argument(
        "--move-deg",
        type=float,
        default=4.0,
        help="Leave last letter by this many degrees before the next can print",
    )
    parser.add_argument(
        "--invert",
        action="store_true",
        help="Force reverse direction (normally taken from the saved --calibrate map)",
    )
    parser.add_argument(
        "--sound",
        action="store_true",
        help="Speak each typed letter (espeak-ng or pyttsx3; offline)",
    )
    parser.add_argument(
        "--restore",
        nargs="?",
        const="list",
        metavar="FILE",
        help=(
            "Restore a config backup from config-backups/ "
            "(no name lists them; 'latest' is newest). No Nano needed."
        ),
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Raw angle stream instead of letters",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "logs",
    )
    args = parser.parse_args()

    if args.restore is not None:
        return cmd_restore(args.restore)

    port = args.port or guess_port()
    if not port:
        print(
            "No serial port found.\n"
            "  ls /dev/ttyUSB* /dev/ttyACM*\n"
            "  sudo usermod -aG uucp $USER   # then log out"
        )
        return 1

    print(f"Opening {port} at {args.baud} baud...")
    ser = open_serial(port, args.baud)
    time.sleep(0.3)
    try:
        if args.calibrate:
            return cmd_calibrate(ser, args.invert)
        if args.diagnostic:
            return cmd_diagnostic(ser, args.invert, args.log_dir)
        if args.debug:
            return cmd_debug(ser)
        if args.span:
            return cmd_span(ser, args.span_seconds)
        if args.stream or args.all or args.change_pct != 10.0:
            return cmd_stream(ser, args.change_pct, args.all)
        return cmd_letters(
            ser,
            args.delay,
            args.still_tol_deg,
            args.move_deg,
            args.invert,
            args.log_dir,
            wrap_cols=args.wrap_cols,
            sound=args.sound,
        )
    finally:
        ser.close()


if __name__ == "__main__":
    raise SystemExit(main())
