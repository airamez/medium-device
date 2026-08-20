#!/usr/bin/env python3
"""On-screen dial for the needle device.

The printed paper letters no longer have to line up with the magnet or the
base. This window *is* the dial: A is always at the top (north), then
clockwise A–Z and 0–9.

Each session:
  1. The window starts paused so a missing dial does not spin the pointer.
     Setup the board and click Start Capture (or press P).
  2. Move the physical needle until the on-screen pointer sits on A.
  3. Click Start (or press Space).
  4. Move off that letter, then hold a character to type it.
     Park in a gap for a space.

  python host/capture-gui.py
  python host/capture-gui.py --sound
  python host/capture-gui.py --demo          # mouse moves the needle (no Nano)
"""

from __future__ import annotations

import argparse
import math
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

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

import capture as c

# Paper-like face on a dark bench. Cardinals match the printable dial.
BG = (36, 32, 28)
PANEL = (48, 43, 38)
PANEL_LINE = (72, 64, 56)
FACE = (244, 237, 222)
RIM = (52, 44, 38)
INK = (32, 28, 24)
MUTED = (118, 108, 96)
CARDINAL = (148, 36, 32)
NEEDLE = (196, 32, 48)
NEEDLE_DARK = (120, 16, 28)
HUB = (28, 24, 22)
GOLD = (214, 168, 64)
GOLD_DIM = (168, 132, 56)
HILITE = (255, 214, 140)
SPACE_HILITE = (214, 206, 190)
TEXT_BG = (28, 24, 22)
TEXT_FG = (236, 228, 214)
OK = (86, 150, 92)
BTN_BG = (64, 56, 50)
BTN_HOVER = (86, 74, 64)
BTN_PRIMARY = (148, 36, 32)
BTN_PRIMARY_HOVER = (176, 48, 42)
BTN_ON = (70, 118, 78)
WHITE = (250, 246, 238)

WIN_W, WIN_H = 1100, 720
FPS = 60


class AnglePump:
    """Background reader for `a=…` lines. GUI thread only drains the queue."""

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
            angle = c._decode_angle(raw) if raw else None
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
        self,
        label: str,
        kind: str = "ghost",
        toggle: bool = False,
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
            bg = (56, 50, 46)
            fg = MUTED
        elif self.kind == "primary":
            bg = BTN_PRIMARY_HOVER if hover else BTN_PRIMARY
            fg = WHITE
        elif self.toggle and self.on:
            bg = BTN_ON
            fg = WHITE
        else:
            bg = BTN_HOVER if hover else BTN_BG
            fg = WHITE
        pygame.draw.rect(surf, bg, self.rect, border_radius=8)
        text = font.render(self.label, True, fg)
        surf.blit(text, text.get_rect(center=self.rect.center))


def _sys_font(size: int, bold: bool = False) -> pygame.font.Font:
    names = "dejavusans,liberation sans,freesans,arial,sans"
    font = pygame.font.SysFont(names, size, bold=bold)
    if font is not None:
        return font
    return pygame.font.Font(None, size)


def _sys_mono_font(size: int, bold: bool = False) -> pygame.font.Font:
    names = (
        "dejavusansmono,liberation mono,nimbus mono,consolas,"
        "courier new,monospace"
    )
    font = pygame.font.SysFont(names, size, bold=bold)
    if font is not None:
        return font
    return pygame.font.Font(None, size)


