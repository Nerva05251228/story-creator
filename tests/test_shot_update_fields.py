import os
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}")

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402


class ShotUpdateFieldTests(unittest.TestCase):
    def test_shot_update_accepts_clone_sync_fields(self):
        payload = main.ShotUpdate.model_validate(
            {
                "scene_override_locked": True,
                "sora_prompt_status": "completed",
                "storyboard_image_path": "https://img.example.com/storyboard.jpg",
                "first_frame_reference_image_url": "https://img.example.com/storyboard.jpg",
            }
        )

        self.assertTrue(payload.scene_override_locked)
        self.assertEqual(payload.sora_prompt_status, "completed")
        self.assertEqual(
            payload.storyboard_image_path,
            "https://img.example.com/storyboard.jpg",
        )
        self.assertEqual(
            payload.first_frame_reference_image_url,
            "https://img.example.com/storyboard.jpg",
        )


if __name__ == "__main__":
    unittest.main()
