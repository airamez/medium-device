"""Serial reader for the wire_check Arduino sketch.

wire_check.ino is a diagnostic sketch that tests the physical connections
between the Arduino Nano and the AS5600: VCC, GND, SDA (A4), SCL (A5),
DIR→GND, and the optional OUT→A0 jumper. It prints a verdict every few
seconds. This script connects to the selected Nano port, echoes the output,
and color-codes the verdict so you can see at a glance which wire is wrong.
"""

import argparse
import sys
import time

import serial
from serial.tools.list_ports import comports

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"
ERASE_LINE = "\033[2K"


def colorize(line: str) -> str:
    """Color-code the final verdict lines."""
    if line.startswith("  I2C works"):
        return GREEN + line + RESET
    if line.startswith("  Module looks POWERED"):
        return YELLOW + line + RESET
    if line.startswith(("  A4 and/or A5 sit LOW", "  I2C dead AND A0 near 0", "  A0 is maxed out")):
        return RED + line + RESET
    if line.startswith("  ->"):
        # colored arrows inherit the severity of the previous verdict;
        # keep them in the same red/yellow palette, but always be cautious.
        if "suspect SDA" in line or "suspect SCL" in line or "VCC or GND" in line or "Still no I2C" in line:
            return RED + line + RESET
        return YELLOW + line + RESET
    return line


WIRING = [
    ("AS5600 VCC", "Nano 5V"),
    ("AS5600 GND", "Nano GND"),
    ("AS5600 SDA", "Nano A4"),
    ("AS5600 SCL", "Nano A5"),
    ("AS5600 DIR", "Nano GND"),
]


def print_wiring_ok(addr: str) -> None:
    """Print a satisfying summary when the AS5600 is found on I2C."""
    print(f"AS5600 found at {addr}")
    for src, dst in WIRING:
        print(f"  {src:12} -> {dst:12}  OK")
    print()


def pick_port(given: str | None) -> str:
    if given:
        return given
    ports = [c.device for c in comports()]
    if not ports:
        raise SystemExit("No serial ports found. Is the Nano plugged in?")
    if len(ports) == 1:
        return ports[0]
    print("Available serial ports:")
    for i, p in enumerate(ports, start=1):
        print(f"  {i}. {p}")
    raise SystemExit("More than one port found — use --port to choose one.")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Read and color the output of firmware/wire_check.ino.",
        epilog="Upload wire_check.ino first, then run this script.",
    )
    p.add_argument("--port", help="Serial port, e.g. /dev/ttyUSB0 or COM3")
    p.add_argument("--baud", type=int, default=115200, help="Baud rate (default 115200)")
    p.add_argument("--once", action="store_true", help="Stop after the first complete verdict")
    p.add_argument(
        "--timeout",
        type=float,
        default=0.1,
        help="Serial read timeout in seconds (default 0.1)",
    )
    args = p.parse_args(argv)

    port = pick_port(args.port)
    print(f"Opening {port} at {args.baud} baud... (press Ctrl+C to stop)")

    try:
        with serial.Serial(port, args.baud, timeout=args.timeout) as ser:
            # Let the first `=== wire_check ===` banner arrive, then sync.
            time.sleep(0.3)
            ser.reset_input_buffer()

            in_verdict = False
            last_was_a = False
            while True:
                try:
                    raw = ser.readline()
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if not line:
                        continue

                    if line.startswith("--- test ---"):
                        in_verdict = False
                        print("\n" + line)
                    elif "3) Verdict:" in line:
                        in_verdict = True
                        print(line)
                    elif in_verdict:
                        colored = colorize(line)
                        print(colored)
                        if (
                            args.once and colored != line
                        ) or line.startswith("  I2C works"):
                            break
                    elif line.startswith(("  found 0x", "  device at 0x")):
                        addr = line.split()[-1]
                        print_wiring_ok(addr)
                        last_was_a = False
                    elif line.startswith("a="):
                        # Streaming angle values overwrite the same terminal line.
                        print(f"\r{ERASE_LINE}{line}", end="", flush=True)
                        last_was_a = True
                    else:
                        if last_was_a:
                            print()
                            last_was_a = False
                        print(line)
                except KeyboardInterrupt:
                    break
    except serial.SerialException as e:
        raise SystemExit(f"Could not open {port}: {e}")

    print("\nStopped.")


if __name__ == "__main__":
    main()