class CaptureGui:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        cfg = c.load_config()
        self.delay_s = (
            args.delay if args.delay is not None else float(cfg["delay_s"])
        )
        self.wrap_cols = (
            args.wrap_cols
            if args.wrap_cols is not None
            else int(cfg["wrap_cols"])
        )
        self.still_tol = float(cfg["still_tol_deg"])
        self.invert = bool(args.invert or cfg["invert"])
        self.sound = bool(args.sound)
        self.demo = bool(args.demo)
        self.log_dir: Path = args.log_dir

        self.mode = "align"  # align | wait | type
        self.paused = True
        self.raw = 0.0
        self.draw_dial = 0.0
        self.offset = 0.0
        self.shown: str | None = None
        self.hold = c.LetterHold(self.delay_s)
        self.rest = c.RestWindow(self.delay_s, self.still_tol)
        self.must_leave = False
        self.last_emitted: str | None = None
        self.parked_char: str | None = None
        self.parked_angle: float | None = None
        self.typed: list[str] = []
        self.line = ""
        self.txt_path: Path | None = None
        self.log_path: Path | None = None
        self.status = ""
        self.flash_until = 0.0

        self.btn_pause = Button("Start Capture", kind="primary", toggle=True)
        self.btn_start = Button("Start — this is A", kind="primary")
        self.btn_reverse = Button("Reverse direction", toggle=True)
        self.btn_sound = Button("Speak letters", toggle=True)
        self.btn_nudge_ccw = Button("◀  0.5°")
        self.btn_nudge_cw = Button("0.5°  ▶")
        self.btn_clear = Button("Clear text")
        self.btn_reverse.on = self.invert
        self.btn_sound.on = self.sound
        self.buttons = [
            self.btn_pause,
            self.btn_start,
            self.btn_reverse,
            self.btn_sound,
            self.btn_nudge_ccw,
            self.btn_nudge_cw,
            self.btn_clear,
        ]

        if not pygame.get_init():
            pygame.init()
        pygame.font.init()
        self.font_title = _sys_font(28, bold=True)
        self.font_ui = _sys_font(18)
        self.font_ui_b = _sys_font(18, bold=True)
        self.font_small = _sys_font(15)
        self.font_letter = _sys_font(18, bold=True)
        self.font_cardinal = _sys_font(20, bold=True)
        self.font_text = _sys_mono_font(28, bold=True)

        self.cx = 0.0
        self.cy = 0.0
        self.radius = 1.0
        self.hub_r = 48.0
        self.panel = pygame.Rect(0, 0, 0, 0)
        self.text_box = pygame.Rect(0, 0, 0, 0)
        self.log_label_rect = pygame.Rect(0, 0, 0, 0)
        self.win_size = (WIN_W, WIN_H)
        self.layout(WIN_W, WIN_H)
        self.set_paused(True)

    def layout(self, w: int, h: int) -> None:
        text_h = max(150, int(h * 0.26))
        log_h = 26
        footer = text_h + log_h + 20
        self.text_box = pygame.Rect(16, h - footer, w - 32, text_h)
        self.log_label_rect = pygame.Rect(
            16, self.text_box.bottom + 4, w - 32, log_h
        )
        top_h = h - footer
        panel_w = max(320, int(w * 0.34))
        self.panel = pygame.Rect(w - panel_w, 0, panel_w, top_h)
        dial_w = w - panel_w
        self.cx = dial_w / 2.0
        self.cy = top_h / 2.0
        self.radius = max(90.0, min(dial_w, top_h) / 2.0 - 16.0)
        self.hub_r = max(40.0, min(70.0, self.radius * 0.22))
        px = self.panel.x + 20
        pw = self.panel.w - 40
        y = top_h - 16 - 44
        self.btn_clear.place(px, y, pw, 44)
        y -= 56
        half = (pw - 10) // 2
        self.btn_nudge_ccw.place(px, y, half, 44)
        self.btn_nudge_cw.place(px + half + 10, y, pw - half - 10, 44)
        y -= 56
        self.btn_sound.place(px, y, pw, 44)
        y -= 56
        self.btn_reverse.place(px, y, pw, 44)
        y -= 64
        self.btn_start.place(px, y, pw, 52)
        y -= 64
        self.btn_pause.place(px, y, pw, 52)

    def mapped_dial(self) -> float:
        if self.mode == "align":
            return c.offset_to_dial(self.raw, 0.0, False)
        return c.offset_to_dial(self.raw, self.offset, self.invert)

    def current_char(self) -> str:
        return c.hysteretic_dial_char(self.mapped_dial(), self.shown)

    def start_session_logs(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        started = datetime.now()
        stamp = started.strftime("%Y_%m_%d_%H_%M")
        self.txt_path = self.log_dir / f"Session_{stamp}.txt"
        self.log_path = self.log_dir / f"Session_{stamp}.log"
        header = (
            f"# session start {started.isoformat(timespec='seconds')}\n"
            f"# invert={self.invert} mapper=gui-a-north delay_s={self.delay_s} "
            f"wrap_cols={self.wrap_cols} still_tol_deg={self.still_tol}\n"
            f"# A locked at raw={self.offset:.2f}\n"
        )
        self.txt_path.write_text(header)
        self.log_path.write_text(header)

    def write_out(self, char: str) -> None:
        if self.txt_path is None or self.log_path is None:
            return
        with self.txt_path.open("a") as fh:
            fh.write(char)
            fh.flush()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.log_path.open("a") as fh:
            fh.write(f"{ts}  {c.log_glyph(char)}  angle={c.int_deg(self.raw)}\n")
            fh.flush()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.btn_pause.on = paused
        self.btn_pause.label = "Start Capture" if paused else "Pause Capture"
        self.btn_pause.kind = "primary" if paused else "ghost"
        self.btn_start.enabled = not paused
        self.hold.clear()
        self.rest.clear()
        if paused:
            self.status = "Setup the board and click the Start Capture button"

    def lock_a(self) -> None:
        if self.paused:
            return
        self.offset = self.raw
        self.mode = "wait"
        self.shown = None
        self.hold.clear()
        self.rest.clear()
        self.must_leave = False
        self.last_emitted = None
        self.parked_angle = self.raw
        self.parked_char = c.dial_to_char(
            c.offset_to_dial(self.raw, self.offset, self.invert)
        )
        self.btn_start.label = "Realign A"
        self.start_session_logs()

    def realign(self) -> None:
        self.mode = "align"
        self.offset = 0.0
        self.shown = None
        self.hold.clear()
        self.rest.clear()
        self.must_leave = False
        self.last_emitted = None
        self.parked_char = None
        self.parked_angle = None
        self.btn_start.label = "Start — this is A"

    def nudge(self, step: float) -> None:
        if self.paused or self.mode == "align":
            return
        # Positive step rotates the virtual dial clockwise (needle appears
        # to move the other way), so a 1° miss on A is an easy fix.
        self.offset = (self.offset - step) % 360.0

    def emit(self, char: str) -> None:
        self.line += char
        self.typed.append(char)
        self.write_out(char)
        if self.sound:
            c.speak_glyph(char)
        self.last_emitted = char
        self.must_leave = True
        self.hold.clear()
        self.rest.clear()
        self.flash_until = time.time() + 0.35
        if c.should_wrap_line(self.line, char, self.wrap_cols):
            self.line = ""
            self.typed.append("\n")
            self.write_out("\n")

    def tick(self, now: float) -> None:
        if self.paused:
            self.status = "Setup the board and click the Start Capture button"
            return
        dial = self.mapped_dial()
        self.draw_dial = (
            dial
            if self.shown is None
            else c.circular_nudge(self.draw_dial, dial, 0.40)
        )
        char = c.hysteretic_dial_char(dial, self.shown)
        self.shown = char
        self.rest.add(now, self.raw)
        stopped = self.rest.is_still(now)

        if self.mode == "align":
            self.status = "Move the needle onto A (top), then Start."
            return

        if self.mode == "wait":
            if not c.typing_unlocked(
                self.parked_char, self.parked_angle, char, self.raw
            ):
                self.status = "Move off this letter, then hold to type."
                return
            self.mode = "type"
            self.hold.clear()
            self.rest.clear()

        if self.must_leave:
            self.status = "Typed. Move to the next letter."
            if char != self.last_emitted:
                self.must_leave = False
                self.hold.clear()
            return

        if not stopped:
            self.hold.clear()
            self.status = "Moving — stop on a letter to start the timer."
            return

        ready = self.hold.update(now, char)
        if char == " ":
            self.status = "Gap — hold still for a space."
        else:
            self.status = f"Hold {char} to type it."
        if not ready:
            return
        if not c.allow_emit(char, self.typed):
            self.status = "Space already typed — move to a letter."
            return
        self.emit(char)

    def handle_event(self, event: pygame.event.Event) -> bool:
        """True if the window should close."""
        if event.type == pygame.QUIT:
            return True
        if event.type == pygame.VIDEORESIZE:
            self.layout(event.w, event.h)
            return False
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE,):
                return True
            if event.key in (pygame.K_SPACE, pygame.K_RETURN):
                if self.mode == "align" and not self.paused:
                    self.lock_a()
                return False
            if event.key == pygame.K_p:
                self.set_paused(not self.paused)
                return False
            if event.key == pygame.K_LEFT:
                self.nudge(-0.5)
            elif event.key == pygame.K_RIGHT:
                self.nudge(0.5)
            elif event.key == pygame.K_i:
                self.invert = not self.invert
                self.btn_reverse.on = self.invert
            elif event.key == pygame.K_s and (event.mod & pygame.KMOD_CTRL):
                self.sound = not self.sound
                self.btn_sound.on = self.sound
            return False
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        pos = event.pos
        if self.btn_pause.hit(pos):
            self.set_paused(not self.paused)
        elif self.btn_start.hit(pos):
            if self.paused:
                return False
            if self.mode == "align":
                self.lock_a()
            else:
                self.realign()
        elif self.btn_reverse.hit(pos):
            self.invert = not self.invert
            self.btn_reverse.on = self.invert
        elif self.btn_sound.hit(pos):
            self.sound = not self.sound
            self.btn_sound.on = self.sound
        elif self.btn_nudge_ccw.hit(pos):
            self.nudge(-0.5)
        elif self.btn_nudge_cw.hit(pos):
            self.nudge(0.5)
        elif self.btn_clear.hit(pos):
            self.typed.clear()
            self.line = ""
        return False

    def feed_demo_mouse(self, pos: tuple[int, int]) -> None:
        dx = pos[0] - self.cx
        dy_up = self.cy - pos[1]
        if math.hypot(dx, pos[1] - self.cy) > self.radius * 1.35:
            return
        self.raw = math.degrees(math.atan2(dx, dy_up)) % 360.0

    def draw(self, surf: pygame.Surface) -> None:
        size = surf.get_size()
        if size != self.win_size:
            self.win_size = size
            self.layout(*size)
        surf.fill(BG)
        self._draw_dial(surf)
        self._draw_panel(surf)

    def _draw_dial(self, surf: pygame.Surface) -> None:
        cx, cy, r = self.cx, self.cy, self.radius
        pygame.draw.circle(surf, RIM, (int(cx), int(cy)), int(r + 4))
        pygame.draw.circle(surf, FACE, (int(cx), int(cy)), int(r))

        now = time.time()
        char = self.shown or c.dial_to_char(self.mapped_dial())
        flashing = now < self.flash_until
        letter_r = r - 30
        box_half = max(8.0, min(13.0, letter_r * math.radians(c.HALF_LETTER_DEG)))
        space_r = letter_r
        space_half_tan = max(6.0, box_half * 0.70)
        space_half_rad = max(2.4, box_half * 0.26)

        # Every letter box and every gap mark is static (A at north, clockwise).
        for i, ch in enumerate(c.CHARS):
            d = i * c.SECTOR_DEG
            cardinal = ch in c.CAL_REFS
            on = ch == char
            ink = CARDINAL if cardinal else INK
            box = _oriented_square(cx, cy, d, letter_r, box_half)
            if on:
                pygame.draw.polygon(surf, GOLD if flashing else HILITE, box)
            pygame.draw.polygon(surf, ink, box, width=2 if cardinal or on else 1)
            lx, ly = c.dial_xy(d, cx, cy, letter_r)
            font = self.font_cardinal if cardinal else self.font_letter
            label = font.render(ch, True, ink)
            surf.blit(label, label.get_rect(center=(int(lx), int(ly))))

            gap_d = (i + 0.5) * c.SECTOR_DEG
            gap_on = (
                char == " "
                and int(self.mapped_dial() // c.SECTOR_DEG) % len(c.CHARS) == i
            )
            self._draw_space_mark(
                surf,
                cx,
                cy,
                gap_d,
                space_r,
                space_half_rad,
                space_half_tan,
                gap_on,
                flashing,
            )

        self._draw_needle(surf, self.draw_dial)
        self._draw_center_glyph(surf, char, flashing)

    def _draw_center_glyph(
        self, surf: pygame.Surface, char: str, flashing: bool
    ) -> None:
        cx, cy, hub = self.cx, self.cy, self.hub_r
        fill = (250, 246, 236) if not flashing else (255, 236, 180)
        pygame.draw.circle(surf, fill, (int(cx), int(cy)), int(hub))
        pygame.draw.circle(surf, GOLD if flashing else GOLD_DIM, (int(cx), int(cy)), int(hub), width=3)
        pygame.draw.circle(surf, RIM, (int(cx), int(cy)), int(hub), width=1)
        if char == " ":
            bar_w = hub * 1.15
            bar_h = hub * 0.32
            rect = pygame.Rect(0, 0, int(bar_w), int(bar_h))
            rect.center = (int(cx), int(cy))
            fill = GOLD if flashing else INK
            pygame.draw.rect(surf, fill, rect, border_radius=int(bar_h / 2))
            pygame.draw.rect(surf, FACE, rect, width=2, border_radius=int(bar_h / 2))
            return
        size = max(28, int(hub * 1.15))
        font = _sys_font(size, bold=True)
        gcol = CARDINAL if flashing else INK
        gsurf = font.render(char, True, gcol)
        surf.blit(gsurf, gsurf.get_rect(center=(int(cx), int(cy))))

    def _draw_space_mark(
        self,
        surf: pygame.Surface,
        cx: float,
        cy: float,
        dial: float,
        radius: float,
        half_rad: float,
        half_tan: float,
        on: bool,
        flashing: bool,
    ) -> None:
        """Mini space-bar key in the gap between letters."""
        pts = _oriented_rect(cx, cy, dial, radius, half_rad, half_tan)
        fill = (GOLD if flashing else HILITE) if on else SPACE_HILITE
        outline = GOLD if on else MUTED
        pygame.draw.polygon(surf, fill, pts)
        pygame.draw.polygon(surf, outline, pts, width=2 if on else 1)
        # Rounded end-caps so it reads as a keyboard space key.
        rad = math.radians(dial)
        px, py = math.sin(rad), -math.cos(rad)
        tx, ty = -py, px
        mx, my = cx + px * radius, cy + py * radius
        cap_r = max(2, int(half_rad))
        for sign in (1.0, -1.0):
            pygame.draw.circle(
                surf,
                fill,
                (int(mx + tx * half_tan * sign), int(my + ty * half_tan * sign)),
                cap_r,
            )

    def _draw_needle(self, surf: pygame.Surface, dial: float) -> None:
        """Pointer starts on the center-circle rim and aims at the letter."""
        cx, cy, r, hub = self.cx, self.cy, self.radius, self.hub_r
        rad = math.radians(dial)
        px, py = math.sin(rad), -math.cos(rad)
        qx, qy = -py, px
        # Tuck the base under the center ring. Stop short of the letter
        # boxes so every rim glyph stays readable.
        start = hub - 2
        tip_r = r - 56
        base_w = max(7.0, hub * 0.16)
        tip = (cx + px * tip_r, cy + py * tip_r)
        left = (cx + px * start + qx * base_w, cy + py * start + qy * base_w)
        right = (cx + px * start - qx * base_w, cy + py * start - qy * base_w)
        pygame.draw.polygon(
            surf,
            NEEDLE,
            [
                (int(tip[0]), int(tip[1])),
                (int(left[0]), int(left[1])),
                (int(right[0]), int(right[1])),
            ],
        )
        pygame.draw.polygon(
            surf,
            NEEDLE_DARK,
            [
                (int(tip[0]), int(tip[1])),
                (int(left[0]), int(left[1])),
                (int(right[0]), int(right[1])),
            ],
            width=1,
        )

    def _draw_panel(self, surf: pygame.Surface) -> None:
        pygame.draw.rect(surf, PANEL, self.panel)
        pygame.draw.line(
            surf,
            PANEL_LINE,
            (self.panel.x, 0),
            (self.panel.x, self.panel.bottom),
            2,
        )
        x = self.panel.x + 20
        y = 22
        title = self.font_title.render("Needle capture", True, WHITE)
        surf.blit(title, (x, y))
        y += 38
        sub = (
            "On-screen dial  ·  A at north"
            if not self.demo
            else "Demo  ·  drag around the dial"
        )
        surf.blit(self.font_small.render(sub, True, MUTED), (x, y))
        y += 28

        if self.paused:
            mode_label, mode_col = "PAUSED", GOLD
        else:
            mode_label = {
                "align": "ALIGN",
                "wait": "MOVE",
                "type": "CAPTURE",
            }[self.mode]
            mode_col = GOLD if self.mode == "align" else (OK if self.mode == "type" else WHITE)
        surf.blit(self.font_ui_b.render(mode_label, True, mode_col), (x, y))
        y += 26
        for line in _wrap(self.status, 34):
            surf.blit(self.font_ui.render(line, True, TEXT_FG), (x, y))
            y += 22
        y += 8

        now = time.time()
        need = self.hold.need_s(self.shown)
        held = self.hold.held_s(now) if self.mode == "type" and not self.must_leave else 0.0
        frac = 0.0 if need <= 0 else min(1.0, held / need)
        bar = pygame.Rect(x, y, self.panel.w - 40, 12)
        pygame.draw.rect(surf, TEXT_BG, bar, border_radius=6)
        if frac > 0:
            fill = pygame.Rect(bar.x, bar.y, int(bar.w * frac), bar.h)
            pygame.draw.rect(surf, GOLD if (self.shown == " ") else OK, fill, border_radius=6)
        y += 22
        timer = "ok" if self.must_leave else f"{held:.1f}s"
        surf.blit(self.font_small.render(f"Hold  {timer}", True, MUTED), (x, y))
        y += 28

        raw_txt = (
            f"raw {self.raw:6.1f}°    dial {self.mapped_dial():6.1f}°"
        )
        surf.blit(self.font_small.render(raw_txt, True, MUTED), (x, y))
        y += 18
        lock_txt = (
            "A is not locked yet"
            if self.mode == "align"
            else f"A locked at {self.offset:.1f}° raw"
        )
        surf.blit(self.font_small.render(lock_txt, True, MUTED), (x, y))

        self._draw_output(surf)

        mouse = pygame.mouse.get_pos()
        self.btn_start.enabled = not self.paused
        for btn in self.buttons:
            if btn in (self.btn_nudge_ccw, self.btn_nudge_cw):
                btn.enabled = (not self.paused) and self.mode != "align"
            btn.draw(surf, self.font_ui_b, btn.hit(mouse))

    def _log_hint(self) -> str:
        if self.txt_path is not None:
            return f"Saving to {self.txt_path}"
        return f"Logs will be saved in {self.log_dir.resolve()}"

    def _draw_output(self, surf: pygame.Surface) -> None:
        box = self.text_box
        pygame.draw.rect(surf, TEXT_BG, box, border_radius=10)
        pygame.draw.rect(surf, PANEL_LINE, box, width=1, border_radius=10)
        pad = 14
        line_h = self.font_text.get_linesize()
        cell_w = max(self.font_text.size("M")[0], 1)
        max_chars = max(8, (box.w - pad * 2) // cell_w)
        max_lines = max(2, (box.h - pad * 2) // line_h)

        if self.typed:
            raw_lines = "".join(self.typed).split("\n")
            lines: list[str] = []
            for para in raw_lines:
                if para == "":
                    lines.append("")
                    continue
                for i in range(0, len(para), max_chars):
                    lines.append(para[i : i + max_chars])
            visible = lines[-max_lines:]
            y = box.y + pad
            last_x = box.x + pad
            last_y = y
            for li, line in enumerate(visible):
                x = box.x + pad
                for ci, ch in enumerate(line):
                    is_last = (
                        li == len(visible) - 1 and ci == len(line) - 1
                    )
                    hot = is_last and time.time() < self.flash_until
                    if ch == " ":
                        bar = pygame.Rect(
                            x + 3,
                            y + line_h - 11,
                            max(10, cell_w - 6),
                            6,
                        )
                        pygame.draw.rect(
                            surf,
                            GOLD if hot else (150, 140, 126),
                            bar,
                            border_radius=3,
                        )
                    else:
                        color = GOLD if hot else TEXT_FG
                        glyph = self.font_text.render(ch, True, color)
                        surf.blit(glyph, (x, y))
                    x += cell_w
                last_x, last_y = x, y
                y += line_h
            caret_on = int(time.time() * 2) % 2 == 0
            if caret_on:
                pygame.draw.rect(
                    surf,
                    TEXT_FG,
                    pygame.Rect(last_x + 1, last_y + 4, 3, line_h - 8),
                )
        else:
            hint = self.font_ui.render(
                "Typed letters appear here. Spaces show as a bar.",
                True,
                MUTED,
            )
            surf.blit(hint, (box.x + pad, box.y + pad))

        log = self.font_small.render(self._log_hint(), True, MUTED)
        surf.blit(log, self.log_label_rect)


def _oriented_square(
    cx: float, cy: float, dial_deg: float, radius: float, half: float
) -> list[tuple[int, int]]:
    """Square in the radial/tangential frame: sits on the letter (or gap) mark."""
    rad = math.radians(dial_deg)
    px, py = math.sin(rad), -math.cos(rad)
    tx, ty = -py, px
    pts: list[tuple[int, int]] = []
    for sr, st in ((1.0, 1.0), (1.0, -1.0), (-1.0, -1.0), (-1.0, 1.0)):
        pts.append(
            (
                int(cx + px * (radius + sr * half) + tx * st * half),
                int(cy + py * (radius + sr * half) + ty * st * half),
            )
        )
    return pts


def _oriented_rect(
    cx: float,
    cy: float,
    dial_deg: float,
    radius: float,
    half_rad: float,
    half_tan: float,
) -> list[tuple[int, int]]:
    """Wide, short rectangle in the radial/tangential frame (space-bar shape)."""
    rad = math.radians(dial_deg)
    px, py = math.sin(rad), -math.cos(rad)
    tx, ty = -py, px
    pts: list[tuple[int, int]] = []
    for sr, st in ((1.0, 1.0), (1.0, -1.0), (-1.0, -1.0), (-1.0, 1.0)):
        pts.append(
            (
                int(cx + px * (radius + sr * half_rad) + tx * st * half_tan),
                int(cy + py * (radius + sr * half_rad) + ty * st * half_tan),
            )
        )
    return pts


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
            "On-screen A-north dial. Point the needle at A, then hold letters to type."
        )
    )
    p.add_argument("--port", help="Serial port, e.g. /dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument(
        "--delay",
        type=float,
        default=None,
        help=f"Seconds to hold a letter (default {c.DEFAULT_DELAY_S})",
    )
    p.add_argument(
        "--wrap",
        type=int,
        default=None,
        dest="wrap_cols",
        help=f"New line after this many characters (default {c.DEFAULT_WRAP_COLS})",
    )
    p.add_argument(
        "--invert",
        action="store_true",
        help="Sensor counts opposite the paper (also a button in the window)",
    )
    p.add_argument(
        "--sound",
        action="store_true",
        help="Speak each typed letter",
    )
    p.add_argument(
        "--demo",
        action="store_true",
        help="No Nano: move the mouse around the dial to aim the needle",
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
        port = args.port or c.guess_port()
        if not port:
            print(
                "No serial port found.\n"
                "  ls /dev/ttyUSB* /dev/ttyACM*\n"
                "  python host/capture-gui.py --demo    # try the dial with the mouse"
            )
            return 1
        print(f"Opening {port} at {args.baud} baud...")
        ser = c.open_serial(port, args.baud)
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
            "  pip install pygame-ce\n"
            "Then:  python host/capture-gui.py"
        )
        pygame.quit()
        return 1
    pygame.display.set_caption("Needle capture")
    screen = pygame.display.set_mode((WIN_W, WIN_H), pygame.RESIZABLE)
    clock = pygame.time.Clock()
    gui = CaptureGui(args)

    running = True
    try:
        while running:
            for event in pygame.event.get():
                if gui.handle_event(event):
                    running = False
            if pump is not None:
                latest = pump.latest()
                if latest is not None and not gui.paused:
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
