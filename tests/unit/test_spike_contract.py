from fractions import Fraction
from types import SimpleNamespace
import unittest

from scripts.spike_autolabel import frame_identity, proposal_objects


class SpikeContractTests(unittest.TestCase):
    def test_pts_with_source_offset(self):
        frame = SimpleNamespace(pts=1370, time_base=Fraction(1, 1000), width=810, height=1080)
        result = frame_identity(frame, 2, Fraction(1))
        self.assertEqual(result["pts"], 1370)
        self.assertEqual(result["time_base"], {"numerator": 1, "denominator": 1000})
        self.assertEqual(result["source_timestamp_seconds"], 1.37)
        self.assertEqual(result["seconds_from_stream_start"], 0.37)

    def test_missing_pts_is_not_replaced_by_frame_number(self):
        with self.assertRaisesRegex(ValueError, "MISSING_SOURCE_PTS"):
            frame_identity(SimpleNamespace(pts=None, time_base=Fraction(1, 30)), 0, 0)

    def test_model_anchors_are_not_ground_truth(self):
        result = proposal_objects([[10, 20, 40, 80, 0.9, 0]], 100, 100)[0]
        self.assertEqual(result["geometry"]["xyxy_source_px"], [10, 20, 40, 80])
        self.assertEqual(result["ground_anchor"]["xy_source_px"], [25, 80])
        self.assertFalse(result["ground_anchor"]["reviewed"])
        self.assertEqual(result["membership"], "UNCERTAIN")
        self.assertEqual(result["kind"], "person_box")

    def test_empty_prediction_is_preserved(self):
        self.assertEqual(proposal_objects([], 100, 100), [])

    def test_invalid_boxes_or_classes_fail_instead_of_silent_repair(self):
        for box in [[-1, 0, 10, 10, 0.9, 0], [1, 1, 101, 10, 0.9, 0], [1, 1, 10, 10, 0.9, 1], [1, 1, 10, 10, 0.9, 0.5]]:
            with self.subTest(box=box), self.assertRaises(ValueError):
                proposal_objects([box], 100, 100)


if __name__ == "__main__":
    unittest.main()
