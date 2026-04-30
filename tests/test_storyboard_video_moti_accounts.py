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
import models  # noqa: E402


class StoryboardVideoMotiAccountsTests(unittest.TestCase):
    def test_moti_payload_includes_selected_appoint_account(self):
        payload = main._build_unified_storyboard_video_task_payload(
            shot=None,
            db=None,
            username="alex",
            model_name="Seedance 2.0",
            provider="moti",
            full_prompt="手握枪柄",
            aspect_ratio="1:1",
            duration=5,
            first_frame_image_url="https://example.com/frame.png",
            appoint_account="罗西剧场",
        )

        self.assertEqual(payload["provider"], "moti")
        self.assertEqual(payload["extra"], {"appoint_accounts": ["罗西剧场"]})

    def test_moti_payload_omits_extra_when_account_is_blank(self):
        payload = main._build_unified_storyboard_video_task_payload(
            shot=None,
            db=None,
            username="alex",
            model_name="Seedance 2.0",
            provider="moti",
            full_prompt="手握枪柄",
            aspect_ratio="1:1",
            duration=5,
            first_frame_image_url="https://example.com/frame.png",
            appoint_account="  ",
        )

        self.assertNotIn("extra", payload)

    def test_effective_video_settings_keep_episode_default_appoint_account(self):
        episode = models.Episode(
            storyboard_video_model="Seedance 2.0",
            storyboard_video_aspect_ratio="1:1",
            storyboard_video_duration=5,
            storyboard_video_appoint_account="account-a",
        )
        shot = models.StoryboardShot(
            storyboard_video_model="",
            storyboard_video_model_override_enabled=False,
            duration_override_enabled=False,
        )

        settings = main._get_effective_storyboard_video_settings_for_shot(shot, episode)

        self.assertEqual(settings["appoint_account"], "account-a")


if __name__ == "__main__":
    unittest.main()
