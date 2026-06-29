import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from engine import (
    _bin_index,
    _checked_imwrite,
    _maximum_feret_diameter,
    _read_and_normalize,
    _runs,
    analyze_image,
    detect_yellow_scale_gap,
    AnalysisSettings,
)


class RunsTests(unittest.TestCase):
    """_runs 将排序后的整数列表分组为连续区间。"""

    def test_consecutive_integers_become_single_run(self):
        values = np.array([3, 4, 5, 6, 7])
        self.assertEqual(_runs(values), [(3, 7)])

    def test_gaps_split_runs(self):
        values = np.array([1, 2, 3, 5, 6, 8])
        self.assertEqual(_runs(values), [(1, 3), (5, 6), (8, 8)])

    def test_single_value_is_length_one_run(self):
        values = np.array([42])
        self.assertEqual(_runs(values), [(42, 42)])

    def test_empty_input_returns_empty_list(self):
        values = np.array([], dtype=np.int32)
        self.assertEqual(_runs(values), [])

    def test_non_consecutive_pair_is_two_runs(self):
        values = np.array([10, 20])
        self.assertEqual(_runs(values), [(10, 10), (20, 20)])


class MaximumFeretTests(unittest.TestCase):
    def test_uses_diagonal_instead_of_bounding_box_side(self):
        contour = np.array([[[0, 0]], [[3, 0]], [[3, 4]], [[0, 4]]], dtype=np.int32)

        self.assertAlmostEqual(_maximum_feret_diameter(contour), 6.0)

    def test_single_pixel_has_one_pixel_extent(self):
        contour = np.array([[[8, 12]]], dtype=np.int32)

        self.assertEqual(_maximum_feret_diameter(contour), 1.0)

    def test_rotating_calipers_matches_brute_force_for_random_hulls(self):
        random = np.random.default_rng(20260627)
        for _ in range(100):
            points = random.integers(0, 500, size=(40, 1, 2), dtype=np.int32)
            hull = cv2.convexHull(points).reshape(-1, 2).astype(np.float64)
            deltas = hull[:, np.newaxis, :] - hull[np.newaxis, :, :]
            expected = np.sqrt(np.max(np.sum(deltas * deltas, axis=2))) + 1.0

            self.assertAlmostEqual(_maximum_feret_diameter(points), expected)


class BinBoundaryTests(unittest.TestCase):
    def test_upper_boundary_stays_in_lower_bin(self):
        self.assertEqual(_bin_index(50.0), 0)
        self.assertEqual(_bin_index(100.0), 1)
        self.assertEqual(_bin_index(200.0), 2)

    def test_value_above_boundary_moves_to_next_bin(self):
        self.assertEqual(_bin_index(50.01), 1)
        self.assertEqual(_bin_index(200.01), 3)

    def test_below_first_bin_raises_value_error(self):
        with self.assertRaises(ValueError):
            _bin_index(10.0)


class YellowScaleDetectionTests(unittest.TestCase):
    """detect_yellow_scale_gap 的合成图像测试。"""

    @staticmethod
    def _make_image(width=1200, height=900) -> np.ndarray:
        """创建一个右下角有两条标准黄色竖线的 BGR 合成图像。"""
        image = np.full((height, width, 3), (48, 48, 52), dtype=np.uint8)
        return image

    def _draw_scale_strokes(
        self,
        image: np.ndarray,
        gap_px: int = 36,
        stroke_w: int = 24,
        stroke_h: int = 120,
    ) -> None:
        """在图像右下角绘制 U 形黄色比例尺（两竖线底部桥接为单一连通组件）。

        detect_yellow_scale_gap 要求同一连通组件内存在至少 2 个 column_run。
        真实的显微镜图像中黄色竖线通常因遮罩扩张而自然合并；合成测试中
        用底部桥接模拟此行为。
        """
        h, w = image.shape[:2]
        left_x = int(w * 0.85)
        right_x = left_x + gap_px
        top_y = int(h * 0.70)
        yellow_bgr = (0, 255, 255)
        cv2.rectangle(image, (left_x, top_y), (left_x + stroke_w, top_y + stroke_h), yellow_bgr, cv2.FILLED)
        cv2.rectangle(image, (right_x, top_y), (right_x + stroke_w, top_y + stroke_h), yellow_bgr, cv2.FILLED)
        # 底部桥接，将两竖线合并为单一连通组件
        bridge_y = top_y + stroke_h - 8
        cv2.rectangle(image, (left_x, bridge_y), (right_x + stroke_w, top_y + stroke_h), yellow_bgr, cv2.FILLED)

    def test_detects_outer_edge_distance(self):
        image = self._make_image()
        self._draw_scale_strokes(image, gap_px=36)
        gap, meta = detect_yellow_scale_gap(image)
        # 两条竖线各宽 25 px（OpenCV 矩形端点均包含），中心距 36 px，
        # 因此外侧边缘距离为 36 + 24 = 60 px。
        self.assertAlmostEqual(gap, 60.0, delta=3.0)
        self.assertEqual(meta["measurement"], "outer_edges")
        self.assertIn("outer_edges_px", meta)
        self.assertIn("line_centers_px", meta)
        self.assertIn("component_bbox", meta)

    def test_raises_on_no_yellow_strokes(self):
        image = self._make_image()
        with self.assertRaises(ValueError):
            detect_yellow_scale_gap(image)


