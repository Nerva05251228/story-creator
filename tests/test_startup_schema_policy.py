import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import startup_schema_policy


class StartupSchemaPolicyTests(unittest.TestCase):
    def test_storyboard_boolean_columns_skip_heavy_postgres_alter(self):
        self.assertFalse(
            startup_schema_policy.should_apply_runtime_postgres_alter(
                "storyboard_shots",
                "use_uploaded_scene_image",
            )
        )
        self.assertFalse(
            startup_schema_policy.should_apply_runtime_postgres_alter(
                "storyboard_shots",
                "duration_override_enabled",
            )
        )

    def test_other_columns_still_allow_runtime_postgres_alter(self):
        self.assertTrue(
            startup_schema_policy.should_apply_runtime_postgres_alter(
                "episodes",
                "detail_images_model",
            )
        )

    def test_detail_image_provider_migration_does_not_pin_future_seedream_models(self):
        main_source = (BACKEND_DIR / "main.py").read_text(encoding="utf-8")

        self.assertNotIn("LIKE 'seedream-4.%'", main_source)
        self.assertIn("'seedream-4.0', 'seedream-4.1', 'seedream-4.5', 'seedream-4.6'", main_source)


if __name__ == "__main__":
    unittest.main()
