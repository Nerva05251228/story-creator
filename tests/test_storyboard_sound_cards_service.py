import json
import os
import sys
import unittest
from pathlib import Path

from fastapi import HTTPException
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
from api.services import storyboard_sound_cards  # noqa: E402


class StoryboardSoundCardsServiceTests(unittest.TestCase):
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
            other_library = models.StoryLibrary(user_id=user.id, episode_id=episode.id, name="other")
            db.add_all([library, other_library])
            db.flush()

            self.hero_role = models.SubjectCard(library_id=library.id, name="Hero", card_type="角色")
            self.friend_role = models.SubjectCard(library_id=library.id, name="Friend", card_type="角色")
            self.prop_card = models.SubjectCard(library_id=library.id, name="Prop", card_type="道具")
            db.add_all([self.hero_role, self.friend_role, self.prop_card])
            db.flush()

            self.hero_sound = models.SubjectCard(
                library_id=library.id,
                name="Hero Voice",
                card_type="声音",
                linked_card_id=self.hero_role.id,
            )
            self.friend_sound = models.SubjectCard(library_id=library.id, name="Friend", card_type="声音")
            self.narrator_sound = models.SubjectCard(library_id=library.id, name="旁白", card_type="声音")
            self.other_library_sound = models.SubjectCard(
                library_id=other_library.id,
                name="Other Voice",
                card_type="声音",
            )
            db.add_all([
                self.hero_sound,
                self.friend_sound,
                self.narrator_sound,
                self.other_library_sound,
            ])
            db.flush()

            self.auto_shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                selected_card_ids=json.dumps([
                    self.friend_role.id,
                    self.prop_card.id,
                    self.hero_role.id,
                ]),
                selected_sound_card_ids=None,
            )
            self.explicit_shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=2,
                selected_card_ids=json.dumps([self.hero_role.id]),
                selected_sound_card_ids=json.dumps([
                    self.other_library_sound.id,
                    self.narrator_sound.id,
                    self.hero_sound.id,
                    self.narrator_sound.id,
                ]),
            )
            db.add_all([self.auto_shot, self.explicit_shot])
            db.commit()

            self.episode_id = int(episode.id)
            self.hero_sound_id = int(self.hero_sound.id)
            self.friend_sound_id = int(self.friend_sound.id)
            self.narrator_sound_id = int(self.narrator_sound.id)
            self.other_library_sound_id = int(self.other_library_sound.id)
            self.auto_shot_id = int(self.auto_shot.id)
            self.explicit_shot_id = int(self.explicit_shot.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_parse_storyboard_sound_card_ids_accepts_json_and_filters_invalid_duplicate_values(self):
        raw_value = json.dumps([self.hero_sound_id, str(self.narrator_sound_id), 0, "-2", "abc", self.hero_sound_id])

        self.assertEqual(
            storyboard_sound_cards.parse_storyboard_sound_card_ids(raw_value),
            [self.hero_sound_id, self.narrator_sound_id],
        )
        self.assertEqual(storyboard_sound_cards.parse_storyboard_sound_card_ids([self.friend_sound_id, "x"]), [self.friend_sound_id])
        self.assertIsNone(storyboard_sound_cards.parse_storyboard_sound_card_ids(""))
        self.assertIsNone(storyboard_sound_cards.parse_storyboard_sound_card_ids("not json"))
        self.assertIsNone(storyboard_sound_cards.parse_storyboard_sound_card_ids({"not": "a list"}))

    def test_normalize_storyboard_selected_sound_card_ids_preserves_order_and_rejects_invalid_scope(self):
        db = self.Session()
        try:
            normalized = storyboard_sound_cards.normalize_storyboard_selected_sound_card_ids(
                [self.narrator_sound_id, self.hero_sound_id, self.narrator_sound_id, -1, "bad"],
                self.episode_id,
                db,
            )
            empty = storyboard_sound_cards.normalize_storyboard_selected_sound_card_ids([], self.episode_id, db)
            missing = storyboard_sound_cards.normalize_storyboard_selected_sound_card_ids(None, self.episode_id, db)
            with self.assertRaises(HTTPException) as exc:
                storyboard_sound_cards.normalize_storyboard_selected_sound_card_ids(
                    [self.other_library_sound_id],
                    self.episode_id,
                    db,
                )
        finally:
            db.close()

        self.assertEqual(normalized, [self.narrator_sound_id, self.hero_sound_id])
        self.assertEqual(empty, [])
        self.assertIsNone(missing)
        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("存在无效声音卡片ID", exc.exception.detail)

    def test_resolve_storyboard_selected_sound_cards_uses_explicit_ids_with_library_scope(self):
        db = self.Session()
        try:
            shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == self.explicit_shot_id).first()
            cards = storyboard_sound_cards.resolve_storyboard_selected_sound_cards(shot, db)
        finally:
            db.close()

        self.assertEqual([card.id for card in cards], [self.narrator_sound_id, self.hero_sound_id])

    def test_resolve_storyboard_selected_sound_cards_defaults_to_linked_named_and_narrator_cards(self):
        db = self.Session()
        try:
            shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == self.auto_shot_id).first()
            cards = storyboard_sound_cards.resolve_storyboard_selected_sound_cards(shot, db)
        finally:
            db.close()

        self.assertEqual([card.id for card in cards], [
            self.friend_sound_id,
            self.hero_sound_id,
            self.narrator_sound_id,
        ])


if __name__ == "__main__":
    unittest.main()
