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
from api.services import storyboard_reference_assets  # noqa: E402


class StoryboardReferenceAssetsServiceTests(unittest.TestCase):
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

            self.library = models.StoryLibrary(user_id=user.id, episode_id=episode.id, name="library")
            other_library = models.StoryLibrary(user_id=user.id, episode_id=episode.id, name="other")
            db.add_all([self.library, other_library])
            db.flush()

            self.role_card = models.SubjectCard(library_id=self.library.id, name="Role", card_type="角色")
            self.scene_card = models.SubjectCard(library_id=self.library.id, name="Scene", card_type="场景")
            self.prop_card = models.SubjectCard(library_id=self.library.id, name="Prop", card_type="道具")
            self.extra_prop_card = models.SubjectCard(library_id=self.library.id, name="Extra Prop", card_type="道具")
            self.other_card = models.SubjectCard(library_id=other_library.id, name="Other", card_type="角色")
            db.add_all([
                self.role_card,
                self.scene_card,
                self.prop_card,
                self.extra_prop_card,
                self.other_card,
            ])
            db.flush()

            self.shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                selected_card_ids=json.dumps([
                    self.role_card.id,
                    self.scene_card.id,
                    self.prop_card.id,
                    self.extra_prop_card.id,
                ]),
            )
            db.add(self.shot)
            db.commit()

            self.episode_id = int(episode.id)
            self.library_id = int(self.library.id)
            self.role_card_id = int(self.role_card.id)
            self.scene_card_id = int(self.scene_card.id)
            self.prop_card_id = int(self.prop_card.id)
            self.extra_prop_card_id = int(self.extra_prop_card.id)
            self.other_card_id = int(self.other_card.id)
            self.shot_id = int(self.shot.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_parse_card_ids_accepts_json_and_filters_invalid_duplicate_values(self):
        raw_value = json.dumps([self.role_card_id, str(self.scene_card_id), 0, "-2", "abc", self.role_card_id])

        self.assertEqual(
            storyboard_reference_assets.parse_card_ids(raw_value),
            [self.role_card_id, self.scene_card_id],
        )
        self.assertEqual(storyboard_reference_assets.parse_card_ids({"not": "a list"}), [])
        self.assertEqual(storyboard_reference_assets.parse_card_ids("not json"), [])

    def test_resolve_selected_cards_preserves_order_and_library_scope(self):
        db = self.Session()
        try:
            cards = storyboard_reference_assets.resolve_selected_cards(
                db,
                [self.other_card_id, self.prop_card_id, self.role_card_id, 999999, self.scene_card_id],
                library_id=self.library_id,
            )
        finally:
            db.close()

        self.assertEqual([card.id for card in cards], [self.prop_card_id, self.role_card_id, self.scene_card_id])

    def test_collect_storyboard_subject_reference_urls_preserves_order_dedupes_and_uses_fallback(self):
        db = self.Session()
        try:
            db.add_all([
                models.GeneratedImage(
                    card_id=self.role_card_id,
                    image_path=" https://cdn.example.com/role.png ",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
                models.GeneratedImage(
                    card_id=self.prop_card_id,
                    image_path="https://cdn.example.com/role.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
                models.GeneratedImage(
                    card_id=self.extra_prop_card_id,
                    image_path="https://cdn.example.com/prop.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
                models.CardImage(
                    card_id=self.scene_card_id,
                    image_path="https://cdn.example.com/scene-upload.png",
                    order=2,
                ),
            ])
            db.commit()

            shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == self.shot_id).first()
            urls_with_fallback = storyboard_reference_assets.collect_storyboard_subject_reference_urls(shot, db)
            urls_without_fallback = storyboard_reference_assets.collect_storyboard_subject_reference_urls(
                shot,
                db,
                allow_uploaded_fallback=False,
            )
        finally:
            db.close()

        self.assertEqual(
            urls_with_fallback,
            [
                "https://cdn.example.com/role.png",
                "https://cdn.example.com/scene-upload.png",
                "https://cdn.example.com/prop.png",
            ],
        )
        self.assertEqual(
            urls_without_fallback,
            [
                "https://cdn.example.com/role.png",
                "https://cdn.example.com/prop.png",
            ],
        )

    def test_resolve_selected_scene_reference_image_url_honors_uploaded_scene_toggle(self):
        db = self.Session()
        try:
            db.add(
                models.GeneratedImage(
                    card_id=self.scene_card_id,
                    image_path="https://cdn.example.com/scene-card.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                )
            )
            db.commit()

            shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == self.shot_id).first()
            shot.uploaded_scene_image_url = "https://cdn.example.com/uploaded-scene.png"
            shot.use_uploaded_scene_image = False
            selected_url = storyboard_reference_assets.resolve_selected_scene_reference_image_url(shot, db)

            shot.use_uploaded_scene_image = True
            uploaded_url = storyboard_reference_assets.resolve_selected_scene_reference_image_url(shot, db)
        finally:
            db.close()

        self.assertEqual(selected_url, "https://cdn.example.com/scene-card.png")
        self.assertEqual(uploaded_url, "https://cdn.example.com/uploaded-scene.png")


if __name__ == "__main__":
    unittest.main()
