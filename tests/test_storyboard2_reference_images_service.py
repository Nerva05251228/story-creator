import json
import os
import sys
import unittest
from pathlib import Path

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

import models  # noqa: E402
from api.services import storyboard2_reference_images  # noqa: E402


class Storyboard2ReferenceImagesServiceTests(unittest.TestCase):
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

            episode = models.Episode(script_id=script.id, name="S01")
            db.add(episode)
            db.flush()

            library = models.StoryLibrary(user_id=user.id, episode_id=episode.id, name="library")
            db.add(library)
            db.flush()

            self.role_card = models.SubjectCard(library_id=library.id, name="Hero", card_type="角色")
            self.scene_card = models.SubjectCard(library_id=library.id, name="Room", card_type="场景")
            self.prop_card = models.SubjectCard(library_id=library.id, name="Key", card_type="道具")
            self.upload_only_card = models.SubjectCard(library_id=library.id, name="Letter", card_type="道具")
            db.add_all([self.role_card, self.scene_card, self.prop_card, self.upload_only_card])
            db.flush()

            source_shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                selected_card_ids=json.dumps([self.role_card.id, self.scene_card.id], ensure_ascii=False),
            )
            db.add(source_shot)
            db.flush()

            storyboard2_shot = models.Storyboard2Shot(
                episode_id=episode.id,
                source_shot_id=source_shot.id,
                shot_number=1,
                selected_card_ids=json.dumps([self.role_card.id, self.scene_card.id, self.prop_card.id], ensure_ascii=False),
            )
            fallback_storyboard2_shot = models.Storyboard2Shot(
                episode_id=episode.id,
                source_shot_id=source_shot.id,
                shot_number=2,
                selected_card_ids="[]",
            )
            db.add_all([storyboard2_shot, fallback_storyboard2_shot])
            db.flush()

            sub_shot = models.Storyboard2SubShot(
                storyboard2_shot_id=storyboard2_shot.id,
                sub_shot_index=1,
                selected_card_ids=json.dumps([
                    self.upload_only_card.id,
                    self.scene_card.id,
                    self.role_card.id,
                ], ensure_ascii=False),
            )
            db.add(sub_shot)
            db.flush()

            db.add_all([
                models.GeneratedImage(
                    card_id=self.role_card.id,
                    image_path="https://cdn.example.com/shared-ref.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
                models.GeneratedImage(
                    card_id=self.scene_card.id,
                    image_path="https://cdn.example.com/scene-ref.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
                models.GeneratedImage(
                    card_id=self.prop_card.id,
                    image_path="https://cdn.example.com/shared-ref.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
                models.CardImage(
                    card_id=self.upload_only_card.id,
                    image_path="https://cdn.example.com/upload-fallback.png",
                    order=2,
                ),
            ])
            db.commit()

            self.role_card_id = int(self.role_card.id)
            self.scene_card_id = int(self.scene_card.id)
            self.prop_card_id = int(self.prop_card.id)
            self.upload_only_card_id = int(self.upload_only_card.id)
            self.storyboard2_shot_id = int(storyboard2_shot.id)
            self.fallback_storyboard2_shot_id = int(fallback_storyboard2_shot.id)
            self.sub_shot_id = int(sub_shot.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_parse_storyboard2_card_ids_accepts_json_and_filters_invalid_duplicate_values(self):
        raw_value = json.dumps([self.role_card_id, str(self.scene_card_id), 0, "-2", "abc", self.role_card_id])

        self.assertEqual(
            storyboard2_reference_images.parse_storyboard2_card_ids(raw_value),
            [self.role_card_id, self.scene_card_id],
        )
        self.assertEqual(storyboard2_reference_images.parse_storyboard2_card_ids({"not": "a list"}), [])
        self.assertEqual(storyboard2_reference_images.parse_storyboard2_card_ids("not json"), [])

    def test_resolve_storyboard2_selected_card_ids_falls_back_to_source_shot(self):
        db = self.Session()
        try:
            storyboard2_shot = db.query(models.Storyboard2Shot).filter(
                models.Storyboard2Shot.id == self.fallback_storyboard2_shot_id
            ).first()
            card_ids = storyboard2_reference_images.resolve_storyboard2_selected_card_ids(storyboard2_shot, db)
        finally:
            db.close()

        self.assertEqual(card_ids, [self.role_card_id, self.scene_card_id])

    def test_collect_storyboard2_reference_images_excludes_scene_by_default_and_dedupes(self):
        db = self.Session()
        try:
            storyboard2_shot = db.query(models.Storyboard2Shot).filter(
                models.Storyboard2Shot.id == self.storyboard2_shot_id
            ).first()
            urls = storyboard2_reference_images.collect_storyboard2_reference_images(storyboard2_shot, db)
        finally:
            db.close()

        self.assertEqual(urls, ["https://cdn.example.com/shared-ref.png"])

    def test_collect_storyboard2_reference_images_prefers_subshot_ids_and_can_include_scene(self):
        db = self.Session()
        try:
            storyboard2_shot = db.query(models.Storyboard2Shot).filter(
                models.Storyboard2Shot.id == self.storyboard2_shot_id
            ).first()
            sub_shot = db.query(models.Storyboard2SubShot).filter(
                models.Storyboard2SubShot.id == self.sub_shot_id
            ).first()
            urls = storyboard2_reference_images.collect_storyboard2_reference_images(
                storyboard2_shot,
                db,
                sub_shot=sub_shot,
                include_scene_references=True,
            )
        finally:
            db.close()

        self.assertEqual(
            urls,
            [
                "https://cdn.example.com/upload-fallback.png",
                "https://cdn.example.com/scene-ref.png",
                "https://cdn.example.com/shared-ref.png",
            ],
        )


if __name__ == "__main__":
    unittest.main()
