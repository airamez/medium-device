#!/usr/bin/env python3
"""Linear-grid capture. Small needle turns step to the previous or next character.

This layout shows ten characters per row, with space, complete, backspace,
then enter at the right of every line (letters ␣ ✓ ⌫ ↵). Numbers follow on
their own row. Each cell — letter or control — owns the same amount of
needle travel (default 10°), so the grid is not squeezed into one 360° turn.

Letters go into a current-word box in the middle of the transcript.
Space commits the letters you actually typed. Complete (✓) takes the
suggested dictionary word so the needle does not have to finish it.

Each session:
  1. Starts paused. Setup the board and click Start Capture (or press P).
     The needle's current pose is A. Nothing is typed until it moves.
  2. Rotate a little clockwise for the next character, the other way for
     the previous one. Hold to type. Hold space to enter the word, ⌫ to
     delete, ✓ to take the autocomplete suggestion.

  python host/capture.py
  python host/capture.py --sound
  python host/capture.py --demo
  python host/capture.py --step 3
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import queue
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

try:
    import pygame
except ImportError:
    sys.exit(
        "pygame is not installed (the pygame-ce package).\n"
        "  python3 -m venv .venv && source .venv/bin/activate\n"
        "  pip install -r host/requirements.txt\n"
        "Arch:  sudo pacman -S python-pygame python-pyserial\n"
        "On Python 3.14 use pygame-ce if the pygame font module fails to load."
    )

import words as w

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
HYST_DEG = 1.0  # stay on the current letter/space until the needle leaves this extra band
SPACE_HOLD_MULT = 1.6  # gaps need a longer hold so a sweep does not type a space
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

    def is_still(self, now: float, window_s: float = 0.30) -> bool:
        """True if the most recent window_s of samples stay within tol_deg.

        Used to start the type timer only after the needle has actually
        stopped, not merely while it lingers on the same letter.
        """
        recent = [a for t, a in self.samples if now - t <= window_s]
        if len(recent) < 2:
            return False
        return circular_range(recent) <= self.tol_deg

    def mean(self) -> float:
        return circular_mean([a for _, a in self.samples])


class LetterHold:
    """Type when the decoded letter stays the same for hold_s.

    Analog jitter of a few degrees is still the same letter on a 10° map.
    RestWindow's 2° band treated that jitter as 'still moving' and never typed.
    Spaces use a longer hold so a slow sweep through a gap does not type.
    """

    def __init__(self, hold_s: float, space_hold_s: float | None = None) -> None:
        self.hold_s = hold_s
        self.space_hold_s = (
            hold_s * SPACE_HOLD_MULT if space_hold_s is None else space_hold_s
        )
        self.char: str | None = None
        self.t0: float | None = None

    def need_s(self, char: str | None = None) -> float:
        ch = self.char if char is None else char
        return self.space_hold_s if ch == " " else self.hold_s

    def update(self, now: float, char: str) -> bool:
        if char != self.char or self.t0 is None:
            self.char = char
            self.t0 = now
            return False
        return (now - self.t0) >= self.need_s(char) * 0.85

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


def dial_xy(
    dial_deg: float, cx: float, cy: float, radius: float
) -> tuple[float, float]:
    """Screen point for a dial angle. 0° is north (up), then clockwise."""
    rad = math.radians(float(dial_deg))
    return cx + radius * math.sin(rad), cy - radius * math.cos(rad)


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


def nearest_cal_window(
    angle: float, points: list[dict], gap_deg: float = GAP_BETWEEN_DEG
) -> tuple[str, float, float]:
    """Nearest saved mark, distance to it, and that letter's half-width.

    The gap is a fraction of the local step (3.5° of a 10° paper sector),
    not a fixed 3.5° in sensor space. Compressed chip sectors still have
    a usable letter window.
    """
    if not points:
        return "?", 0.0, HALF_LETTER_DEG
    by = {p["char"]: p for p in points}
    ordered = [by[ch] for ch in CHARS if ch in by]
    if len(ordered) < 2:
        ch = nearest_cal_char(angle, points)
        mark = by.get(ch, ordered[0] if ordered else {"angle": 0.0})
        return ch, circular_delta(mark["angle"], angle), HALF_LETTER_DEG
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
    frac = gap_deg / SECTOR_DEG
    half = max(0.2, span * (1.0 - frac) / 2.0)
    return nearest["char"], d_near, half


def cal_char_or_space(
    angle: float, points: list[dict], gap_deg: float = GAP_BETWEEN_DEG
) -> str:
    """Nearest saved mark, or space if the needle sits in the gap."""
    if not points:
        return "?"
    ch, d_near, half = nearest_cal_window(angle, points, gap_deg)
    if d_near <= half:
        return ch
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


def travel_slot(
    travel_deg: float,
    step_deg: float,
    current_slot: int,
    hyst_frac: float = 0.25,
) -> int:
    """Map unwrapped needle travel onto equal-sized character slots.

    Every character (letter, space, backspace) owns the same step_deg.
    Once a slot is selected, travel must pass the boundary by hyst_frac
    of a step before the selection moves. That gives a wide stop window.

    A noisy or batched sample can jump many degrees at once. Never skip
    slots: walk one cell toward the target so a little move cannot leap
    over letters. Fast spins catch up on later frames.
    """
    if step_deg <= 0:
        return current_slot
    pos = travel_deg / step_deg
    lo = current_slot - 0.5 - hyst_frac
    hi = current_slot + 0.5 + hyst_frac
    if lo <= pos <= hi:
        return current_slot
    target = int(math.floor(pos + 0.5))
    if target > current_slot:
        return current_slot + 1
    if target < current_slot:
        return current_slot - 1
    return current_slot


def slot_offset_frac(
    travel_deg: float, step_deg: float, slot: int
) -> float:
    """Where the needle sits inside the current cell, 0..1.

    0 is the previous-letter edge, 0.5 is the cell centre, 1 is the next
    letter edge. Values are clamped so a lagged catch-up still draws.
    """
    if step_deg <= 0:
        return 0.5
    offset = travel_deg - slot * step_deg
    return max(0.0, min(1.0, 0.5 + offset / step_deg))


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


def hysteretic_dial_char(
    dial: float,
    prev: str | None,
    gap_deg: float = GAP_BETWEEN_DEG,
    hyst_deg: float = HYST_DEG,
) -> str:
    """Letter/space with a Schmitt trigger so the edge does not flicker.

    Stay on a letter until the needle is clearly in the gap (or the next
    letter). Stay on a space until the needle is clearly inside a letter.
    """
    raw = dial_to_char(dial, gap_deg)
    if prev is None or prev == raw:
        return raw
    half = (SECTOR_DEG - gap_deg) / 2.0
    if prev != " " and prev in CHARS:
        if abs(signed_from_center(char_dial(prev), dial)) <= half + hyst_deg:
            return prev
    if prev == " " and raw != " ":
        center = nearest_letter_idx(dial) * SECTOR_DEG
        if abs(signed_from_center(center, dial)) > max(0.2, half - hyst_deg):
            return " "
    return raw


def hysteretic_cal_char(
    angle: float,
    points: list[dict],
    prev: str | None,
    gap_deg: float = GAP_BETWEEN_DEG,
    hyst_deg: float = HYST_DEG,
) -> str:
    """Same edge hold as hysteretic_dial_char, using saved marks."""
    raw = cal_char_or_space(angle, points, gap_deg)
    if prev is None or prev == raw:
        return raw
    by = {p["char"]: p for p in points}
    if prev != " " and prev in by:
        _ch, _d, half = nearest_cal_window(by[prev]["angle"], points, gap_deg)
        if circular_delta(by[prev]["angle"], angle) <= half + hyst_deg:
            return prev
    if prev == " " and raw != " ":
        _ch, d_near, half = nearest_cal_window(angle, points, gap_deg)
        if d_near > max(0.2, half - hyst_deg):
            return " "
    return raw


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
    if char == " ":
        return "SP"
    if char == "\b":
        return "BS"
    if char == "\t":
        return "OK"
    return char


_SPEAK_WORDS = {
    " ": "space",
    "\b": "backspace",
    "\t": "complete",
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
    """Letters and backspace always. A second space needs a letter in between."""
    if char == "\b":
        return True
    if char == " " and typed and typed[-1] == " ":
        return False
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


BG = (36, 32, 28)
PANEL = (48, 43, 38)
PANEL_LINE = (72, 64, 56)
FACE = (244, 237, 222)
CELL = (250, 246, 236)
INK = (32, 28, 24)
MUTED = (118, 108, 96)
CARDINAL = (148, 36, 32)
GOLD = (214, 168, 64)
HILITE = (255, 214, 140)
SPACE_FILL = (214, 206, 190)
BS_FILL = (236, 214, 208)
COMPLETE_FILL = (208, 228, 210)
ENTER_FILL = (206, 216, 232)
WORD_CARD = (52, 46, 40)
WORD_CARD_INNER = (32, 28, 24)
TEXT_BG = (28, 24, 22)
TEXT_FG = (236, 228, 214)
GHOST_FG = (168, 156, 138)
OK = (86, 150, 92)
BTN_BG = (64, 56, 50)
BTN_HOVER = (86, 74, 64)
BTN_PRIMARY = (148, 36, 32)
BTN_PRIMARY_HOVER = (176, 48, 42)
BTN_ON = (70, 118, 78)
WHITE = (250, 246, 238)

WIN_W, WIN_H = 1440, 900
FPS = 60
DEFAULT_STEP_DEG = 10.0  # equal needle travel per cell; not limited to 360/N
DELAY_CHOICES = (0.5, 0.7, 1, 1.5, 2, 3)
CONTENT_COLS = 10  # letters/digits per line
# Stable tokens — not ASCII \\b/\\t, which some paths treat as controls and drop.
BS = "⌫"
ACCEPT = "✓"
ENTER = "↵"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DIGITS = "0123456789"


def _chunk(seq: str, n: int) -> list[str]:
    return [seq[i : i + n] for i in range(0, len(seq), n)]


def _center_pad(chars: list[str], n: int) -> list[str | None]:
    """Pad a short row with empty slots so the content sits in the middle."""
    if len(chars) >= n:
        return list(chars)
    extra = n - len(chars)
    left = extra // 2
    right = extra - left
    return [None] * left + list(chars) + [None] * right


def _rows_with_ends(seq: str, n: int) -> list[list[str | None]]:
    """Each line is: up to n characters (centered), then space, complete, backspace, enter."""
    return [
        _center_pad(list(part), n) + [" ", ACCEPT, BS, ENTER] for part in _chunk(seq, n)
    ]


LETTER_ROWS = _rows_with_ends(LETTERS, CONTENT_COLS)
DIGIT_ROWS = _rows_with_ends(DIGITS, CONTENT_COLS)
GRID_ROWS = LETTER_ROWS + DIGIT_ROWS
KEYS = [ch for row in GRID_ROWS for ch in row if ch is not None]
ROW_CELLS = CONTENT_COLS + 4  # chars ␣ ✓ ⌫ ↵


class AnglePump:
    def __init__(self, ser) -> None:
        self.ser = ser
        self.ser.timeout = 0.05
        self._q: queue.Queue[float] = queue.Queue(maxsize=16)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> float | None:
        angle = None
        while True:
            try:
                angle = self._q.get_nowait()
            except queue.Empty:
                return angle

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self.ser.readline()
            except Exception:
                time.sleep(0.05)
                continue
            angle = _decode_angle(raw) if raw else None
            if angle is None:
                continue
            try:
                self._q.put_nowait(angle)
            except queue.Full:
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._q.put_nowait(angle)
                except queue.Full:
                    pass


class Button:
    def __init__(
        self, label: str, kind: str = "ghost", toggle: bool = False
    ) -> None:
        self.label = label
        self.kind = kind
        self.toggle = toggle
        self.on = False
        self.enabled = True
        self.rect = pygame.Rect(0, 0, 0, 0)

    def place(self, x: int, y: int, w: int, h: int) -> None:
        self.rect = pygame.Rect(x, y, w, h)

    def hit(self, pos: tuple[int, int]) -> bool:
        return self.enabled and self.rect.collidepoint(pos)

    def draw(self, surf: pygame.Surface, font: pygame.font.Font, hover: bool) -> None:
        if not self.enabled:
            bg, fg = (56, 50, 46), MUTED
        elif self.kind == "primary":
            bg = BTN_PRIMARY_HOVER if hover else BTN_PRIMARY
            fg = WHITE
        elif self.toggle and self.on:
            bg, fg = BTN_ON, WHITE
        else:
            bg = BTN_HOVER if hover else BTN_BG
            fg = WHITE
        pygame.draw.rect(surf, bg, self.rect, border_radius=8)
        text = font.render(self.label, True, fg)
        surf.blit(text, text.get_rect(center=self.rect.center))


def _sys_font(size: int, bold: bool = False) -> pygame.font.Font:
    names = "dejavusans,liberation sans,freesans,arial,sans"
    font = pygame.font.SysFont(names, size, bold=bold)
    return font if font is not None else pygame.font.Font(None, size)


def _sys_mono_font(size: int, bold: bool = False) -> pygame.font.Font:
    names = (
        "dejavusansmono,liberation mono,nimbus mono,consolas,"
        "courier new,monospace"
    )
    font = pygame.font.SysFont(names, size, bold=bold)
    return font if font is not None else pygame.font.Font(None, size)


class LinearCaptureGui:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        cfg = load_config()
        raw_delay = (
            args.delay if args.delay is not None else float(cfg["delay_s"])
        )
        snapped = min(DELAY_CHOICES, key=lambda s: abs(s - raw_delay))
        self.delay_s = float(snapped)
        self.wrap_cols = (
            args.wrap_cols
            if args.wrap_cols is not None
            else int(cfg["wrap_cols"])
        )
        self.still_tol = float(cfg["still_tol_deg"])
        self.invert = bool(args.invert or cfg["invert"])
        self.sound = bool(args.sound)
        self.autocomplete = True
        self.demo = bool(args.demo)
        self.log_dir: Path = args.log_dir
        self.step_deg = max(4.0, float(args.step))

        self.paused = True
        self.raw = 0.0
        self.last_raw: float | None = None
        self.travel = 0.0
        self.slot = 0
        self.index = 0
        self.hold = LetterHold(self.delay_s)
        self.rest = RestWindow(self.delay_s, self.still_tol)
        self.must_leave = False
        self.last_emitted: str | None = None
        self.last_index: int | None = None
        self.hold_lock_angle: float | None = None
        self.typed: list[str] = []
        self.line = ""
        # Text cleared from the on-screen buffer, but already flushed to
        # txt_path — permanently part of the saved transcript and never
        # rewritten or erased by rewrite_txt() again.
        self.persisted_prefix = ""
        self._typed_rev = 0
        self._wrap_cache_key: tuple[int, int] | None = None
        self._wrap_cache_lines: list[str] = []
        self.scroll_offset = 0
        self._output_max_lines = 1
        self._output_max_chars = 8
        self.lexicon = w.load_index()
        self.draft = w.WordDraft(self.lexicon)
        self.draft.enabled = self.autocomplete
        self.txt_path: Path | None = None
        self.log_path: Path | None = None
        self.status = ""
        self.flash_until = 0.0
        self._last_mouse: tuple[int, int] | None = None
        self.logging_started = False

        self.btn_pause = Button("Start Capture", kind="primary", toggle=True)
        self.btn_reverse = Button("Reverse direction", toggle=True)
        self.btn_sound = Button("Speak letters", toggle=True)
        self.btn_clear = Button("Clear text")
        self.btn_exit = Button("Exit")
        self.btn_reverse.on = self.invert
        self.btn_sound.on = self.sound
        self.buttons = [
            self.btn_pause,
            self.btn_reverse,
            self.btn_sound,
            self.btn_clear,
            self.btn_exit,
        ]

        if not pygame.get_init():
            pygame.init()
        pygame.font.init()
        self.font_title = _sys_font(26, bold=True)
        self.font_ui = _sys_font(18)
        self.font_ui_b = _sys_font(18, bold=True)
        self.font_small = _sys_font(15)
        self.font_cell = _sys_font(28, bold=True)
        self.font_big = _sys_font(64, bold=True)
        self.font_text = _sys_mono_font(28, bold=True)
        self.font_word = _sys_font(48, bold=True)
        self.font_ghost = _sys_mono_font(26, bold=True)

        self.text_box = pygame.Rect(0, 0, 0, 0)
        self.word_box = pygame.Rect(0, 0, 0, 0)
        self.log_label_rect = pygame.Rect(0, 0, 0, 0)
        self.panel_rect = pygame.Rect(0, 0, 0, 0)
        self.grid_origin = (0, 0)
        self.cell = 64
        self.gap = 8
        self.cell_rects: list[pygame.Rect] = []
        self.delay_hits: list[tuple[int, pygame.Rect]] = []
        self.delay_label_pos = (0, 0)
        self.autocomplete_box = pygame.Rect(0, 0, 0, 0)
        self.autocomplete_label_pos = (0, 0)
        self.win_size = (WIN_W, WIN_H)
        self.layout(WIN_W, WIN_H)
        self.set_paused(True)

    @property
    def shown(self) -> str:
        return KEYS[self.index]

    def layout(self, w: int, h: int) -> None:
        panel_w = max(200, min(260, int(w * 0.18)))
        self.panel_rect = pygame.Rect(w - panel_w, 0, panel_w, h)
        content_w = w - panel_w
        word_h = 92
        log_h = 22
        text_h = max(160, int(h * 0.28))
        footer = word_h + text_h + log_h + 28
        top = 10
        grid_w = content_w - 40
        grid_h = max(120, h - top - footer)
        n_rows = len(GRID_ROWS)
        n_letter_rows = len(LETTER_ROWS)
        gap_frac = 0.12
        fit_w = ROW_CELLS + gap_frac * (ROW_CELLS - 1)
        fit_h = n_rows + gap_frac * (n_rows - 1)
        cell = min(grid_w / fit_w, (grid_h - 12) / fit_h)
        self.cell = max(40, int(cell))
        self.gap = max(6, int(self.cell * gap_frac))
        pitch = self.cell + self.gap
        total_w = ROW_CELLS * self.cell + (ROW_CELLS - 1) * self.gap
        total_h = n_rows * self.cell + (n_rows - 1) * self.gap + 12
        ox = 20 + max(0, (grid_w - total_w) // 2)
        oy = top + 6
        self.grid_origin = (ox, oy)

        self.cell_rects = []
        y = oy
        for ri, row in enumerate(GRID_ROWS):
            if ri == n_letter_rows:
                y += 10
            x = ox
            for ch in row:
                if ch is not None:
                    self.cell_rects.append(pygame.Rect(x, y, self.cell, self.cell))
                x += pitch
            y += pitch
        grid_bottom = y

        ww = min(640, max(300, int(content_w * 0.5)))
        self.word_box = pygame.Rect(0, 0, ww, word_h)
        self.word_box.centerx = content_w // 2
        self.word_box.y = grid_bottom + 10
        self.text_box = pygame.Rect(
            16, self.word_box.bottom + 10, content_w - 32, text_h
        )
        self.log_label_rect = pygame.Rect(
            16, min(h - log_h - 4, self.text_box.bottom + 4), content_w - 32, log_h
        )

        # Right-side vertical control panel: buttons stacked top to bottom,
        # then the Hold delay picker below them.
        pad = 20
        px = self.panel_rect.x + pad
        pw = panel_w - pad * 2
        bh = 46
        gap_b = 12
        by = pad
        for btn in (
            self.btn_pause,
            self.btn_reverse,
            self.btn_sound,
            self.btn_clear,
        ):
            btn.place(px, by, pw, bh)
            by += bh + gap_b
        self.btn_exit.place(px, self.panel_rect.bottom - pad - bh, pw, bh)
        by += 8
        self.delay_label_pos = (px, by)
        by += 22
        self.delay_hits = []
        cols = 3
        radio_h = 26
        radio_gap = 6
        slot = pw // cols
        for i, sec in enumerate(DELAY_CHOICES):
            row, col = divmod(i, cols)
            rx = px + col * slot
            ry = by + row * (radio_h + radio_gap)
            self.delay_hits.append((sec, pygame.Rect(rx, ry, slot, radio_h)))
        rows = -(-len(DELAY_CHOICES) // cols)
        by += rows * (radio_h + radio_gap) + 10
        self.autocomplete_box = pygame.Rect(px, by, 18, 18)
        self.autocomplete_label_pos = (px + 26, by - 2)

    def start_session_logs(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        started = datetime.now()
        stamp = started.strftime("%Y_%m_%d_%H_%M")
        self.txt_path = self.log_dir / f"Session_{stamp}.txt"
        self.log_path = self.log_dir / f"Session_{stamp}.log"
        header = (
            f"# session start {started.isoformat(timespec='seconds')}\n"
            f"# invert={self.invert} mapper=gui-linear step_deg={self.step_deg} "
            f"delay_s={self.delay_s} wrap_cols={self.wrap_cols} "
            f"words={len(self.lexicon)}\n"
        )
        self.log_header = header
        self.txt_path.write_text(header)
        self.log_path.write_text(header)
        self.logging_started = True

    def write_out(self, char: str) -> None:
        if self.txt_path is None:
            return
        with self.txt_path.open("a") as fh:
            fh.write(char)
            fh.flush()

    def write_log(self, glyph: str) -> None:
        if self.log_path is None:
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.log_path.open("a") as fh:
            fh.write(f"{ts}  {glyph}  angle={int_deg(self.raw)}\n")
            fh.flush()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.btn_pause.on = paused
        self.btn_pause.label = "Start Capture" if paused else "Pause Capture"
        self.btn_pause.kind = "primary" if paused else "ghost"
        self._cancel_hold()
        self.rest.clear()
        if paused:
            self.status = "Setup the board and click the Start Capture button"
            self._last_mouse = None
            self.last_raw = None
            return
        # Homing: wherever the needle sits now is the centre of A.
        self.slot = 0
        self.index = 0
        self.travel = 0.0
        self.last_raw = self.raw
        self.must_leave = True
        self.last_index = 0
        self.last_emitted = None
        self._cancel_hold()
        self.rest.clear()
        self.status = "On A. Move the needle to start capturing."
        if not self.logging_started:
            self.start_session_logs()

    def step_index(self, delta: int) -> None:
        if delta == 0:
            return
        self.slot += delta
        self.index = self.slot % len(KEYS)
        self.travel = self.slot * self.step_deg
        self.last_raw = self.raw
        self._cancel_hold()
        self.rest.clear()

    def _follow_needle(self) -> None:
        """Unwrapped travel: every cell (letter, space, backspace) is step_deg wide.

        travel_slot() only advances one cell per call (by design — see its
        docstring), so a fast spin followed by a stop must be resolved to the
        final slot within this same tick. Otherwise the on-screen letter
        keeps stepping forward on later frames even though the needle has
        already stopped, which looks like the indicator "jumping" after the
        fact instead of tracking the needle in real time.
        """
        if self.last_raw is None:
            self.last_raw = self.raw
            return
        delta = signed_turn(self.last_raw, self.raw)
        self.last_raw = self.raw
        if self.invert:
            delta = -delta
        self.travel += delta
        moved = False
        while True:
            new_slot = travel_slot(self.travel, self.step_deg, self.slot)
            if new_slot == self.slot:
                break
            self.slot = new_slot
            moved = True
        if not moved:
            return
        self.index = self.slot % len(KEYS)
        self._cancel_hold()
        self.rest.clear()

    def rewrite_txt(self) -> None:
        if self.txt_path is None:
            return
        header = getattr(self, "log_header", "")
        self.txt_path.write_text(
            header + self.persisted_prefix + "".join(self.typed)
        )

    def _refresh_line(self) -> None:
        self.line = "".join(self.typed).split("\n")[-1] if self.typed else ""

    def _wrapped_lines(self, max_chars: int) -> list[str]:
        """Word-wrap self.typed for display, cached by revision + width.

        Recomputing "".join(self.typed).split(...) from scratch every frame
        (60x/sec) made the whole app get slower the longer a session ran,
        since the cost grows with total characters typed. Only redo the
        wrap when the text actually changed or the box was resized.
        """
        key = (self._typed_rev, max_chars)
        if self._wrap_cache_key == key:
            return self._wrap_cache_lines
        raw_lines = "".join(self.typed).split("\n")
        lines: list[str] = []
        for para in raw_lines:
            if para == "":
                lines.append("")
                continue
            for i in range(0, len(para), max_chars):
                lines.append(para[i : i + max_chars])
        self._wrap_cache_key = key
        self._wrap_cache_lines = lines
        return lines

    def _append_committed(self, text: str) -> None:
        for ch in text:
            self.line += ch
            self.typed.append(ch)
            self.write_out(ch)
            if ch == "\n":
                self.line = ""
                continue
            if should_wrap_line(self.line, ch, self.wrap_cols):
                self.line = ""
                self.typed.append("\n")
                self.write_out("\n")
        self._typed_rev += 1
        self.scroll_offset = 0

    def emit_backspace(self) -> bool:
        changed = w.delete_current_word(self.draft, self.typed)
        if not changed:
            return False
        self._typed_rev += 1
        self._refresh_line()
        self.rewrite_txt()
        self.scroll_offset = 0
        self.write_log("BS")
        if self.sound:
            speak_glyph("\b")
        return True

    def _commit_word(self, word: str, via: str) -> None:
        if not word:
            return
        self._append_committed(word + " ")
        if via == "accept":
            self.write_log(f"OK  {word}")
            if self.sound:
                speak_glyph(word)
        else:
            self.write_log(f"SP  {word}")
            if self.sound:
                speak_glyph(" ")

    def _is_backspace(self, char: str) -> bool:
        return char in (BS, "\b")

    def _is_enter(self, char: str) -> bool:
        return char in (ENTER, "\n")

    def emit(self, char: str) -> None:
        if self._is_backspace(char):
            self.emit_backspace()
        elif char == ACCEPT:
            word = self.draft.take_suggestion()
            self._commit_word(word, via="accept")
        elif char == " ":
            word = self.draft.take_typed()
            self._commit_word(word, via="space")
        elif self._is_enter(char):
            word = self.draft.take_typed()
            if word:
                self._append_committed(word)
                self.write_log(f"SP  {word}")
                if self.sound:
                    speak_glyph(word)
            self._append_committed("\n")
            self.write_log("NL")
            if self.sound:
                speak_glyph("\n")
        else:
            self.draft.add(char)
            self.write_log(log_glyph(char))
            if self.sound:
                speak_glyph(char)
        self.last_emitted = char
        self.last_index = self.index
        self.must_leave = True
        self._cancel_hold()
        self.rest.clear()
        self.flash_until = time.time() + 0.35

    def _cancel_hold(self) -> None:
        self.hold.clear()
        self.hold_lock_angle = None

    def tick(self, now: float) -> None:
        if self.paused:
            self.status = "Setup the board and click the Start Capture button"
            return
        self.rest.add(now, self.raw)
        self._follow_needle()
        char = self.shown
        stopped = self.rest.is_still(now, window_s=0.12)
        abort_deg = min(self.still_tol, self.step_deg * 0.4)
        left_park = (
            self.hold_lock_angle is not None
            and circular_delta(self.raw, self.hold_lock_angle) > abort_deg
        )

        if self.must_leave:
            if self.last_emitted is None:
                self.status = "On A. Move the needle to start capturing."
            else:
                self.status = "Typed. Rotate a little to the next character."
            if self.index != self.last_index:
                self.must_leave = False
                self._cancel_hold()
            return

        if not stopped or left_park:
            self._cancel_hold()
            self.status = "Moving — stop on a letter to start the timer."
            return

        if self.hold.t0 is None or self.hold.char != char:
            self.hold_lock_angle = self.raw
        ready = self.hold.update(now, char)
        if self._is_backspace(char):
            shown_word = self.draft.suggestion or self.draft.typed
            if shown_word:
                self.status = (
                    f"Backspace — hold still to delete in {shown_word}."
                )
            elif self.typed:
                self.status = "Backspace — hold still to edit the last word."
            else:
                self.status = "Backspace — type a letter first."
        elif char == " ":
            self.status = "Space — hold still to enter the letters you typed."
        elif char == ACCEPT:
            if not self.draft.typed:
                self.status = "Complete — type letters first, then hold here."
            else:
                self.status = (
                    f"Complete — hold still to take {self.draft.suggestion}."
                )
        else:
            self.status = f"Hold {char} to type it."
        if not ready:
            return
        if char == ACCEPT and not self.draft.typed:
            self.status = "Complete — type letters first, then hold here."
            return
        if char == " " and not self.draft.typed:
            self.status = "Type a word, then hold space to enter it."
            return
        self.emit(char)

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.QUIT:
            return True
        if event.type == pygame.VIDEORESIZE:
            self.layout(event.w, event.h)
            return False
        if event.type == pygame.MOUSEWHEEL:
            if self.text_box.collidepoint(pygame.mouse.get_pos()):
                lines = self._wrapped_lines(self._output_max_chars)
                max_scroll = max(0, len(lines) - self._output_max_lines)
                self.scroll_offset = max(
                    0, min(max_scroll, self.scroll_offset + event.y * 3)
                )
            return False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                return True
            if event.key == pygame.K_p:
                self.set_paused(not self.paused)
                return False
            if event.key == pygame.K_i:
                self.invert = not self.invert
                self.btn_reverse.on = self.invert
                return False
            if event.key == pygame.K_s and (event.mod & pygame.KMOD_CTRL):
                self.sound = not self.sound
                self.btn_sound.on = self.sound
                return False
            if not self.paused and event.key == pygame.K_BACKSPACE:
                self.emit(BS)
                return False
            if not self.paused and event.key == pygame.K_LEFT:
                self.step_index(-1)
            elif not self.paused and event.key == pygame.K_RIGHT:
                self.step_index(1)
            return False
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        pos = event.pos
        if self.btn_exit.hit(pos):
            return True
        if self.btn_pause.hit(pos):
            self.set_paused(not self.paused)
        elif self.btn_reverse.hit(pos):
            self.invert = not self.invert
            self.btn_reverse.on = self.invert
        elif self.btn_sound.hit(pos):
            self.sound = not self.sound
            self.btn_sound.on = self.sound
        elif self.btn_clear.hit(pos):
            # Only clears the on-screen buffer. The saved transcript
            # (self.txt_path) is append-only and must not be touched here,
            # or clearing the screen would erase everything logged so far.
            # What's cleared is already written to disk, so fold it into
            # persisted_prefix — a subsequent backspace's rewrite_txt()
            # must never lose it again.
            self.persisted_prefix += "".join(self.typed)
            self.typed.clear()
            self.line = ""
            self._typed_rev += 1
            self.draft.clear()
            self.scroll_offset = 0
            self.write_log("CLR")
        elif self._hit_delay(pos):
            pass
        elif self.autocomplete_box.collidepoint(pos):
            self.autocomplete = not self.autocomplete
            self.draft.enabled = self.autocomplete
        elif not self.paused:
            for i, rect in enumerate(self.cell_rects):
                if rect.collidepoint(pos):
                    self.index = i
                    self.slot = i
                    self.travel = self.slot * self.step_deg
                    self.last_raw = self.raw
                    self._cancel_hold()
                    break
        return False

    def feed_demo_mouse(self, pos: tuple[int, int]) -> None:
        if self._last_mouse is None:
            self._last_mouse = pos
            return
        dx = pos[0] - self._last_mouse[0]
        self._last_mouse = pos
        # ~0.2° per pixel: a short drag moves about one cell, not a leap.
        self.raw = (self.raw + dx * 0.2) % 360.0

    def draw(self, surf: pygame.Surface) -> None:
        size = surf.get_size()
        if size != self.win_size:
            self.win_size = size
            self.layout(*size)
        surf.fill(BG)
        self._draw_panel(surf)
        self._draw_grid(surf)
        self._draw_word_box(surf)
        self._draw_output(surf)

    def _draw_grid(self, surf: pygame.Surface) -> None:
        now = time.time()
        flashing = now < self.flash_until

        for i, ch in enumerate(KEYS):
            rect = self.cell_rects[i]
            on = i == self.index
            if ch == BS:
                fill = GOLD if (on and flashing) else (HILITE if on else BS_FILL)
                pygame.draw.rect(surf, fill, rect, border_radius=8)
                pygame.draw.rect(
                    surf,
                    GOLD if on else CARDINAL,
                    rect,
                    width=4 if on else 2,
                    border_radius=8,
                )
                self._draw_backspace_glyph(surf, rect, CARDINAL)
                if on and not self.paused:
                    self._draw_hold_meter(surf, rect, now)
            elif ch == " ":
                fill = GOLD if (on and flashing) else (HILITE if on else SPACE_FILL)
                pygame.draw.rect(surf, fill, rect, border_radius=8)
                pygame.draw.rect(
                    surf,
                    GOLD if on else MUTED,
                    rect,
                    width=4 if on else 2,
                    border_radius=8,
                )
                self._draw_space_glyph(surf, rect, INK)
                if on and not self.paused:
                    self._draw_hold_meter(surf, rect, now)
            elif ch == ACCEPT:
                offered = bool(self.draft.ghost)
                idle = HILITE if on else (
                    (228, 238, 216) if offered else COMPLETE_FILL
                )
                fill = GOLD if (on and flashing) else idle
                pygame.draw.rect(surf, fill, rect, border_radius=8)
                pygame.draw.rect(
                    surf,
                    GOLD if on else OK,
                    rect,
                    width=4 if on else 2,
                    border_radius=8,
                )
                self._draw_complete_glyph(surf, rect, OK if not on else INK)
                if on and not self.paused:
                    self._draw_hold_meter(surf, rect, now)
            elif ch == ENTER:
                fill = GOLD if (on and flashing) else (HILITE if on else ENTER_FILL)
                pygame.draw.rect(surf, fill, rect, border_radius=8)
                pygame.draw.rect(
                    surf,
                    GOLD if on else MUTED,
                    rect,
                    width=4 if on else 2,
                    border_radius=8,
                )
                self._draw_enter_glyph(surf, rect, INK)
                if on and not self.paused:
                    self._draw_hold_meter(surf, rect, now)
            else:
                fill = GOLD if (on and flashing) else (HILITE if on else CELL)
                pygame.draw.rect(surf, fill, rect, border_radius=8)
                pygame.draw.rect(
                    surf,
                    GOLD if on else (CARDINAL if ch in "AJS1" else INK),
                    rect,
                    width=4 if on else 1,
                    border_radius=8,
                )
                if on and not self.paused:
                    self._draw_hold_meter(surf, rect, now)
                glyph = self.font_cell.render(ch, True, CARDINAL if ch in "AJS1" else INK)
                surf.blit(glyph, glyph.get_rect(center=rect.center))

        if 0 <= self.index < len(self.cell_rects):
            self._draw_slot_needle(surf, self.cell_rects[self.index])

    def _draw_space_glyph(
        self, surf: pygame.Surface, rect: pygame.Rect, color: tuple[int, int, int]
    ) -> None:
        """Keyboard space mark: wide open box (␣), not a minus bar."""
        w = max(16, int(rect.w * 0.64))
        h = max(12, int(rect.h * 0.42))
        thick = max(3, int(min(w, h) * 0.22))
        x = rect.centerx - w // 2
        y = rect.centery - h // 2 + 1
        radius = max(1, thick // 2)
        pygame.draw.rect(surf, color, pygame.Rect(x, y, thick, h), border_radius=radius)
        pygame.draw.rect(
            surf, color, pygame.Rect(x + w - thick, y, thick, h), border_radius=radius
        )
        pygame.draw.rect(
            surf, color, pygame.Rect(x, y + h - thick, w, thick), border_radius=radius
        )

    def _draw_backspace_glyph(
        self, surf: pygame.Surface, rect: pygame.Rect, color: tuple[int, int, int]
    ) -> None:
        """Keyboard-style backspace: left-pointing key with an X."""
        w = max(18, int(rect.w * 0.62))
        h = max(14, int(rect.h * 0.42))
        thick = max(3, int(min(w, h) * 0.16))
        cx, cy = rect.centerx, rect.centery
        tip = (cx - w // 2, cy)
        notch = max(6, w // 4)
        pts = [
            tip,
            (cx - w // 2 + notch, cy - h // 2),
            (cx + w // 2, cy - h // 2),
            (cx + w // 2, cy + h // 2),
            (cx - w // 2 + notch, cy + h // 2),
        ]
        pygame.draw.polygon(surf, color, pts, width=thick)
        # X inside the body
        pad = max(3, thick)
        x0 = cx - w // 2 + notch + pad
        x1 = cx + w // 2 - pad
        y0 = cy - h // 2 + pad + 1
        y1 = cy + h // 2 - pad - 1
        pygame.draw.line(surf, color, (x0, y0), (x1, y1), thick)
        pygame.draw.line(surf, color, (x0, y1), (x1, y0), thick)

    def _draw_complete_glyph(
        self, surf: pygame.Surface, rect: pygame.Rect, color: tuple[int, int, int]
    ) -> None:
        """Check mark: take the suggested word without typing the rest."""
        w = rect.w
        h = rect.h
        p1 = (rect.centerx - int(w * 0.22), rect.centery + int(h * 0.02))
        p2 = (rect.centerx - int(w * 0.04), rect.centery + int(h * 0.22))
        p3 = (rect.centerx + int(w * 0.26), rect.centery - int(h * 0.20))
        pygame.draw.lines(surf, color, False, [p1, p2, p3], width=max(4, w // 10))

    def _draw_enter_glyph(
        self, surf: pygame.Surface, rect: pygame.Rect, color: tuple[int, int, int]
    ) -> None:
        """Return-key hook: down then left, with an arrowhead at the end."""
        w = rect.w
        h = rect.h
        thick = max(3, w // 14)
        top_y = rect.centery - int(h * 0.18)
        bot_y = rect.centery + int(h * 0.16)
        right_x = rect.centerx + int(w * 0.20)
        left_x = rect.centerx - int(w * 0.24)
        pygame.draw.line(surf, color, (right_x, top_y), (right_x, bot_y), thick)
        pygame.draw.line(surf, color, (right_x, bot_y), (left_x, bot_y), thick)
        arrow = [
            (left_x + int(w * 0.16), bot_y - int(h * 0.16)),
            (left_x, bot_y),
            (left_x + int(w * 0.16), bot_y + int(h * 0.16)),
        ]
        pygame.draw.lines(surf, color, False, arrow, width=thick)

    def _draw_slot_needle(
        self, surf: pygame.Surface, rect: pygame.Rect
    ) -> None:
        """Bead along the bottom of the cell: where the needle sits in this letter."""
        frac = slot_offset_frac(self.travel, self.step_deg, self.slot)
        pad = 6
        x0 = rect.x + pad
        width = max(8, rect.w - pad * 2)
        y = rect.bottom - 11
        track = pygame.Rect(x0, y, width, 5)
        pygame.draw.rect(surf, (36, 32, 28), track, border_radius=3)
        cx = int(x0 + width * 0.5)
        pygame.draw.line(surf, MUTED, (cx, y - 2), (cx, y + 7), 2)
        mx = int(x0 + frac * width)
        my = y + 2
        col = GOLD if not self.paused else MUTED
        pygame.draw.circle(surf, col, (mx, my), 6)
        pygame.draw.circle(surf, WHITE, (mx, my), 6, width=1)

    def _draw_hold_meter(
        self, surf: pygame.Surface, rect: pygame.Rect, now: float
    ) -> None:
        need = self.hold.need_s(self.shown)
        held = self.hold.held_s(now)
        frac = 0.0 if need <= 0 else min(1.0, held / need)
        if frac <= 0:
            return
        fill_h = max(3, int((rect.h - 20) * frac))
        meter = pygame.Rect(rect.x + 4, rect.bottom - 16 - fill_h, rect.w - 8, fill_h)
        pygame.draw.rect(surf, OK, meter, border_radius=4)

    def _draw_panel(self, surf: pygame.Surface) -> None:
        pygame.draw.rect(surf, PANEL, self.panel_rect)
        pygame.draw.line(
            surf,
            PANEL_LINE,
            (self.panel_rect.x, 0),
            (self.panel_rect.x, self.panel_rect.h),
            2,
        )
        mouse = pygame.mouse.get_pos()
        for btn in self.buttons:
            btn.draw(surf, self.font_ui_b, btn.hit(mouse))
        self._draw_delay_radios(surf)
        self._draw_autocomplete_checkbox(surf)

    def set_delay_s(self, seconds: float) -> None:
        if seconds not in DELAY_CHOICES:
            return
        if abs(self.delay_s - seconds) < 1e-9:
            return
        self.delay_s = float(seconds)
        self.hold.hold_s = self.delay_s
        self.hold.space_hold_s = self.delay_s * SPACE_HOLD_MULT
        self._cancel_hold()
        self.rest = RestWindow(self.delay_s, self.still_tol)
        cfg = load_config()
        save_config(cfg["points"], cfg["invert"], delay_s=self.delay_s)

    def _hit_delay(self, pos: tuple[int, int]) -> bool:
        for sec, rect in self.delay_hits:
            if rect.collidepoint(pos):
                self.set_delay_s(sec)
                return True
        return False

    def _draw_delay_radios(self, surf: pygame.Surface) -> None:
        lx, ly = self.delay_label_pos
        surf.blit(
            self.font_small.render("Delay to Capture (seconds)", True, MUTED),
            (lx, ly),
        )
        for sec, rect in self.delay_hits:
            on = abs(sec - self.delay_s) < 1e-9
            cx = rect.x + 12
            cy = rect.centery
            pygame.draw.circle(surf, WHITE, (cx, cy), 9, width=2)
            if on:
                pygame.draw.circle(surf, GOLD, (cx, cy), 5)
            label = self.font_ui.render(str(sec), True, GOLD if on else TEXT_FG)
            surf.blit(label, (cx + 16, rect.y + (rect.h - label.get_height()) // 2))

    def _draw_autocomplete_checkbox(self, surf: pygame.Surface) -> None:
        box = self.autocomplete_box
        pygame.draw.rect(surf, WHITE, box, width=2, border_radius=3)
        if self.autocomplete:
            pygame.draw.line(
                surf, GOLD, (box.x + 3, box.centery), (box.x + 7, box.bottom - 4), 2
            )
            pygame.draw.line(
                surf, GOLD, (box.x + 7, box.bottom - 4), (box.right - 3, box.y + 3), 2
            )
        label = self.font_ui.render("Auto-complete", True, TEXT_FG)
        surf.blit(label, self.autocomplete_label_pos)

    def _log_hint(self) -> str:
        if self.txt_path is not None:
            return f"Saving to {self.txt_path}"
        return f"Logs will be saved in {self.log_dir.resolve()}"

    def _draw_output(self, surf: pygame.Surface) -> None:
        box = self.text_box
        pygame.draw.rect(surf, TEXT_BG, box, border_radius=10)
        pygame.draw.rect(surf, PANEL_LINE, box, width=1, border_radius=10)
        pad = 14
        committed = pygame.Rect(
            box.x + pad,
            box.y + pad,
            box.w - pad * 2,
            max(24, box.h - pad * 2),
        )
        self._draw_committed(surf, committed)
        surf.blit(
            self.font_small.render(self._log_hint(), True, MUTED), self.log_label_rect
        )

    def _draw_committed(self, surf: pygame.Surface, area: pygame.Rect) -> None:
        line_h = self.font_text.get_linesize()
        cell_w = max(self.font_text.size("M")[0], 1)
        max_chars = max(8, area.w // cell_w)
        max_lines = max(1, area.h // line_h)
        self._output_max_chars = max_chars
        self._output_max_lines = max_lines
        if not self.typed:
            hint = self.font_ui.render(
                "Entered words land here after space or complete (✓).",
                True,
                MUTED,
            )
            surf.blit(hint, (area.x, area.y))
            return
        lines = self._wrapped_lines(max_chars)
        total = len(lines)
        max_scroll = max(0, total - max_lines)
        if self.scroll_offset > max_scroll:
            self.scroll_offset = max_scroll
        end = total - self.scroll_offset
        start = max(0, end - max_lines)
        visible = lines[start:end]
        at_bottom = self.scroll_offset == 0
        y = area.y
        last_x, last_y = area.x, y
        for li, line in enumerate(visible):
            x = area.x
            for ci, ch in enumerate(line):
                hot = (
                    at_bottom
                    and li == len(visible) - 1
                    and ci == len(line) - 1
                    and time.time() < self.flash_until
                )
                if ch == " ":
                    bar = pygame.Rect(x + 3, y + line_h - 11, max(10, cell_w - 6), 6)
                    pygame.draw.rect(
                        surf,
                        GOLD if hot else (150, 140, 126),
                        bar,
                        border_radius=3,
                    )
                else:
                    glyph = self.font_text.render(
                        ch, True, GOLD if hot else TEXT_FG
                    )
                    surf.blit(glyph, (x, y))
                x += cell_w
            last_x, last_y = x, y
            y += line_h
        if at_bottom and int(time.time() * 2) % 2 == 0 and not self.draft.typed:
            pygame.draw.rect(
                surf, TEXT_FG, pygame.Rect(last_x + 1, last_y + 4, 3, line_h - 8)
            )
        if max_scroll > 0:
            self._draw_scrollbar(surf, area, total, max_lines, start)

    def _draw_scrollbar(
        self,
        surf: pygame.Surface,
        area: pygame.Rect,
        total_lines: int,
        max_lines: int,
        start_line: int,
    ) -> None:
        track = pygame.Rect(area.right + 4, area.y, 6, area.h)
        pygame.draw.rect(surf, PANEL_LINE, track, border_radius=3)
        thumb_h = max(20, int(track.h * max_lines / total_lines))
        max_start = max(1, total_lines - max_lines)
        thumb_y = track.y + int((track.h - thumb_h) * (start_line / max_start))
        thumb = pygame.Rect(track.x, thumb_y, track.w, thumb_h)
        pygame.draw.rect(surf, MUTED, thumb, border_radius=3)

    def _draw_word_box(self, surf: pygame.Surface) -> None:
        card = self.word_box
        pygame.draw.rect(surf, WORD_CARD, card, border_radius=12)
        pygame.draw.rect(surf, GOLD, card, width=2, border_radius=12)
        inner = card.inflate(-10, -10)
        pygame.draw.rect(surf, WORD_CARD_INNER, inner, border_radius=8)

        label = self.font_small.render("current word", True, MUTED)
        surf.blit(label, (card.centerx - label.get_width() // 2, card.y + 6))

        typed = self.draft.typed
        suggestion = self.draft.suggestion
        ghost = self.draft.ghost
        if not typed:
            return

        hot = time.time() < self.flash_until
        max_w = inner.w - 16
        if ghost or suggestion == typed:
            tsurf = self.font_word.render(typed, True, GOLD if hot else WHITE)
            gsurf = (
                self.font_word.render(ghost, True, GHOST_FG) if ghost else None
            )
            total_w = tsurf.get_width() + (gsurf.get_width() if gsurf else 0)
            if total_w > max_w and total_w > 0:
                scale = max_w / total_w
                th = max(18, int(tsurf.get_height() * scale))
                tsurf = pygame.transform.smoothscale(
                    tsurf, (max(1, int(tsurf.get_width() * scale)), th)
                )
                if gsurf is not None:
                    gsurf = pygame.transform.smoothscale(
                        gsurf, (max(1, int(gsurf.get_width() * scale)), th)
                    )
                total_w = tsurf.get_width() + (gsurf.get_width() if gsurf else 0)
            x = card.centerx - total_w // 2
            y = card.centery - tsurf.get_height() // 2 + 4
            surf.blit(tsurf, (x, y))
            if gsurf is not None:
                surf.blit(gsurf, (x + tsurf.get_width(), y))
        else:
            tsurf = self.font_word.render(typed, True, GOLD if hot else WHITE)
            if tsurf.get_width() > max_w:
                th = max(18, int(tsurf.get_height() * max_w / tsurf.get_width()))
                tsurf = pygame.transform.smoothscale(tsurf, (max_w, th))
            surf.blit(tsurf, tsurf.get_rect(center=(card.centerx, card.centery - 2)))
            line = self.font_small.render(
                f"→  {suggestion}", True, GHOST_FG
            )
            surf.blit(
                line,
                line.get_rect(midbottom=(card.centerx, card.bottom - 8)),
            )


def _wrap(text: str, width: int) -> list[str]:
    if not text:
        return [""]
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        buf = ""
        for ch in para:
            buf += ch
            if len(buf) >= width:
                lines.append(buf)
                buf = ""
        if buf:
            lines.append(buf)
    return lines or [""]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Linear grid: a small needle turn selects the previous or next character."
        )
    )
    p.add_argument("--port", help="Serial port, e.g. /dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--delay", type=float, default=None)
    p.add_argument("--wrap", type=int, default=None, dest="wrap_cols")
    p.add_argument("--invert", action="store_true")
    p.add_argument("--sound", action="store_true")
    p.add_argument(
        "--demo",
        action="store_true",
        help="No Nano: drag the mouse sideways to step through characters",
    )
    p.add_argument(
        "--step",
        type=float,
        default=DEFAULT_STEP_DEG,
        help=(
            "Degrees of needle rotation for each character, including "
            f"space, backspace, and complete (default {DEFAULT_STEP_DEG})"
        ),
    )
    p.add_argument(
        "--log-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "logs",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ser = None
    pump = None
    if not args.demo:
        port = args.port or guess_port()
        if not port:
            print(
                "No serial port found.\n"
                "  python host/capture.py --demo"
            )
            return 1
        print(f"Opening {port} at {args.baud} baud...")
        ser = open_serial(port, args.baud)
        time.sleep(0.3)
        pump = AnglePump(ser)
        pump.start()

    pygame.init()
    try:
        pygame.font.init()
        pygame.font.Font(None, 24)
    except (NotImplementedError, ImportError, pygame.error):
        print(
            "pygame's font module failed to load (common with stock pygame on "
            "Python 3.14).\n"
            "  pip install pygame-ce"
        )
        pygame.quit()
        return 1
    pygame.display.set_caption("Medium Device")
    screen = pygame.display.set_mode((WIN_W, WIN_H), pygame.RESIZABLE)
    clock = pygame.time.Clock()
    gui = LinearCaptureGui(args)

    running = True
    try:
        while running:
            for event in pygame.event.get():
                if gui.handle_event(event):
                    running = False
            if pump is not None:
                latest = pump.latest()
                if latest is not None:
                    gui.raw = latest
            elif not gui.paused:
                gui.feed_demo_mouse(pygame.mouse.get_pos())
            gui.tick(time.time())
            gui.draw(screen)
            pygame.display.flip()
            clock.tick(FPS)
    finally:
        if pump is not None:
            pump.stop()
        if ser is not None:
            ser.close()
        pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
