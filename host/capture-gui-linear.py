#!/usr/bin/env python3
"""Linear-grid capture. Small needle turns step to the previous or next character.

The circular dial is still host/capture-gui.py. This layout shows five
characters per row, with space, complete, then backspace at the right of
every line (letters ␣ ✓ ⌫). The leftover Z row is centered. Numbers
follow on their own rows. Each cell — letter or control — owns the same
amount of needle travel (default 10°), so the grid is not squeezed into
one 360° turn.

Letters go into a current-word box in the middle of the transcript.
Space commits the letters you actually typed. Complete (✓) takes the
suggested dictionary word so the needle does not have to finish it.

Each session:
  1. Starts paused. Setup the board and click Start Capture (or press P).
     The needle's current pose is A. Nothing is typed until it moves.
  2. Rotate a little clockwise for the next character, the other way for
     the previous one. Hold to type. Hold space to enter the word, ⌫ to
     delete, ✓ to take the autocomplete suggestion.

  python host/capture-gui-linear.py
  python host/capture-gui-linear.py --sound
  python host/capture-gui-linear.py --demo
  python host/capture-gui-linear.py --step 3
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
import words as w

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
DELAY_CHOICES = (1, 2, 3)
CONTENT_COLS = 5  # letters/digits per line
# Stable tokens — not ASCII \\b/\\t, which some paths treat as controls and drop.
BS = "⌫"
ACCEPT = "✓"
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
    """Each line is: up to n characters (centered), then space, complete, backspace."""
    return [
        _center_pad(list(part), n) + [" ", ACCEPT, BS] for part in _chunk(seq, n)
    ]


LETTER_ROWS = _rows_with_ends(LETTERS, CONTENT_COLS)
DIGIT_ROWS = _rows_with_ends(DIGITS, CONTENT_COLS)
GRID_ROWS = LETTER_ROWS + DIGIT_ROWS
KEYS = [ch for row in GRID_ROWS for ch in row if ch is not None]
ROW_CELLS = CONTENT_COLS + 3  # chars ␣ ✓ ⌫


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
        cfg = c.load_config()
        raw_delay = (
            args.delay if args.delay is not None else float(cfg["delay_s"])
        )
        snapped = int(round(raw_delay))
        if snapped not in DELAY_CHOICES:
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
        self.demo = bool(args.demo)
        self.log_dir: Path = args.log_dir
        self.step_deg = max(4.0, float(args.step))

        self.paused = True
        self.raw = 0.0
        self.last_raw: float | None = None
        self.travel = 0.0
        self.slot = 0
        self.index = 0
        self.hold = c.LetterHold(self.delay_s)
        self.rest = c.RestWindow(self.delay_s, self.still_tol)
        self.must_leave = False
        self.last_emitted: str | None = None
        self.last_index: int | None = None
        self.hold_lock_angle: float | None = None
        self.typed: list[str] = []
        self.line = ""
        self.lexicon = w.load_index()
        self.draft = w.WordDraft(self.lexicon)
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
        self.btn_reverse.on = self.invert
        self.btn_sound.on = self.sound
        self.buttons = [
            self.btn_pause,
            self.btn_reverse,
            self.btn_sound,
            self.btn_clear,
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
        self.toolbar_rect = pygame.Rect(0, 0, 0, 0)
        self.grid_origin = (0, 0)
        self.cell = 64
        self.gap = 8
        self.cell_rects: list[pygame.Rect] = []
        self.delay_hits: list[tuple[int, pygame.Rect]] = []
        self.delay_label_pos = (0, 0)
        self.win_size = (WIN_W, WIN_H)
        self.layout(WIN_W, WIN_H)
        self.set_paused(True)

    @property
    def shown(self) -> str:
        return KEYS[self.index]

    def layout(self, w: int, h: int) -> None:
        toolbar_h = 58
        status_h = 26
        word_h = 92
        log_h = 22
        text_h = max(72, int(h * 0.14))
        footer = word_h + text_h + log_h + 28
        top = toolbar_h + status_h
        self.toolbar_rect = pygame.Rect(0, 0, w, toolbar_h)
        grid_w = w - 40
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

        ww = min(640, max(300, int(w * 0.5)))
        self.word_box = pygame.Rect(0, 0, ww, word_h)
        self.word_box.centerx = w // 2
        self.word_box.y = grid_bottom + 10
        self.text_box = pygame.Rect(
            16, self.word_box.bottom + 10, w - 32, text_h
        )
        self.log_label_rect = pygame.Rect(
            16, min(h - log_h - 4, self.text_box.bottom + 4), w - 32, log_h
        )

        bh = 40
        by = 10
        x = w - 16
        for btn, bw in (
            (self.btn_clear, 118),
            (self.btn_sound, 138),
            (self.btn_reverse, 168),
            (self.btn_pause, 158),
        ):
            x -= bw
            btn.place(x, by, bw, bh)
            x -= 8
        delay_w = 132
        x -= delay_w + 12
        self.delay_label_pos = (x, 8)
        self.delay_hits = []
        slot = delay_w // len(DELAY_CHOICES)
        for i, sec in enumerate(DELAY_CHOICES):
            self.delay_hits.append(
                (sec, pygame.Rect(x + i * slot, 28, slot, 26))
            )

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
            fh.write(f"{ts}  {glyph}  angle={c.int_deg(self.raw)}\n")
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
        """Unwrapped travel: every cell (letter, space, backspace) is step_deg wide."""
        if self.last_raw is None:
            self.last_raw = self.raw
            return
        delta = c.signed_turn(self.last_raw, self.raw)
        self.last_raw = self.raw
        if self.invert:
            delta = -delta
        self.travel += delta
        new_slot = c.travel_slot(self.travel, self.step_deg, self.slot)
        if new_slot == self.slot:
            return
        self.slot = new_slot
        self.index = self.slot % len(KEYS)
        self._cancel_hold()
        self.rest.clear()

    def rewrite_txt(self) -> None:
        if self.txt_path is None:
            return
        header = getattr(self, "log_header", "")
        self.txt_path.write_text(header + "".join(self.typed))

    def _refresh_line(self) -> None:
        self.line = "".join(self.typed).split("\n")[-1] if self.typed else ""

    def _append_committed(self, text: str) -> None:
        for ch in text:
            self.line += ch
            self.typed.append(ch)
            self.write_out(ch)
            if c.should_wrap_line(self.line, ch, self.wrap_cols):
                self.line = ""
                self.typed.append("\n")
                self.write_out("\n")

    def emit_backspace(self) -> bool:
        changed = w.delete_current_word(self.draft, self.typed)
        if not changed:
            return False
        self._refresh_line()
        self.rewrite_txt()
        self.write_log("BS")
        if self.sound:
            c.speak_glyph("\b")
        return True

    def _commit_word(self, word: str, via: str) -> None:
        if not word:
            return
        self._append_committed(word + " ")
        if via == "accept":
            self.write_log(f"OK  {word}")
            if self.sound:
                c.speak_glyph(word)
        else:
            self.write_log(f"SP  {word}")
            if self.sound:
                c.speak_glyph(" ")

    def _is_backspace(self, char: str) -> bool:
        return char in (BS, "\b")

    def emit(self, char: str) -> None:
        if self._is_backspace(char):
            self.emit_backspace()
            # Stay on ⌫: another hold peels the next letter of this word.
            self.last_emitted = char
            self.last_index = self.index
            self.must_leave = False
            self.hold.clear()
            self.hold_lock_angle = self.raw
            self.flash_until = time.time() + 0.35
            return
        if char == ACCEPT:
            word = self.draft.take_suggestion()
            self._commit_word(word, via="accept")
        elif char == " ":
            word = self.draft.take_typed()
            self._commit_word(word, via="space")
        else:
            self.draft.add(char)
            self.write_log(c.log_glyph(char))
            if self.sound:
                c.speak_glyph(char)
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
            and c.circular_delta(self.raw, self.hold_lock_angle) > abort_deg
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
        if self.btn_pause.hit(pos):
            self.set_paused(not self.paused)
        elif self.btn_reverse.hit(pos):
            self.invert = not self.invert
            self.btn_reverse.on = self.invert
        elif self.btn_sound.hit(pos):
            self.sound = not self.sound
            self.btn_sound.on = self.sound
        elif self.btn_clear.hit(pos):
            self.typed.clear()
            self.line = ""
            self.draft.clear()
            self.rewrite_txt()
        elif self._hit_delay(pos):
            pass
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
        self._draw_toolbar(surf)
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

    def _draw_slot_needle(
        self, surf: pygame.Surface, rect: pygame.Rect
    ) -> None:
        """Bead along the bottom of the cell: where the needle sits in this letter."""
        frac = c.slot_offset_frac(self.travel, self.step_deg, self.slot)
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

    def _draw_toolbar(self, surf: pygame.Surface) -> None:
        pygame.draw.rect(surf, PANEL, self.toolbar_rect)
        pygame.draw.line(
            surf,
            PANEL_LINE,
            (0, self.toolbar_rect.bottom),
            (self.toolbar_rect.w, self.toolbar_rect.bottom),
            2,
        )
        title = self.font_title.render("Medium Device", True, WHITE)
        surf.blit(title, (20, 16))
        status = self.font_small.render(self.status, True, MUTED)
        surf.blit(status, (20, self.toolbar_rect.bottom + 4))
        self._draw_delay_radios(surf)
        mouse = pygame.mouse.get_pos()
        for btn in self.buttons:
            btn.draw(surf, self.font_ui_b, btn.hit(mouse))

    def set_delay_s(self, seconds: int) -> None:
        if seconds not in DELAY_CHOICES:
            return
        if abs(self.delay_s - seconds) < 1e-9:
            return
        self.delay_s = float(seconds)
        self.hold.hold_s = self.delay_s
        self.hold.space_hold_s = self.delay_s * c.SPACE_HOLD_MULT
        self._cancel_hold()
        self.rest = c.RestWindow(self.delay_s, self.still_tol)
        cfg = c.load_config()
        c.save_config(cfg["points"], cfg["invert"], delay_s=self.delay_s)

    def _hit_delay(self, pos: tuple[int, int]) -> bool:
        for sec, rect in self.delay_hits:
            if rect.collidepoint(pos):
                self.set_delay_s(sec)
                return True
        return False

    def _draw_delay_radios(self, surf: pygame.Surface) -> None:
        lx, ly = self.delay_label_pos
        surf.blit(self.font_small.render("Hold", True, MUTED), (lx, ly))
        chosen = int(round(self.delay_s))
        for sec, rect in self.delay_hits:
            on = sec == chosen
            cx = rect.x + 12
            cy = rect.centery
            pygame.draw.circle(surf, WHITE, (cx, cy), 9, width=2)
            if on:
                pygame.draw.circle(surf, GOLD, (cx, cy), 5)
            label = self.font_ui.render(str(sec), True, GOLD if on else TEXT_FG)
            surf.blit(label, (cx + 16, rect.y + (rect.h - label.get_height()) // 2))

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
        if not self.typed:
            hint = self.font_ui.render(
                "Entered words land here after space or complete (✓).",
                True,
                MUTED,
            )
            surf.blit(hint, (area.x, area.y))
            return
        raw_lines = "".join(self.typed).split("\n")
        lines: list[str] = []
        for para in raw_lines:
            if para == "":
                lines.append("")
                continue
            for i in range(0, len(para), max_chars):
                lines.append(para[i : i + max_chars])
        visible = lines[-max_lines:]
        y = area.y
        last_x, last_y = area.x, y
        for li, line in enumerate(visible):
            x = area.x
            for ci, ch in enumerate(line):
                hot = (
                    li == len(visible) - 1
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
        if int(time.time() * 2) % 2 == 0 and not self.draft.typed:
            pygame.draw.rect(
                surf, TEXT_FG, pygame.Rect(last_x + 1, last_y + 4, 3, line_h - 8)
            )

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
            empty = self.font_ui.render("letters collect here", True, MUTED)
            surf.blit(empty, empty.get_rect(center=(card.centerx, card.centery + 8)))
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
        port = args.port or c.guess_port()
        if not port:
            print(
                "No serial port found.\n"
                "  python host/capture-gui-linear.py --demo"
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
