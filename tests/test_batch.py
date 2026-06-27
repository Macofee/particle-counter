import tempfile
import unittest
import zipfile
from pathlib import Path

import cv2
import numpy as np

from batch import run_batch
from engine import AnalysisSettings


class BatchWorkflowTests(unittest.TestCase):
    def test_processes_folder_and_builds_summary_bundle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            for name, x in (("sample-a.png", 150), ("sample-b.png", 230)):
                image = np.full((400, 400, 3), 185, dtype=np.uint8)
                cv2.circle(image, (x, 200), 12, (25, 25, 25), cv2.FILLED)
                self.assertTrue(cv2.imwrite(str(input_dir / name), image))

            report = run_batch(
                input_dir,
                output_dir,
                AnalysisSettings(
                    scale_um=500,
                    scale_px=100,
                    center_x=0.5,
                    center_y=0.5,
                    radius_x=0.45,
                    radius_y=0.45,
                    guard_um=0,
                ),
                batch_id="B-001",
                operator="tester",
                inspection_date="2026-06-27",
            )

            self.assertEqual(report["successful_files"], 2)
            self.assertEqual(report["failed_files"], 0)
            self.assertTrue((output_dir / "batch_summary.csv").is_file())
            with zipfile.ZipFile(output_dir / "batch_bundle.zip") as archive:
                self.assertIn("batch_summary.csv", archive.namelist())
                self.assertEqual(sum(name.endswith("result_bundle.zip") for name in archive.namelist()), 2)

    def test_refuses_to_overwrite_nonempty_output_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            (output_dir / "keep.txt").write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "非空"):
                run_batch(input_dir, output_dir, AnalysisSettings())


if __name__ == "__main__":
    unittest.main()
