import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


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

import database  # noqa: E402
import models  # noqa: E402
from api.routers import episodes  # noqa: E402
from api.schemas.episodes import (  # noqa: E402
    BatchGenerateSoraPromptsRequest,
    EpisodeCreate,
    ManagedSessionStatusResponse,
)


EXPECTED_EPISODE_ROUTES = {
    ("POST", "/api/scripts/{script_id}/episodes"),
    ("GET", "/api/scripts/{script_id}/episodes"),
    ("POST", "/api/scripts/{script_id}/episodes/{episode_id}/convert-to-narration"),
    ("POST", "/api/scripts/{script_id}/episodes/{episode_id}/generate-opening"),
    ("GET", "/api/episodes/{episode_id}"),
    ("PUT", "/api/episodes/{episode_id}"),
    ("GET", "/api/episodes/{episode_id}/poll-status"),
    ("GET", "/api/episodes/{episode_id}/total-cost"),
    ("PUT", "/api/episodes/{episode_id}/storyboard2-duration"),
    ("POST", "/api/episodes/{episode_id}/generate-simple-storyboard"),
    ("GET", "/api/episodes/{episode_id}/simple-storyboard"),
    ("GET", "/api/episodes/{episode_id}/simple-storyboard/status"),
    ("POST", "/api/episodes/{episode_id}/simple-storyboard/retry-failed-batches"),
    ("PUT", "/api/episodes/{episode_id}/simple-storyboard"),
    ("POST", "/api/episodes/{episode_id}/generate-detailed-storyboard"),
    ("POST", "/api/episodes/{episode_id}/analyze-storyboard"),
    ("GET", "/api/episodes/{episode_id}/detailed-storyboard"),
    ("PUT", "/api/episodes/{episode_id}/voiceover"),
    ("GET", "/api/episodes/{episode_id}/voiceover/shared"),
    ("POST", "/api/episodes/{episode_id}/voiceover/shared/voice-references"),
    ("PUT", "/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}"),
    ("GET", "/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}/preview"),
    ("DELETE", "/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}"),
    ("POST", "/api/episodes/{episode_id}/voiceover/shared/vector-presets"),
    ("DELETE", "/api/episodes/{episode_id}/voiceover/shared/vector-presets/{preset_id}"),
    ("POST", "/api/episodes/{episode_id}/voiceover/shared/emotion-audio-presets"),
    ("DELETE", "/api/episodes/{episode_id}/voiceover/shared/emotion-audio-presets/{preset_id}"),
    ("POST", "/api/episodes/{episode_id}/voiceover/shared/setting-templates"),
    ("DELETE", "/api/episodes/{episode_id}/voiceover/shared/setting-templates/{template_id}"),
    ("POST", "/api/episodes/{episode_id}/voiceover/lines/{line_id}/generate"),
    ("POST", "/api/episodes/{episode_id}/voiceover/generate-all"),
    ("GET", "/api/episodes/{episode_id}/voiceover/tts-status"),
    ("GET", "/api/episodes/{episode_id}/storyboard"),
    ("GET", "/api/episodes/{episode_id}/storyboard/status"),
    ("PUT", "/api/episodes/{episode_id}/storyboard"),
    ("POST", "/api/episodes/{episode_id}/create-from-storyboard"),
    ("POST", "/api/episodes/{episode_id}/batch-generate-sora-prompts"),
    ("POST", "/api/episodes/{episode_id}/batch-generate-sora-videos"),
    ("POST", "/api/episodes/{episode_id}/start-managed-generation"),
    ("POST", "/api/episodes/{episode_id}/stop-managed-generation"),
    ("GET", "/api/episodes/{episode_id}/managed-session-status"),
    ("POST", "/api/episodes/{episode_id}/refresh-videos"),
    ("POST", "/api/episodes/{episode_id}/import-storyboard"),
    ("GET", "/api/episodes/{episode_id}/export-storyboard"),
    ("GET", "/api/episodes/{episode_id}/export-all"),
    ("GET", "/api/episodes/{episode_id}/storyboard2"),
    ("POST", "/api/episodes/{episode_id}/storyboard2/batch-generate-sora-prompts"),
}


