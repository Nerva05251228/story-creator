import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.schemas.episodes import DEFAULT_STORYBOARD_VIDEO_MODEL  # noqa: E402
from api.services import storyboard_video_settings  # noqa: E402


class StoryboardVideoSettingsServiceTests(unittest.TestCase):
    def test_normalizes_model_and_provider_defaults(self):
        self.assertEqual(
            storyboard_video_settings.normalize_storyboard_video_model("  grok  "),
            "grok",
        )
        self.assertEqual(
            storyboard_video_settings.normalize_storyboard_video_model("unknown"),
            DEFAULT_STORYBOARD_VIDEO_MODEL,
        )
        self.assertEqual(
            storyboard_video_settings.resolve_storyboard_video_model_by_provider(
                "yijia",
                default_model="Seedance 2.0 Fast",
            ),
            "grok",
        )
        self.assertEqual(
            storyboard_video_settings.resolve_storyboard_video_model_by_provider(
                "moti",
                default_model="grok",
            ),
            DEFAULT_STORYBOARD_VIDEO_MODEL,
        )
        self.assertTrue(storyboard_video_settings.is_moti_storyboard_video_model("Seedance 2.0"))
        self.assertFalse(storyboard_video_settings.is_moti_storyboard_video_model("grok"))

    def test_normalizes_ratio_duration_resolution_and_appoint_account(self):
        self.assertEqual(
            storyboard_video_settings.normalize_storyboard_video_aspect_ratio(
                "1:2",
                model="grok",
                default_ratio="16:9",
            ),
            "9:16",
        )
        self.assertEqual(
            storyboard_video_settings.normalize_storyboard_video_duration(
                999,
                model="grok",
                default_duration=20,
            ),
            20,
        )
        self.assertEqual(
            storyboard_video_settings.normalize_storyboard_video_resolution_name(
                "bad",
                model="grok",
                default_resolution="480p",
            ),
            "480p",
        )
        self.assertEqual(
            storyboard_video_settings.normalize_storyboard_video_resolution_name(
                "720P",
                model="grok",
            ),
            "720p",
        )
        self.assertEqual(
            storyboard_video_settings.normalize_storyboard_video_appoint_account("  account-a  "),
            "account-a",
        )

    def test_effective_settings_use_episode_defaults(self):
        episode = SimpleNamespace(
            storyboard_video_model="grok",
            storyboard_video_aspect_ratio="1:2",
            storyboard_video_duration=30,
            storyboard_video_resolution_name="480P",
            storyboard_video_appoint_account=" account-a ",
        )
        shot = SimpleNamespace(
            storyboard_video_model="Seedance 2.0",
            storyboard_video_model_override_enabled=False,
            duration=5,
            duration_override_enabled=False,
        )

        settings = storyboard_video_settings.get_effective_storyboard_video_settings_for_shot(shot, episode)

        self.assertEqual(settings["model"], "grok")
        self.assertEqual(settings["aspect_ratio"], "9:16")
        self.assertEqual(settings["duration"], 30)
        self.assertEqual(settings["resolution_name"], "480p")
        self.assertEqual(settings["provider"], "yijia")
        self.assertEqual(settings["appoint_account"], "account-a")
        self.assertFalse(settings["model_override_enabled"])
        self.assertFalse(settings["duration_override_enabled"])
        self.assertEqual(settings["prompt_template_duration"], 25)

    def test_effective_settings_apply_shot_model_and_duration_overrides(self):
        episode = SimpleNamespace(
            storyboard_video_model="Seedance 2.0",
            storyboard_video_aspect_ratio="16:9",
            storyboard_video_duration=10,
            storyboard_video_resolution_name="",
            storyboard_video_appoint_account="account-a",
        )
        shot = SimpleNamespace(
            storyboard_video_model="grok",
            storyboard_video_model_override_enabled=True,
            duration=20,
            duration_override_enabled=True,
        )

        settings = storyboard_video_settings.get_effective_storyboard_video_settings_for_shot(shot, episode)

        self.assertEqual(settings["model"], "grok")
        self.assertEqual(settings["aspect_ratio"], "16:9")
        self.assertEqual(settings["duration"], 20)
        self.assertEqual(settings["provider"], "yijia")
        self.assertTrue(settings["model_override_enabled"])
        self.assertTrue(settings["duration_override_enabled"])
        self.assertEqual(settings["prompt_template_duration"], 25)


if __name__ == "__main__":
    unittest.main()
