import os
import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import video_api_config  # noqa: E402


class VideoApiConfigTests(unittest.TestCase):
    def test_default_video_api_urls_use_new_api_video_prefix(self):
        self.assertEqual(
            video_api_config.VIDEO_API_BASE_URL,
            "https://ne.mocatter.cn/api/video",
        )
        self.assertEqual(
            video_api_config.get_video_task_create_url(),
            "https://ne.mocatter.cn/api/video/tasks",
        )
        self.assertEqual(
            video_api_config.get_video_task_status_url("task-123"),
            "https://ne.mocatter.cn/api/video/tasks/task-123",
        )
        self.assertEqual(
            video_api_config.get_video_task_urls_update_url("task-123"),
            "https://ne.mocatter.cn/api/video/tasks/task-123/urls",
        )
        self.assertEqual(
            video_api_config.get_video_models_url(),
            "https://ne.mocatter.cn/api/video/models",
        )
        self.assertEqual(
            video_api_config.get_video_tasks_cancel_url(),
            "https://ne.mocatter.cn/api/video/tasks/cancel",
        )

    def test_docs_url_is_normalized_to_real_api_base(self):
        normalized = video_api_config.normalize_video_api_base_url(
            "https://ne.mocatter.cn/api/video/docs"
        )

        self.assertEqual(
            normalized,
            "https://ne.mocatter.cn/api/video",
        )


if __name__ == "__main__":
    unittest.main()
