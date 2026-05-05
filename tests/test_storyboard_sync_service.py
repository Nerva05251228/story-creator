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

    def test_create_shots_from_storyboard_data_reuses_subject_assets_and_formats_dialogue(self):
        db, episode, library = self._seed_episode_with_library()
        try:
            script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
            reusable_episode = models.Episode(script_id=script.id, name="Reusable Episode")
            db.add(reusable_episode)
            db.flush()
            reusable_library = models.StoryLibrary(
                user_id=script.user_id,
                episode_id=reusable_episode.id,
                name="Reusable Library",
            )
            db.add(reusable_library)
            db.flush()
            reusable_card = models.SubjectCard(
                library_id=reusable_library.id,
                name="Hero",
                card_type=ROLE_TYPE,
                alias="hero alias",
                ai_prompt="hero prompt",
                role_personality="brave",
            )
            old_card = models.SubjectCard(
                library_id=library.id,
                name="Old",
                card_type=ROLE_TYPE,
            )
            db.add_all([reusable_card, old_card])
            db.flush()
            db.add_all([
                models.CardImage(
                    card_id=reusable_card.id,
                    image_path="https://cdn.example/hero.png",
                    order=3,
                ),
                models.GeneratedImage(
                    card_id=reusable_card.id,
                    image_path="https://cdn.example/generated.png",
                    model_name="seedream",
                    is_reference=True,
                    task_id="task-1",
                    status="completed",
                ),
                models.SubjectCardAudio(
                    card_id=reusable_card.id,
                    audio_path="https://cdn.example/hero.mp3",
                    file_name="hero.mp3",
                    duration_seconds=-5,
                    is_reference=True,
                ),
                models.CardImage(
                    card_id=old_card.id,
                    image_path="https://cdn.example/old.png",
                    order=0,
                ),
            ])
            episode.storyboard_data = json.dumps(
                {
                    "subjects": [
                        {"name": "Hero", "type": ROLE_TYPE},
                        {"name": "Garden", "type": SCENE_TYPE, "ai_prompt": "quiet garden"},
                        {"name": "Unsupported", "type": "\u58f0\u97f3"},
                    ],
                    "shots": [
                        {
                            "shot_number": 1,
                            "subjects": [
                                {"name": "Hero", "type": ROLE_TYPE},
                                {"name": "Garden", "type": SCENE_TYPE},
                            ],
                            "original_text": "Scene text",
                            "voice_type": "dialogue",
                            "dialogue": [
                                {
                                    "speaker": "Hero",
                                    "gender": "F",
                                    "target": "Villain",
                                    "emotion": "angry",
                                    "text": "Stop",
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            )
            db.commit()

            storyboard_sync.create_shots_from_storyboard_data(episode.id, db)

            cards = db.query(models.SubjectCard).filter(
                models.SubjectCard.library_id == library.id
            ).order_by(models.SubjectCard.name.asc()).all()
            cards_by_name = {card.name: card for card in cards}
            self.assertEqual(set(cards_by_name), {"Garden", "Hero"})
            self.assertEqual(cards_by_name["Hero"].alias, "hero alias")
            self.assertEqual(cards_by_name["Hero"].ai_prompt, "hero prompt")
            self.assertEqual(cards_by_name["Hero"].role_personality, "brave")
            self.assertEqual(cards_by_name["Garden"].ai_prompt, "quiet garden")

            copied_images = db.query(models.CardImage).filter(
                models.CardImage.card_id == cards_by_name["Hero"].id
            ).all()
            self.assertEqual([(image.image_path, image.order) for image in copied_images], [
                ("https://cdn.example/hero.png", 3),
            ])
            copied_generated = db.query(models.GeneratedImage).filter(
                models.GeneratedImage.card_id == cards_by_name["Hero"].id
            ).one()
            self.assertEqual(copied_generated.image_path, "https://cdn.example/generated.png")
            self.assertTrue(copied_generated.is_reference)
            copied_audio = db.query(models.SubjectCardAudio).filter(
                models.SubjectCardAudio.card_id == cards_by_name["Hero"].id
            ).one()
            self.assertEqual(copied_audio.audio_path, "https://cdn.example/hero.mp3")
            self.assertEqual(copied_audio.duration_seconds, 0.0)

            shot = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.episode_id == episode.id
            ).one()
            self.assertEqual(
                json.loads(shot.selected_card_ids),
                [cards_by_name["Hero"].id, cards_by_name["Garden"].id],
            )
            expected_dialogue = "Hero\uff08F\uff09\u5bf9Villain\u8bf4\uff08angry\uff09\uff1aStop"
            self.assertEqual(shot.storyboard_dialogue, expected_dialogue)
            self.assertEqual(shot.sora_prompt, f"Scene text\n{expected_dialogue}")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
