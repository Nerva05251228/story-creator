import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
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


CARD_MEDIA_SERVICE = "api.services.card_media"
CARD_IMAGE_GENERATION_SERVICE = "api.services.card_image_generation"


class SubjectCardMediaRouteTests(unittest.TestCase):
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

    def _seed_library(self, user_id, name="Library"):
        db = self.Session()
        try:
            library = models.StoryLibrary(user_id=user_id, name=name)
            db.add(library)
            db.commit()
            return library
        finally:
            db.close()

    def _seed_card(self, library_id, name="Card", card_type="角色"):
        db = self.Session()
        try:
            card = models.SubjectCard(
                library_id=library_id,
                name=name,
                card_type=card_type,
            )
            db.add(card)
            db.commit()
            return card
        finally:
            db.close()

    def _seed_card_with_prompt(self, library_id, name="Card", card_type="瑙掕壊", ai_prompt=""):
        db = self.Session()
        try:
            card = models.SubjectCard(
                library_id=library_id,
                name=name,
                card_type=card_type,
                ai_prompt=ai_prompt,
            )
            db.add(card)
            db.commit()
            return card
        finally:
            db.close()

    def _seed_generated_image(
        self,
        card_id,
        image_path,
        model_name="seedream",
        is_reference=False,
        status="completed",
        created_at=None,
    ):
        db = self.Session()
        try:
            generated_image = models.GeneratedImage(
                card_id=card_id,
                image_path=image_path,
                model_name=model_name,
                is_reference=is_reference,
                status=status,
                created_at=created_at,
            )
            db.add(generated_image)
            db.commit()
            return generated_image
        finally:
            db.close()

    def _seed_card_image(self, card_id, image_path, order=0, created_at=None):
        db = self.Session()
        try:
            image = models.CardImage(
                card_id=card_id,
                image_path=image_path,
                order=order,
                created_at=created_at,
            )
            db.add(image)
            db.commit()
            return image
        finally:
            db.close()

    def _seed_audio(
        self,
        card_id,
        audio_path,
        file_name="audio.wav",
        duration_seconds=1.0,
        is_reference=False,
        created_at=None,
    ):
        db = self.Session()
        try:
            audio = models.SubjectCardAudio(
                card_id=card_id,
                audio_path=audio_path,
                file_name=file_name,
                duration_seconds=duration_seconds,
                is_reference=is_reference,
                created_at=created_at,
            )
            db.add(audio)
            db.commit()
            return audio
        finally:
            db.close()

    def _set_audio_created_at(self, audio_id, created_at):
        db = self.Session()
        try:
            audio = db.query(models.SubjectCardAudio).filter_by(id=audio_id).one()
            audio.created_at = created_at
            db.commit()
        finally:
            db.close()

    def test_generate_image_for_card_submits_task_and_creates_processing_row(self):
        owner, _ = self._seed_users()
        library = self._seed_library(owner.id)
        card = self._seed_card_with_prompt(
            library.id,
            "Hero",
            ai_prompt="hero visual prompt",
        )

        with patch(
            f"{CARD_IMAGE_GENERATION_SERVICE}.submit_image_generation",
            return_value="card-task-1",
        ) as submit_task, patch(
            f"{CARD_IMAGE_GENERATION_SERVICE}.save_ai_debug",
            return_value="debug-folder",
        ):
            response = self.client.post(
                f"/api/cards/{card.id}/generate-image",
                json={
                    "provider": "momo",
                    "model": "banana2",
                    "size": "1:1",
                    "resolution": "2K",
                    "n": 2,
                    "generation_mode": "default",
                },
                headers=self._auth_headers(owner.token),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["task_id"], "card-task-1")
        self.assertIsInstance(payload["generated_image_id"], int)
        self.assertEqual(submit_task.call_count, 1)

        db = self.Session()
        try:
            generated = (
                db.query(models.GeneratedImage)
                .filter_by(id=payload["generated_image_id"])
                .one()
            )
            updated_card = db.query(models.SubjectCard).filter_by(id=card.id).one()
            self.assertEqual(generated.card_id, card.id)
            self.assertEqual(generated.status, "processing")
            self.assertEqual(generated.task_id, "card-task-1")
            self.assertEqual(generated.model_name, "nano-banana-2")
            self.assertFalse(generated.is_reference)
            self.assertTrue(updated_card.is_generating_images)
            self.assertEqual(updated_card.generating_count, 2)
        finally:
            db.close()

    def test_generate_image_for_card_rejects_non_owner_and_missing_prompt(self):
        owner, other = self._seed_users()
        library = self._seed_library(owner.id)
        card = self._seed_card_with_prompt(
            library.id,
            "Hero",
            ai_prompt="hero visual prompt",
        )
        promptless_card = self._seed_card(library.id, "Blank")

        blocked_response = self.client.post(
            f"/api/cards/{card.id}/generate-image",
            json={"model": "banana2"},
            headers=self._auth_headers(other.token),
        )
        missing_prompt_response = self.client.post(
            f"/api/cards/{promptless_card.id}/generate-image",
            json={"model": "banana2"},
            headers=self._auth_headers(owner.token),
        )

        self.assertEqual(blocked_response.status_code, 403)
        self.assertEqual(missing_prompt_response.status_code, 400)

        db = self.Session()
        try:
            self.assertEqual(db.query(models.GeneratedImage).count(), 0)
        finally:
            db.close()

    def test_upload_image_as_owner_creates_card_image_and_reference_upload_generated_image(self):
        owner, _ = self._seed_users()
        library = self._seed_library(owner.id)
        card = self._seed_card(library.id, "Hero", "角色")
        previous = self._seed_generated_image(
            card.id,
            "https://cdn.example.test/old.png",
            is_reference=True,
        )

        with patch(
            f"{CARD_MEDIA_SERVICE}.save_and_upload_to_cdn",
            return_value="https://cdn.example.test/uploaded.png",
        ):
            response = self.client.post(
                f"/api/cards/{card.id}/images",
                files={"file": ("hero.png", b"image-bytes", "image/png")},
                headers=self._auth_headers(owner.token),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["card_id"], card.id)
        self.assertEqual(payload["image_path"], "https://cdn.example.test/uploaded.png")
        self.assertEqual(payload["order"], 0)

        db = self.Session()
        try:
            card_image = db.query(models.CardImage).filter_by(id=payload["id"]).one()
            self.assertEqual(card_image.card_id, card.id)
            self.assertEqual(card_image.image_path, payload["image_path"])

            refreshed_previous = (
                db.query(models.GeneratedImage).filter_by(id=previous.id).one()
            )
            upload_generated = (
                db.query(models.GeneratedImage)
                .filter_by(card_id=card.id, image_path=payload["image_path"])
                .one()
            )
            self.assertFalse(refreshed_previous.is_reference)
            self.assertEqual(upload_generated.model_name, "upload")
            self.assertEqual(upload_generated.status, "completed")
            self.assertTrue(upload_generated.is_reference)
        finally:
            db.close()

    def test_upload_image_by_non_owner_returns_403_without_creating_rows(self):
        owner, other = self._seed_users()
        library = self._seed_library(owner.id)
        card = self._seed_card(library.id, "Hero", "角色")

        with patch(
            f"{CARD_MEDIA_SERVICE}.save_and_upload_to_cdn",
            return_value="https://cdn.example.test/blocked.png",
        ) as upload:
            response = self.client.post(
                f"/api/cards/{card.id}/images",
                files={"file": ("blocked.png", b"image-bytes", "image/png")},
                headers=self._auth_headers(other.token),
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(upload.call_count, 0)

        db = self.Session()
        try:
            self.assertEqual(db.query(models.CardImage).count(), 0)
            self.assertEqual(db.query(models.GeneratedImage).count(), 0)
        finally:
            db.close()

    def test_delete_upload_image_removes_upload_generated_image_or_blocks_last_reference(self):
        owner, _ = self._seed_users()
        library = self._seed_library(owner.id)
        card = self._seed_card(library.id, "Hero", "角色")
        uploaded_path = "https://cdn.example.test/uploaded.png"
        card_image = self._seed_card_image(card.id, uploaded_path)
        upload_generated = self._seed_generated_image(
            card.id,
            uploaded_path,
            model_name="upload",
            is_reference=True,
        )
        fallback = self._seed_generated_image(
            card.id,
            "https://cdn.example.test/fallback.png",
            is_reference=False,
        )

        response = self.client.delete(
            f"/api/images/{card_image.id}",
            headers=self._auth_headers(owner.token),
        )

        self.assertEqual(response.status_code, 200)
        db = self.Session()
        try:
            self.assertEqual(
                db.query(models.CardImage).filter_by(id=card_image.id).count(),
                0,
            )
            self.assertEqual(
                db.query(models.GeneratedImage).filter_by(id=upload_generated.id).count(),
                0,
            )
            self.assertTrue(
                db.query(models.GeneratedImage).filter_by(id=fallback.id).one().is_reference
            )
        finally:
            db.close()

        last_card = self._seed_card(library.id, "Solo", "角色")
        last_path = "https://cdn.example.test/last.png"
        last_image = self._seed_card_image(last_card.id, last_path)
        last_generated = self._seed_generated_image(
            last_card.id,
            last_path,
            model_name="upload",
            is_reference=True,
        )

        blocked_response = self.client.delete(
            f"/api/images/{last_image.id}",
            headers=self._auth_headers(owner.token),
        )

        self.assertEqual(blocked_response.status_code, 400)
        self.assertEqual(
            blocked_response.json()["detail"],
            "不能删除最后一张主体素材图",
        )
        verify_db = self.Session()
        try:
            self.assertEqual(
                verify_db.query(models.CardImage).filter_by(id=last_image.id).count(),
                1,
            )
            self.assertEqual(
                verify_db.query(models.GeneratedImage)
                .filter_by(id=last_generated.id)
                .count(),
                1,
            )
        finally:
            verify_db.close()

    def test_audio_routes_support_sound_cards_order_by_newest_and_promote_fallback(self):
        owner, _ = self._seed_users()
        library = self._seed_library(owner.id)
        role_card = self._seed_card(library.id, "Hero", "角色")
        sound_card = self._seed_card(library.id, "Hero Voice", "声音")
        old_audio = self._seed_audio(
            sound_card.id,
            "https://cdn.example.test/old.wav",
            is_reference=True,
            created_at=datetime(2024, 1, 1, 8, 0, 0),
        )

        non_sound_response = self.client.post(
            f"/api/cards/{role_card.id}/audios",
            files={"file": ("voice.wav", b"audio-bytes", "audio/wav")},
            headers=self._auth_headers(owner.token),
        )
        self.assertEqual(non_sound_response.status_code, 400)
        self.assertEqual(non_sound_response.json()["detail"], "只有声音卡片支持上传音频")

        with patch(
            f"{CARD_MEDIA_SERVICE}.save_audio_and_upload_to_cdn",
            return_value=("https://cdn.example.test/new.wav", 2.5),
        ):
            upload_response = self.client.post(
                f"/api/cards/{sound_card.id}/audios",
                files={"file": ("new.wav", b"audio-bytes", "audio/wav")},
                headers=self._auth_headers(owner.token),
            )

        self.assertEqual(upload_response.status_code, 200)
        uploaded_payload = upload_response.json()
        uploaded_id = uploaded_payload["id"]
        self.assertEqual(uploaded_payload["card_id"], sound_card.id)
        self.assertEqual(uploaded_payload["audio_path"], "https://cdn.example.test/new.wav")
        self.assertEqual(uploaded_payload["file_name"], "new.wav")
        self.assertEqual(uploaded_payload["duration_seconds"], 2.5)
        self.assertTrue(uploaded_payload["is_reference"])

        newest_low_id = self._seed_audio(
            sound_card.id,
            "https://cdn.example.test/newest-low.wav",
            file_name="newest-low.wav",
            duration_seconds=3.0,
            is_reference=False,
            created_at=datetime(2024, 1, 3, 9, 0, 0),
        )
        newest_high_id = self._seed_audio(
            sound_card.id,
            "https://cdn.example.test/newest-high.wav",
            file_name="newest-high.wav",
            duration_seconds=4.0,
            is_reference=False,
            created_at=datetime(2024, 1, 3, 9, 0, 0),
        )
        self._set_audio_created_at(uploaded_id, datetime(2024, 1, 2, 9, 0, 0))

        db = self.Session()
        try:
            self.assertFalse(
                db.query(models.SubjectCardAudio).filter_by(id=old_audio.id).one().is_reference
            )
            self.assertTrue(
                db.query(models.SubjectCardAudio).filter_by(id=uploaded_id).one().is_reference
            )
        finally:
            db.close()

        list_response = self.client.get(
            f"/api/cards/{sound_card.id}/audios",
            headers=self._auth_headers(owner.token),
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in list_response.json()],
            [newest_high_id.id, newest_low_id.id, uploaded_id, old_audio.id],
        )

        delete_response = self.client.delete(
            f"/api/cards/{sound_card.id}/audios/{uploaded_id}",
            headers=self._auth_headers(owner.token),
        )
        self.assertEqual(delete_response.status_code, 200)

        verify_db = self.Session()
        try:
            self.assertEqual(
                verify_db.query(models.SubjectCardAudio)
                .filter_by(id=uploaded_id)
                .count(),
                0,
            )
            self.assertTrue(
                verify_db.query(models.SubjectCardAudio)
                .filter_by(id=newest_high_id.id)
                .one()
                .is_reference
            )
        finally:
            verify_db.close()

    def test_get_generated_images_is_public_newest_first_and_missing_card_returns_404(self):
        owner, _ = self._seed_users()
        library = self._seed_library(owner.id)
        card = self._seed_card(library.id, "Hero", "角色")
        older = self._seed_generated_image(
            card.id,
            "https://cdn.example.test/older.png",
            created_at=datetime(2024, 1, 1, 8, 0, 0),
        )
        newer = self._seed_generated_image(
            card.id,
            "https://cdn.example.test/newer.png",
            created_at=datetime(2024, 1, 2, 8, 0, 0),
        )

        response = self.client.get(f"/api/cards/{card.id}/generated-images")
        missing_response = self.client.get("/api/cards/999999/generated-images")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()], [newer.id, older.id])
        self.assertEqual(missing_response.status_code, 404)

    def test_set_reference_images_requires_owner_and_sets_only_selected_card_images(self):
        owner, other = self._seed_users()
        library = self._seed_library(owner.id)
        card = self._seed_card(library.id, "Hero", "角色")
        other_card = self._seed_card(library.id, "Scene", "场景")
        first = self._seed_generated_image(
            card.id,
            "https://cdn.example.test/first.png",
            is_reference=True,
        )
        second = self._seed_generated_image(
            card.id,
            "https://cdn.example.test/second.png",
            is_reference=False,
        )
        other_card_image = self._seed_generated_image(
            other_card.id,
            "https://cdn.example.test/other-card.png",
            is_reference=False,
        )

        response = self.client.put(
            f"/api/cards/{card.id}/reference-images",
            json={"generated_image_ids": [second.id, other_card_image.id]},
            headers=self._auth_headers(owner.token),
        )
        self.assertEqual(response.status_code, 200)

        db = self.Session()
        try:
            self.assertFalse(db.query(models.GeneratedImage).filter_by(id=first.id).one().is_reference)
            self.assertTrue(db.query(models.GeneratedImage).filter_by(id=second.id).one().is_reference)
            self.assertFalse(
                db.query(models.GeneratedImage)
                .filter_by(id=other_card_image.id)
                .one()
                .is_reference
            )
        finally:
            db.close()

        blocked_response = self.client.put(
            f"/api/cards/{card.id}/reference-images",
            json={"generated_image_ids": [first.id]},
            headers=self._auth_headers(other.token),
        )
        self.assertEqual(blocked_response.status_code, 403)

        verify_db = self.Session()
        try:
            self.assertFalse(
                verify_db.query(models.GeneratedImage).filter_by(id=first.id).one().is_reference
            )
            self.assertTrue(
                verify_db.query(models.GeneratedImage).filter_by(id=second.id).one().is_reference
            )
        finally:
            verify_db.close()

    def test_delete_generated_image_blocks_last_reference_promotes_fallback_and_requires_owner(self):
        owner, other = self._seed_users()
        library = self._seed_library(owner.id)
        card = self._seed_card(library.id, "Hero", "角色")
        target = self._seed_generated_image(
            card.id,
            "https://cdn.example.test/target.png",
            is_reference=True,
        )
        fallback = self._seed_generated_image(
            card.id,
            "https://cdn.example.test/fallback.png",
            is_reference=False,
        )

        blocked_owner_response = self.client.delete(
            f"/api/generated-images/{target.id}",
            headers=self._auth_headers(other.token),
        )
        self.assertEqual(blocked_owner_response.status_code, 403)

        delete_response = self.client.delete(
            f"/api/generated-images/{target.id}",
            headers=self._auth_headers(owner.token),
        )
        self.assertEqual(delete_response.status_code, 200)

        db = self.Session()
        try:
            self.assertEqual(
                db.query(models.GeneratedImage).filter_by(id=target.id).count(),
                0,
            )
            self.assertTrue(
                db.query(models.GeneratedImage).filter_by(id=fallback.id).one().is_reference
            )
        finally:
            db.close()

        solo_card = self._seed_card(library.id, "Solo", "角色")
        solo = self._seed_generated_image(
            solo_card.id,
            "https://cdn.example.test/solo.png",
            is_reference=True,
        )

        final_response = self.client.delete(
            f"/api/generated-images/{solo.id}",
            headers=self._auth_headers(owner.token),
        )
        self.assertEqual(final_response.status_code, 400)
        self.assertEqual(final_response.json()["detail"], "不能删除最后一张主体素材图")

        verify_db = self.Session()
        try:
            self.assertEqual(
                verify_db.query(models.GeneratedImage).filter_by(id=solo.id).count(),
                1,
            )
        finally:
            verify_db.close()


if __name__ == "__main__":
    unittest.main()
