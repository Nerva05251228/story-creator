import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.services import storyboard2_media  # noqa: E402


class Storyboard2MediaServiceTests(unittest.TestCase):
    def test_normalize_jimeng_ratio_preserves_allowed_values_and_maps_legacy_values(self):
        self.assertEqual(storyboard2_media.normalize_jimeng_ratio("16:9"), "16:9")
        self.assertEqual(storyboard2_media.normalize_jimeng_ratio(" 1:2 "), "9:16")
        self.assertEqual(storyboard2_media.normalize_jimeng_ratio("2:1"), "16:9")
        self.assertEqual(storyboard2_media.normalize_jimeng_ratio("bad", default_ratio="3:4"), "3:4")
        self.assertEqual(storyboard2_media.normalize_jimeng_ratio("bad", default_ratio="bad"), "9:16")

    def test_normalize_storyboard2_video_status_maps_provider_values(self):
        cases = {
            "success": "completed",
            "done": "completed",
            "failure": "failed",
            "timed_out": "failed",
            "queued": "pending",
            "waiting": "pending",
            "running": "processing",
            "in_progress": "processing",
        }
        for raw_status, expected in cases.items():
            with self.subTest(raw_status=raw_status):
                self.assertEqual(storyboard2_media.normalize_storyboard2_video_status(raw_status), expected)

        self.assertEqual(
            storyboard2_media.normalize_storyboard2_video_status("unknown", default_value="idle"),
            "idle",
        )

    def test_is_storyboard2_video_processing_uses_normalized_status(self):
        self.assertTrue(storyboard2_media.is_storyboard2_video_processing("queued"))
        self.assertTrue(storyboard2_media.is_storyboard2_video_processing("running"))
        self.assertFalse(storyboard2_media.is_storyboard2_video_processing("completed"))
        self.assertFalse(storyboard2_media.is_storyboard2_video_processing("failed"))


if __name__ == "__main__":
    unittest.main()
