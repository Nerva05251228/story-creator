import json
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import models  # noqa: E402
from api.services import storyboard_sync  # noqa: E402


ROLE_TYPE = "\u89d2\u8272"
SCENE_TYPE = "\u573a\u666f"


class StoryboardSyncServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed_episode_with_library(self):
        db = self.Session()
        user = models.User(username="sync-user", token="sync-token")
        db.add(user)
        db.flush()

        script = models.Script(user_id=user.id, name="Sync Script")
        db.add(script)
        db.flush()

        episode = models.Episode(script_id=script.id, name="Episode 1")
        db.add(episode)
        db.flush()

        library = models.StoryLibrary(
            user_id=user.id,
            episode_id=episode.id,
            name="Episode Library",
        )
        db.add(library)
        db.flush()
        return db, episode, library

    def test_sync_subjects_creates_cards_updates_metadata_and_selected_card_ids(self):
        db, episode, library = self._seed_episode_with_library()
        try:
            hero = models.SubjectCard(
                library_id=library.id,
                name="Hero",
                card_type=ROLE_TYPE,
                alias="old alias",
                ai_prompt="old prompt",
                role_personality="old personality",
            )
            shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                variant_index=0,
                selected_card_ids="[]",
            )
            db.add_all([hero, shot])
            db.commit()

            storyboard_data = {
                "subjects": [
                    {
                        "name": "Hero",
                        "type": ROLE_TYPE,
                        "alias": "new alias",
                        "ai_prompt": "new prompt",
                        "role_personality": "brave",
                    },
                    {
                        "name": "Garden",
                        "type": SCENE_TYPE,
                        "ai_prompt": "quiet garden",
                    },
                ],
                "shots": [
                    {
                        "shot_number": 1,
                        "subjects": [
                            {"name": "Hero", "type": ROLE_TYPE},
                            {"name": "Garden", "type": SCENE_TYPE},
                        ],
                    }
                ],
            }

            storyboard_sync.sync_subjects_to_database(episode.id, storyboard_data, db)

            db.expire_all()
            cards = db.query(models.SubjectCard).filter(
                models.SubjectCard.library_id == library.id
            ).all()
            cards_by_name = {card.name: card for card in cards}
            self.assertEqual(cards_by_name["Hero"].alias, "new alias")
            self.assertEqual(cards_by_name["Hero"].ai_prompt, "new prompt")
            self.assertEqual(cards_by_name["Hero"].role_personality, "brave")
            self.assertEqual(cards_by_name["Garden"].card_type, SCENE_TYPE)

            updated_shot = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.id == shot.id
            ).first()
            selected_ids = json.loads(updated_shot.selected_card_ids)
            self.assertEqual(
                selected_ids,
                [cards_by_name["Hero"].id, cards_by_name["Garden"].id],
            )
        finally:
            db.close()

    def test_sync_storyboard_to_shots_creates_variant_for_modified_shot_with_video(self):
        db, episode, library = self._seed_episode_with_library()
        try:
            hero = models.SubjectCard(
                library_id=library.id,
                name="Hero",
                card_type=ROLE_TYPE,
            )
            shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                stable_id="stable-1",
                variant_index=0,
                script_excerpt="old text",
                storyboard_dialogue="old dialogue",
                selected_card_ids="[]",
                sora_prompt="old prompt",
                sora_prompt_status="completed",
                video_status="processing",
            )
            db.add_all([hero, shot])
            db.commit()

            old_storyboard_data = {
                "shots": [
                    {
                        "id": shot.id,
                        "stable_id": "stable-1",
                        "shot_number": "1",
                        "original_text": "old text",
                        "dialogue_text": "old dialogue",
                    }
                ]
            }
            new_storyboard_data = {
                "shots": [
                    {
                        "id": shot.id,
                        "stable_id": "stable-1",
                        "shot_number": "1",
                        "original_text": "new text",
                        "dialogue_text": "new dialogue",
                        "subjects": [{"name": "Hero", "type": ROLE_TYPE}],
                    }
                ]
            }

            storyboard_sync.sync_storyboard_to_shots(
                episode.id,
                new_storyboard_data,
                old_storyboard_data,
                db,
            )

            variants = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.episode_id == episode.id
            ).order_by(
                models.StoryboardShot.variant_index.asc(),
            ).all()
            self.assertEqual(len(variants), 2)
            self.assertEqual(variants[0].variant_index, 0)
            self.assertEqual(variants[0].script_excerpt, "old text")
            self.assertEqual(variants[1].variant_index, 1)
            self.assertEqual(variants[1].script_excerpt, "new text")
            self.assertEqual(variants[1].storyboard_dialogue, "new dialogue")
            self.assertEqual(json.loads(variants[1].selected_card_ids), [hero.id])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
