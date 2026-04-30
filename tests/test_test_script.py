import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import test as upstream_test_script


class TestScriptCapabilityTests(unittest.TestCase):
    def test_jimeng_45_capabilities_are_exposed(self):
        capabilities = upstream_test_script.get_model_capabilities("jimeng-4.5")

        self.assertEqual(capabilities["provider"], "jimeng")
        self.assertIn("9:16", capabilities["ratios"])
        self.assertIn("2K", capabilities["resolutions"])
        self.assertTrue(capabilities["supports_reference"])

    def test_moti_capabilities_do_not_expose_resolution(self):
        capabilities = upstream_test_script.get_model_capabilities("banana2-moti")

        self.assertEqual(capabilities["provider"], "moti")
        self.assertEqual(capabilities["resolutions"], [])
        self.assertTrue(capabilities["supports_reference"])

    def test_validate_run_config_rejects_unsupported_ratio(self):
        with self.assertRaises(ValueError) as ctx:
            upstream_test_script.validate_run_config(
                provider="banana",
                model="banana2",
                action="text2image",
                ratio="32:9",
                resolution="2K",
                reference_urls=[],
            )

        self.assertIn("unsupported ratio", str(ctx.exception).lower())

    def test_validate_run_config_rejects_resolution_for_moti(self):
        with self.assertRaises(ValueError) as ctx:
            upstream_test_script.validate_run_config(
                provider="moti",
                model="banana2-moti",
                action="text2image",
                ratio="9:16",
                resolution="2K",
                reference_urls=[],
            )

        self.assertIn("resolution", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
