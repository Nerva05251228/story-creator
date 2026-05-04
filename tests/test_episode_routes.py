import os
import sys
import unittest
from datetime import datetime, timedelta
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
from api.routers import episodes, managed_generation, storyboard2, storyboard_excel  # noqa: E402
from api.schemas.episodes import (  # noqa: E402
    BatchGenerateSoraPromptsRequest,
    EpisodeCreate,
    ManagedSessionStatusResponse,
    Storyboard2GenerateImagesRequest,
    Storyboard2GenerateVideoRequest,
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
    ("POST", "/api/episodes/{episode_id}/generate-detailed-storyboard"),
    ("POST", "/api/episodes/{episode_id}/analyze-storyboard"),
    ("GET", "/api/episodes/{episode_id}/detailed-storyboard"),
    ("GET", "/api/episodes/{episode_id}/storyboard"),
    ("GET", "/api/episodes/{episode_id}/storyboard/status"),
    ("PUT", "/api/episodes/{episode_id}/storyboard"),
    ("POST", "/api/episodes/{episode_id}/create-from-storyboard"),
    ("POST", "/api/episodes/{episode_id}/batch-generate-sora-prompts"),
    ("POST", "/api/episodes/{episode_id}/batch-generate-sora-videos"),
    ("POST", "/api/episodes/{episode_id}/start-managed-generation"),
    ("POST", "/api/episodes/{episode_id}/refresh-videos"),
    ("GET", "/api/episodes/{episode_id}/export-all"),
}

EXPECTED_MANAGED_GENERATION_ROUTES = {
    ("POST", "/api/episodes/{episode_id}/stop-managed-generation"),
    ("GET", "/api/managed-sessions/{session_id}/tasks"),
    ("GET", "/api/episodes/{episode_id}/managed-session-status"),
}

EXPECTED_STORYBOARD2_ROUTES = {
    ("GET", "/api/episodes/{episode_id}/storyboard2"),
    ("POST", "/api/episodes/{episode_id}/storyboard2/batch-generate-sora-prompts"),
    ("PATCH", "/api/storyboard2/shots/{storyboard2_shot_id}"),
    ("PATCH", "/api/storyboard2/subshots/{sub_shot_id}"),
    ("POST", "/api/storyboard2/subshots/{sub_shot_id}/generate-images"),
    ("POST", "/api/storyboard2/subshots/{sub_shot_id}/generate-video"),
    ("PATCH", "/api/storyboard2/subshots/{sub_shot_id}/current-image"),
    ("DELETE", "/api/storyboard2/images/{image_id}"),
    ("DELETE", "/api/storyboard2/videos/{video_id}"),
}

