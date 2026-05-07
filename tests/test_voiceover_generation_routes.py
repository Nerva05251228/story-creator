import importlib
import contextlib
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}")

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tests.env_defaults import apply_test_env_defaults  # noqa: E402

apply_test_env_defaults()

import auth  # noqa: E402
import database  # noqa: E402


class VoiceoverGenerationRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._httpx_client_init = httpx.Client.__init__
        if "app" not in cls._httpx_client_init.__code__.co_varnames:

            def compatible_client_init(self, *args, app=None, **kwargs):
                return cls._httpx_client_init(self, *args, **kwargs)

            httpx.Client.__init__ = compatible_client_init

    @classmethod
    def tearDownClass(cls):
        httpx.Client.__init__ = cls._httpx_client_init

    def setUp(self):
        self.user = SimpleNamespace(id=42, username="route-user")
        self.db = object()

    @contextlib.contextmanager
    def _build_client(self, **service_patches):
        service_module = importlib.import_module("api.services.voiceover_generation")
        router_module = importlib.import_module("api.routers.voiceover")
        try:
            with mock.patch.multiple(service_module, **service_patches):
                router_module = importlib.reload(router_module)
                app = FastAPI()
                app.include_router(router_module.router)
                app.dependency_overrides[auth.get_current_user] = lambda: self.user
                app.dependency_overrides[database.get_db] = lambda: self.db
                app.dependency_overrides[router_module.get_current_user] = lambda: self.user
                app.dependency_overrides[router_module.get_db] = lambda: self.db
                with TestClient(app, raise_server_exceptions=False) as client:
                    yield client
        finally:
            router_module = importlib.reload(router_module)

    def test_line_generate_route_delegates_to_generation_service(self):
        async def fake_enqueue(episode_id, line_id, request, user, db):
            return {"success": True, "episode_id": episode_id, "line_id": line_id, "status": "pending"}

        with self._build_client(enqueue_voiceover_line_generate=fake_enqueue) as client:
            response = client.post(
                "/api/episodes/12/voiceover/lines/line-1/generate",
                json={"text": "hello"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["line_id"], "line-1")

    def test_generate_all_route_delegates_to_generation_service(self):
        async def fake_enqueue_all(episode_id, user, db):
            return {"success": True, "enqueued_count": 2, "skipped_count": 0, "enqueued_line_ids": ["a", "b"], "skipped": [], "queue": {"pending": 2, "processing": 0}}

        with self._build_client(enqueue_voiceover_generate_all=fake_enqueue_all) as client:
            response = client.post("/api/episodes/12/voiceover/generate-all")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["enqueued_count"], 2)

    def test_tts_status_route_delegates_to_generation_service(self):
        def fake_status(episode_id, user, db):
            return {"success": True, "line_states": [], "queue": {"pending": 1, "processing": 1}}

        with self._build_client(get_voiceover_tts_status=fake_status) as client:
            response = client.get("/api/episodes/12/voiceover/tts-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["queue"]["pending"], 1)