class AnalyzeImageIntegrationTests(unittest.TestCase):
    """analyze_image 端到端测试，使用合成图像和临时目录。"""

    @staticmethod
    def _make_particle_image(
        width: int = 800,
        height: int = 600,
        particle_radius: int = 12,
    ) -> np.ndarray:
        """创建含一颗深色圆形颗粒和黄色比例尺的合成图像。"""
        # 灰色背景模拟滤膜
        image = np.full((height, width, 3), (180, 185, 190), dtype=np.uint8)
        # 深色颗粒（位于图像中央偏下）
        particle_center = (width // 2, int(height * 0.55))
        cv2.circle(image, particle_center, particle_radius, (42, 45, 50), cv2.FILLED)
        # U 形黄色比例尺（底部桥接为单一连通组件）
        h, w = image.shape[:2]
        left_x = int(w * 0.85)
        right_x = left_x + 36
        top_y = int(h * 0.70)
        yellow_bgr = (0, 255, 255)
        sw, sh2 = 24, 120
        cv2.rectangle(image, (left_x, top_y), (left_x + sw, top_y + sh2), yellow_bgr, cv2.FILLED)
        cv2.rectangle(image, (right_x, top_y), (right_x + sw, top_y + sh2), yellow_bgr, cv2.FILLED)
        bridge_y = top_y + sh2 - 8
        cv2.rectangle(image, (left_x, bridge_y), (right_x + sw, top_y + sh2), yellow_bgr, cv2.FILLED)
        return image

    def test_analyze_image_detects_particle_and_writes_outputs(self):
        image = self._make_particle_image()
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "test_particle.png"
            cv2.imwrite(str(image_path), image)
            result_dir = Path(tmpdir) / "results"

            settings = AnalysisSettings(
                scale_um=500.0,
                scale_px=None,  # auto-detect
                center_x=0.50,
                center_y=0.50,
                radius_x=0.45,
                radius_y=0.42,
                edge_threshold=20,
                seed_threshold=40,
                guard_um=130.0,
                min_size_um=25.0,
            )

            result = analyze_image(image_path, result_dir, settings)

            # 基本结果结构校验
            self.assertIn("total", result)
            self.assertIn("counts", result)
            self.assertIn("scale_px", result)
            # 应至少检测到一颗颗粒
            self.assertGreaterEqual(result["total"], 1, "应检测到至少一颗颗粒")
            # 输出文件应存在
            self.assertTrue((result_dir / "annotated.jpg").is_file())
            self.assertTrue((result_dir / "preview.jpg").is_file())
            self.assertTrue((result_dir / "summary.csv").is_file())
            self.assertTrue((result_dir / "measurements.csv").is_file())
            self.assertTrue((result_dir / "analysis.json").is_file())
            self.assertTrue((result_dir / "result_bundle.zip").is_file())
            self.assertTrue((result_dir / "report.pdf").read_bytes().startswith(b"%PDF"))
            self.assertTrue((result_dir / result["source"]["file"]).is_file())
            with zipfile.ZipFile(result_dir / "result_bundle.zip") as archive:
                self.assertIn("report.pdf", archive.namelist())
                self.assertIn(result["source"]["file"], archive.namelist())

    def test_vda_mode_reports_standard_bins_and_resolution_evidence(self):
        image = self._make_particle_image()
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "vda_particle.png"
            cv2.imwrite(str(image_path), image)
            result_dir = Path(tmpdir) / "vda_results"
            settings = AnalysisSettings(
                analysis_mode="vda19_1",
                scale_um=500.0,
                scale_px=100.0,
                center_x=0.50,
                center_y=0.50,
                radius_x=0.45,
                radius_y=0.42,
                guard_um=130.0,
            )

            result = analyze_image(image_path, result_dir, settings)

            self.assertEqual(result["analysis_mode"]["key"], "vda19_1")
            self.assertEqual([item["code"] for item in result["bins"]], list("EFGHIJKLMN"))
            self.assertTrue(result["resolution_check"]["compliant"])
            self.assertEqual(result["settings"]["min_size_um"], 50.0)

    def test_grayscale_image_is_normalized(self):
        """验证灰度图输入能被正确归一化为三通道 BGR，不崩溃。"""
        gray_image = np.full((200, 300), 128, dtype=np.uint8)
        # 在右下角绘制白色竖线（灰度图里黄色变白色，在 HSV 里无法检测
        # 黄色 — 这个测试只验证灰度归一化不崩溃，不保证自动检测成功）
        with tempfile.TemporaryDirectory() as tmpdir:
            # 给灰度图加两颗"深色颗粒"
            cv2.circle(gray_image, (150, 110), 10, (30,), cv2.FILLED)
            # 因为没有黄色比例尺，auto-detect 会失败 — 使用手动 scale_px
            image_path = Path(tmpdir) / "gray_test.png"
            cv2.imwrite(str(image_path), gray_image)
            result_dir = Path(tmpdir) / "results_gray"

            settings = AnalysisSettings(
                scale_um=500.0,
                scale_px=100.0,  # 手动指定，绕过黄色检测
                center_x=0.50,
                center_y=0.50,
                radius_x=0.45,
                radius_y=0.42,
                min_size_um=10.0,  # 降低以捕获小颗粒
            )

            result = analyze_image(image_path, result_dir, settings)
            # 灰度归一化不崩溃即可
            self.assertIn("total", result)
            # 至少一个文件被写出
            self.assertTrue((result_dir / "analysis.json").is_file())


class ImageIoReliabilityTests(unittest.TestCase):
    def test_16_bit_grayscale_is_preserved_then_normalized_to_bgr(self):
        source = np.array([[0, 257, 65535]], dtype=np.uint16)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sixteen_bit.png"
            self.assertTrue(cv2.imwrite(str(path), source))

            normalized = _read_and_normalize(path)

        self.assertEqual(normalized.dtype, np.uint8)
        self.assertEqual(normalized.shape, (1, 3, 3))
        self.assertEqual(normalized[0, 0].tolist(), [0, 0, 0])
        self.assertEqual(normalized[0, 1].tolist(), [1, 1, 1])
        self.assertEqual(normalized[0, 2].tolist(), [255, 255, 255])

    def test_bgra_image_is_normalized_to_bgr(self):
        source = np.array([[[10, 20, 30, 40]]], dtype=np.uint8)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "alpha.png"
            self.assertTrue(cv2.imwrite(str(path), source))

            normalized = _read_and_normalize(path)

        self.assertEqual(normalized.shape, (1, 1, 3))
        self.assertEqual(normalized[0, 0].tolist(), [10, 20, 30])

    def test_false_imencode_result_raises_os_error(self):
        with patch("engine.cv2.imencode", return_value=(False, None)):
            with self.assertRaisesRegex(OSError, "未能编码"):
                _checked_imwrite(Path("preview.jpg"), np.zeros((1, 1, 3), dtype=np.uint8), [])

    def test_unicode_path_read_and_write(self):
        """验证中文/Unicode 路径下的图片能被读取并写出结果。

        在英文 Windows 上，OpenCV 的 cv2.imread/cv2.imwrite 因 C 运行时
        fopen 不支持 UTF-8 路径而失败。本测试用 Python Path 写入字节，
        确保 analyze_image 的 imdecode + imencode 路径能正确处理 Unicode。
        """
        image = AnalyzeImageIntegrationTests._make_particle_image()
        with tempfile.TemporaryDirectory(prefix="中文测试_") as tmpdir:
            image_path = Path(tmpdir) / "显微照片_示例.png"
            # 用 cv2.imencode + write_bytes 写入，避免依赖 cv2.imwrite 的 Unicode 支持
            success, buf = cv2.imencode(image_path.suffix, image)
            self.assertTrue(success)
            image_path.write_bytes(buf.tobytes())

            result_dir = Path(tmpdir) / "结果输出_示例"
            settings = AnalysisSettings(
                scale_um=500.0,
                scale_px=None,  # 依赖自动黄色比例尺检测
                center_x=0.50,
                center_y=0.50,
                radius_x=0.45,
                radius_y=0.42,
                edge_threshold=20,
                seed_threshold=40,
                guard_um=130.0,
                min_size_um=25.0,
            )

            result = analyze_image(image_path, result_dir, settings)

            self.assertGreaterEqual(result["total"], 1, "应检测到至少一颗颗粒")
            self.assertTrue((result_dir / "annotated.jpg").is_file())
            self.assertTrue((result_dir / "preview.jpg").is_file())
            self.assertTrue((result_dir / "summary.csv").is_file())
            self.assertTrue((result_dir / "measurements.csv").is_file())
            self.assertTrue((result_dir / "analysis.json").is_file())
            self.assertTrue((result_dir / "result_bundle.zip").is_file())
            self.assertTrue((result_dir / "report.pdf").is_file())
            self.assertTrue((result_dir / result["source"]["file"]).is_file())


if __name__ == "__main__":
    unittest.main()
