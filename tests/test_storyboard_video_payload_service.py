import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.services import storyboard_video_payload  # noqa: E402


class StoryboardVideoPayloadServiceTests(unittest.TestCase):
    def test_moti_payload_includes_reference_content_and_appoint_account(self):
        payload = storyboard_video_payload._build_unified_storyboard_video_task_payload(
            shot=None,
            db=None,
            username=" alex ",
            model_name="Seedance 2.0",
            provider="moti",
            full_prompt=" prompt ",
            aspect_ratio="1:1",
            duration=5,
            first_frame_image_url="https://example.com/frame.png",
            appoint_account=" account-a ",
        )

        self.assertEqual(payload["username"], "alex")
        self.assertEqual(payload["provider"], "moti")
        self.assertEqual(payload["model"], "Seedance 2.0")
        self.assertEqual(payload["ratio"], "1:1")
        self.assertEqual(payload["duration"], 5)
        self.assertEqual(payload["extra"], {"appoint_accounts": ["account-a"]})
        self.assertFalse(payload["watermark"])
        self.assertEqual(
            payload["content"][1],
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/frame.png"},
                "role": "reference_image",
            },
        )

    def test_grok_payload_normalizes_provider_resolution_and_reference_content(self):
        payload = storyboard_video_payload._build_unified_storyboard_video_task_payload(
            shot=None,
            db=None,
            username="alex",
            model_name="grok",
            provider="yijia-grok",
            full_prompt=" prompt ",
            aspect_ratio="1:2",
            duration=20,
            first_frame_image_url="https://example.com/frame.png",
            resolution_name="480P",
        )

        self.assertEqual(payload["provider"], "yijia")
        self.assertEqual(payload["model"], "grok")
        self.assertEqual(payload["ratio"], "9:16")
        self.assertEqual(payload["duration"], 20)
        self.assertEqual(payload["resolution_name"], "480p")
        self.assertEqual(payload["content"][0], {"type": "text", "text": "prompt"})
        self.assertEqual(
            payload["content"][1],
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/frame.png"},
                "role": "reference_image",
            },
        )

    def test_sora_payload_uses_prompt_aspect_ratio_and_first_frame_url(self):
        payload = storyboard_video_payload._build_unified_storyboard_video_task_payload(
            shot=None,
            db=None,
            username="alex",
            model_name="sora-2",
            provider="yijia",
            full_prompt=" prompt ",
            aspect_ratio="2:1",
            duration=25,
            first_frame_image_url=" https://example.com/frame.png ",
        )

        self.assertEqual(payload["provider"], "yijia")
        self.assertEqual(payload["model"], "sora-2")
        self.assertEqual(payload["ratio"], "16:9")
        self.assertEqual(payload["duration"], 25)
        self.assertEqual(payload["prompt"], "prompt")
        self.assertEqual(payload["aspect_ratio"], "16:9")
        self.assertEqual(payload["image_url"], "https://example.com/frame.png")


if __name__ == "__main__":
    unittest.main()