EXPECTED_STORYBOARD_EXCEL_ROUTES = {
    ("POST", "/api/episodes/{episode_id}/import-storyboard"),
    ("GET", "/api/episodes/{episode_id}/export-storyboard"),
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
        self.app.include_router(managed_generation.router)
        self.app.include_router(storyboard2.router)
        self.app.include_router(storyboard_excel.router)
        self.app.dependency_overrides[database.get_db] = override_get_db
        self.app.dependency_overrides[episodes.get_current_user] = override_get_current_user
        self.app.dependency_overrides[managed_generation.get_current_user] = override_get_current_user
        self.app.dependency_overrides[storyboard2.get_current_user] = override_get_current_user
        self.app.dependency_overrides[storyboard_excel.get_current_user] = override_get_current_user
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

    def _seed_managed_session_with_tasks(self):
        db = self.Session()
        try:
            owner = models.User(username="owner", token="owner-token")
            other = models.User(username="other", token="other-token")
            db.add_all([owner, other])
            db.flush()
            script = models.Script(user_id=owner.id, name="Script")
            db.add(script)
            db.flush()
            episode = models.Episode(script_id=script.id, name="Episode 1")
            db.add(episode)
            db.flush()
            original_a = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=7,
                stable_id="stable-a",
                variant_index=0,
            )
            variant_a = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=9,
                stable_id="stable-a",
                variant_index=2,
            )
            original_b = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=3,
                stable_id="stable-b",
                variant_index=0,
            )
            variant_b = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=4,
                stable_id="stable-b",
                variant_index=1,
            )
            db.add_all([original_a, variant_a, original_b, variant_b])
            db.flush()
            session = models.ManagedSession(
                episode_id=episode.id,
                status="running",
                total_shots=2,
                completed_shots=1,
            )
            db.add(session)
            db.flush()
            created_at = datetime(2026, 5, 2, 10, 0, 0)
            pending_task = models.ManagedTask(
                session_id=session.id,
                shot_id=variant_a.id,
                shot_stable_id="stable-a",
                video_path="",
                status="pending",
                error_message="",
                task_id="task-pending",
                prompt_text="prompt pending",
                created_at=created_at + timedelta(minutes=5),
            )
            completed_task = models.ManagedTask(
                session_id=session.id,
                shot_id=variant_b.id,
                shot_stable_id="stable-b",
                video_path="video.mp4",
                status="completed",
                error_message="",
                task_id="task-completed",
                prompt_text="prompt completed",
                created_at=created_at,
                completed_at=created_at + timedelta(minutes=10),
            )
            db.add_all([pending_task, completed_task])
            db.commit()
            return owner, other, session.id
        finally:
            db.close()

    def _seed_storyboard2_edit_fixture(self):
        db = self.Session()
        try:
            owner = models.User(username="owner", token="owner-token")
            other = models.User(username="other", token="other-token")
            db.add_all([owner, other])
            db.flush()
            script = models.Script(user_id=owner.id, name="Script")
            db.add(script)
            db.flush()
            episode = models.Episode(script_id=script.id, name="Episode 1")
            db.add(episode)
            db.flush()
            library = models.StoryLibrary(
                user_id=owner.id,
                episode_id=episode.id,
                name="Episode Library",
            )
            db.add(library)
            db.flush()
            scene_card = models.SubjectCard(
                library_id=library.id,
                name="Garden",
                card_type="场景",
                ai_prompt="生成图片中场景的是：green garden",
            )
            db.add(scene_card)
            db.flush()
            storyboard2_shot = models.Storyboard2Shot(
                episode_id=episode.id,
                shot_number=1,
                excerpt="old excerpt",
                selected_card_ids="[]",
            )
            db.add(storyboard2_shot)
            db.flush()
            sub_shot = models.Storyboard2SubShot(
                storyboard2_shot_id=storyboard2_shot.id,
                sub_shot_index=1,
                sora_prompt="old prompt",
                scene_override="",
                selected_card_ids="[]",
            )
            referencing_sub_shot = models.Storyboard2SubShot(
                storyboard2_shot_id=storyboard2_shot.id,
                sub_shot_index=2,
                sora_prompt="other prompt",
                scene_override="",
                selected_card_ids="[]",
            )
            db.add_all([sub_shot, referencing_sub_shot])
            db.flush()
            image_to_delete = models.Storyboard2SubShotImage(
                sub_shot_id=sub_shot.id,
                image_url="https://cdn.example.test/image-a.png",
                size="9:16",
            )
            current_image = models.Storyboard2SubShotImage(
                sub_shot_id=sub_shot.id,
                image_url="https://cdn.example.test/image-b.png",
                size="9:16",
            )
            db.add_all([image_to_delete, current_image])
            db.flush()
            referencing_sub_shot.current_image_id = image_to_delete.id
            video = models.Storyboard2SubShotVideo(
                sub_shot_id=sub_shot.id,
                task_id="video-task-1",
                status="completed",
                video_url="https://cdn.example.test/video.mp4",
            )
            db.add(video)
            db.commit()
            return {
                "owner": owner,
                "other": other,
                "storyboard2_shot_id": storyboard2_shot.id,
                "sub_shot_id": sub_shot.id,
                "referencing_sub_shot_id": referencing_sub_shot.id,
                "scene_card_id": scene_card.id,
                "image_to_delete_id": image_to_delete.id,
                "current_image_id": current_image.id,
                "video_id": video.id,
            }
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

    def test_managed_generation_router_owns_managed_generation_routes(self):
        registered = set()
        for route in managed_generation.router.routes:
            methods = getattr(route, "methods", set()) or set()
            path = getattr(route, "path", "")
            for method in methods:
                if method not in {"HEAD", "OPTIONS"}:
                    registered.add((method, path))

        self.assertEqual(registered, EXPECTED_MANAGED_GENERATION_ROUTES)

    def test_storyboard2_router_owns_storyboard2_routes(self):
        registered = set()
        for route in storyboard2.router.routes:
            methods = getattr(route, "methods", set()) or set()
            path = getattr(route, "path", "")
            for method in methods:
                if method not in {"HEAD", "OPTIONS"}:
                    registered.add((method, path))

        self.assertEqual(registered, EXPECTED_STORYBOARD2_ROUTES)

    def test_storyboard_excel_router_owns_storyboard_excel_routes(self):
        registered = set()
        for route in storyboard_excel.router.routes:
            methods = getattr(route, "methods", set()) or set()
            path = getattr(route, "path", "")
            for method in methods:
                if method not in {"HEAD", "OPTIONS"}:
                    registered.add((method, path))

        self.assertEqual(registered, EXPECTED_STORYBOARD_EXCEL_ROUTES)

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
        self.assertEqual(Storyboard2GenerateImagesRequest().size, "9:16")
        self.assertEqual(Storyboard2GenerateImagesRequest().timeout_seconds, 420)
        self.assertEqual(Storyboard2GenerateVideoRequest().model, "grok")

    def test_get_managed_tasks_preserves_payload_ordering_and_status_filter(self):
        owner, _, session_id = self._seed_managed_session_with_tasks()
        self.current_user = owner

        response = self.client.get(f"/api/managed-sessions/{session_id}/tasks")
        self.assertEqual(response.status_code, 200)
        tasks = response.json()

        self.assertEqual([task["task_id"] for task in tasks], ["task-completed", "task-pending"])
        self.assertEqual(tasks[0]["prompt_text"], "prompt completed")
        self.assertEqual(tasks[0]["shot_number"], 4)
        self.assertEqual(tasks[0]["variant_index"], 1)
        self.assertEqual(tasks[0]["original_shot_number"], 3)
        self.assertEqual(tasks[1]["prompt_text"], "prompt pending")
        self.assertEqual(tasks[1]["shot_number"], 9)
        self.assertEqual(tasks[1]["variant_index"], 2)
        self.assertEqual(tasks[1]["original_shot_number"], 7)

        filtered_response = self.client.get(
            f"/api/managed-sessions/{session_id}/tasks?status_filter=completed"
        )
        self.assertEqual(filtered_response.status_code, 200)
        self.assertEqual(
            [task["task_id"] for task in filtered_response.json()],
            ["task-completed"],
        )

    def test_get_managed_tasks_preserves_missing_session_and_owner_errors(self):
        owner, other, session_id = self._seed_managed_session_with_tasks()

        self.current_user = owner
        missing_response = self.client.get("/api/managed-sessions/999/tasks")
        self.assertEqual(missing_response.status_code, 404)

        self.current_user = other
        forbidden_response = self.client.get(f"/api/managed-sessions/{session_id}/tasks")
        self.assertEqual(forbidden_response.status_code, 403)

    def test_start_managed_generation_keeps_dashboard_task_sync_available(self):
        owner, _, episode_id = self._seed_episode()
        self.current_user = owner

        with patch.object(episodes, "sync_managed_task_to_dashboard") as sync_mock:
            response = self.client.post(
                f"/api/episodes/{episode_id}/start-managed-generation",
                json={"variant_count": 1},
            )

        self.assertEqual(response.status_code, 200)
        sync_mock.assert_called_once()

    def test_storyboard2_edit_routes_preserve_update_payloads_and_owner_checks(self):
        fixture = self._seed_storyboard2_edit_fixture()
        self.current_user = fixture["owner"]

        shot_response = self.client.patch(
            f"/api/storyboard2/shots/{fixture['storyboard2_shot_id']}",
            json={
                "excerpt": "  New excerpt  ",
                "selected_card_ids": [fixture["scene_card_id"], fixture["scene_card_id"], 0],
            },
        )
        self.assertEqual(shot_response.status_code, 200)
        self.assertEqual(shot_response.json()["excerpt"], "New excerpt")
        self.assertEqual(shot_response.json()["selected_card_ids"], [fixture["scene_card_id"]])

        sub_shot_response = self.client.patch(
            f"/api/storyboard2/subshots/{fixture['sub_shot_id']}",
            json={
                "sora_prompt": "  new sora prompt  ",
                "scene_override": "  manual scene  ",
                "selected_card_ids": [fixture["scene_card_id"]],
            },
        )
        self.assertEqual(sub_shot_response.status_code, 200)
        self.assertEqual(sub_shot_response.json()["sora_prompt"], "new sora prompt")
        self.assertEqual(sub_shot_response.json()["scene_override"], "manual scene")
        self.assertEqual(sub_shot_response.json()["scene_override_locked"], True)
        self.assertEqual(sub_shot_response.json()["selected_card_ids"], [fixture["scene_card_id"]])

        self.current_user = fixture["other"]
        forbidden_response = self.client.patch(
            f"/api/storyboard2/shots/{fixture['storyboard2_shot_id']}",
            json={"excerpt": "nope"},
        )
        self.assertEqual(forbidden_response.status_code, 403)

    def test_storyboard2_media_edit_delete_routes_preserve_payloads(self):
        fixture = self._seed_storyboard2_edit_fixture()
        self.current_user = fixture["owner"]

        current_image_response = self.client.patch(
            f"/api/storyboard2/subshots/{fixture['sub_shot_id']}/current-image",
            json={"current_image_id": fixture["current_image_id"]},
        )
        self.assertEqual(current_image_response.status_code, 200)
        self.assertEqual(
            current_image_response.json()["current_image_id"],
            fixture["current_image_id"],
        )

        first_video_delete = self.client.delete(
            f"/api/storyboard2/videos/{fixture['video_id']}"
        )
        self.assertEqual(first_video_delete.status_code, 200)
        self.assertEqual(first_video_delete.json()["video_id"], fixture["video_id"])

        second_video_delete = self.client.delete(
            f"/api/storyboard2/videos/{fixture['video_id']}"
        )
        self.assertEqual(second_video_delete.status_code, 200)
        self.assertEqual(second_video_delete.json()["message"], "视频已删除")

        image_delete_response = self.client.delete(
            f"/api/storyboard2/images/{fixture['image_to_delete_id']}"
        )
        self.assertEqual(image_delete_response.status_code, 200)
        self.assertEqual(image_delete_response.json()["image_id"], fixture["image_to_delete_id"])
        self.assertEqual(image_delete_response.json()["cleared_current_count"], 1)

        db = self.Session()
        try:
            self.assertIsNone(
                db.query(models.Storyboard2SubShotImage).filter(
                    models.Storyboard2SubShotImage.id == fixture["image_to_delete_id"]
                ).first()
            )
            referencing_sub_shot = db.query(models.Storyboard2SubShot).filter(
                models.Storyboard2SubShot.id == fixture["referencing_sub_shot_id"]
            ).first()
            self.assertIsNone(referencing_sub_shot.current_image_id)
        finally:
            db.close()

    def test_storyboard2_generate_images_route_marks_processing_without_worker_side_effects(self):
        fixture = self._seed_storyboard2_edit_fixture()
        self.current_user = fixture["owner"]
        created_threads = []

        class FakeThread:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                self.daemon = False
                created_threads.append(self)

            def start(self):
                return None

        with patch.object(
            storyboard2,
            "_build_image_generation_debug_meta",
            return_value={"actual_model": "actual-image-model", "provider": "mock-provider"},
            create=True,
        ), patch.object(
            storyboard2,
            "_get_optional_prompt_config_content",
            return_value="image prefix",
            create=True,
        ), patch.object(
            storyboard2,
            "_collect_storyboard2_reference_images",
            return_value=["https://cdn.example.test/reference.png"],
            create=True,
        ), patch.object(
            storyboard2,
            "_save_storyboard2_image_debug",
            create=True,
        ), patch.object(
            storyboard2,
            "Thread",
            FakeThread,
            create=True,
        ):
            response = self.client.post(
                f"/api/storyboard2/subshots/{fixture['sub_shot_id']}/generate-images",
                json={"requirement": "  extra detail  ", "style": "soft light"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["sub_shot_id"], fixture["sub_shot_id"])
        self.assertEqual(payload["status"], "processing")
        self.assertEqual(payload["progress"], "1/4")
        self.assertEqual(len(created_threads), 1)

        db = self.Session()
        try:
            sub_shot = db.query(models.Storyboard2SubShot).filter(
                models.Storyboard2SubShot.id == fixture["sub_shot_id"]
            ).one()
            self.assertEqual(sub_shot.image_generate_status, "processing")
            self.assertEqual(sub_shot.image_generate_progress, "1/4")
        finally:
            db.close()

    def test_storyboard2_generate_video_route_submits_task_and_reuses_active_task(self):
        fixture = self._seed_storyboard2_edit_fixture()
        self.current_user = fixture["owner"]

        db = self.Session()
        try:
            db.query(models.Storyboard2SubShotVideo).filter(
                models.Storyboard2SubShotVideo.id == fixture["video_id"]
            ).delete()
            db.commit()
        finally:
            db.close()

        class FakeVideoResponse:
            status_code = 200
            text = '{"task_id":"storyboard2-video-task"}'

            def json(self):
                return {
                    "task_id": "storyboard2-video-task",
                    "status": "pending",
                    "progress": 12,
                }

        started_pollers = []

        class FakeThread:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                self.daemon = False
                started_pollers.append(self)

            def start(self):
                return None

        with patch.object(
            storyboard2.requests,
            "post",
            return_value=FakeVideoResponse(),
        ), patch.object(
            storyboard2,
            "get_video_task_create_url",
            return_value="https://video.example.test/create",
            create=True,
        ), patch.object(
            storyboard2,
            "get_video_api_headers",
            return_value={"Authorization": "Bearer test"},
        ), patch.object(
            storyboard2,
            "_save_storyboard2_video_debug",
            create=True,
        ), patch.object(
            storyboard2,
            "_record_storyboard2_video_charge",
        ) as record_video_charge, patch.object(
            storyboard2,
            "Thread",
            FakeThread,
            create=True,
        ):
            response = self.client.post(
                f"/api/storyboard2/subshots/{fixture['sub_shot_id']}/generate-video",
                json={"duration": 6, "aspect_ratio": "9:16", "resolution_name": "720p"},
            )
            second_response = self.client.post(
                f"/api/storyboard2/subshots/{fixture['sub_shot_id']}/generate-video",
                json={"duration": 6, "aspect_ratio": "9:16", "resolution_name": "720p"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["sub_shot_id"], fixture["sub_shot_id"])
        self.assertEqual(payload["task_id"], "storyboard2-video-task")
        self.assertEqual(payload["status"], "processing")
        self.assertEqual(payload["progress"], 12)
        self.assertEqual(len(started_pollers), 1)
        record_video_charge.assert_called_once()
        charge_kwargs = record_video_charge.call_args.kwargs
        self.assertEqual(charge_kwargs["task_id"], "storyboard2-video-task")
        self.assertEqual(charge_kwargs["model_name"], "grok")
        self.assertEqual(charge_kwargs["duration"], 6)
        self.assertEqual(charge_kwargs["detail_payload"]["aspect_ratio"], "9:16")
        self.assertEqual(charge_kwargs["detail_payload"]["resolution_name"], "720p")

        self.assertEqual(second_response.status_code, 200)
        second_payload = second_response.json()
        self.assertEqual(second_payload["task_id"], "storyboard2-video-task")
        self.assertEqual(second_payload["status"], "processing")

        db = self.Session()
        try:
            video = db.query(models.Storyboard2SubShotVideo).filter(
                models.Storyboard2SubShotVideo.task_id == "storyboard2-video-task"
            ).one()
            self.assertEqual(video.status, "pending")
            self.assertEqual(video.progress, 12)
        finally:
            db.close()

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
