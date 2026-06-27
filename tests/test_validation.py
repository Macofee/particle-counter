import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from validation import _match_particles, validate_manifest


class MatchParticlesTests(unittest.TestCase):
    def test_matches_nearest_particles_only_once(self):
        expected = [
            {"center_x_px": 10, "center_y_px": 10},
            {"center_x_px": 30, "center_y_px": 30},
        ]
        actual = [
            {"center_x_px": 11, "center_y_px": 11},
            {"center_x_px": 31, "center_y_px": 31},
            {"center_x_px": 200, "center_y_px": 200},
        ]

        self.assertEqual(sorted(_match_particles(expected, actual, 5)), [(0, 0), (1, 1)])


class ValidationManifestTests(unittest.TestCase):
    def test_rejects_manifest_without_acceptance_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.json"
            path.write_text(json.dumps({"schema_version": 1, "cases": [{}]}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "acceptance"):
                validate_manifest(path)

    def test_generates_passing_report_from_declared_truth(self):
        acceptance = {
            "min_precision": 1.0,
            "min_recall": 1.0,
            "min_bin_accuracy": 1.0,
            "max_abs_count_error_per_bin": 0,
            "max_scale_error_px": 0.0,
            "require_repeatability": True,
        }
        fake_result = {"counts": [1, 0, 0, 0], "scale_px": 91.0}
        measurements = (
            "编号,center_x_px,center_y_px,length_px,length_um,pixel_area,bin\n"
            "1,100,120,8,42.5,20,25<n<=50\n"
        )

        def fake_analyze(_image, result_dir, _settings):
            result_dir.mkdir(parents=True, exist_ok=True)
            (result_dir / "measurements.csv").write_text(measurements, encoding="utf-8-sig")
            return fake_result

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "image.png").write_bytes(b"placeholder")
            manifest = {
                "schema_version": 1,
                "acceptance": acceptance,
                "cases": [
                    {
                        "id": "golden",
                        "image": "image.png",
                        "expected": {
                            "scale_px": 91,
                            "counts": [1, 0, 0, 0],
                            "particles": [
                                {
                                    "center_x_px": 100,
                                    "center_y_px": 120,
                                    "length_um": 42.5,
                                    "bin": "25<n<=50",
                                }
                            ],
                        },
                    }
                ],
            }
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            with patch("validation.analyze_image", side_effect=fake_analyze):
                report = validate_manifest(path)

        self.assertTrue(report["passed"])
        self.assertEqual(report["cases"][0]["metrics"]["precision"], 1.0)
        self.assertTrue(report["cases"][0]["metrics"]["repeatable"])


if __name__ == "__main__":
    unittest.main()
