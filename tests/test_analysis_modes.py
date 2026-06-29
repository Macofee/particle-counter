import unittest

from analysis_modes import get_analysis_mode


class AnalysisModeTests(unittest.TestCase):
    def test_custom_mode_preserves_existing_classification_boundaries(self):
        mode = get_analysis_mode("custom")

        self.assertEqual(mode.key, "custom")
        self.assertEqual(mode.minimum_size_um, 25.0)
        self.assertEqual(mode.classify(50.0).label, "25<n<=50")
        self.assertEqual(mode.classify(50.01).label, "50<n<=100")

    def test_vda_mode_uses_lower_inclusive_standard_size_classes(self):
        mode = get_analysis_mode("vda19_1")

        self.assertEqual(mode.minimum_size_um, 50.0)
        self.assertEqual(mode.classify(50.0).code, "E")
        self.assertEqual(mode.classify(99.99).code, "E")
        self.assertEqual(mode.classify(100.0).code, "F")
        self.assertEqual(mode.classify(200.0).code, "H")
        self.assertEqual(mode.classify(1000.0).code, "K")
        self.assertEqual(mode.classify(3000.0).code, "N")

    def test_vda_mode_requires_ten_pixels_for_smallest_reported_particle(self):
        mode = get_analysis_mode("vda19_1")

        check = mode.validate_resolution(5.0)
        self.assertEqual(check["minimum_particle_pixels"], 10.0)
        self.assertTrue(check["compliant"])

        with self.assertRaisesRegex(ValueError, "至少需要 10 像素"):
            mode.validate_resolution(5.1)

    def test_mode_minimum_boundary_matches_its_first_size_class(self):
        self.assertFalse(get_analysis_mode("custom").should_report(25.0))
        self.assertTrue(get_analysis_mode("vda19_1").should_report(50.0))


if __name__ == "__main__":
    unittest.main()
