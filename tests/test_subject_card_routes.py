import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
from api.routers import subject_cards  # noqa: E402


class SubjectCardRouteTests(unittest.TestCase):
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
            db.add_all([owner, other])
            db.commit()
            return owner, other
        finally:
            db.close()

    def _seed_library(self, user_id, name="Library", episode_id=None):
        db = self.Session()
        try:
            library = models.StoryLibrary(
                user_id=user_id,
                name=name,
                episode_id=episode_id,
            )
            db.add(library)
            db.commit()
            return library
        finally:
            db.close()

    def _seed_script_episode_library(self, user_id):
        db = self.Session()
        try:
            script = models.Script(user_id=user_id, name="Script")
            db.add(script)
            db.flush()
            episode = models.Episode(
                script_id=script.id,
                name="Episode 1",
                content="Episode content",
            )
            db.add(episode)
            db.flush()
            library = models.StoryLibrary(
                user_id=user_id,
                episode_id=episode.id,
                name="Library",
            )
            db.add(library)
            db.commit()
            return script, episode, library
        finally:
            db.close()

    def _seed_card(
        self,
        library_id,
        name,
        card_type="角色",
        alias="",
        ai_prompt="",
        role_personality="",
        linked_card_id=None,
        is_protagonist=False,
        protagonist_gender="",
        ai_prompt_status=None,
    ):
        db = self.Session()
        try:
            card = models.SubjectCard(
                library_id=library_id,
                name=name,
                alias=alias,
                card_type=card_type,
                ai_prompt=ai_prompt,
                role_personality=role_personality,
                linked_card_id=linked_card_id,
                is_protagonist=is_protagonist,
                protagonist_gender=protagonist_gender,
                ai_prompt_status=ai_prompt_status,
            )
            db.add(card)
            db.commit()
            return card
        finally:
            db.close()

    def test_owner_can_create_role_card_with_default_response_fields(self):
        owner, _ = self._seed_users()
        library = self._seed_library(owner.id)

        response = self.client.post(
            f"/api/libraries/{library.id}/cards",
            json={"name": " Hero ", "alias": "Lead", "card_type": "角色"},
            headers=self._auth_headers(owner.token),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsInstance(payload["id"], int)
        self.assertEqual(payload["library_id"], library.id)
        self.assertEqual(payload["name"], "Hero")
        self.assertEqual(payload["alias"], "Lead")
        self.assertEqual(payload["card_type"], "角色")
        self.assertIsNone(payload["linked_card_id"])
        self.assertEqual(payload["ai_prompt"], "")
        self.assertEqual(payload["role_personality"], "")
        self.assertFalse(payload["is_protagonist"])
        self.assertEqual(payload["protagonist_gender"], "")
        self.assertFalse(payload["is_generating_images"])
        self.assertEqual(payload["generating_count"], 0)
        self.assertEqual(payload["images"], [])
        self.assertEqual(payload["audios"], [])
        self.assertEqual(payload["generated_images"], [])

    def test_public_list_excludes_sound_cards_unless_requested(self):
        owner, _ = self._seed_users()
        library = self._seed_library(owner.id)
        self._seed_card(library.id, "Role", "角色")
        self._seed_card(library.id, "Scene", "场景")
        self._seed_card(library.id, "Prop", "道具")
        self._seed_card(library.id, "Voice", "声音")

        default_response = self.client.get(f"/api/libraries/{library.id}/cards")
        include_sound_response = self.client.get(
            f"/api/libraries/{library.id}/cards?include_sound=true"
        )

        self.assertEqual(default_response.status_code, 200)
        self.assertEqual(
            [item["card_type"] for item in default_response.json()],
            ["角色", "场景", "道具"],
        )
        self.assertEqual(include_sound_response.status_code, 200)
        self.assertEqual(
            [item["card_type"] for item in include_sound_response.json()],
            ["角色", "场景", "道具", "声音"],
        )

    def test_update_role_validates_gender_persists_personality_and_blocks_non_owner(self):
        owner, other = self._seed_users()
        library = self._seed_library(owner.id)
        card = self._seed_card(library.id, "Lead", role_personality="Original")

        invalid_response = self.client.put(
            f"/api/cards/{card.id}",
            json={"protagonist_gender": "unknown"},
            headers=self._auth_headers(owner.token),
        )

        self.assertEqual(invalid_response.status_code, 400)
        self.assertEqual(
            invalid_response.json(),
            {"detail": "主角性别仅支持 male/female"},
        )

        update_response = self.client.put(
            f"/api/cards/{card.id}",
            json={
                "role_personality": " Calm and direct ",
                "is_protagonist": True,
                "protagonist_gender": "FEMALE",
            },
            headers=self._auth_headers(owner.token),
        )

        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["role_personality"], "Calm and direct")
        self.assertTrue(update_response.json()["is_protagonist"])
        self.assertEqual(update_response.json()["protagonist_gender"], "female")

        blocked_response = self.client.put(
            f"/api/cards/{card.id}",
            json={
                "name": "Changed by other",
                "role_personality": "Changed",
                "protagonist_gender": "male",
            },
            headers=self._auth_headers(other.token),
        )

        self.assertEqual(blocked_response.status_code, 403)
        db = self.Session()
        try:
            unchanged = db.query(models.SubjectCard).filter_by(id=card.id).one()
            self.assertEqual(unchanged.name, "Lead")
            self.assertEqual(unchanged.role_personality, "Calm and direct")
            self.assertTrue(unchanged.is_protagonist)
            self.assertEqual(unchanged.protagonist_gender, "female")
        finally:
            db.close()

    def test_delete_role_removes_local_image_file_and_unlinks_same_library_sound_cards(self):
        owner, other = self._seed_users()
        library = self._seed_library(owner.id)
        other_library = self._seed_library(other.id, name="Other")
        role = self._seed_card(library.id, "Lead")
        same_library_sound = self._seed_card(
            library.id,
            "Lead",
            card_type="声音",
            linked_card_id=role.id,
        )
        other_library_sound = self._seed_card(
            other_library.id,
            "Lead",
            card_type="声音",
            linked_card_id=role.id,
        )
        image_file = tempfile.NamedTemporaryFile(delete=False)
        image_path = image_file.name
        image_file.write(b"image")
        image_file.close()

        db = self.Session()
        try:
            db.add(models.CardImage(card_id=role.id, image_path=image_path))
            db.commit()
        finally:
            db.close()

        try:
            response = self.client.delete(
                f"/api/cards/{role.id}",
                headers=self._auth_headers(owner.token),
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"message": "Card deleted successfully"})
            self.assertFalse(os.path.exists(image_path))

            verify_db = self.Session()
            try:
                self.assertEqual(
                    verify_db.query(models.SubjectCard).filter_by(id=role.id).count(),
                    0,
                )
                self.assertIsNone(
                    verify_db.query(models.SubjectCard)
                    .filter_by(id=same_library_sound.id)
                    .one()
                    .linked_card_id
                )
                self.assertEqual(
                    verify_db.query(models.SubjectCard)
                    .filter_by(id=other_library_sound.id)
                    .one()
                    .linked_card_id,
                    role.id,
                )
            finally:
                verify_db.close()
        finally:
            if os.path.exists(image_path):
                os.remove(image_path)

    def test_get_card_requires_owner_and_returns_explicit_fields(self):
        owner, other = self._seed_users()
        library = self._seed_library(owner.id)
        card = self._seed_card(
            library.id,
            "Lead",
            alias="Hero",
            ai_prompt="visual prompt",
            role_personality="steady",
            is_protagonist=True,
            protagonist_gender="male",
            ai_prompt_status="completed",
        )

        blocked_response = self.client.get(
            f"/api/cards/{card.id}",
            headers=self._auth_headers(other.token),
        )
        owner_response = self.client.get(
            f"/api/cards/{card.id}",
            headers=self._auth_headers(owner.token),
        )

        self.assertEqual(blocked_response.status_code, 403)
        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(
            owner_response.json(),
            {
                "id": card.id,
                "name": "Lead",
                "card_type": "角色",
                "linked_card_id": None,
                "ai_prompt": "visual prompt",
                "role_personality": "steady",
                "alias": "Hero",
                "is_protagonist": True,
                "protagonist_gender": "male",
                "ai_prompt_status": "completed",
            },
        )

    def test_generate_ai_prompt_marks_card_generating_and_returns_task_id(self):
        owner, _ = self._seed_users()
        library = self._seed_library(owner.id)
        card = self._seed_card(library.id, "Lead")

        with patch.object(
            subject_cards,
            "_submit_subject_prompt_task",
            return_value=SimpleNamespace(external_task_id="relay-task-1"),
        ) as submit_task:
            response = self.client.post(
                f"/api/cards/{card.id}/generate-ai-prompt",
                headers=self._auth_headers(owner.token),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "message": "已开始生成AI提示词",
                "status": "generating",
                "task_id": "relay-task-1",
            },
        )
        self.assertEqual(submit_task.call_count, 1)

        db = self.Session()
        try:
            updated = db.query(models.SubjectCard).filter_by(id=card.id).one()
            self.assertEqual(updated.ai_prompt_status, "generating")
        finally:
            db.close()

    def test_batch_generate_prompts_submits_only_non_sound_cards_with_blank_prompts(self):
        owner, _ = self._seed_users()
        _, _, library = self._seed_script_episode_library(owner.id)
        role = self._seed_card(library.id, "Role", "角色", ai_prompt="")
        scene = self._seed_card(library.id, "Scene", "场景", ai_prompt="already done")
        prop = self._seed_card(library.id, "Prop", "道具", ai_prompt=None)
        sound = self._seed_card(library.id, "Sound", "声音", ai_prompt="")

        with patch.object(
            subject_cards,
            "_submit_subject_prompt_task",
            return_value=SimpleNamespace(external_task_id="relay-task"),
        ) as submit_task:
            response = self.client.post(
                f"/api/libraries/{library.id}/batch-generate-prompts",
                headers=self._auth_headers(owner.token),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["generated_count"], 2)
        self.assertEqual(response.json()["failed_count"], 0)
        self.assertEqual(response.json()["failed_cards"], [])
        self.assertEqual(
            [call.args[1].name for call in submit_task.call_args_list],
            ["Role", "Prop"],
        )

        db = self.Session()
        try:
            statuses = {
                card.name: card.ai_prompt_status
                for card in db.query(models.SubjectCard).filter(
                    models.SubjectCard.id.in_([role.id, scene.id, prop.id, sound.id])
                )
            }
            self.assertEqual(statuses["Role"], "generating")
            self.assertIsNone(statuses["Scene"])
            self.assertEqual(statuses["Prop"], "generating")
            self.assertIsNone(statuses["Sound"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