class EpisodeRouterTests(unittest.TestCase):
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
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.current_user = None

        def override_get_db():
            request_db = self.Session()
            try:
                yield request_db
            finally:
                request_db.close()

        def override_get_current_user():
            return self.current_user

        self.app = FastAPI()
        self.app.include_router(episodes.router)
        self.app.dependency_overrides[database.get_db] = override_get_db
        self.app.dependency_overrides[episodes.get_current_user] = override_get_current_user
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed_episode(self):
        db = self.Session()
        try:
            owner = models.User(username="owner", token="owner-token")
            other = models.User(username="other", token="other-token")
            db.add_all([owner, other])
            db.flush()
            script = models.Script(user_id=owner.id, name="Script")
            db.add(script)
            db.flush()
            episode = models.Episode(
                script_id=script.id,
                name="Episode 1",
                content="Original",
                billing_version=0,
                storyboard_data='{"shots":[{"shot_number":"1"}]}',
            )
            db.add(episode)
            db.flush()
            shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                price=125,
            )
            db.add(shot)
            db.commit()
            return owner, other, episode.id
        finally:
            db.close()

    def _seed_script_for_owner(self):
        db = self.Session()
        try:
            owner = models.User(username="owner", token="owner-token")
            other = models.User(username="other", token="other-token")
            db.add_all([owner, other])
            db.flush()
            script = models.Script(user_id=owner.id, name="Script")
            db.add(script)
            db.commit()
            return owner, other, script.id
        finally:
            db.close()

    def _seed_script_episode_for_owner(self):
        db = self.Session()
        try:
            owner = models.User(username="owner", token="owner-token")
            other = models.User(username="other", token="other-token")
            db.add_all([owner, other])
            db.flush()
            script = models.Script(user_id=owner.id, name="Script")
            db.add(script)
            db.flush()
            episode = models.Episode(
                script_id=script.id,
                name="Episode 1",
                content="Original content",
            )
            db.add(episode)
            db.commit()
            return owner, other, script.id, episode.id
        finally:
            db.close()

    def test_router_owns_episode_routes_without_shot_crud_routes(self):
        registered = set()
        for route in episodes.router.routes:
            methods = getattr(route, "methods", set()) or set()
            path = getattr(route, "path", "")
            for method in methods:
                if method not in {"HEAD", "OPTIONS"}:
                    registered.add((method, path))

        self.assertEqual(registered, EXPECTED_EPISODE_ROUTES)
        self.assertNotIn(("POST", "/api/episodes/{episode_id}/shots"), registered)
        self.assertFalse(any(path.startswith("/api/shots/") for _, path in registered))

    def test_schema_defaults_match_legacy_episode_contracts(self):
        self.assertEqual(
            EpisodeCreate(name="Episode").storyboard_video_model,
            "Seedance 2.0 Fast",
        )
        self.assertEqual(
            BatchGenerateSoraPromptsRequest().default_template,
            "2d漫画风格（细）",
        )
        self.assertEqual(
            ManagedSessionStatusResponse(
                session_id=None,
                status="none",
                total_shots=0,
                completed_shots=0,
                created_at=None,
            ).status,
            "none",
        )

    def test_get_update_poll_status_and_total_cost_preserve_owner_behavior(self):
        owner, other, episode_id = self._seed_episode()

        self.current_user = owner
        get_response = self.client.get(f"/api/episodes/{episode_id}")
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json()["name"], "Episode 1")

        update_response = self.client.put(
            f"/api/episodes/{episode_id}",
            json={"name": "Updated", "content": "Changed"},
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["name"], "Updated")

        poll_response = self.client.get(f"/api/episodes/{episode_id}/poll-status")
        self.assertEqual(poll_response.status_code, 200)
        self.assertEqual(poll_response.json()["batch_generating_prompts"], False)

        cost_response = self.client.get(f"/api/episodes/{episode_id}/total-cost")
        self.assertEqual(cost_response.status_code, 200)
        self.assertEqual(cost_response.json()["total_cost_cents"], 125)
        self.assertEqual(cost_response.json()["total_cost_yuan"], 1.25)

        self.current_user = other
        forbidden_response = self.client.get(f"/api/episodes/{episode_id}")
        self.assertEqual(forbidden_response.status_code, 403)
        self.assertEqual(forbidden_response.json(), {"detail": "无权限"})

    def test_script_episode_create_and_list_preserve_owner_behavior(self):
        owner, other, script_id = self._seed_script_for_owner()

        self.current_user = owner
        create_response = self.client.post(
            f"/api/scripts/{script_id}/episodes",
            json={"name": "Episode 1", "content": "Opening content"},
        )

        self.assertEqual(create_response.status_code, 200)
        created = create_response.json()
        self.assertEqual(created["script_id"], script_id)
        self.assertEqual(created["name"], "Episode 1")
        self.assertEqual(created["content"], "Opening content")

        db = self.Session()
        try:
            library = db.query(models.StoryLibrary).filter_by(
                episode_id=created["id"]
            ).one()
            library_id = library.id
            self.assertEqual(library.user_id, owner.id)
        finally:
            db.close()

        list_response = self.client.get(f"/api/scripts/{script_id}/episodes")

        self.assertEqual(list_response.status_code, 200)
        listed = list_response.json()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], created["id"])
        self.assertEqual(listed[0]["library_id"], library_id)
        self.assertEqual(listed[0]["storyboard_video_model"], "Seedance 2.0 Fast")

        self.current_user = other
        blocked_response = self.client.get(f"/api/scripts/{script_id}/episodes")
        self.assertEqual(blocked_response.status_code, 403)

    def test_script_episode_text_relay_endpoints_submit_tasks_and_set_flags(self):
        owner, _other, script_id, episode_id = self._seed_script_episode_for_owner()
        self.current_user = owner
        submitted = []

        def fake_submit(_db, **kwargs):
            submitted.append(kwargs)
            return SimpleNamespace(external_task_id=f"relay-{len(submitted)}")

        with patch.object(
            episodes,
            "get_ai_config",
            return_value={"model": "test-model"},
        ), patch.object(
            episodes,
            "submit_and_persist_text_task",
            side_effect=fake_submit,
        ):
            narration_response = self.client.post(
                f"/api/scripts/{script_id}/episodes/{episode_id}/convert-to-narration",
                json={"content": "Narration source", "template": "Narrate:"},
            )
            opening_response = self.client.post(
                f"/api/scripts/{script_id}/episodes/{episode_id}/generate-opening",
                json={"template": "Open:"},
            )

        self.assertEqual(narration_response.status_code, 200)
        self.assertEqual(opening_response.status_code, 200)
        self.assertEqual(narration_response.json()["task_id"], "relay-1")
        self.assertEqual(opening_response.json()["task_id"], "relay-2")
        self.assertEqual([item["task_type"] for item in submitted], ["narration", "opening"])
        self.assertEqual([item["function_key"] for item in submitted], ["narration", "opening"])
        self.assertIn(
            "Narrate:",
            submitted[0]["request_payload"]["messages"][0]["content"],
        )
        self.assertIn(
            "Narration source",
            submitted[1]["request_payload"]["messages"][0]["content"],
        )

        db = self.Session()
        try:
            episode = db.query(models.Episode).filter_by(id=episode_id).one()
            self.assertEqual(episode.content, "Narration source")
            self.assertTrue(episode.narration_converting)
            self.assertEqual(episode.narration_error, "")
            self.assertTrue(episode.opening_generating)
            self.assertEqual(episode.opening_error, "")
        finally:
            db.close()

    def test_storyboard_status_counts_json_shots(self):
        owner, _, episode_id = self._seed_episode()
        self.current_user = owner

        response = self.client.get(f"/api/episodes/{episode_id}/storyboard/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"generating": False, "error": "", "shots_count": 1},
        )


if __name__ == "__main__":
    unittest.main()
