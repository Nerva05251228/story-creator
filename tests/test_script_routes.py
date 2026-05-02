import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

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
import main  # noqa: E402
import models  # noqa: E402


class ScriptRouteTests(unittest.TestCase):
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

        def override_get_db():
            request_db = self.Session()
            try:
                yield request_db
            finally:
                request_db.close()

        main.app.dependency_overrides[database.get_db] = override_get_db
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def tearDown(self):
        main.app.dependency_overrides.pop(database.get_db, None)
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _auth_headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _seed_users(self):
        db = self.Session()
        try:
            owner = models.User(username="owner", token="owner-token")
            other = models.User(username="other", token="other-token")
            target = models.User(username="target", token="target-token")
            db.add_all([owner, other, target])
            db.commit()
            return owner.id, other.id, target.id
        finally:
            db.close()

    def _seed_script(self, user_id, **overrides):
        values = {
            "user_id": user_id,
            "name": "Script Alpha",
            "sora_prompt_style": "sora style",
            "video_prompt_template": "video template",
            "style_template": "style template",
            "narration_template": "narration template",
            "voiceover_shared_data": "voice data",
            "created_at": datetime(2026, 1, 1, 8, 0, 0),
        }
        values.update(overrides)
        db = self.Session()
        try:
            row = models.Script(**values)
            db.add(row)
            db.commit()
            return row.id
        finally:
            db.close()

    def test_script_crud_uses_authenticated_owner_and_preserves_response_shape(self):
        owner_id, other_id, _ = self._seed_users()
        older_id = self._seed_script(
            owner_id,
            name="Older",
            created_at=datetime(2026, 1, 1, 8, 0, 0),
        )
        newer_id = self._seed_script(
            owner_id,
            name="Newer",
            created_at=datetime(2026, 1, 2, 8, 0, 0),
        )
        other_script_id = self._seed_script(other_id, name="Other")

        create_response = self.client.post(
            "/api/scripts",
            json={
                "name": "Created",
                "video_prompt_template": "video",
                "style_template": "style",
            },
            headers=self._auth_headers("owner-token"),
        )
        list_response = self.client.get(
            "/api/scripts/my",
            headers=self._auth_headers("owner-token"),
        )
        get_response = self.client.get(
            f"/api/scripts/{newer_id}",
            headers=self._auth_headers("owner-token"),
        )
        update_response = self.client.put(
            f"/api/scripts/{older_id}",
            json={
                "name": "Updated",
                "sora_prompt_style": "updated sora",
                "video_prompt_template": "updated video",
                "style_template": "updated style",
                "narration_template": "updated narration",
            },
            headers=self._auth_headers("owner-token"),
        )
        missing_response = self.client.get(
            "/api/scripts/999999",
            headers=self._auth_headers("owner-token"),
        )
        forbidden_response = self.client.get(
            f"/api/scripts/{other_script_id}",
            headers=self._auth_headers("owner-token"),
        )

        self.assertEqual(create_response.status_code, 200)
        created = create_response.json()
        self.assertEqual(created["user_id"], owner_id)
        self.assertEqual(created["name"], "Created")
        self.assertEqual(
            set(created),
            {
                "id",
                "user_id",
                "name",
                "sora_prompt_style",
                "video_prompt_template",
                "style_template",
                "narration_template",
                "created_at",
            },
        )

        self.assertEqual(list_response.status_code, 200)
        names = [item["name"] for item in list_response.json()]
        self.assertEqual(names[:3], ["Created", "Newer", "Older"])
        self.assertNotIn("Other", names)

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json()["id"], newer_id)

        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["name"], "Updated")
        self.assertEqual(update_response.json()["sora_prompt_style"], "updated sora")
        self.assertEqual(missing_response.status_code, 404)
        self.assertEqual(forbidden_response.status_code, 403)

    def test_delete_script_cleans_non_cascaded_episode_dependencies(self):
        owner_id, _, _ = self._seed_users()
        script_id = self._seed_script(owner_id)

        db = self.Session()
        try:
            episode = models.Episode(script_id=script_id, name="Episode One")
            db.add(episode)
            db.flush()
            library = models.StoryLibrary(
                user_id=owner_id,
                episode_id=episode.id,
                name="Episode Library",
            )
            session = models.ManagedSession(
                episode_id=episode.id,
                total_shots=1,
                completed_shots=0,
                status="running",
            )
            db.add_all([library, session])
            db.flush()
            db.add_all([
                models.SimpleStoryboardBatch(
                    episode_id=episode.id,
                    batch_index=1,
                    total_batches=1,
                ),
                models.ManagedTask(
                    session_id=session.id,
                    shot_id=0,
                    shot_stable_id="legacy-slot",
                    status="pending",
                ),
                models.VoiceoverTtsTask(
                    episode_id=episode.id,
                    line_id="line-1",
                    status="completed",
                    request_json="{}",
                    result_json="{}",
                ),
            ])
            db.commit()
            library_id = library.id
        finally:
            db.close()

        response = self.client.delete(
            f"/api/scripts/{script_id}",
            headers=self._auth_headers("owner-token"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["script_id"], script_id)

        verify_db = self.Session()
        try:
            self.assertIsNone(
                verify_db.query(models.Script).filter_by(id=script_id).first()
            )
            self.assertEqual(verify_db.query(models.SimpleStoryboardBatch).count(), 0)
            self.assertEqual(verify_db.query(models.ManagedTask).count(), 0)
            self.assertEqual(verify_db.query(models.ManagedSession).count(), 0)
            self.assertEqual(verify_db.query(models.VoiceoverTtsTask).count(), 0)
            self.assertIsNone(
                verify_db.query(models.StoryLibrary)
                .filter_by(id=library_id)
                .one()
                .episode_id
            )
        finally:
            verify_db.close()

    def test_copy_script_rejects_invalid_requests_and_deep_copies_basic_graph(self):
        owner_id, other_id, target_id = self._seed_users()
        script_id = self._seed_script(owner_id, name="Source Script")
        other_script_id = self._seed_script(other_id, name="Other Script")

        db = self.Session()
        try:
            episode = models.Episode(
                script_id=script_id,
                name="Episode One",
                content="episode content",
                storyboard_data=json.dumps({"shots": []}),
            )
            db.add(episode)
            db.flush()
            library = models.StoryLibrary(
                user_id=owner_id,
                episode_id=episode.id,
                name="Source Library",
                description="source desc",
            )
            db.add(library)
            db.flush()
            card = models.SubjectCard(
                library_id=library.id,
                name="Hero",
                alias="Lead",
                card_type="character",
                ai_prompt="prompt",
            )
            db.add(card)
            db.flush()
            db.add_all([
                models.CardImage(card_id=card.id, image_path="cdn://card.png", order=2),
                models.GeneratedImage(
                    card_id=card.id,
                    image_path="cdn://generated.png",
                    model_name="seedream",
                    is_reference=True,
                    task_id="task-source",
                    status="completed",
                ),
                models.SubjectCardAudio(
                    card_id=card.id,
                    audio_path="cdn://voice.wav",
                    file_name="voice.wav",
                    duration_seconds=1.25,
                    is_reference=True,
                ),
            ])
            shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                selected_card_ids=json.dumps([card.id]),
                sora_prompt="shot prompt",
            )
            db.add(shot)
            db.flush()
            db.add(models.ShotVideo(shot_id=shot.id, video_path="cdn://video.mp4"))
            episode.storyboard_data = json.dumps({"shots": [{"id": shot.id}]})
            db.commit()
            old_card_id = card.id
            old_shot_id = shot.id
        finally:
            db.close()

        empty_response = self.client.post(
            f"/api/scripts/{script_id}/copy",
            json={"user_ids": []},
            headers=self._auth_headers("owner-token"),
        )
        missing_user_response = self.client.post(
            f"/api/scripts/{script_id}/copy",
            json={"user_ids": [999999]},
            headers=self._auth_headers("owner-token"),
        )
        forbidden_response = self.client.post(
            f"/api/scripts/{other_script_id}/copy",
            json={"user_ids": [target_id]},
            headers=self._auth_headers("owner-token"),
        )
        copy_response = self.client.post(
            f"/api/scripts/{script_id}/copy",
            json={"user_ids": [target_id]},
            headers=self._auth_headers("owner-token"),
        )

        self.assertEqual(empty_response.status_code, 400)
        self.assertEqual(missing_user_response.status_code, 400)
        self.assertEqual(forbidden_response.status_code, 403)
        self.assertEqual(copy_response.status_code, 200)
        self.assertEqual(copy_response.json()["success_count"], 1)
        self.assertEqual(copy_response.json()["failed_count"], 0)

        verify_db = self.Session()
        try:
            copied_script = verify_db.query(models.Script).filter(
                models.Script.user_id == target_id,
                models.Script.name == "Source Script",
            ).one()
            copied_episode = verify_db.query(models.Episode).filter(
                models.Episode.script_id == copied_script.id,
            ).one()
            copied_library = verify_db.query(models.StoryLibrary).filter(
                models.StoryLibrary.episode_id == copied_episode.id,
            ).one()
            copied_card = verify_db.query(models.SubjectCard).filter(
                models.SubjectCard.library_id == copied_library.id,
            ).one()
            copied_shot = verify_db.query(models.StoryboardShot).filter(
                models.StoryboardShot.episode_id == copied_episode.id,
            ).one()

            self.assertNotEqual(copied_card.id, old_card_id)
            self.assertNotEqual(copied_shot.id, old_shot_id)
            self.assertEqual(json.loads(copied_shot.selected_card_ids), [copied_card.id])
            self.assertEqual(json.loads(copied_episode.storyboard_data)["shots"][0]["id"], copied_shot.id)
            self.assertEqual(len(copied_card.images), 1)
            self.assertEqual(len(copied_card.generated_images), 1)
            self.assertEqual(copied_card.generated_images[0].task_id, "")
            self.assertEqual(len(copied_card.audios), 1)
            self.assertEqual(len(copied_shot.videos), 1)
        finally:
            verify_db.close()


if __name__ == "__main__":
    unittest.main()
