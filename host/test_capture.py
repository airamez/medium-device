#!/usr/bin/env python3
"""Unit tests for multi-point mapping and 1\" / 0.5\" letter-gap sectors."""

import tempfile
import unittest
from pathlib import Path

import capture as c


def pts(*pairs: tuple[str, float]) -> list[dict]:
    return [c.make_point(ch, ang) for ch, ang in pairs]


class DialToCharTests(unittest.TestCase):
    def test_sticker_centers(self):
        for i, ch in enumerate(c.CHARS):
            self.assertEqual(c.dial_to_char(i * 10.0), ch)

    def test_inside_letter_width(self):
        # Letter is 6.67° wide, so ±3.33° from the center is still the letter.
        self.assertEqual(c.dial_to_char(3.3), "A")
        self.assertEqual(c.dial_to_char(356.7), "A")
        self.assertEqual(c.dial_to_char(10.0 + 3.3), "B")

    def test_gap_is_space(self):
        # Halfway between A (0°) and B (10°) is a 0.5\" gap.
        self.assertEqual(c.dial_to_char(5.0), " ")
        self.assertEqual(c.dial_to_char(15.0), " ")
        self.assertEqual(c.dial_to_char(355.0), " ")

    def test_zero_and_wrap(self):
        self.assertEqual(c.dial_to_char(260.0), "0")
        self.assertEqual(c.dial_to_char(230.0), "X")
        self.assertEqual(c.dial_to_char(220.0), "W")


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

    def test_space_between_interpolated_letters(self):
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

    def test_save_and_load_delay(self):
        points = pts(("A", 10), ("E", 50))
        c.save_config(points, invert=False, delay_s=2.5)
        cfg = c.load_config()
        self.assertEqual(cfg["delay_s"], 2.5)
        self.assertEqual([p["char"] for p in cfg["points"]], ["A", "E"])

    def test_save_preserves_existing_delay(self):
        points = pts(("A", 10), ("E", 50))
        c.save_config(points, invert=False, delay_s=2.5)
        c.save_config(points, invert=False)
        self.assertEqual(c.load_config()["delay_s"], 2.5)

    def test_invalid_delay_falls_back(self):
        c.CONFIG_PATH.write_text('{"delay_s": 0, "points": []}')
        self.assertEqual(c.load_config()["delay_s"], c.DEFAULT_DELAY_S)


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


class CompassAutoTests(unittest.TestCase):
    def setUp(self):
        # Perfect sensor: raw angle = dial angle.
        self.points = [
            c.make_point(ch, c.char_dial(ch))
            for ch, _compass, _name in c.COMPASS_REFS
        ]

    def test_eight_refs(self):
        self.assertEqual([p["char"] for p in self.points], list("AEJNSW15"))

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

    def test_no_leading_space(self):
        self.assertFalse(c.allow_emit(" ", []))

    def test_no_double_space(self):
        self.assertFalse(c.allow_emit(" ", ["H", "I", " "]))

    def test_one_space_between_words(self):
        self.assertTrue(c.allow_emit(" ", ["H", "I"]))


class OffsetFallbackTests(unittest.TestCase):
    def test_offset_treats_a_as_center(self):
        # No multi-point list: offset is the A sticker center.
        self.assertEqual(c.angle_to_char(100.0, offset=100.0), "A")
        self.assertEqual(c.angle_to_char(105.0, offset=100.0), " ")
        self.assertEqual(c.angle_to_char(110.0, offset=100.0), "B")


if __name__ == "__main__":
    unittest.main()
