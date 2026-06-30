import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from engine import AnalysisSettings, analyze_image
from review import apply_review_action


class ReviewWorkflowTests(unittest.TestCase):
    def _analyzed_result(self, root: Path, analysis_mode: str = "custom") -> tuple[Path, dict]:
        image = np.full((400, 400, 3), 185, dtype=np.uint8)
        cv2.circle(image, (200, 200), 12, (25, 25, 25), cv2.FILLED)
        image_path = root / "source.png"
        self.assertTrue(cv2.imwrite(str(image_path), image))
        result_dir = root / "result"
        result = analyze_image(
            image_path,
            result_dir,
            AnalysisSettings(
                analysis_mode=analysis_mode,
                scale_um=500,
                scale_px=100,
                center_x=0.5,
                center_y=0.5,
                radius_x=0.45,
                radius_y=0.45,
                guard_um=0,
            ),
        )
        self.assertGreaterEqual(result["total"], 1)
        return result_dir, result

    def test_vda_review_keeps_standard_classification(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result_dir, original = self._analyzed_result(Path(tmpdir), "vda19_1")
            particle_id = original["particles"][0]["id"]

            reviewed = apply_review_action(
                result_dir,
                {"type": "remove", "particle_id": particle_id},
                "vda-operator",
            )

            self.assertEqual(reviewed["total"], original["total"] - 1)
            self.assertEqual([item["code"] for item in reviewed["bins"]], list("–EFGHIJKLMN"))

    def test_vda_review_rejects_typed_manual_particle_sizes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result_dir, _ = self._analyzed_result(Path(tmpdir), "vda19_1")

            with self.assertRaisesRegex(ValueError, "真实轮廓"):
                apply_review_action(
                    result_dir,
                    {"type": "add", "x_px": 130, "y_px": 150, "length_um": 100},
                    "vda-operator",
                )

    def test_remove_and_undo_rebuilds_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result_dir, original = self._analyzed_result(Path(tmpdir))
            particle_id = original["particles"][0]["id"]

            removed = apply_review_action(
                result_dir,
                {"type": "remove", "particle_id": particle_id},
                "tester",
            )
            restored = apply_review_action(result_dir, {"type": "undo"}, "tester")

            self.assertEqual(removed["total"], original["total"] - 1)
            self.assertEqual(restored["total"], original["total"])
            self.assertEqual(restored["review_audit"][0]["actor"], "tester")
            self.assertTrue((result_dir / "result_bundle.zip").is_file())

    def test_add_manual_particle_updates_count_and_audit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result_dir, original = self._analyzed_result(Path(tmpdir))

            reviewed = apply_review_action(
                result_dir,
                {"type": "add", "x_px": 130, "y_px": 150, "length_um": 75},
                "operator-7",
            )

            self.assertEqual(reviewed["total"], original["total"] + 1)
            manual = next(item for item in reviewed["particles"] if item["source"] == "manual")
            self.assertEqual(manual["bin"], "50<n<=100")
            self.assertEqual(reviewed["review_audit"][-1]["actor"], "operator-7")

    def test_rejects_manual_particle_outside_region(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result_dir, _ = self._analyzed_result(Path(tmpdir))

            with self.assertRaisesRegex(ValueError, "统计区域"):
                apply_review_action(
                    result_dir,
                    {"type": "add", "x_px": 1, "y_px": 1, "length_um": 75},
                )

    def test_split_and_undo_replaces_one_particle_with_two(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result_dir, original = self._analyzed_result(Path(tmpdir))
            particle_id = original["particles"][0]["id"]

            split = apply_review_action(
                result_dir,
                {
                    "type": "split",
                    "particle_id": particle_id,
                    "particles": [
                        {"x_px": 190, "y_px": 200, "length_um": 55},
                        {"x_px": 210, "y_px": 200, "length_um": 65},
                    ],
                },
                "tester",
            )
            restored = apply_review_action(result_dir, {"type": "undo"}, "tester")

            self.assertEqual(split["total"], original["total"] + 1)
            self.assertEqual(len(split["review_audit"][-1]["replacements"]), 2)
            self.assertEqual(restored["total"], original["total"])
            self.assertTrue(any(item["id"] == particle_id for item in restored["particles"]))


if __name__ == "__main__":
    unittest.main()
