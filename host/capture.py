#!/usr/bin/env python3
"""Read angles from the Nano and map a 360° circle to A–Z, 0–9, and spaces.

Each session you point at 8 letters (A E J N S W 1 5). The rest of the ring
is interpolated. Gaps between stickers are a space.

Examples:
  python capture.py                    # 8 letters, then type
  python capture.py --debug            # live pointer angle only
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import sys
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
# 8 stickers used as references. Dial angles are the letter centers.
COMPASS_REFS = (
    ("A", "N", "North"),
    ("E", "NE", "Northeast"),
    ("J", "E", "East"),
    ("N", "SE", "Southeast"),
    ("S", "S", "South"),
    ("W", "SW", "Southwest"),
    ("1", "W", "West"),
    ("5", "NW", "Northwest"),
)
COMPASS_STEP_TOL_DEG = 20.0
LETTER_INCH = 1.0
GAP_INCH = 0.5
PITCH_INCH = LETTER_INCH + GAP_INCH
LETTER_DEG = SECTOR_DEG * (LETTER_INCH / PITCH_INCH)  # ~6.67°
HALF_LETTER_DEG = LETTER_DEG / 2.0  # ~3.33° from sticker center
DEFAULT_DELAY_S = 1.0
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


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
        return {"offset": 0.0, "invert": False, "points": [], "delay_s": DEFAULT_DELAY_S}
    data = json.loads(CONFIG_PATH.read_text())
    points = []
    for item in data.get("points") or []:
        ch = str(item.get("char", "")).upper()
        if ch not in CHARS:
            continue
        points.append(make_point(ch, item["angle"]))
    by_char = {p["char"]: p for p in points}
    order = [ch for ch, _c, _n in COMPASS_REFS]
    points = [by_char[c] for c in order if c in by_char]
    offset = float(data.get("offset", points[0]["angle"] if points else 0.0))
    delay_s = float(data.get("delay_s", DEFAULT_DELAY_S))
    if delay_s <= 0:
        delay_s = DEFAULT_DELAY_S
    return {
        "offset": offset,
        "invert": bool(data.get("invert", False)),
        "points": points,
        "delay_s": delay_s,
    }


def save_config(
    points: list[dict], invert: bool = False, delay_s: float | None = None
) -> None:
    if delay_s is None:
        delay_s = load_config()["delay_s"]
    a = next((p["angle"] for p in points if p["char"] == "A"), points[0]["angle"])
    payload = {
        "offset": round(a, 3),
        "invert": invert,
        "delay_s": round(float(delay_s), 3),
        "points": [
            {"char": p["char"], "angle": round(p["angle"], 3)} for p in points
        ],
    }
    CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n")


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


def dial_to_char(dial: float) -> str:
    """Letter if inside the ~1\" sticker, space if in the ~0.5\" gap."""
    idx = round(dial / SECTOR_DEG) % len(CHARS)
    center = (idx * SECTOR_DEG) % 360.0
    if circular_delta(dial, center) <= HALF_LETTER_DEG + 1e-9:
        return CHARS[idx]
    return " "


def angle_to_char(
    angle: float,
    offset: float = 0.0,
    invert: bool = False,
    points: list[dict] | None = None,
) -> str:
    if points:
        dial = raw_to_dial(angle, points, invert)
    else:
        dial = (angle - offset) % 360.0
        if invert:
            dial = (360.0 - dial) % 360.0
    return dial_to_char(dial)


def log_glyph(char: str) -> str:
    return "SP" if char == " " else char


def allow_emit(char: str, typed: list[str]) -> bool:
    """At most one space in a row, and never a leading space."""
    if char != " ":
        return True
    return bool(typed) and typed[-1] != " "


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
    recent: list[float] = []
    fd = sys.stdin.fileno()
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
        while True:
            ready, _, _ = select.select([ser, sys.stdin], [], [], 0.05)
            if ser in ready:
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
            if sys.stdin in ready:
                sys.stdin.read(1)
                if end_line:
                    print()
                break
    except (KeyboardInterrupt, EOFError):
        print()
        raise
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    if recent:
        return circular_mean(recent[-12:])
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
            print(f"\r  now a={angle:6.1f}   min={lo:6.1f}  max={hi:6.1f}  left={left:4.1f}s   ", end="", flush=True)
    except KeyboardInterrupt:
        print()
    print()
    span = hi - lo
    print(f"Samples: {n}")
    print(f"Min a={lo:.1f}°   Max a={hi:.1f}°   Span={span:.1f}°")
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
    return 0


def expected_step_deg(prev: str, cur: str) -> float:
    return (char_dial(cur) - char_dial(prev)) % 360.0


