import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import storyboard_variant


class StoryboardVariantTests(unittest.TestCase):
    def _build_source_shot(self):
        return SimpleNamespace(
            episode_id=276,
            shot_number=1,
            stable_id="shot-1",
            prompt_template="prompt",
            script_excerpt="excerpt",
            storyboard_video_prompt="video",
            storyboard_audio_prompt="audio",
            storyboard_dialogue="dialogue",
            scene_override="scene",
            scene_override_locked=True,
            sora_prompt="sora",
            sora_prompt_is_full=True,
            sora_prompt_status="completed",
            selected_card_ids="[1,2,3]",
            selected_sound_card_ids="[9]",
            first_frame_reference_image_url="https://img.example.com/frame.jpg",
            uploaded_scene_image_url="https://img.example.com/scene.jpg",
            use_uploaded_scene_image=True,
            aspect_ratio="16:9",
            duration=15,
            duration_override_enabled=True,
            provider="moti",
            timeline_json='{"timeline":true}',
            detail_image_prompt_overrides='{"1":"override"}',
            storyboard_image_path="https://img.example.com/frame.jpg",
            storyboard_image_status="completed",
            storyboard_image_task_id="old-image-task",
            storyboard_image_model="banana-pro",
            video_status="completed",
            task_id="old-video-task",
            video_path="https://video.example.com/old.mp4",
            thumbnail_video_path="https://video.example.com/old-cover.mp4",
            video_error_message="",
            video_submitted_at="2026-04-02T00:00:00",
            cdn_uploaded=True,
        )

    def test_duplicate_video_variant_preserves_storyboard_image_and_first_frame(self):
        source_shot = self._build_source_shot()

        payload = storyboard_variant.build_duplicate_shot_payload(
            source_shot,
            next_variant=1,
        )

        self.assertEqual(payload["variant_index"], 1)
        self.assertEqual(payload["storyboard_image_path"], "https://img.example.com/frame.jpg")
        self.assertEqual(payload["storyboard_image_status"], "completed")
        self.assertEqual(payload["storyboard_image_model"], "banana-pro")
        self.assertEqual(payload["first_frame_reference_image_url"], "https://img.example.com/frame.jpg")
        self.assertEqual(payload["video_status"], "idle")
        self.assertEqual(payload["task_id"], "")
        self.assertEqual(payload["video_path"], "")
        self.assertEqual(payload["storyboard_image_task_id"], "")

    def test_sync_variant_preserves_storyboard_image_and_first_frame(self):
        source_shot = self._build_source_shot()

        payload = storyboard_variant.build_storyboard_sync_variant_payload(
            source_shot,
            next_variant=2,
            script_excerpt="new excerpt",
            storyboard_dialogue="new dialogue",
            selected_card_ids="[4,5]",
            sora_prompt="new sora",
        )

        self.assertEqual(payload["variant_index"], 2)
        self.assertEqual(payload["script_excerpt"], "new excerpt")
        self.assertEqual(payload["storyboard_dialogue"], "new dialogue")
        self.assertEqual(payload["selected_card_ids"], "[4,5]")
        self.assertEqual(payload["sora_prompt"], "new sora")
        self.assertEqual(payload["sora_prompt_status"], "idle")
        self.assertEqual(payload["storyboard_image_path"], "https://img.example.com/frame.jpg")
        self.assertEqual(payload["storyboard_image_status"], "completed")
        self.assertEqual(payload["storyboard_image_model"], "banana-pro")
        self.assertEqual(payload["first_frame_reference_image_url"], "https://img.example.com/frame.jpg")
        self.assertEqual(payload["storyboard_image_task_id"], "")
        self.assertEqual(payload["video_status"], "idle")

    def test_storyboard_image_variant_preserves_current_image_for_first_frame(self):
        source_shot = self._build_source_shot()

        payload = storyboard_variant.build_storyboard_image_variant_payload(
            source_shot,
            next_variant=3,
        )

        self.assertEqual(payload["variant_index"], 3)
        self.assertEqual(payload["storyboard_image_path"], "https://img.example.com/frame.jpg")
        self.assertEqual(payload["storyboard_image_status"], "processing")
        self.assertEqual(payload["storyboard_image_model"], "banana-pro")
        self.assertEqual(payload["first_frame_reference_image_url"], "https://img.example.com/frame.jpg")
        self.assertEqual(payload["storyboard_image_task_id"], "")

    def test_choose_storyboard_reference_source_prefers_matching_first_frame(self):
        target_shot = SimpleNamespace(
            id=2,
            variant_index=1,
            storyboard_image_path="",
            first_frame_reference_image_url="https://img.example.com/frame.jpg",
        )
        family_shots = [
            SimpleNamespace(
                id=1,
                variant_index=0,
                storyboard_image_path="https://img.example.com/frame.jpg",
            ),
            SimpleNamespace(
                id=3,
                variant_index=2,
                storyboard_image_path="https://img.example.com/other.jpg",
            ),
        ]

        selected = storyboard_variant.choose_storyboard_reference_source(
            target_shot,
            family_shots,
        )

        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, 1)

    def test_choose_storyboard_reference_source_falls_back_to_main_variant(self):
        target_shot = SimpleNamespace(
            id=2,
            variant_index=3,
            storyboard_image_path="",
            first_frame_reference_image_url="",
        )
        family_shots = [
            SimpleNamespace(
                id=1,
                variant_index=0,
                storyboard_image_path="https://img.example.com/main.jpg",
            ),
            SimpleNamespace(
                id=4,
                variant_index=2,
                storyboard_image_path="https://img.example.com/variant.jpg",
            ),
        ]

        selected = storyboard_variant.choose_storyboard_reference_source(
            target_shot,
            family_shots,
        )

        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, 1)


if __name__ == "__main__":
    unittest.main()
