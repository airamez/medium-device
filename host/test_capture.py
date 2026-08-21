#!/usr/bin/env python3
"""Unit tests for multi-point mapping and 1\" / 0.5\" letter-gap sectors."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import capture as c
import words as w


def pts(*pairs: tuple[str, float]) -> list[dict]:
    return [c.make_point(ch, ang) for ch, ang in pairs]


class DialToCharTests(unittest.TestCase):
    def test_sticker_centers(self):
        for i, ch in enumerate(c.CHARS):
            self.assertEqual(c.dial_to_char(i * 10.0), ch)

    def test_letter_window_and_gap(self):
        self.assertEqual(c.dial_to_char(0.0), "A")
        self.assertEqual(c.dial_to_char(2.9), "A")
        self.assertEqual(c.dial_to_char(3.2), "A")
        self.assertEqual(c.dial_to_char(4.9), " ")
        self.assertEqual(c.dial_to_char(5.0), " ")
        self.assertEqual(c.dial_to_char(7.0), "B")
        self.assertEqual(c.dial_to_char(357.0), "A")

    def test_zero_and_wrap(self):
        self.assertEqual(c.dial_to_char(260.0), "0")
        self.assertEqual(c.dial_to_char(230.0), "X")
        self.assertEqual(c.dial_to_char(220.0), "W")


class IntDegTests(unittest.TestCase):
    def test_drops_decimal_jitter(self):
        self.assertEqual(c.int_deg(23.6), 23)
        self.assertEqual(c.int_deg(23.7), 23)
        self.assertEqual(c.int_deg(23.8), 23)
        self.assertEqual(c.int_deg(359.9), 359)

    def test_still_on_mark_ignores_one_degree_noise(self):
        self.assertTrue(c.still_on_mark(31, 32))
        self.assertTrue(c.still_on_mark(24, 23))
        self.assertTrue(c.still_on_mark(359, 1))
        # Next letter is 10° away — that is a move.
        self.assertFalse(c.still_on_mark(33, 23))
        self.assertFalse(c.still_on_mark(10, 0))

    def test_still_on_angle_uses_decimal_tolerance(self):
        # 10.9 vs 11.0 must count as stopped at the default 2° tolerance.
        self.assertTrue(c.still_on_angle(10.9, 11.0, 2.0))
        self.assertTrue(c.still_on_angle(11.0, 10.9, 2.0))
        self.assertTrue(c.still_on_angle(359.8, 0.3, 2.0))
        self.assertFalse(c.still_on_angle(10.0, 20.0, 2.0))


class SpaceGapTests(unittest.TestCase):
    def test_even_ten_degree_ring(self):
        points = [c.make_point(ch, i * 10.0) for i, ch in enumerate(c.CHARS)]
        self.assertEqual(c.cal_char_or_space(0.0, points), "A")
        self.assertEqual(c.cal_char_or_space(3.0, points), "A")
        self.assertEqual(c.cal_char_or_space(5.0, points), " ")
        self.assertEqual(c.cal_char_or_space(10.0, points), "B")
        self.assertEqual(c.live_char(5.0, points, False), " ")

    def test_speak_words(self):
        self.assertEqual(c.speak_word("A"), "A")
        self.assertEqual(c.speak_word(" "), "space")
        self.assertEqual(c.speak_word("0"), "zero")
        self.assertEqual(c.speak_word("8"), "eight")
        self.assertEqual(c.speak_word("."), "period")
        self.assertEqual(c.speak_word(","), "comma")
        self.assertEqual(c.speak_word("?"), "question mark")


class CardinalConfirmTests(unittest.TestCase):
    def test_j_and_s_same_angle_rejected(self):
        err = c.cardinal_tap_error(31.4, prev_angle=31.5)
        self.assertIsNotNone(err)
        self.assertIn("only", err)

    def test_j_undershoot_vs_saved_rejected(self):
        # Saved A=327, J=63. Session A matches. Tap at 31° is G, not J.
        err = c.cardinal_tap_error(31.5, prev_angle=327.6, expected=63.0)
        self.assertIsNotNone(err)
        self.assertIn("saved mark", err)

    def test_good_j_accepted(self):
        self.assertIsNone(c.cardinal_tap_error(63.0, prev_angle=327.6, expected=63.0))

    def test_expected_follows_a_shift(self):
        saved = pts(("A", 327.35), ("J", 63.017), ("S", 158.333), ("1", 240.667))
        exp_j = c.expected_session_ref(saved, 327.35, "J")
        self.assertAlmostEqual(exp_j, 63.017, places=3)


class TypingStartTests(unittest.TestCase):
    def test_stays_locked_on_parked_letter(self):
        self.assertFalse(c.typing_unlocked("1", 240.0, "1", 241.0))
        self.assertFalse(c.typing_unlocked("1", 240.0, "1", 250.0))

    def test_unlocks_after_real_move_to_new_letter(self):
        self.assertTrue(c.typing_unlocked("1", 240.0, "A", 328.0))

    def test_small_gap_jitter_stays_locked(self):
        self.assertFalse(c.typing_unlocked("1", 240.0, " ", 242.0))
        self.assertFalse(c.typing_unlocked("1", 240.0, " ", 244.0))

    def test_unlocks_once_off_the_letter_and_far_enough(self):
        self.assertTrue(c.typing_unlocked("1", 240.0, " ", 246.0))

    def test_unknown_park_stays_locked(self):
        self.assertFalse(c.typing_unlocked(None, None, "A", 10.0))


class TravelSlotTests(unittest.TestCase):
    def test_equal_steps_for_every_cell(self):
        # 10° per character, starting on slot 0 at travel 0.
        self.assertEqual(c.travel_slot(0.0, 10.0, 0), 0)
        self.assertEqual(c.travel_slot(4.9, 10.0, 0), 0)
        self.assertEqual(c.travel_slot(7.6, 10.0, 0), 1)
        self.assertEqual(c.travel_slot(10.0, 10.0, 1), 1)
        self.assertEqual(c.travel_slot(17.6, 10.0, 1), 2)

    def test_space_and_backspace_same_width_as_letters(self):
        # Slot 5 (e.g. space) and 6 (backspace) use the same 10° as A–E.
        self.assertEqual(c.travel_slot(50.0, 10.0, 5), 5)
        self.assertEqual(c.travel_slot(57.6, 10.0, 5), 6)
        self.assertEqual(c.travel_slot(60.0, 10.0, 6), 6)

    def test_hysteresis_holds_the_current_slot(self):
        # Boundary without hyst is 5°. Stay on 0 until past 7.5° (0.5+0.25).
        self.assertEqual(c.travel_slot(7.4, 10.0, 0), 0)
        self.assertEqual(c.travel_slot(7.6, 10.0, 0), 1)

    def test_fast_spin_steps_one_slot_at_a_time(self):
        # 34° is three cells from 0, but a single update must not skip B and C.
        self.assertEqual(c.travel_slot(34.0, 10.0, 0), 1)
        self.assertEqual(c.travel_slot(34.0, 10.0, 1), 2)
        self.assertEqual(c.travel_slot(34.0, 10.0, 2), 3)
        self.assertEqual(c.travel_slot(34.0, 10.0, 3), 3)

    def test_small_overshoot_does_not_skip_a_letter(self):
        # 15° is 1.5 cells — nearest-slot rounding used to jump 0 → 2.
        self.assertEqual(c.travel_slot(15.0, 10.0, 0), 1)
        self.assertEqual(c.travel_slot(0.0, 10.0, 2), 1)

    def test_slot_offset_frac(self):
        self.assertAlmostEqual(c.slot_offset_frac(0.0, 10.0, 0), 0.5)
        self.assertAlmostEqual(c.slot_offset_frac(5.0, 10.0, 0), 1.0)
        self.assertAlmostEqual(c.slot_offset_frac(-5.0, 10.0, 0), 0.0)
        self.assertAlmostEqual(c.slot_offset_frac(10.0, 10.0, 1), 0.5)
        self.assertAlmostEqual(c.slot_offset_frac(12.0, 10.0, 1), 0.7)
        self.assertEqual(c.slot_offset_frac(80.0, 10.0, 0), 1.0)


class ApplyPunctTests(unittest.TestCase):
    def test_replaces_trailing_space(self):
        chars = list("HELLO ")
        c.apply_punct(chars, ".")
        self.assertEqual("".join(chars), "HELLO. ")

    def test_attaches_when_no_trailing_space(self):
        chars = list("HELLO")
        c.apply_punct(chars, "?")
        self.assertEqual("".join(chars), "HELLO? ")

    def test_commits_current_word_then_mark(self):
        chars = list("HI ")
        c.apply_punct(chars, ",", current_word="THERE")
        self.assertEqual("".join(chars), "HI THERE, ")

    def test_empty_transcript_types_the_mark(self):
        chars: list[str] = []
        self.assertTrue(c.apply_punct(chars, "."))
        self.assertEqual("".join(chars), ". ")

    def test_refuses_stacked_punctuation(self):
        chars = list("HELLO. ")
        self.assertFalse(c.apply_punct(chars, "."))
        self.assertEqual("".join(chars), "HELLO. ")
        self.assertFalse(c.apply_punct(chars, "?"))
        self.assertEqual("".join(chars), "HELLO. ")

    def test_new_word_after_punct_may_take_a_mark(self):
        chars = list("HI. ")
        self.assertTrue(c.apply_punct(chars, ".", current_word="THERE"))
        self.assertEqual("".join(chars), "HI. THERE. ")


class PunctGridTests(unittest.TestCase):
    def test_period_comma_question_sit_after_z(self):
        self.assertEqual(c.PUNCT, ".,?")
        last = c.LETTER_ROWS[-1]
        self.assertEqual(
            last,
            list("UVWXYZ.,?") + [None, " ", c.ACCEPT, c.BS, c.ENTER],
        )

    def test_keys_step_from_z_into_punct(self):
        z = c.KEYS.index("Z")
        self.assertEqual(c.KEYS[z : z + 4], list("Z.,?"))


class LetterHoldTests(unittest.TestCase):
    def test_same_letter_through_angle_jitter_types(self):
        h = c.LetterHold(hold_s=1.0)
        t0 = 1000.0
        self.assertFalse(h.update(t0, "A"))
        self.assertFalse(h.update(t0 + 0.4, "A"))
        self.assertTrue(h.update(t0 + 0.85, "A"))

    def test_letter_change_restarts(self):
        h = c.LetterHold(hold_s=1.0)
        t0 = 1000.0
        h.update(t0, "A")
        self.assertFalse(h.update(t0 + 0.8, "B"))
        self.assertFalse(h.update(t0 + 1.5, "B"))
        self.assertTrue(h.update(t0 + 1.8, "B"))


class RestWindowTests(unittest.TestCase):
    def test_decimal_wobble_is_stopped(self):
        w = c.RestWindow(hold_s=0.5, tol_deg=2.0)
        t0 = 1000.0
        for i, a in enumerate((10.9, 11.0, 10.95, 11.05, 10.92)):
            w.add(t0 + i * 0.12, a)
        self.assertTrue(w.ready(t0 + 0.50))
        self.assertLess(c.circular_range([10.9, 11.0, 10.95]), 0.2)

    def test_sweep_to_next_letter_is_not_stopped(self):
        w = c.RestWindow(hold_s=0.5, tol_deg=2.0)
        t0 = 1000.0
        # 10° in 0.5s — the range is the move, not noise.
        for i in range(6):
            w.add(t0 + i * 0.1, 20.0 + i * 2.0)
        self.assertFalse(w.ready(t0 + 0.50))

    def test_range_across_zero(self):
        self.assertAlmostEqual(c.circular_range([359.8, 0.2]), 0.4, places=5)

    def test_is_still_false_while_sweeping(self):
        w = c.RestWindow(hold_s=1.0, tol_deg=2.0)
        t0 = 1000.0
        for i in range(6):
            w.add(t0 + i * 0.08, 20.0 + i * 1.5)
        self.assertFalse(w.is_still(t0 + 0.40, window_s=0.30))

    def test_is_still_true_after_parking(self):
        w = c.RestWindow(hold_s=1.0, tol_deg=2.0)
        t0 = 1000.0
        for i in range(6):
            w.add(t0 + i * 0.08, 20.0 + i * 2.0)
        for i in range(6):
            w.add(t0 + 0.55 + i * 0.06, 31.0)
        self.assertTrue(w.is_still(t0 + 0.90, window_s=0.30))


class InvertDetectTests(unittest.TestCase):
    def test_clockwise(self):
        points = pts(
            ("A", 10),
            ("G", 70),
            ("J", 100),
            ("N", 140),
            ("S", 190),
            ("W", 230),
            ("X", 240),
            ("0", 270),
        )
        self.assertFalse(c.detect_invert(points))

    def test_counterclockwise(self):
        points = pts(
            ("A", 10),
            ("G", 310),
            ("J", 280),
            ("N", 240),
            ("S", 190),
            ("W", 150),
            ("X", 140),
            ("0", 110),
        )
        self.assertTrue(c.detect_invert(points))


class InterpolationTests(unittest.TestCase):
    def setUp(self):
        # Perfect sensor: raw = dial + 10°.
        self.offset = 10.0
        self.points = pts(
            ("A", 10),
            ("G", 70),
            ("J", 100),
            ("N", 140),
            ("S", 190),
            ("W", 230),
            ("X", 240),
            ("0", 270),
        )

    def test_identity_at_marks(self):
        for p in self.points:
            self.assertAlmostEqual(
                c.raw_to_dial(p["angle"], self.points, invert=False),
                p["dial"],
                places=6,
            )

    def test_mid_segment(self):
        # Halfway A(0) → G(60) in dial is 30° = D.
        self.assertAlmostEqual(c.raw_to_dial(40.0, self.points, False), 30.0, places=6)
        self.assertEqual(c.angle_to_char(40.0, points=self.points), "D")

    def test_midway_is_space(self):
        # Dial 5° is the A–B gap. Raw = dial + 10 = 15°.
        self.assertEqual(c.angle_to_char(15.0, points=self.points), " ")

    def test_wrap_from_zero_to_a(self):
        # 0 is dial 260 / raw 270; A is dial 0 / raw 10.
        # Halfway along that 100° arc is dial 310 = digit 5.
        self.assertAlmostEqual(c.raw_to_dial(320.0, self.points, False), 310.0, places=6)
        self.assertEqual(c.angle_to_char(320.0, points=self.points), "5")

    def test_inverted_marks(self):
        points = pts(
            ("A", 10),
            ("G", 310),
            ("J", 280),
            ("N", 240),
            ("S", 190),
            ("W", 150),
            ("X", 140),
            ("0", 110),
        )
        self.assertTrue(c.detect_invert(points))
        self.assertAlmostEqual(c.raw_to_dial(10.0, points, True), 0.0, places=6)
        self.assertAlmostEqual(c.raw_to_dial(310.0, points, True), 60.0, places=6)
        # 30° ccw from A (raw 10 → 340) is dial 30 = D.
        self.assertAlmostEqual(c.raw_to_dial(340.0, points, True), 30.0, places=6)
        self.assertEqual(c.angle_to_char(340.0, invert=True, points=points), "D")

    def test_uneven_sensor_stretch(self):
        # A→G is 80° of raw for 60° of dial (local scale error).
        points = pts(
            ("A", 0),
            ("G", 80),
            ("J", 110),
            ("N", 150),
            ("S", 200),
            ("W", 240),
            ("X", 250),
            ("0", 280),
        )
        # 40° raw is halfway A→G → dial 30 = D.
        self.assertAlmostEqual(c.raw_to_dial(40.0, points, False), 30.0, places=6)
        self.assertEqual(c.angle_to_char(40.0, points=points), "D")
        # G itself still maps to G, not shifted by the stretch.
        self.assertEqual(c.angle_to_char(80.0, points=points), "G")


class PointsConsistentTests(unittest.TestCase):
    def test_good_calibration(self):
        points = pts(
            ("A", 10),
            ("G", 70),
            ("J", 100),
            ("N", 140),
            ("S", 190),
            ("W", 230),
            ("X", 240),
            ("0", 270),
        )
        self.assertTrue(c.points_consistent(points, False))

    def test_small_sensor_error_ok(self):
        points = pts(
            ("A", 10),
            ("G", 75),
            ("J", 102),
            ("N", 138),
            ("S", 192),
            ("W", 228),
            ("X", 241),
            ("0", 268),
        )
        self.assertTrue(c.points_consistent(points, False))

    def test_bad_calibration_rejected(self):
        # Real-world bad capture: N, S clumped near J and W/X/0 clumped near 178°.
        points = pts(
            ("A", 276.0),
            ("G", 353.952),
            ("J", 21.909),
            ("N", 31.245),
            ("S", 50.195),
            ("W", 178.939),
            ("X", 177.835),
            ("0", 179.555),
        )
        self.assertFalse(c.points_consistent(points, False))

    def test_uneven_wrap_from_real_session_is_ok(self):
        # Full 36-point run: most steps ~7–12°, leftover slack lands on 9→A (18°).
        # That used to make --letters throw the whole calibration away.
        points = pts(
            ("A", 202.9),
            ("B", 212.2),
            ("C", 219.1),
            ("D", 227.9),
            ("E", 237.4),
            ("F", 245.0),
            ("G", 254.2),
            ("H", 263.7),
            ("I", 273.7),
            ("J", 284.2),
            ("K", 291.2),
            ("L", 302.3),
            ("M", 311.7),
            ("N", 322.6),
            ("O", 331.9),
            ("P", 342.8),
            ("Q", 353.4),
            ("R", 3.4),
            ("S", 13.4),
            ("T", 22.8),
            ("U", 33.0),
            ("V", 43.7),
            ("W", 53.9),
            ("X", 64.2),
            ("Y", 74.1),
            ("Z", 83.3),
            ("0", 96.2),
            ("1", 107.1),
            ("2", 116.7),
            ("3", 127.9),
            ("4", 138.4),
            ("5", 147.6),
            ("6", 157.2),
            ("7", 165.2),
            ("8", 176.6),
            ("9", 185.0),
        )
        self.assertTrue(c.points_consistent(points, False))
        self.assertEqual(c.angle_to_char(202.9, points=points), "A")
        self.assertEqual(c.angle_to_char(219.1, points=points), "C")
        self.assertEqual(c.angle_to_char(185.0, points=points), "9")
        # Each saved center must decode as itself — no session A-shift.
        for p in points:
            self.assertEqual(
                c.angle_to_char(p["angle"], points=points),
                p["char"],
                msg=f"{p['char']} at {p['angle']}",
            )


class DelayConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_path = c.CONFIG_PATH
        c.CONFIG_PATH = Path(self.tmp.name) / "config.json"

    def tearDown(self):
        c.CONFIG_PATH = self.orig_path
        self.tmp.cleanup()

    def test_default_when_missing(self):
        self.assertEqual(c.load_config()["delay_s"], c.DEFAULT_DELAY_S)
        self.assertEqual(c.load_config()["wrap_cols"], c.DEFAULT_WRAP_COLS)
        self.assertEqual(c.load_config()["still_tol_deg"], c.DEFAULT_STILL_TOL_DEG)
        self.assertEqual(c.load_config()["lang"], w.DEFAULT_LANG)

    def test_save_and_load_lang(self):
        c.save_config(invert=False, lang="pt-BR")
        self.assertEqual(c.load_config()["lang"], "pt-BR")
        c.save_config(invert=False)
        self.assertEqual(c.load_config()["lang"], "pt-BR")

    def test_save_and_load_wrap(self):
        points = pts(("A", 10), ("J", 100))
        c.save_config(points, invert=False, wrap_cols=40)
        self.assertEqual(c.load_config()["wrap_cols"], 40)

    def test_save_and_load_delay(self):
        points = pts(("A", 10), ("J", 100))
        c.save_config(points, invert=False, delay_s=2.5)
        cfg = c.load_config()
        self.assertEqual(cfg["delay_s"], 2.5)
        self.assertEqual([p["char"] for p in cfg["points"]], ["A", "J"])

    def test_save_preserves_existing_delay(self):
        points = pts(("A", 10), ("J", 100))
        c.save_config(points, invert=False, delay_s=2.5)
        c.save_config(points, invert=False)
        self.assertEqual(c.load_config()["delay_s"], 2.5)

    def test_invalid_delay_falls_back(self):
        c.CONFIG_PATH.write_text('{"delay_s": 0, "points": []}')
        self.assertEqual(c.load_config()["delay_s"], c.DEFAULT_DELAY_S)

    def test_load_keeps_all_letters(self):
        points = [c.make_point(ch, i * 10.0) for i, ch in enumerate(c.CHARS)]
        c.save_config(points, invert=False)
        loaded = c.load_config()["points"]
        self.assertEqual([p["char"] for p in loaded], list(c.CHARS))
        self.assertTrue(c.is_full_cal(loaded))
        self.assertFalse(c.is_full_cal(pts(("A", 0), ("J", 90))))

    def test_backup_none_when_missing(self):
        self.assertIsNone(c.backup_config())

    def test_backup_and_restore_latest(self):
        first = pts(("A", 10.0), ("J", 100.0))
        c.save_config(first, invert=False)
        bak = c.backup_config()
        self.assertIsNotNone(bak)
        self.assertTrue(bak.exists())
        self.assertIn("config_", bak.name)
        second = pts(("A", 20.0), ("J", 110.0))
        c.save_config(second, invert=True)
        src = c.restore_config("latest")
        self.assertEqual(src, bak)
        cfg = c.load_config()
        self.assertAlmostEqual(cfg["points"][0]["angle"], 10.0)
        self.assertFalse(cfg["invert"])


class MonotonicCalTests(unittest.TestCase):
    """config.json after the 36-point run: 4–9 landed on A–O."""

    def setUp(self):
        self.saved = pts(
            ("A", 354.592),
            ("B", 359.608),
            ("C", 6.15),
            ("D", 11.975),
            ("E", 18.483),
            ("F", 22.592),
            ("G", 28.35),
            ("H", 35.575),
            ("I", 45.05),
            ("J", 54.775),
            ("K", 67.6),
            ("L", 82.983),
            ("M", 99.108),
            ("N", 113.583),
            ("O", 124.75),
            ("P", 134.25),
            ("Q", 143.3),
            ("R", 152.358),
            ("S", 160.95),
            ("T", 170.608),
            ("U", 180.4),
            ("V", 192.692),
            ("W", 202.725),
            ("X", 212.408),
            ("Y", 220.575),
            ("Z", 229.175),
            ("0", 237.1),
            ("1", 246.583),
            ("2", 252.917),
            ("3", 255.758),
            ("4", 123.733),
            ("5", 5.675),
            ("6", 13.933),
            ("7", 11.775),
            ("8", 5.908),
            ("9", 358.958),
        )

    def test_drops_wrapped_digits(self):
        kept, dropped = c.monotonic_cal_points(self.saved, invert=False)
        self.assertEqual(dropped, list("456789"))
        self.assertEqual(kept[0]["char"], "A")
        self.assertEqual(kept[-1]["char"], "3")

    def test_session_a_no_longer_reads_as_9(self):
        kept, _dropped = c.monotonic_cal_points(self.saved, invert=False)
        session = pts(("A", 4.25), ("J", 50.74), ("S", 161.50), ("1", 247.33))
        aligned = c.align_cal_to_session(kept, session, invert=False)
        # Session_2026_08_16_21_14: sat on A at 6° and typed 9 because
        # saved 9 sat between A and B.
        self.assertEqual(c.nearest_cal_char(6.0, aligned), "A")
        self.assertEqual(c.nearest_cal_char(8.0, aligned), "B")
        self.assertEqual(c.nearest_cal_char(13.0, aligned), "C")

    def test_raw_map_without_filter_is_the_bug(self):
        session = pts(("A", 4.25), ("J", 50.74), ("S", 161.50), ("1", 247.33))
        aligned = c.align_cal_to_session(self.saved, session, invert=False)
        self.assertEqual(c.nearest_cal_char(6.0, aligned), "9")

    def test_wrong_way_tap_rejected(self):
        # 4 at 123° is backward from 3 at 256°.
        kept3 = []
        for p in self.saved:
            if p["char"] == "4":
                break
            kept3.append(p)
        err = c.cal_mark_error(kept3, 123.733, invert=False)
        self.assertIsNotNone(err)
        self.assertIn("not past", err)

    def test_tap_back_on_c_while_doing_4_rejected(self):
        prefix = []
        for p in self.saved:
            if p["char"] == "4":
                break
            prefix.append(p)
        err = c.cal_mark_error(prefix, 6.15, invert=False)
        self.assertIsNotNone(err)
        self.assertIn("not past", err)

    def test_a_outlier_warning(self):
        session = pts(("A", 4.25), ("J", 50.74), ("S", 161.50), ("1", 247.33))
        kept, _ = c.monotonic_cal_points(self.saved, False)
        warn = c.session_cardinal_warning(kept, session)
        self.assertIsNotNone(warn)
        self.assertIn("printed A", warn)


class CalibrateRewindTests(unittest.TestCase):
    def test_u_too_close_redoes_s_t_u(self):
        # U is index 20; 20 saved marks (A–T).
        self.assertEqual(c.calibrate_rewind(20, 20), 18)
        self.assertEqual(c.CHARS[18:21], "STU")

    def test_b_too_close_redoes_a_b(self):
        self.assertEqual(c.calibrate_rewind(1, 1), 0)

    def test_a_has_nothing_to_drop(self):
        self.assertEqual(c.calibrate_rewind(0, 0), 0)

    def test_step_label_hides_wraparound(self):
        # 186.9 → 180.8 is 6.1° the other way, not +353.9°.
        self.assertIn("other way", c.format_cal_step(186.9, 180.8, invert=False))
        self.assertEqual(c.format_cal_step(160.0, 170.0, invert=False), "  (+10.0°)")

    def test_first_close_rewinds_then_accepts(self):
        already: set[int] = set()
        action, redo = c.calibrate_close_action(20, 20, already)
        self.assertEqual(action, "rewind")
        self.assertEqual(redo, 18)
        self.assertEqual(already, {18, 19, 20})
        # S (18) close to R must not walk back to Q.
        action, redo = c.calibrate_close_action(18, 18, already)
        self.assertEqual(action, "accept")
        # U still close after the redo: save it.
        action, _ = c.calibrate_close_action(20, 20, already)
        self.assertEqual(action, "accept")

    def test_later_letter_can_still_rewind_once(self):
        already = {18, 19, 20}
        action, redo = c.calibrate_close_action(21, 21, already)
        self.assertEqual(action, "rewind")
        self.assertEqual(redo, 19)
        self.assertIn(21, already)


class RecalibrateShiftTests(unittest.TestCase):
    def test_shift_applied_to_all_points(self):
        points = pts(("A", 10), ("B", 20), ("C", 30))
        shifted, shift = c.shift_points_to_new_a(points, 25.0)
        self.assertAlmostEqual(shift, 15.0)
        self.assertAlmostEqual(shifted[0]["angle"], 25.0)
        self.assertAlmostEqual(shifted[1]["angle"], 35.0)
        self.assertAlmostEqual(shifted[2]["angle"], 45.0)

    def test_wrap_after_shift(self):
        points = pts(("A", 340), ("B", 350), ("C", 0))
        shifted, shift = c.shift_points_to_new_a(points, 10.0)
        self.assertAlmostEqual(shift, 30.0)
        self.assertAlmostEqual(shifted[0]["angle"], 10.0)
        self.assertAlmostEqual(shifted[1]["angle"], 20.0)
        self.assertAlmostEqual(shifted[2]["angle"], 30.0)

    def test_negative_shift_shortest_way(self):
        points = pts(("A", 10), ("B", 20), ("C", 30))
        shifted, shift = c.shift_points_to_new_a(points, 5.0)
        self.assertAlmostEqual(shift, -5.0)
        self.assertAlmostEqual(shifted[0]["angle"], 5.0)
        self.assertAlmostEqual(shifted[1]["angle"], 15.0)

    def test_moved_base_reads_correct_letters(self):
        # Full-ish ring at original pose. Then the whole base rotates +40°.
        saved = pts(("A", 166.3), ("G", 223.3), ("N", 290.9), ("0", 61.2))
        rotated_raw = {
            "A": (166.3 + 40.0) % 360.0,
            "G": (223.3 + 40.0) % 360.0,
            "N": (290.9 + 40.0) % 360.0,
            "0": (61.2 + 40.0) % 360.0,
        }
        # Without the A baseline, the old map reads the new A as the wrong letter.
        self.assertNotEqual(c.angle_to_char(rotated_raw["A"], points=saved), "A")

        shifted, _shift = c.shift_points_to_new_a(saved, rotated_raw["A"])
        self.assertEqual(c.angle_to_char(rotated_raw["A"], points=shifted), "A")
        self.assertEqual(c.angle_to_char(rotated_raw["G"], points=shifted), "G")
        self.assertEqual(c.angle_to_char(rotated_raw["N"], points=shifted), "N")
        self.assertEqual(c.angle_to_char(rotated_raw["0"], points=shifted), "0")

    def test_offset_angle_identity_when_a_unchanged(self):
        self.assertAlmostEqual(c.offset_angle(212.2, 202.9, 202.9), 212.2, places=6)

    def test_offset_angle_rotates_readings_into_saved_frame(self):
        # Base rotated +10°. Live A is 212.9; saved A is 202.9.
        # Live B (222.2) must map back to saved B (212.2).
        self.assertAlmostEqual(c.offset_angle(212.9, 212.9, 202.9), 202.9, places=6)
        self.assertAlmostEqual(c.offset_angle(222.2, 212.9, 202.9), 212.2, places=6)

    def test_relative_gaps_preserved(self):
        points = pts(("A", 166.318), ("B", 176.77), ("C", 186.048))
        shifted, _shift = c.shift_points_to_new_a(points, 10.0)
        old_ab = c.circular_delta(points[0]["angle"], points[1]["angle"])
        new_ab = c.circular_delta(shifted[0]["angle"], shifted[1]["angle"])
        old_bc = c.circular_delta(points[1]["angle"], points[2]["angle"])
        new_bc = c.circular_delta(shifted[1]["angle"], shifted[2]["angle"])
        self.assertAlmostEqual(old_ab, new_ab, places=6)
        self.assertAlmostEqual(old_bc, new_bc, places=6)


class FitOffsetTests(unittest.TestCase):
    def test_printed_dial_letter_is_not_space(self):
        # Perfect 10° paper. Hand marks are a bit sloppy (the real 8-point cal).
        messy = [
            c.make_point("A", 70.0),
            c.make_point("E", 112.0),   # should be 110
            c.make_point("J", 159.0),   # should be 160
            c.make_point("N", 201.0),
            c.make_point("S", 250.0),
            c.make_point("W", 292.0),
            c.make_point("1", 338.0),
            c.make_point("5", 22.0),
        ]
        invert = c.detect_invert(messy)
        offset = c.fit_offset(messy, invert)
        # Physical Y is 70+240=310. Piecewise used to land this in a space.
        self.assertEqual(c.angle_to_char(310.0, offset=offset, invert=invert), "Y")
        self.assertEqual(c.angle_to_char(70.0, offset=offset, invert=invert), "A")
        self.assertEqual(c.angle_to_char(80.0, offset=offset, invert=invert), "B")
        self.assertEqual(c.angle_to_char(75.0, offset=offset, invert=invert), " ")

    def test_printed_ten_degree_grid_from_a_only(self):
        # Perfect paper: A at 70.3°, then every 10° clockwise.
        a = 70.3
        for i, ch in enumerate(c.CHARS):
            self.assertEqual(
                c.match_char(a + i * 10.0, a, invert=False),
                ch,
                msg=f"center of {ch}",
            )

    def test_clockwise_move_detects_chip_direction(self):
        self.assertFalse(c.invert_from_clockwise_move(70.0, 80.0))
        self.assertTrue(c.invert_from_clockwise_move(70.0, 60.0))
        # Inverted chip: B is A minus 10°.
        a = 70.3
        for i, ch in enumerate(c.CHARS):
            self.assertEqual(
                c.match_char(a - i * 10.0, a, invert=True),
                ch,
                msg=f"invert {ch}",
            )

    def test_six_degree_letter_two_degree_shoulders(self):
        self.assertEqual(c.angle_to_char(70.0, offset=70.0), "A")
        self.assertEqual(c.angle_to_char(73.0, offset=70.0), "A")
        self.assertEqual(c.angle_to_char(75.0, offset=70.0), " ")
        self.assertEqual(c.angle_to_char(77.0, offset=70.0), "B")


class CompassAutoTests(unittest.TestCase):
    def setUp(self):
        # Perfect sensor: raw angle = dial angle.
        self.points = [c.make_point(ch, c.char_dial(ch)) for ch in c.CAL_REFS]

    def test_four_refs(self):
        self.assertEqual([p["char"] for p in self.points], list("AJS1"))

    def test_x_is_x_not_v(self):
        # X is 230°. The old even-10° auto drifted so X read as V (210°).
        self.assertEqual(c.angle_to_char(230.0, points=self.points), "X")
        self.assertEqual(c.angle_to_char(210.0, points=self.points), "V")

    def test_cardinal_letters(self):
        self.assertEqual(c.angle_to_char(0.0, points=self.points), "A")
        self.assertEqual(c.angle_to_char(90.0, points=self.points), "J")
        self.assertEqual(c.angle_to_char(180.0, points=self.points), "S")
        self.assertEqual(c.angle_to_char(270.0, points=self.points), "1")

    def test_interpolated_between_refs(self):
        # B is 10°, between A (0°) and E (40°).
        self.assertEqual(c.angle_to_char(10.0, points=self.points), "B")
        self.assertEqual(c.angle_to_char(5.0, points=self.points), " ")


class AllowEmitTests(unittest.TestCase):
    def test_letters_always_ok(self):
        self.assertTrue(c.allow_emit("A", []))
        self.assertTrue(c.allow_emit("B", ["A"]))
        self.assertTrue(c.allow_emit("C", ["A", " ", "B"]))

    def test_blocks_consecutive_spaces(self):
        self.assertTrue(c.allow_emit(" ", []))
        self.assertTrue(c.allow_emit(" ", ["A"]))
        self.assertFalse(c.allow_emit(" ", ["A", " "]))
        self.assertTrue(c.allow_emit("B", ["A", " "]))

    def test_backspace_always_ok(self):
        self.assertTrue(c.allow_emit("\b", []))
        self.assertTrue(c.allow_emit("\b", ["A"]))
        self.assertTrue(c.allow_emit("\b", ["A", "\b"]))


class DialXyTests(unittest.TestCase):
    def test_a_is_north(self):
        x, y = c.dial_xy(0.0, 100.0, 100.0, 50.0)
        self.assertAlmostEqual(x, 100.0)
        self.assertAlmostEqual(y, 50.0)

    def test_j_is_east(self):
        x, y = c.dial_xy(90.0, 100.0, 100.0, 50.0)
        self.assertAlmostEqual(x, 150.0)
        self.assertAlmostEqual(y, 100.0)

    def test_s_is_south(self):
        x, y = c.dial_xy(180.0, 100.0, 100.0, 50.0)
        self.assertAlmostEqual(x, 100.0)
        self.assertAlmostEqual(y, 150.0)


class HysteresisTests(unittest.TestCase):
    def test_stays_on_letter_through_gap_edge(self):
        # Letter window is ±3.25°. 3.6° is a space without hysteresis,
        # but still inside the 1° hold band of A.
        self.assertEqual(c.dial_to_char(3.6), " ")
        self.assertEqual(c.hysteretic_dial_char(3.6, "A"), "A")

    def test_leaves_letter_once_clearly_in_the_gap(self):
        self.assertEqual(c.hysteretic_dial_char(5.0, "A"), " ")

    def test_space_does_not_enter_letter_at_the_edge(self):
        self.assertEqual(c.dial_to_char(3.0), "A")
        self.assertEqual(c.hysteretic_dial_char(3.0, " "), " ")

    def test_space_enters_letter_when_inside(self):
        self.assertEqual(c.hysteretic_dial_char(1.5, " "), "A")

    def test_jump_to_next_letter(self):
        self.assertEqual(c.hysteretic_dial_char(10.0, "A"), "B")


class CompressedCalGapTests(unittest.TestCase):
    def test_tight_pair_still_reads_as_the_letter(self):
        # Session 2026-08-19: E and F were 5.56° apart after alignment.
        # A fixed 3.5° gap left only ~1° of letter and typed space at 1.5°.
        points = pts(("E", 0.0), ("F", 5.56), ("G", 15.56))
        self.assertEqual(c.cal_char_or_space(1.54, points), "E")
        self.assertEqual(c.hysteretic_cal_char(1.54, points, "E"), "E")


class LetterHoldSpaceTests(unittest.TestCase):
    def test_space_needs_a_longer_hold(self):
        h = c.LetterHold(hold_s=1.0)
        t0 = 1000.0
        self.assertFalse(h.update(t0, " "))
        self.assertFalse(h.update(t0 + 0.85, " "))
        self.assertTrue(h.update(t0 + 1.40, " "))

class WrapLineTests(unittest.TestCase):
    def test_wraps_after_limit(self):
        self.assertTrue(c.should_wrap_line("A" * 60, "B", 60))
        self.assertFalse(c.should_wrap_line("A" * 59, "B", 60))


class OffsetFallbackTests(unittest.TestCase):
    def test_offset_treats_a_as_center(self):
        # No multi-point list: offset is the A sticker center.
        self.assertEqual(c.angle_to_char(100.0, offset=100.0), "A")
        self.assertEqual(c.angle_to_char(103.0, offset=100.0), "A")
        self.assertEqual(c.angle_to_char(105.0, offset=100.0), " ")
        self.assertEqual(c.angle_to_char(110.0, offset=100.0), "B")


class LiveMapperFromDiagnosticTests(unittest.TestCase):
    """Diagnostic_2026_08_16_19_22: piecewise 0/3, A-only 2/3.

    J was marked at 210° (about F, not east). Stretching that 48° arc to 90°
    is why A/D/F typed as B/F/H.
    """

    def setUp(self):
        self.a = 162.617
        self.points = pts(("A", self.a), ("J", 210.483), ("S", 355.117), ("1", 52.533))

    def test_a_only_matches_what_they_pointed_at(self):
        # 166.1 is 3.5° past A — that is the 3.5° gap now, not A.
        self.assertEqual(c.match_char(166.133, self.a, False), " ")
        self.assertEqual(c.match_char(self.a, self.a, False), "A")
        self.assertEqual(c.match_char(201.775, self.a, False), "E")
        self.assertEqual(c.match_char(212.617, self.a, False), "F")

    def test_piecewise_is_what_made_every_letter_run_ahead(self):
        # Same session: interpolation shoved mid-arc samples into the next letter
        # or, with gaps, into space. The J-as-F mark is the root warp.
        self.assertIn(
            c.angle_to_char(166.133, invert=False, points=self.points),
            (" ", "B"),
        )
        self.assertIn(
            c.angle_to_char(188.800, invert=False, points=self.points),
            (" ", "F"),
        )

    def test_j_mark_was_around_f_not_east(self):
        # 210 − 163 ≈ 47°. F is 50° from A. J should be 90°.
        self.assertEqual(c.match_char(210.483, self.a, False), "F")
        self.assertFalse(c.invert_from_clockwise_move(self.a, 210.483))


class FullCalAlignTests(unittest.TestCase):
    def setUp(self):
        # Uneven mechanical ring: B sits 6° from A, not 10°.
        self.saved = []
        ang = 0.0
        steps = [6 if ch == "B" else 10 for ch in c.CHARS]
        # Rebuild from A=0 with a short A→B and the leftover in 1→A.
        # Simpler: start at 0 and walk, last gap absorbs.
        self.saved = [c.make_point("A", 0.0)]
        running = 0.0
        for ch in c.CHARS[1:]:
            running += 6.0 if ch == "B" else 10.0
            self.saved.append(c.make_point(ch, running % 360.0))
        # A=0, B=6, C=16, ... J=86, ...
        self.invert = False

    def test_identity_when_base_unmoved(self):
        session = c.ordered_refs(self.saved)
        aligned = c.align_cal_to_session(self.saved, session, self.invert)
        for p, q in zip(self.saved, aligned):
            self.assertAlmostEqual(p["angle"], q["angle"], places=5)
            self.assertEqual(c.nearest_cal_char(p["angle"], aligned), p["char"])

    def test_rotated_base_keeps_every_letter(self):
        shift = 40.0
        session = [
            c.make_point(p["char"], (p["angle"] + shift) % 360.0)
            for p in c.ordered_refs(self.saved)
        ]
        aligned = c.align_cal_to_session(self.saved, session, self.invert)
        for p in self.saved:
            live = (p["angle"] + shift) % 360.0
            self.assertEqual(
                c.nearest_cal_char(live, aligned),
                p["char"],
                msg=f"{p['char']} at {live}",
            )
            self.assertEqual(c.live_char(live, aligned, self.invert), p["char"])

    def test_short_ab_gap_stays_b(self):
        # B is only 6° from A in the saved map. After a +15° base move,
        # sitting 6° past the new A must still be B, not a 10° grid's C.
        session = [
            c.make_point(p["char"], (p["angle"] + 15.0) % 360.0)
            for p in c.ordered_refs(self.saved)
        ]
        aligned = c.align_cal_to_session(self.saved, session, self.invert)
        self.assertEqual(c.nearest_cal_char(15.0 + 6.0, aligned), "B")
        self.assertEqual(c.nearest_cal_char(15.0 + 16.0, aligned), "C")

    def test_quadrant_stretch_uses_session_js(self):
        # Session J is 20° further than a pure A-shift. Letters in A–J stretch.
        session = c.ordered_refs(self.saved)
        session = [
            c.make_point(p["char"], p["angle"] + (20.0 if p["char"] == "J" else 0.0))
            for p in session
        ]
        aligned = c.align_cal_to_session(self.saved, session, False)
        j = next(p for p in aligned if p["char"] == "J")
        self.assertAlmostEqual(j["angle"], 20.0 + next(
            p["angle"] for p in self.saved if p["char"] == "J"
        ), places=5)
        # Mid A–J should still decode as a letter in A–J, not snap to J.
        mid = c.map_through_refs(
            next(p["angle"] for p in self.saved if p["char"] == "E"),
            c.ordered_refs(self.saved),
            session,
            False,
        )
        self.assertEqual(c.nearest_cal_char(mid, aligned), "E")

    def test_inverted_ring(self):
        saved = [c.make_point(ch, (360.0 - i * 10.0) % 360.0) for i, ch in enumerate(c.CHARS)]
        self.assertTrue(c.detect_invert(saved))
        session = c.ordered_refs(saved)
        aligned = c.align_cal_to_session(saved, session, invert=True)
        for p in saved:
            self.assertEqual(c.nearest_cal_char(p["angle"], aligned), p["char"])


class DiagnosticSampleTests(unittest.TestCase):
    def test_parse_true_char(self):
        self.assertEqual(c.parse_true_char("o"), "O")
        self.assertEqual(c.parse_true_char("  7 "), "7")
        self.assertIsNone(c.parse_true_char(""))
        self.assertIsNone(c.parse_true_char("SP"))
        self.assertIsNone(c.parse_true_char("hello"))

    def test_perfect_grid_zero_error(self):
        points = [c.make_point(ch, c.char_dial(ch)) for ch in c.CAL_REFS]
        rec = c.diagnostic_sample(140.0, "O", points, invert=False)
        self.assertEqual(rec["true"], "O")
        self.assertEqual(rec["piecewise"]["pred"], "O")
        self.assertAlmostEqual(rec["piecewise"]["err_deg"], 0.0, places=3)
        self.assertEqual(rec["a_only"]["pred"], "O")
        self.assertEqual(rec["segment"]["from"], "J")
        self.assertEqual(rec["segment"]["to"], "S")
        self.assertAlmostEqual(rec["segment"]["t"], (140.0 - 90.0) / 90.0, places=5)

    def test_real_session_o_reads_as_p(self):
        # Session_2026_08_16_19_06: pointing near O logged as P at raw 119.
        # J was ~10° early vs A, so piecewise shoves O onto P; A-only stays on O.
        points = pts(("A", 341.19), ("J", 61.35), ("S", 152.43), ("1", 207.48))
        rec = c.diagnostic_sample(119.0, "O", points, invert=False)
        self.assertEqual(rec["piecewise"]["pred"], "P")
        self.assertEqual(rec["a_only"]["pred"], "O")
        self.assertGreater(rec["piecewise"]["err_deg"], 5.0)
        self.assertLess(abs(rec["a_only"]["err_deg"]), 5.0)
        self.assertEqual(rec["segment"]["from"], "J")
        self.assertEqual(rec["segment"]["to"], "S")

    def test_summary_picks_lowest_error(self):
        points = [c.make_point(ch, c.char_dial(ch)) for ch in c.CAL_REFS]
        recs = [
            c.diagnostic_sample(0.0, "A", points, False),
            c.diagnostic_sample(140.0, "O", points, False),
        ]
        text = c.format_diagnostic_summary(recs)
        self.assertIn("Samples: 2", text)
        self.assertIn("correct 2/2", text)
        self.assertIn("Lowest |error|:", text)
        header = c.format_diagnostic_header(points, False)
        self.assertIn("A=0.00", header)
        block = c.format_diagnostic_block(1, recs[1], datetime.now())
        self.assertIn("true=O", block)
        self.assertIn('"raw": 140.0', block)

    def test_cal_spans_flag_warped_quadrant(self):
        points = pts(("A", 341.19), ("J", 61.35), ("S", 152.43), ("1", 207.48))
        spans = {f"{s['from']}→{s['to']}": s["raw_span"] for s in c.cal_spans(points, False)}
        self.assertAlmostEqual(spans["S→1"], 55.05, places=2)
        self.assertAlmostEqual(spans["1→A"], 133.71, places=2)
        header = c.format_diagnostic_header(points, False)
        self.assertIn("WARPED", header)


class WordIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.idx = w.load_index()

    def test_loaded_about_40k(self):
        self.assertGreaterEqual(len(self.idx), 39_000)
        self.assertLessEqual(len(self.idx), 41_000)

    def test_prefix_picks_most_frequent(self):
        self.assertEqual(self.idx.complete("th"), "the")
        self.assertEqual(self.idx.complete("the"), "the")
        self.assertEqual(self.idx.complete("hel"), "help")
        self.assertEqual(self.idx.closest("HEL"), "help")

    def test_tiny_trie_and_draft(self):
        idx = w.WordIndex()
        for word in ("the", "that", "hello", "help", "cat"):
            idx.insert(word)
        self.assertEqual(idx.complete("th"), "the")
        self.assertEqual(idx.complete("hel"), "hello")
        self.assertEqual(idx.complete("c"), "cat")
        draft = w.WordDraft(idx)
        draft.add("H")
        draft.add("E")
        draft.add("L")
        self.assertEqual(draft.typed, "HEL")
        self.assertEqual(draft.suggestion, "HELLO")
        self.assertEqual(draft.ghost, "LO")
        self.assertTrue(draft.is_prefix)
        self.assertEqual(draft.take_suggestion(), "HELLO")
        self.assertEqual(draft.typed, "")

    def test_space_keeps_typed_letters(self):
        idx = w.WordIndex()
        idx.insert("help")
        idx.insert("hello")
        draft = w.WordDraft(idx)
        for ch in "HEL":
            draft.add(ch)
        self.assertEqual(draft.suggestion, "HELP")
        self.assertEqual(draft.take_typed(), "HEL")
        self.assertEqual(draft.typed, "")

    def test_fuzzy_when_prefix_missing(self):
        idx = w.WordIndex()
        for word in ("hello", "help", "world"):
            idx.insert(word)
        self.assertIsNone(idx.complete("helo"))
        self.assertEqual(idx.closest("helo"), "hello")

    def test_take_last_word(self):
        chars = list("HELLO WORLD ")
        self.assertEqual(w.take_last_word(chars), "WORLD")
        self.assertEqual("".join(chars), "HELLO ")
        self.assertEqual(w.take_last_word(chars), "HELLO")
        self.assertEqual("".join(chars), "")
        self.assertIsNone(w.take_last_word(chars))

    def test_backspace_restores_word_into_draft(self):
        chars = list("HELLO WORLD ")
        word = w.take_last_word(chars)
        draft = w.WordDraft(w.WordIndex())
        for ch in word:
            draft.add(ch)
        self.assertEqual(draft.typed, "WORLD")
        self.assertTrue(draft.backspace())
        self.assertEqual(draft.typed, "WORL")

    def test_delete_current_word_peels_draft(self):
        draft = w.WordDraft(w.WordIndex())
        for ch in "HEL":
            draft.add(ch)
        committed: list[str] = []
        self.assertTrue(w.delete_current_word(draft, committed))
        self.assertEqual(draft.typed, "HE")
        self.assertEqual(draft.suggestion, "HE")
        self.assertEqual(committed, [])

    def test_delete_current_word_pulls_then_peels(self):
        draft = w.WordDraft(w.WordIndex())
        committed = list("HELP ")
        self.assertTrue(w.delete_current_word(draft, committed))
        self.assertEqual(draft.typed, "HEL")
        self.assertEqual(draft.suggestion, "HEL")
        self.assertEqual("".join(committed), "")
        empty = w.WordDraft(w.WordIndex())
        self.assertFalse(w.delete_current_word(empty, []))

    def test_backspace_drops_ghost_then_letters(self):
        draft = w.WordDraft(self.idx)
        for ch in "HEL":
            draft.add(ch)
        self.assertEqual(draft.suggestion, "HELP")
        self.assertEqual(draft.ghost, "P")
        self.assertTrue(w.delete_current_word(draft, []))
        self.assertEqual(draft.typed, "HEL")
        self.assertEqual(draft.suggestion, "HEL")
        self.assertEqual(draft.ghost, "")
        self.assertTrue(w.delete_current_word(draft, []))
        self.assertEqual(draft.typed, "HE")
        self.assertEqual(draft.suggestion, "HE")

    def test_backspace_on_the_does_not_stay_the(self):
        draft = w.WordDraft(self.idx)
        for ch in "THE":
            draft.add(ch)
        self.assertEqual(draft.suggestion, "THE")
        self.assertTrue(w.delete_current_word(draft, []))
        self.assertEqual(draft.typed, "TH")
        self.assertEqual(draft.suggestion, "TH")
        self.assertNotEqual(draft.suggestion, "THE")

    def test_draft_ignores_control_tokens(self):
        draft = w.WordDraft(w.WordIndex())
        draft.add("\b")
        draft.add("\t")
        draft.add("⌫")
        draft.add("✓")
        draft.add(" ")
        self.assertEqual(draft.typed, "")
        draft.add("A")
        self.assertEqual(draft.typed, "A")

    def test_draft_ignores_punct(self):
        draft = w.WordDraft(w.WordIndex())
        draft.add("H")
        draft.add(".")
        draft.add(",")
        draft.add("?")
        self.assertEqual(draft.typed, "H")

    def test_digits_are_not_completed(self):
        self.assertIsNone(self.idx.closest("2"))
        self.assertIsNone(self.idx.closest("A1"))

    def test_fold_letters_strips_accents(self):
        self.assertEqual(w.fold_letters("café"), "cafe")
        self.assertEqual(w.fold_letters("não"), "nao")
        self.assertEqual(w.fold_letters("niño"), "nino")
        self.assertEqual(w.fold_letters("l'amour"), "lamour")
        self.assertEqual(w.fold_letters("œuvre"), "oeuvre")

    def test_insert_keeps_accents_and_skips_folded_duplicates(self):
        idx = w.WordIndex()
        idx.insert("café")
        idx.insert("cafe")
        self.assertEqual(len(idx), 1)
        self.assertEqual(idx.complete("caf"), "café")
        self.assertEqual(idx.complete("cafe"), "café")

    def test_draft_shows_accented_prefix_and_ghost(self):
        idx = w.WordIndex()
        idx.insert("não")
        draft = w.WordDraft(idx)
        draft.add("N")
        draft.add("A")
        self.assertEqual(draft.suggestion, "NÃO")
        self.assertEqual(draft.shown_typed, "NÃ")
        self.assertEqual(draft.ghost, "O")
        draft.add("O")
        self.assertEqual(draft.typed, "NAO")
        self.assertEqual(draft.shown_typed, "NÃO")
        self.assertEqual(draft.ghost, "")

    def test_set_index_drops_old_suggestions(self):
        en = w.WordIndex()
        en.insert("hello")
        pt = w.WordIndex()
        pt.insert("que")
        draft = w.WordDraft(en)
        draft.add("H")
        self.assertEqual(draft.suggestion, "HELLO")
        draft.set_index(pt)
        self.assertIs(draft.index, pt)
        self.assertEqual(draft.typed, "H")
        self.assertEqual(draft.suggestion, "H")
        self.assertIsNone(draft.pinned)

    def test_log_glyph_complete(self):
        self.assertEqual(c.log_glyph("\t"), "OK")
        self.assertEqual(c.speak_word("\t"), "complete")


class LanguageListTests(unittest.TestCase):
    def test_lang_label(self):
        self.assertEqual(w.lang_label("en-us"), "en-US")
        self.assertEqual(w.lang_label("pt_br"), "pt-BR")
        self.assertEqual(w.lang_label("es"), "es")
        self.assertEqual(w.lang_label("fr"), "fr")

    def test_lists_txt_files_alphabetically(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            (folder / "pt-br.txt").write_text("oi\n", encoding="utf-8")
            (folder / "en-us.txt").write_text("the\n", encoding="utf-8")
            (folder / "zz.txt").write_text("z\n", encoding="utf-8")
            (folder / "fr.txt").write_text("de\n", encoding="utf-8")
            (folder / "notes.md").write_text("skip", encoding="utf-8")
            labels = [label for label, _ in w.list_languages(folder)]
            self.assertEqual(labels, ["en-US", "fr", "pt-BR", "zz"])
            self.assertEqual(w.resolve_wordlist("en-US", folder).name, "en-us.txt")
            self.assertEqual(w.resolve_wordlist("zz", folder).name, "zz.txt")
            dropped = folder / "de.txt"
            dropped.write_text("und\n", encoding="utf-8")
            labels = [label for label, _ in w.list_languages(folder)]
            self.assertEqual(labels, ["de", "en-US", "fr", "pt-BR", "zz"])

    def test_shipped_langs_include_default_en_us(self):
        labels = [label for label, _ in w.list_languages()]
        self.assertEqual(labels, sorted(labels, key=str.casefold))
        self.assertEqual(w.DEFAULT_LANG, "en-US")
        for name in ("en-US", "es", "fr", "pt-BR"):
            self.assertIn(name, labels)
        self.assertEqual(w.resolve_wordlist().name, "en-us.txt")

    def test_portuguese_completes_folded_forms(self):
        idx = w.load_index(w.resolve_wordlist("pt-BR"))
        self.assertGreater(len(idx), 1000)
        self.assertEqual(idx.complete("nao"), "não")
        self.assertEqual(idx.complete("voce"), "você")
        self.assertEqual(idx.complete("que"), "que")
        with w.resolve_wordlist("pt-BR").open(encoding="utf-8") as fh:
            body = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
        self.assertIn("não", body)
        self.assertIn("você", body)
        self.assertIn("mês", body)
        self.assertIn("e", body)
        self.assertIn("a", body)
        self.assertIn("o", body)
        self.assertIn("mês", idx.words)
        self.assertNotIn("nao", body)
        for junk in ("vocecirc", "shack", "the", "you", "baby", "yeah"):
            self.assertNotIn(junk, body)


def _demo_gui() -> c.LinearCaptureGui:
    return c.LinearCaptureGui(c.parse_args(["--demo"]))


class LanguageGuiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_path = c.CONFIG_PATH
        c.CONFIG_PATH = Path(self.tmp.name) / "config.json"

    def tearDown(self):
        c.CONFIG_PATH = self.orig_path
        self.tmp.cleanup()

    def test_default_language_is_en_us(self):
        gui = _demo_gui()
        self.assertEqual(gui.lang, "en-US")
        labels = [label for label, _ in gui.lang_options]
        self.assertEqual(labels, sorted(labels, key=str.casefold))
        self.assertEqual(gui.lexicon.complete("th"), "the")

    def test_switching_language_reloads_trie(self):
        gui = _demo_gui()
        old_id = id(gui.lexicon)
        gui.set_language("pt-BR")
        self.assertEqual(gui.lang, "pt-BR")
        self.assertNotEqual(id(gui.lexicon), old_id)
        self.assertIs(gui.draft.index, gui.lexicon)
        self.assertEqual(gui.lexicon.complete("nao"), "não")
        self.assertEqual(gui.lexicon.complete("voce"), "você")


class PunctCommitTests(unittest.TestCase):
    def _gui_with(self, letters: str) -> c.LinearCaptureGui:
        gui = _demo_gui()
        for ch in letters:
            gui.draft.add(ch)
        return gui

    def test_period_moves_current_word_to_transcript(self):
        gui = self._gui_with("HI")
        gui.emit(".")
        self.assertEqual("".join(gui.typed), "HI. ")
        self.assertEqual(gui.draft.typed, "")

    def test_comma_and_question_commit_like_period(self):
        gui = self._gui_with("YES")
        gui.emit("?")
        self.assertEqual("".join(gui.typed), "YES? ")
        self.assertEqual(gui.draft.typed, "")
        gui = self._gui_with("HI")
        gui.emit(",")
        self.assertEqual("".join(gui.typed), "HI, ")
        self.assertEqual(gui.draft.typed, "")

    def test_punct_with_empty_word_still_types_the_mark(self):
        gui = self._gui_with("")
        gui.emit(".")
        self.assertEqual("".join(gui.typed), ". ")
        self.assertEqual(gui.draft.typed, "")

    def test_punct_replaces_trailing_space_after_committed_word(self):
        gui = self._gui_with("HI")
        gui.emit(" ")
        self.assertEqual("".join(gui.typed), "HI ")
        gui.emit(".")
        self.assertEqual("".join(gui.typed), "HI. ")
        self.assertEqual(gui.draft.typed, "")

    def test_punct_attaches_when_committed_word_has_no_space(self):
        gui = self._gui_with("")
        gui.typed = list("HI")
        gui.line = "HI"
        gui.emit("?")
        self.assertEqual("".join(gui.typed), "HI? ")
        self.assertEqual(gui.draft.typed, "")

    def test_punct_on_second_word_keeps_the_earlier_space(self):
        gui = self._gui_with("HI")
        gui.emit(" ")
        for ch in "THERE":
            gui.draft.add(ch)
        gui.emit(",")
        self.assertEqual("".join(gui.typed), "HI THERE, ")
        self.assertEqual(gui.draft.typed, "")

    def test_second_punct_is_not_captured(self):
        gui = self._gui_with("HI")
        gui.emit(".")
        gui.emit("?")
        self.assertEqual("".join(gui.typed), "HI. ")
        gui.emit(",")
        self.assertEqual("".join(gui.typed), "HI. ")


if __name__ == "__main__":
    unittest.main()