def calibrate_compass(
    ser: serial.Serial, force_invert: bool
) -> tuple[list[dict], bool]:
    """Eight compass stickers; interpolate the other letters from these."""
    print("Hold on the letter, tap space.\n")
    points: list[dict] = []
    for ch, _compass, _name in COMPASS_REFS:
        while True:
            try:
                angle = confirm_angle(ser, live_label=ch, end_line=False)
            except EOFError:
                print()
                sys.exit("Cancelled.")
            step_ok = True
            if points:
                prev = points[-1]
                got = circular_delta(prev["angle"], angle)
                exp = expected_step_deg(prev["char"], ch)
                step_ok = abs(got - exp) <= COMPASS_STEP_TOL_DEG
            if step_ok:
                print(f"\r{ch}  {angle:5.1f}°")
                points.append(make_point(ch, angle))
                break
            print(f"\r{ch}  {angle:5.1f}°  retry")

    invert = True if force_invert else detect_invert(points)
    return points, invert


def cmd_letters(
    ser: serial.Serial,
    delay_s: float | None,
    still_deg: float,
    move_deg: float,
    invert: bool,
    log_dir: Path,
) -> int:
    del still_deg, move_deg  # reserved CLI knobs; settle uses delay + leave-letter
    cfg = load_config()
    if delay_s is None:
        delay_s = cfg["delay_s"]
    elif abs(delay_s - cfg["delay_s"]) > 1e-9:
        save_config(cfg["points"], invert or cfg["invert"], delay_s)
        print(f"Saved delay of {delay_s:.2f}s in config.json")

    print()
    points, invert = calibrate_compass(ser, invert)
    print()

    log_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now()
    stamp = started.strftime("%Y_%m_%d_%H_%M")
    txt_name = f"Session_{stamp}.txt"
    log_name = f"Session_{stamp}.log"
    txt_path = log_dir / txt_name
    log_path = log_dir / log_name
    point_txt = " ".join(f"{p['char']}={p['angle']:.2f}" for p in points)
    header = (
        f"# session start {started.isoformat(timespec='seconds')}\n"
        f"# invert={invert} delay_s={delay_s} letter_in={LETTER_INCH} gap_in={GAP_INCH}\n"
        f"# points {point_txt}\n"
    )
    txt_path.write_text(header)
    log_path.write_text(header)

    print(f"Logging {txt_name} / {log_name}\n")

    candidate: str | None = None
    candidate_since: float | None = None
    last_emitted: str | None = None
    must_leave = False
    typed: list[str] = []

    def show(angle: float, char: str) -> None:
        print(
            f"\r{angle:6.1f}°  {_glyph(char):<5} | {''.join(typed)}",
            end="",
            flush=True,
        )

    try:
        while True:
            raw = ser.readline()
            if not raw:
                continue
            angle = parse_angle(raw.decode("utf-8", errors="replace"))
            if angle is None:
                continue

            now = time.time()
            char = angle_to_char(angle, invert=invert, points=points)
            show(angle, char)

            if must_leave:
                if char != last_emitted:
                    must_leave = False
                    candidate = char
                    candidate_since = now
                continue

            if char != candidate:
                candidate = char
                candidate_since = now
                continue

            if candidate_since is not None and (now - candidate_since) >= delay_s:
                if not allow_emit(char, typed):
                    candidate_since = None
                    continue
                typed.append(char)
                show(angle, char)
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                with txt_path.open("a") as fh:
                    fh.write(char)
                    fh.flush()
                with log_path.open("a") as fh:
                    fh.write(f"{ts}  {log_glyph(char)}  angle={angle:.1f}\n")
                    fh.flush()
                last_emitted = char
                must_leave = True
                candidate_since = None
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
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
                print(f"a={angle:.1f}    (start)")
                last_printed = angle
                last_status = time.time()
                continue
            delta = circular_delta(last_printed, angle)
            if show_all or delta >= min_deg:
                print(f"a={angle:.1f}    (moved {delta:.1f}°)")
                last_printed = angle
                last_status = time.time()
                continue
            now = time.time()
            if now - last_status >= 2.0:
                print(
                    f"  now a={angle:.1f}  moved {delta:.1f}°  "
                    f"(need {min_deg:.0f}° for a new line)"
                )
                last_status = now
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="360° dial → A–Z, 0–9, and spaces (8 reference letters)"
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
        "--debug",
        action="store_true",
        help="Show only the live pointer angle (use this to rotate the base so A is north)",
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
        "--still-deg",
        type=float,
        default=12.0,
        help="Single-sample jump larger than this counts as moving (degrees)",
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
        help="Force reverse direction (normally detected from the saved marks)",
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
        if args.debug:
            return cmd_debug(ser)
        if args.span:
            return cmd_span(ser, args.span_seconds)
        if args.stream or args.all or args.change_pct != 10.0:
            return cmd_stream(ser, args.change_pct, args.all)
        return cmd_letters(
            ser,
            args.delay,
            args.still_deg,
            args.move_deg,
            args.invert,
            args.log_dir,
        )
    finally:
        ser.close()


if __name__ == "__main__":
    raise SystemExit(main())
