import asyncio
import inspect
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}",
)

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.env_defaults import apply_test_env_defaults  # noqa: E402

apply_test_env_defaults()

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.routers import video as video_router  # noqa: E402


class VideoProxyEndpointTests(unittest.TestCase):
    def test_video_proxy_endpoints_are_sync_routes(self):
        self.assertFalse(inspect.iscoroutinefunction(video_router.get_video_provider_stats))
        self.assertFalse(inspect.iscoroutinefunction(video_router.get_video_quota))
        self.assertTrue(inspect.iscoroutinefunction(video_router.query_task_status))

    def test_provider_stats_proxy_shapes_upstream_list_payload(self):
        class DummyResponse:
            status_code = 200

            def json(self):
                return [{"provider": "moti", "running": 1}]

        with mock.patch.object(video_router.requests, "get", return_value=DummyResponse()) as get_mock:
            result = video_router.get_video_provider_stats(user=SimpleNamespace(id=1))

        self.assertEqual(result, {"providers": [{"provider": "moti", "running": 1}]})
        get_mock.assert_called_once_with(
            "https://video.example.test/api/video/stats/providers",
            headers={"Authorization": "Bearer test-api-token", "Content-Type": "application/json"},
            timeout=5,
        )

    def test_quota_proxy_quotes_username_and_uses_configured_video_base(self):
        class DummyResponse:
            status_code = 200

            def json(self):
                return {"remaining": 12}

        with mock.patch.object(video_router.requests, "get", return_value=DummyResponse()) as get_mock:
            result = video_router.get_video_quota("alice bob", user=SimpleNamespace(id=1))

        self.assertEqual(result, {"remaining": 12})
        get_mock.assert_called_once_with(
            "https://video.example.test/api/video/quota/alice%20bob",
            headers={"Authorization": "Bearer test-api-token", "Content-Type": "application/json"},
            timeout=5,
        )

    def test_query_task_status_returns_raw_upstream_payload(self):
        with mock.patch.object(
            video_router,
            "check_video_status",
            return_value={"status": "completed", "task_id": "task-1"},
        ) as status_mock:
            result = asyncio.run(
                video_router.query_task_status("task-1", user=SimpleNamespace(id=1))
            )

        self.assertEqual(result, {"status": "completed", "task_id": "task-1"})
        status_mock.assert_called_once_with("task-1", return_raw=True)

    def test_query_task_status_wraps_upstream_errors(self):
        with mock.patch.object(video_router, "check_video_status", side_effect=RuntimeError("boom")):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(video_router.query_task_status("task-1", user=SimpleNamespace(id=1)))

        self.assertEqual(ctx.exception.status_code, 500)


if __name__ == "__main__":
    unittest.main()
