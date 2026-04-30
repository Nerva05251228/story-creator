import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import storyboard_video_reference as reference_service


class StoryboardSceneReferenceTests(unittest.TestCase):
    def test_collect_first_frame_candidate_urls_includes_uploaded_first_frame_image(self):
        urls = reference_service.collect_first_frame_candidate_urls(
            storyboard_image_url="https://cdn.example.com/storyboard.png",
            detail_image_urls=[
                "https://cdn.example.com/detail-1.png",
                "https://cdn.example.com/detail-1.png",
            ],
            uploaded_first_frame_image_url="https://cdn.example.com/uploaded-first-frame.png",
        )

        self.assertEqual(
            urls,
            [
                "https://cdn.example.com/storyboard.png",
                "https://cdn.example.com/detail-1.png",
                "https://cdn.example.com/uploaded-first-frame.png",
            ],
        )

    def test_build_seedance_reference_images_places_scene_after_first_frame(self):
        payload = reference_service.build_seedance_reference_images(
            first_frame_image_url="https://cdn.example.com/first-frame.png",
            scene_image_url="https://cdn.example.com/scene.png",
            role_reference_items=[
                ("RoleA", "https://cdn.example.com/role-a.png"),
                ("RoleB", "https://cdn.example.com/role-b.png"),
            ],
        )

        self.assertEqual(
            payload["image_prefix_parts"],
            [
                "首帧[图片1]",
                "场景[图片2]",
                "RoleA[图片3]",
                "RoleB[图片4]",
            ],
        )
        self.assertEqual(
            payload["image_urls"],
            [
                "https://cdn.example.com/first-frame.png",
                "https://cdn.example.com/scene.png",
                "https://cdn.example.com/role-a.png",
                "https://cdn.example.com/role-b.png",
            ],
        )

    def test_build_seedance_reference_images_uses_scene_as_first_image_when_no_first_frame(self):
        payload = reference_service.build_seedance_reference_images(
            scene_image_url="https://cdn.example.com/scene.png",
            role_reference_items=[
                ("RoleA", "https://cdn.example.com/role-a.png"),
            ],
        )

        self.assertEqual(
            payload["image_prefix_parts"],
            [
                "场景[图片1]",
                "RoleA[图片2]",
            ],
        )
        self.assertEqual(
            payload["image_urls"],
            [
                "https://cdn.example.com/scene.png",
                "https://cdn.example.com/role-a.png",
            ],
        )

    def test_build_seedance_reference_images_places_props_between_scene_and_roles(self):
        payload = reference_service.build_seedance_reference_images(
            first_frame_image_url="https://cdn.example.com/first-frame.png",
            scene_image_url="https://cdn.example.com/scene.png",
            prop_reference_items=[
                ("青铜匕首", "https://cdn.example.com/prop-1.png"),
                ("玉佩", "https://cdn.example.com/prop-2.png"),
            ],
            role_reference_items=[
                ("陆云熙", "https://cdn.example.com/role-1.png"),
            ],
        )

        self.assertEqual(
            payload["image_prefix_parts"],
            [
                "首帧[图片1]",
                "场景[图片2]",
                "青铜匕首[图片3]",
                "玉佩[图片4]",
                "陆云熙[图片5]",
            ],
        )
        self.assertEqual(
            payload["image_urls"],
            [
                "https://cdn.example.com/first-frame.png",
                "https://cdn.example.com/scene.png",
                "https://cdn.example.com/prop-1.png",
                "https://cdn.example.com/prop-2.png",
                "https://cdn.example.com/role-1.png",
            ],
        )

    def test_build_seedance_content_text_prefixes_images_and_audio_once(self):
        text = reference_service.build_seedance_content_text(
            prompt="cinematic chase scene",
            image_prefix_parts=["首帧[图片1]", "场景[图片2]", "RoleA[图片3]"],
            audio_prefix_parts=["旁白[音频1]"],
        )

        self.assertEqual(
            text,
            "首帧[图片1]场景[图片2]RoleA[图片3]旁白[音频1]cinematic chase scene",
        )

    def test_resolve_scene_reference_image_url_prefers_uploaded_image_only_when_enabled(self):
        self.assertEqual(
            reference_service.resolve_scene_reference_image_url(
                selected_scene_card_image_url="https://cdn.example.com/scene-card.png",
                uploaded_scene_image_url="https://cdn.example.com/uploaded-scene.png",
                use_uploaded_scene_image=True,
            ),
            "https://cdn.example.com/uploaded-scene.png",
        )
        self.assertEqual(
            reference_service.resolve_scene_reference_image_url(
                selected_scene_card_image_url="https://cdn.example.com/scene-card.png",
                uploaded_scene_image_url="https://cdn.example.com/uploaded-scene.png",
                use_uploaded_scene_image=False,
            ),
            "https://cdn.example.com/scene-card.png",
        )

    def test_should_autofill_scene_override_only_when_unlocked_and_empty(self):
        self.assertTrue(
            reference_service.should_autofill_scene_override(
                current_scene_override="",
                scene_override_locked=False,
            )
        )
        self.assertFalse(
            reference_service.should_autofill_scene_override(
                current_scene_override="already filled",
                scene_override_locked=False,
            )
        )
        self.assertFalse(
            reference_service.should_autofill_scene_override(
                current_scene_override="",
                scene_override_locked=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
