import os
import sys
import unittest
from pathlib import Path

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


EXPECTED_SHOT_ROUTES = {
    ("POST", "/api/episodes/{episode_id}/shots"),
    ("GET", "/api/episodes/{episode_id}/shots"),
    ("GET", "/api/shots/{shot_id}/video-status-info"),
    ("PUT", "/api/shots/{shot_id}"),
    ("DELETE", "/api/shots/{shot_id}"),
    ("GET", "/api/shots/{shot_id}/extract-scene"),
    ("POST", "/api/shots/{shot_id}/duplicate"),
    ("POST", "/api/shots/{shot_id}/generate-sora-prompt"),
    ("POST", "/api/shots/{shot_id}/generate-large-shot-prompt"),
    ("POST", "/api/shots/{shot_id}/manual-sora-prompt"),
    ("GET", "/api/shots/{shot_id}/full-sora-prompt"),
    ("GET", "/api/shots/{shot_id}/videos"),
    ("PUT", "/api/shots/{shot_id}/thumbnail"),
    ("POST", "/api/shots/{shot_id}/generate-video"),
    ("GET", "/api/shots/{shot_id}/video-status"),
    ("GET", "/api/shots/{shot_id}/export"),
    ("POST", "/api/shots/{shot_id}/generate-storyboard-image"),
    ("POST", "/api/shots/{shot_id}/generate-detail-images"),
    ("GET", "/api/shots/{shot_id}/detail-images"),
    ("POST", "/api/shots/{shot_id}/first-frame-reference-image"),
    ("POST", "/api/shots/{shot_id}/scene-image"),
    ("POST", "/api/shots/{shot_id}/reprocess-video"),
}


class ShotRouteTests(unittest.TestCase):
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
        from api.routers import shots

        self.shots = shots
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

        def override_get_db():
            request_db = self.Session()
            try:
                yield request_db
            finally:
                request_db.close()

        self.app = FastAPI()
        self.app.include_router(shots.router)
        self.app.dependency_overrides[database.get_db] = override_get_db
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _auth_headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _seed_episode_with_users(self):
        db = self.Session()
        try:
            owner = models.User(username="owner", token="owner-token")
            other = models.User(username="other", token="other-token")
            db.add_all([owner, other])
            db.flush()
            script = models.Script(user_id=owner.id, name="Script")
            db.add(script)
            db.flush()
            episode = models.Episode(script_id=script.id, name="E01")
            db.add(episode)
            db.commit()
            return owner, other, episode
        finally:
            db.close()

    def _seed_shot(self, episode_id, **overrides):
        db = self.Session()
        try:
            shot = models.StoryboardShot(
                episode_id=episode_id,
                shot_number=overrides.pop("shot_number", 1),
                stable_id=overrides.pop("stable_id", "stable-shot"),
                variant_index=overrides.pop("variant_index", 0),
                selected_card_ids=overrides.pop("selected_card_ids", "[]"),
                selected_sound_card_ids=overrides.pop("selected_sound_card_ids", None),
                **overrides,
            )
            db.add(shot)
            db.commit()
            return shot
        finally:
            db.close()

    def test_router_registers_all_shot_routes(self):
        actual_routes = set()
        for route in self.shots.router.routes:
            for method in getattr(route, "methods", set()):
                if method in {"HEAD", "OPTIONS"}:
                    continue
                actual_routes.add((method, route.path))

        self.assertEqual(actual_routes, EXPECTED_SHOT_ROUTES)

    def test_create_shot_uses_episode_owner_and_returns_legacy_shape(self):
        owner, _, episode = self._seed_episode_with_users()

        response = self.client.post(
            f"/api/episodes/{episode.id}/shots",
            json={
                "shot_number": 1,
                "prompt_template": "template",
                "storyboard_video_prompt": "video",
                "storyboard_audio_prompt": "audio",
                "storyboard_dialogue": "dialogue",
                "sora_prompt": "sora",
                "selected_card_ids": [],
                "aspect_ratio": "16:9",
                "duration": 15,
            },
            headers=self._auth_headers(owner.token),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["episode_id"], episode.id)
        self.assertEqual(payload["shot_number"], 1)
        self.assertEqual(payload["variant_index"], 0)
        self.assertEqual(payload["sora_prompt"], "sora")
        self.assertEqual(payload["detail_images_status"], "idle")

    def test_create_shot_rejects_non_owner(self):
        _, other, episode = self._seed_episode_with_users()

        response = self.client.post(
            f"/api/episodes/{episode.id}/shots",
            json={"shot_number": 1, "selected_card_ids": []},
            headers=self._auth_headers(other.token),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "无权限"})

    def test_videos_route_preserves_owner_check_and_thumbnail_side_effect(self):
        owner, other, episode = self._seed_episode_with_users()
        shot = self._seed_shot(
            episode.id,
            video_path="https://cdn.example/video.mp4",
            thumbnail_video_path="",
        )

        blocked_response = self.client.get(
            f"/api/shots/{shot.id}/videos",
            headers=self._auth_headers(other.token),
        )

        self.assertEqual(blocked_response.status_code, 403)
        self.assertEqual(blocked_response.json(), {"detail": "无权限"})

        response = self.client.get(
            f"/api/shots/{shot.id}/videos",
            headers=self._auth_headers(owner.token),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["video_path"], "https://cdn.example/video.mp4")

        db = self.Session()
        try:
            updated = db.query(models.StoryboardShot).filter_by(id=shot.id).one()
            self.assertEqual(updated.thumbnail_video_path, "https://cdn.example/video.mp4")
            self.assertEqual(db.query(models.ShotVideo).filter_by(shot_id=shot.id).count(), 1)
        finally:
            db.close()

    def test_video_status_route_keeps_legacy_no_auth_behavior(self):
        _, _, episode = self._seed_episode_with_users()
        shot = self._seed_shot(
            episode.id,
            video_status="completed",
            video_path="https://cdn.example/video.mp4",
            task_id="task-1",
        )

        response = self.client.get(f"/api/shots/{shot.id}/video-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "completed",
                "video_path": "https://cdn.example/video.mp4",
                "task_id": "task-1",
            },
        )


if __name__ == "__main__":
    unittest.main()
