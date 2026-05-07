import contextlib
import importlib
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import httpx


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}",
)

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tests.env_defaults import apply_test_env_defaults  # noqa: E402

apply_test_env_defaults()

import auth  # noqa: E402
import database  # noqa: E402


class VoiceoverSharedStateRouteTests(unittest.TestCase):
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

    def _import_module(self, module_name: str):
        try:
            return importlib.import_module(module_name)
        except Exception as exc:
            self.fail(f"Failed to import {module_name}: {exc}")

    @contextlib.contextmanager
    def _build_client(self, **patched_callables):
        service_module = self._import_module("api.services.voiceover_shared_state")
        voiceover_router = self._import_module("api.routers.voiceover")
        episodes_router = self._import_module("api.routers.episodes")

        try:
            with contextlib.ExitStack() as stack:
                for name, endpoint in patched_callables.items():
                    stack.enter_context(mock.patch.object(service_module, name, new=endpoint))

                voiceover_router = importlib.reload(voiceover_router)
                episodes_router = importlib.reload(episodes_router)

                app = FastAPI()
                app.include_router(voiceover_router.router)
                app.include_router(episodes_router.router)
                app.dependency_overrides[auth.get_current_user] = lambda: self.user
                app.dependency_overrides[database.get_db] = lambda: self.db
                app.dependency_overrides[voiceover_router.get_current_user] = lambda: self.user
                app.dependency_overrides[voiceover_router.get_db] = lambda: self.db
                app.dependency_overrides[episodes_router.get_current_user] = lambda: self.user
                app.dependency_overrides[episodes_router.get_db] = lambda: self.db

                with TestClient(app, raise_server_exceptions=False) as client:
                    yield client
        finally:
            importlib.reload(voiceover_router)
            importlib.reload(episodes_router)

    def test_update_voiceover_route_delegates_to_shared_state_service(self):
        captured = {}

        def fake_update_voiceover_data(
            episode_id: int,
            request: dict,
            user=Depends(auth.get_current_user),
            db=Depends(database.get_db),
        ):
            captured["episode_id"] = episode_id
            captured["request"] = request
            captured["user"] = user
            captured["db"] = db
            return {
                "success": True,
                "delegated": "update",
                "episode_id": episode_id,
                "shots": request["shots"],
            }

        with self._build_client(update_voiceover_data=fake_update_voiceover_data) as client:
            response = client.put(
                "/api/episodes/12/voiceover",
                json={"shots": [{"shot_number": 1, "narration": "hello"}]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "success": True,
                "delegated": "update",
                "episode_id": 12,
                "shots": [{"shot_number": 1, "narration": "hello"}],
            },
        )
        self.assertEqual(captured["episode_id"], 12)
        self.assertEqual(
            captured["request"],
            {"shots": [{"shot_number": 1, "narration": "hello"}]},
        )
        self.assertIs(captured["user"], self.user)
        self.assertIs(captured["db"], self.db)

    def test_voiceover_shared_route_delegates_to_shared_state_service(self):
        captured = {}

        def fake_get_voiceover_shared_data(
            episode_id: int,
            user=Depends(auth.get_current_user),
            db=Depends(database.get_db),
        ):
            captured["episode_id"] = episode_id
            captured["user"] = user
            captured["db"] = db
            return {"success": True, "delegated": "shared", "episode_id": episode_id}

        with self._build_client(
            get_voiceover_shared_data=fake_get_voiceover_shared_data,
        ) as client:
            response = client.get("/api/episodes/24/voiceover/shared")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"success": True, "delegated": "shared", "episode_id": 24},
        )
        self.assertEqual(captured["episode_id"], 24)
        self.assertIs(captured["user"], self.user)
        self.assertIs(captured["db"], self.db)

    def test_detailed_storyboard_route_delegates_to_shared_state_service(self):
        captured = {}

        def fake_get_detailed_storyboard(
            episode_id: int,
            user=Depends(auth.get_current_user),
            db=Depends(database.get_db),
        ):
            captured["episode_id"] = episode_id
            captured["user"] = user
            captured["db"] = db
            return {
                "generating": False,
                "delegated": "detailed-storyboard",
                "episode_id": episode_id,
                "shots": [],
                "subjects": [],
                "tts_shared": {},
            }

        with self._build_client(get_detailed_storyboard=fake_get_detailed_storyboard) as client:
            response = client.get("/api/episodes/31/detailed-storyboard")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "generating": False,
                "delegated": "detailed-storyboard",
                "episode_id": 31,
                "shots": [],
                "subjects": [],
                "tts_shared": {},
            },
        )
        self.assertEqual(captured["episode_id"], 31)
        self.assertIs(captured["user"], self.user)
        self.assertIs(captured["db"], self.db)


if __name__ == "__main__":
    unittest.main()
