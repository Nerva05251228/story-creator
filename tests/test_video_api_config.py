import os
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class VideoApiConfigTests(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("video_api_config", None)

    def _load_config(self):
        return importlib.import_module("video_api_config")

    def _load_config_without_app_env(self):
        sys.modules.pop("video_api_config", None)
        sys.modules.pop("env_config", None)
        dotenv_module = types.ModuleType("dotenv")
        dotenv_module.load_dotenv = mock.Mock(return_value=False)
        with mock.patch.dict(sys.modules, {"dotenv": dotenv_module}):
            env_config = importlib.import_module("env_config")
            with mock.patch.object(env_config, "load_app_env", return_value=False):
                return self._load_config()

    @mock.patch.dict(
        os.environ,
        {
            "VIDEO_API_BASE_URL": "https://video.example.test/api/video",
            "VIDEO_API_TOKEN": "test-video-token",
        },
        clear=True,
    )
    def test_configured_video_api_urls_use_api_video_prefix(self):
        video_api_config = self._load_config()

        self.assertEqual(
            video_api_config.VIDEO_API_BASE_URL,
            "https://video.example.test/api/video",
        )
        self.assertEqual(
            video_api_config.get_video_task_create_url(),
            "https://video.example.test/api/video/tasks",
        )
        self.assertEqual(
            video_api_config.get_video_task_status_url("task-123"),
            "https://video.example.test/api/video/tasks/task-123",
        )
        self.assertEqual(
            video_api_config.get_video_task_urls_update_url("task-123"),
            "https://video.example.test/api/video/tasks/task-123/urls",
        )
        self.assertEqual(
            video_api_config.get_video_models_url(),
            "https://video.example.test/api/video/models",
        )
        self.assertEqual(
            video_api_config.get_video_tasks_cancel_url(),
            "https://video.example.test/api/video/tasks/cancel",
        )

    def test_docs_url_is_normalized_to_real_api_base(self):
        video_api_config = self._load_config()
        normalized = video_api_config.normalize_video_api_base_url(
            "https://video.example.test/api/video/docs"
        )

        self.assertEqual(
            normalized,
            "https://video.example.test/api/video",
        )

    def test_video_urls_are_built_from_normalized_configured_base_url(self):
        for configured_base_url in (
            "https://video.example.test/api/video/docs",
            "https://video.example.test/api/video/openapi.json",
        ):
            with self.subTest(configured_base_url=configured_base_url):
                with mock.patch.dict(
                    os.environ,
                    {
                        "VIDEO_API_BASE_URL": configured_base_url,
                        "VIDEO_API_TOKEN": "test-video-token",
                    },
                    clear=True,
                ):
                    video_api_config = self._load_config()

                    self.assertEqual(
                        video_api_config.get_video_task_create_url(),
                        "https://video.example.test/api/video/tasks",
                    )
                    self.assertEqual(
                        video_api_config.get_video_provider_accounts_url(" Kling "),
                        "https://video.example.test/api/video/providers/kling/accounts",
                    )

    @mock.patch.dict(
        os.environ,
        {
            "VIDEO_API_BASE_URL": "https://video.example.test/api/video",
            "VIDEO_API_TOKEN": "test-video-token",
        },
        clear=True,
    )
    def test_provider_stats_url_targets_configured_video_api(self):
        video_api_config = self._load_config()

        self.assertEqual(
            video_api_config.get_video_provider_stats_url(),
            "https://video.example.test/api/video/stats/providers",
        )

    @mock.patch.dict(
        os.environ,
        {
            "VIDEO_API_BASE_URL": "https://video.example.invalid/api/video",
            "VIDEO_API_TOKEN": "<set-local-video-token>",
        },
        clear=True,
    )
    def test_video_request_helpers_reject_placeholder_values(self):
        video_api_config = self._load_config_without_app_env()

        with self.assertRaisesRegex(RuntimeError, "placeholder"):
            video_api_config.get_video_api_headers()
        with self.assertRaisesRegex(RuntimeError, "placeholder"):
            video_api_config.get_video_provider_stats_url()

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_video_token_raises_clear_error_when_headers_are_needed(self):
        video_api_config = self._load_config_without_app_env()

        with self.assertRaisesRegex(RuntimeError, "VIDEO_API_TOKEN"):
            video_api_config.get_video_api_headers()

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_video_token_is_not_repopulated_from_dotenv_during_import(self):
        sys.modules.pop("env_config", None)
        dotenv_module = types.ModuleType("dotenv")
        dotenv_module.load_dotenv = mock.Mock(return_value=False)
        with mock.patch.dict(sys.modules, {"dotenv": dotenv_module}):
            env_config = importlib.import_module("env_config")
            with tempfile.TemporaryDirectory() as temp_dir:
                dotenv_path = Path(temp_dir) / ".env"
                dotenv_path.write_text(
                    "VIDEO_API_TOKEN=token-from-dotenv\n",
                    encoding="utf-8",
                )

                def load_dotenv(*args, **kwargs):
                    os.environ["VIDEO_API_TOKEN"] = "token-from-dotenv"
                    return True

                dotenv_module.load_dotenv.side_effect = load_dotenv
                with mock.patch.object(env_config, "DEFAULT_ENV_PATH", dotenv_path):
                    with mock.patch.object(env_config, "load_app_env", return_value=False):
                        video_api_config = self._load_config()

        with self.assertRaisesRegex(RuntimeError, "VIDEO_API_TOKEN"):
            video_api_config.get_video_api_headers()


if __name__ == "__main__":
    unittest.main()
