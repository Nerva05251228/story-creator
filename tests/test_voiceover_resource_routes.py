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


class VoiceoverSharedResourceRouteTests(unittest.TestCase):
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
        self.user = SimpleNamespace(id=42, username="route-tester")
        self.db = object()

    def _import_module(self, module_name: str):
        try:
            return importlib.import_module(module_name)
        except Exception as exc:
            self.fail(f"Failed to import {module_name}: {exc}")

    @contextlib.contextmanager
    def _client_with_service_patch(self, **patched_callables):
        router_module = None
        service_module = self._import_module("api.services.voiceover_resources")
        router_module = self._import_module("api.routers.voiceover")

        try:
            with contextlib.ExitStack() as stack:
                for name, endpoint in patched_callables.items():
                    stack.enter_context(mock.patch.object(service_module, name, new=endpoint))

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
            if router_module is not None:
                importlib.reload(router_module)

    def test_rename_voice_reference_route_delegates_to_shared_resources_service(self):
        captured = {}

        async def fake_rename_voiceover_voice_reference(
            episode_id: int,
            reference_id: str,
            request: dict,
            user=Depends(auth.get_current_user),
            db=Depends(database.get_db),
        ):
            captured["episode_id"] = episode_id
            captured["reference_id"] = reference_id
            captured["request"] = request
            captured["user"] = user
            captured["db"] = db
            return {
                "success": True,
                "delegated": "rename",
                "reference_id": reference_id,
                "name": request["name"],
            }

        with self._client_with_service_patch(
            rename_voiceover_voice_reference=fake_rename_voiceover_voice_reference,
        ) as client:
            response = client.put(
                "/api/episodes/12/voiceover/shared/voice-references/ref-7",
                json={"name": "Renamed"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "success": True,
                "delegated": "rename",
                "reference_id": "ref-7",
                "name": "Renamed",
            },
        )
        self.assertEqual(captured["episode_id"], 12)
        self.assertEqual(captured["reference_id"], "ref-7")
        self.assertEqual(captured["request"], {"name": "Renamed"})
        self.assertIs(captured["user"], self.user)
        self.assertIs(captured["db"], self.db)

    def test_vector_preset_route_delegates_to_shared_resources_service(self):
        captured = {}

        async def fake_upsert_voiceover_vector_preset(
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
                "delegated": "vector",
                "preset_id": "vector-1",
                "name": request["name"],
            }

        with self._client_with_service_patch(
            upsert_voiceover_vector_preset=fake_upsert_voiceover_vector_preset,
        ) as client:
            response = client.post(
                "/api/episodes/34/voiceover/shared/vector-presets",
                json={"name": "Preset A", "vector_config": {"joy": 0.5}},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "success": True,
                "delegated": "vector",
                "preset_id": "vector-1",
                "name": "Preset A",
            },
        )
        self.assertEqual(captured["episode_id"], 34)
        self.assertEqual(
            captured["request"],
            {"name": "Preset A", "vector_config": {"joy": 0.5}},
        )
        self.assertIs(captured["user"], self.user)
        self.assertIs(captured["db"], self.db)

    def test_setting_template_route_delegates_to_shared_resources_service(self):
        captured = {}

        async def fake_upsert_voiceover_setting_template(
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
                "delegated": "setting-template",
                "template_id": "template-9",
                "name": request["name"],
            }

        with self._client_with_service_patch(
            upsert_voiceover_setting_template=fake_upsert_voiceover_setting_template,
        ) as client:
            response = client.post(
                "/api/episodes/55/voiceover/shared/setting-templates",
                json={
                    "name": "Template One",
                    "settings": {"voice_reference_id": "voice-1"},
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "success": True,
                "delegated": "setting-template",
                "template_id": "template-9",
                "name": "Template One",
            },
        )
        self.assertEqual(captured["episode_id"], 55)
        self.assertEqual(
            captured["request"],
            {
                "name": "Template One",
                "settings": {"voice_reference_id": "voice-1"},
            },
        )
        self.assertIs(captured["user"], self.user)
        self.assertIs(captured["db"], self.db)


if __name__ == "__main__":
    unittest.main()
