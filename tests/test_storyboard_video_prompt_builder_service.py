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
from api.services import storyboard_video_prompt_builder  # noqa: E402


class StoryboardVideoPromptBuilderServiceTests(unittest.TestCase):
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

            self.script = models.Script(user_id=user.id, name="script")
            db.add(self.script)
            db.flush()

            self.episode = models.Episode(script_id=self.script.id, name="S01")
            db.add(self.episode)
            db.flush()

            library = models.StoryLibrary(user_id=user.id, episode_id=self.episode.id, name="library")
            db.add(library)
            db.flush()

            self.role_card = models.SubjectCard(library_id=library.id, name="Hero", card_type="角色")
            self.prop_card = models.SubjectCard(library_id=library.id, name="Key", card_type="道具")
            self.scene_a = models.SubjectCard(
                library_id=library.id,
                name="Warehouse",
                card_type="场景",
                ai_prompt="生成图片的风格是：noir\n生成图片中场景的是：dark aisles",
            )
            self.scene_b = models.SubjectCard(
                library_id=library.id,
                name="Garden",
                card_type="场景",
                ai_prompt="生成图片中场景的是：moonlit stones",
            )
            db.add_all([self.role_card, self.prop_card, self.scene_a, self.scene_b])
            db.flush()

            self.shot = models.StoryboardShot(
                episode_id=self.episode.id,
                shot_number=1,
                selected_card_ids=json.dumps([
                    self.role_card.id,
                    self.scene_b.id,
                    self.prop_card.id,
                    self.scene_a.id,
                ], ensure_ascii=False),
                scene_override="",
                storyboard_video_prompt="table prompt",
                sora_prompt="",
            )
            db.add(self.shot)
            db.commit()

            self.episode_id = int(self.episode.id)
            self.shot_id = int(self.shot.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_extract_scene_description_preserves_selected_scene_order_and_strips_prefixes(self):
        db = self.Session()
        try:
            shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == self.shot_id).first()
            description = storyboard_video_prompt_builder.extract_scene_description(shot, db)
        finally:
            db.close()

        self.assertEqual(description, "Gardenmoonlit stones；Warehousedark aisles")
        self.assertNotIn("Hero", description)
        self.assertNotIn("Key", description)
        self.assertNotIn("生成图片", description)

    def test_build_sora_prompt_returns_full_prompt_without_db_lookup(self):
        shot = models.StoryboardShot(
            episode_id=1,
            shot_number=1,
            sora_prompt="  full prompt  ",
            sora_prompt_is_full=True,
        )

        self.assertEqual(storyboard_video_prompt_builder.build_sora_prompt(shot, db=None), "full prompt")

    def test_build_sora_prompt_prefers_episode_template_scene_override_and_table_sora_prompt(self):
        db = self.Session()
        try:
            template = models.VideoStyleTemplate(
                name="Selected",
                sora_rule="template rule",
                style_prompt="template style",
            )
            db.add(template)
            db.flush()
            episode = db.query(models.Episode).filter(models.Episode.id == self.episode_id).first()
            episode.video_style_template_id = template.id
            episode.video_prompt_template = "episode style"
            shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == self.shot_id).first()
            shot.scene_override = "override scene"
            shot.sora_prompt = "manual table"
            db.commit()

            prompt = storyboard_video_prompt_builder.build_sora_prompt(shot, db)
        finally:
            db.close()

        self.assertEqual(prompt.splitlines(), [
            "template rule",
            "episode style",
            "场景：override scene",
            "manual table",
        ])

    def test_build_sora_prompt_uses_global_settings_and_card_scene_when_no_template_override(self):
        db = self.Session()
        try:
            db.add_all([
                models.GlobalSettings(key="sora_rule", value="global rule"),
                models.GlobalSettings(key="prompt_template", value="global style"),
            ])
            shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == self.shot_id).first()
            shot.sora_prompt = ""
            shot.storyboard_video_prompt = "generated table"
            db.commit()

            prompt = storyboard_video_prompt_builder.build_sora_prompt(shot, db)
        finally:
            db.close()

        self.assertEqual(prompt.splitlines(), [
            "global rule",
            "global style",
            "场景：Gardenmoonlit stones；Warehousedark aisles",
            "generated table",
        ])


if __name__ == "__main__":
    unittest.main()
