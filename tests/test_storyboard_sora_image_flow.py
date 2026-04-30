import json
import os
import sys
import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
TESTS_DIR = ROOT_DIR / "tests"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}")

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from env_defaults import apply_test_env_defaults  # noqa: E402

apply_test_env_defaults()

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402
import models  # noqa: E402


class StoryboardSoraImageFlowTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        db = self.Session()
        try:
            user = models.User(username="tester", token="token", password_hash="hash", password_plain="123456")
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="script")
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="S01",
                shot_image_size="9:16",
                storyboard_video_aspect_ratio="16:9",
                detail_images_model="seedream-4.0",
                detail_images_provider="jimeng",
            )
            db.add(episode)
            db.flush()

            library = models.StoryLibrary(user_id=user.id, episode_id=episode.id, name="library")
            db.add(library)
            db.flush()

            self.role_card = models.SubjectCard(library_id=library.id, name="陆云熙", card_type="角色")
            self.scene_card = models.SubjectCard(library_id=library.id, name="饭桌", card_type="场景")
            self.prop_card = models.SubjectCard(library_id=library.id, name="茶", card_type="道具")
            db.add_all([self.role_card, self.scene_card, self.prop_card])
            db.flush()

            db.add_all([
                models.GeneratedImage(
                    card_id=self.role_card.id,
                    image_path="https://cdn.example.com/role.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
                models.GeneratedImage(
                    card_id=self.scene_card.id,
                    image_path="https://cdn.example.com/scene.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
                models.GeneratedImage(
                    card_id=self.prop_card.id,
                    image_path="https://cdn.example.com/prop.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
            ])

            self.shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=14,
                selected_card_ids=json.dumps(
                    [self.role_card.id, self.scene_card.id, self.prop_card.id],
                    ensure_ascii=False,
                ),
            )
            db.add(self.shot)
            db.commit()

            self.episode_id = int(episode.id)
            self.shot_id = int(self.shot.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_collect_storyboard_subject_reference_urls_keeps_role_scene_and_prop(self):
        db = self.Session()
        try:
            shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == self.shot_id).first()
            urls = main._collect_storyboard_subject_reference_urls(shot, db)
        finally:
            db.close()

        self.assertEqual(
            urls,
            [
                "https://cdn.example.com/role.png",
                "https://cdn.example.com/scene.png",
                "https://cdn.example.com/prop.png",
            ],
        )

    def test_storyboard_sora_image_ratio_prefers_video_ratio_over_stale_shot_image_size(self):
        db = self.Session()
        try:
            episode = db.query(models.Episode).filter(models.Episode.id == self.episode_id).first()
            ratio = main._resolve_storyboard_sora_image_ratio(episode, requested_size="9:16")
        finally:
            db.close()

        self.assertEqual(ratio, "16:9")

    def test_storyboard_video_settings_persists_detail_image_provider(self):
        db = self.Session()
        try:
            user = db.query(models.User).filter(models.User.username == "tester").first()
            request = main.EpisodeStoryboardVideoSettingsUpdateRequest(
                detail_images_provider="MoMo",
                detail_images_model="nano-banana-2",
                model=main.DEFAULT_STORYBOARD_VIDEO_MODEL,
                aspect_ratio="16:9",
                duration=15,
                resolution_name="720p",
            )
            result = asyncio.run(
                main.update_episode_storyboard_video_settings(
                    self.episode_id,
                    request,
                    user=user,
                    db=db,
                )
            )
            episode = db.query(models.Episode).filter(models.Episode.id == self.episode_id).first()
        finally:
            db.close()

        self.assertEqual(result["detail_images_provider"], "momo")
        self.assertEqual(result["detail_images_model"], "nano-banana-2")
        self.assertEqual(episode.detail_images_provider, "momo")

    def test_detail_image_model_accepts_local_key_when_catalog_unavailable(self):
        with patch.object(
            main.image_platform_client,
            "resolve_image_route",
            side_effect=RuntimeError("catalog unavailable"),
        ):
            self.assertEqual(
                main._normalize_detail_images_model(
                    "nano-banana-2",
                    default_model="seedream-4.0",
                ),
                "nano-banana-2",
            )
            self.assertEqual(
                main._normalize_detail_images_model(
                    "banana2-moti",
                    default_model="seedream-4.0",
                ),
                "nano-banana-2",
            )

    def test_detail_image_provider_defaults_to_episode_setting(self):
        db = self.Session()
        try:
            episode = db.query(models.Episode).filter(models.Episode.id == self.episode_id).first()
            episode.detail_images_provider = "momo"
            db.commit()
            provider = main._resolve_episode_detail_images_provider(episode)
        finally:
            db.close()

        self.assertEqual(provider, "momo")
        self.assertEqual(main._resolve_episode_detail_images_provider(None, "banana"), "momo")


if __name__ == "__main__":
    unittest.main()
