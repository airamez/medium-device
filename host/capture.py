#!/usr/bin/env python3
"""Read angles from the Nano and map a 360° circle to A–Z and 0–9.

36 symbols × 10° each:

  A at the calibrated offset, then B, C, … Z, 0, 1, … 9 around the circle.

Examples:
  python capture.py --all              # raw angles (debug)
  python capture.py --calibrate        # point magnet/needle at A, then save offset
  python capture.py --letters          # print a character when it settles
"""

from __future__ import annotations

import argparse
import glob
import json
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
SECTOR_DEG = 360.0 / len(CHARS)  # 10°
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


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"offset": 0.0, "invert": False}
    data = json.loads(CONFIG_PATH.read_text())
    return {
        "offset": float(data.get("offset", 0.0)),
        "invert": bool(data.get("invert", False)),
    }


def save_config(offset: float, invert: bool = False) -> None:
    CONFIG_PATH.write_text(
        json.dumps({"offset": offset, "invert": invert}, indent=2) + "\n"
    )


def angle_to_char(angle: float, offset: float, invert: bool = False) -> str:
    """0–10° from A/north = A, then clockwise B…Z, 0–9."""
    corrected = (angle - offset) % 360.0
    if invert:
        corrected = (360.0 - corrected) % 360.0
    index = int(corrected // SECTOR_DEG) % len(CHARS)
    return CHARS[index]


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
        print("Good: almost a full circle. You can use --calibrate and --letters.")
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


def wait_any_key() -> None:
    """Wait for one key (no Enter needed). Fall back to Enter if the tty is not raw-capable."""
    try:
        import termios
        import tty
    except ImportError:
        input()
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print()


def calibrate_to_a(ser: serial.Serial, invert: bool) -> float:
    print()
    print("Calibration — set letter A")
    print("  1. Move the needle until it points at A on the dial.")
    print("  2. Hold it still.")
    print("  3. Press any key.")
    print()
    wait_any_key()
    ser.reset_input_buffer()
    angle = read_one_angle(ser)
    save_config(angle, invert)
    print(f"A is set to {angle:.1f}°.")
    print("Other marks: each 10° clockwise = next letter, then 0–9 after Z.")
    print()
    return angle


def cmd_calibrate(ser: serial.Serial, invert: bool) -> int:
    calibrate_to_a(ser, invert)
    print(f"Saved in {CONFIG_PATH}")
    print("Run:  python host/capture.py")
    return 0


def cmd_letters(
    ser: serial.Serial,
    dwell_s: float,
    still_deg: float,
    move_deg: float,
    invert: bool,
    log_dir: Path,
) -> int:
    cfg = load_config()
    invert = invert or cfg["invert"]
    offset = calibrate_to_a(ser, invert)

    log_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now()
    stamp = started.strftime("%Y_%m_%d_%H_%M")
    txt_name = f"Session_{stamp}.txt"
    log_name = f"Session_{stamp}.log"
    txt_path = log_dir / txt_name
    log_path = log_dir / log_name
    header = (
        f"# session start {started.isoformat(timespec='seconds')}\n"
        f"# A offset_deg={offset:.2f} invert={invert}\n"
    )
    txt_path.write_text(header)
    log_path.write_text(header)

    print("Map: 0–10°=A … Z then 0–9 clockwise from A")
    print("Silent while moving; one character when you pause. Ctrl+C to stop.")
    print(f"Logging this session to {txt_name} (letters only)")
    print(f"Logging this session to {log_name} (letters + angles)")
    print(f"(folder: {log_dir})\n")

    candidate: str | None = None
    candidate_since: float | None = None
    last_emitted: str | None = None
    must_leave = False

    try:
        while True:
            raw = ser.readline()
            if not raw:
                continue
            angle = parse_angle(raw.decode("utf-8", errors="replace"))
            if angle is None:
                continue

            now = time.time()
            char = angle_to_char(angle, offset, invert)

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

            if candidate_since is not None and (now - candidate_since) >= dwell_s:
                if char != last_emitted:
                    print(char, end="", flush=True)
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    with txt_path.open("a") as fh:
                        fh.write(char)
                        fh.flush()
                    with log_path.open("a") as fh:
                        fh.write(f"{ts}  {char}  angle={angle:.1f}\n")
                        fh.flush()
                    last_emitted = char
                    must_leave = True
                    candidate_since = None
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        return 0


def cmd_stream(ser: serial.Serial, change_pct: float, show_all: bool) -> int:
    min_deg = 360.0 * (change_pct / 100.0)
    if show_all:
        print("Printing every sample (--all).")
    else:
        print(
            f"New line only if angle moves {change_pct:.0f}% ({min_deg:.0f}°). "
            "Status every 2s. Use --all or --letters."
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
        description="360° circle → A–Z and 0–9 (36 sectors of 10°)"
    )
    parser.add_argument("--port", help="Serial port, e.g. /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--change-pct",
        type=float,
        default=10.0,
        help="For raw stream: print when angle moves this %% of a turn",
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
        "--calibrate",
        action="store_true",
        help="Save current angle as letter A",
    )
    parser.add_argument(
        "--letters",
        action="store_true",
        help="Print A–Z / 0–9 when the needle settles",
    )
    parser.add_argument("--dwell", type=float, default=0.45, help="Seconds the same letter must hold before printing")
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
        help="If letters go the wrong way around the circle, use this (then --calibrate again)",
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
        if args.span:
            return cmd_span(ser, args.span_seconds)
        if args.calibrate:
            return cmd_calibrate(ser, args.invert)
        if args.stream or args.all or (
            args.change_pct != 10.0 and not args.letters
        ):
            return cmd_stream(ser, args.change_pct, args.all)
        return cmd_letters(
            ser,
            args.dwell,
            args.still_deg,
            args.move_deg,
            args.invert,
            args.log_dir,
        )
    finally:
        ser.close()


if __name__ == "__main__":
    raise SystemExit(main())
