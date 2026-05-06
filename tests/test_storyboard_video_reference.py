import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import storyboard_video_reference as reference_service


class StoryboardVideoReferenceTests(unittest.TestCase):
    def test_collect_first_frame_candidate_urls_preserves_order_and_deduplicates(self):
        candidates = reference_service.collect_first_frame_candidate_urls(
            storyboard_image_url="https://cdn.example.com/storyboard.png",
            detail_image_urls=[
                "https://cdn.example.com/detail-1.png",
                "https://cdn.example.com/storyboard.png",
                " https://cdn.example.com/detail-2.png ",
                "",
            ],
        )

        self.assertEqual(
            candidates,
            [
                "https://cdn.example.com/storyboard.png",
                "https://cdn.example.com/detail-1.png",
                "https://cdn.example.com/detail-2.png",
            ],
        )

    def test_is_allowed_first_frame_candidate_url_accepts_storyboard_and_detail_images(self):
        self.assertTrue(
            reference_service.is_allowed_first_frame_candidate_url(
                target_url="https://cdn.example.com/storyboard.png",
                storyboard_image_url="https://cdn.example.com/storyboard.png",
                detail_image_urls=["https://cdn.example.com/detail-1.png"],
            )
        )
        self.assertTrue(
            reference_service.is_allowed_first_frame_candidate_url(
                target_url="https://cdn.example.com/detail-1.png",
                storyboard_image_url="https://cdn.example.com/storyboard.png",
                detail_image_urls=["https://cdn.example.com/detail-1.png"],
            )
        )
        self.assertFalse(
            reference_service.is_allowed_first_frame_candidate_url(
                target_url="https://cdn.example.com/other.png",
                storyboard_image_url="https://cdn.example.com/storyboard.png",
                detail_image_urls=["https://cdn.example.com/detail-1.png"],
            )
        )

    def test_build_seedance_prompt_prefixes_selected_first_frame_reference(self):
        prompt = reference_service.build_seedance_prompt(
            prompt="电影级追逐镜头",
            first_frame_image_url="https://cdn.example.com/storyboard.png",
        )

        self.assertEqual(prompt, "首帧[图片1]电影级追逐镜头")

    def test_build_seedance_reference_images_places_first_frame_before_role_references(self):
        payload = reference_service.build_seedance_reference_images(
            first_frame_image_url="https://cdn.example.com/storyboard.png",
            role_reference_items=[
                ("陆振川", "https://cdn.example.com/role-1.png"),
                ("苏晚", "https://cdn.example.com/role-2.png"),
            ],
        )

        self.assertEqual(
            payload["image_prefix_parts"],
            [
                "首帧[图片1]",
                "陆振川[图片2]",
                "苏晚[图片3]",
            ],
        )
        self.assertEqual(
            payload["image_urls"],
            [
                "https://cdn.example.com/storyboard.png",
                "https://cdn.example.com/role-1.png",
                "https://cdn.example.com/role-2.png",
            ],
        )


if __name__ == "__main__":
    unittest.main()
