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
from api.services import storyboard2_board  # noqa: E402


ROLE_TYPE = "\u89d2\u8272"
SCENE_TYPE = "\u573a\u666f"
PROP_TYPE = "\u9053\u5177"
SOUND_TYPE = "\u58f0\u97f3"


class Storyboard2BoardServiceTests(unittest.TestCase):
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

            self.role_card = models.SubjectCard(
                library_id=library.id,
                name="Hero",
                alias="H",
                card_type=ROLE_TYPE,
            )
            self.scene_card = models.SubjectCard(
                library_id=library.id,
                name="Room",
                alias="R",
                card_type=SCENE_TYPE,
                ai_prompt=(
                    "\u751f\u6210\u56fe\u7247\u7684\u98ce\u683c\u662f\uff1anoir\n"
                    "\u751f\u6210\u56fe\u7247\u4e2d\u573a\u666f\u7684\u662f\uff1amoon room"
                ),
            )
            self.prop_card = models.SubjectCard(
                library_id=library.id,
                name="Key",
                alias="K",
                card_type=PROP_TYPE,
            )
            self.sound_card = models.SubjectCard(
                library_id=library.id,
                name="Voice",
                card_type=SOUND_TYPE,
            )
            db.add_all([self.role_card, self.scene_card, self.prop_card, self.sound_card])
            db.flush()

            self.source_shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                variant_index=0,
                script_excerpt="source excerpt",
                selected_card_ids=json.dumps([
                    self.role_card.id,
                    self.scene_card.id,
                    self.prop_card.id,
                    self.sound_card.id,
                ], ensure_ascii=False),
            )
            db.add(self.source_shot)
            db.commit()

            self.episode_id = int(episode.id)
            self.source_shot_id = int(self.source_shot.id)
            self.role_card_id = int(self.role_card.id)
            self.scene_card_id = int(self.scene_card.id)
            self.prop_card_id = int(self.prop_card.id)
            self.sound_card_id = int(self.sound_card.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_initialize_creates_board_from_source_shots_and_extracts_scene_override(self):
        db = self.Session()
        try:
            initialized = storyboard2_board.ensure_storyboard2_initialized(self.episode_id, db)
            initialized_again = storyboard2_board.ensure_storyboard2_initialized(self.episode_id, db)

            board_shot = db.query(models.Storyboard2Shot).filter(
                models.Storyboard2Shot.episode_id == self.episode_id
            ).one()
            sub_shot = db.query(models.Storyboard2SubShot).filter(
                models.Storyboard2SubShot.storyboard2_shot_id == board_shot.id
            ).one()
        finally:
            db.close()

        self.assertTrue(initialized)
        self.assertFalse(initialized_again)
        self.assertEqual(board_shot.source_shot_id, self.source_shot_id)
        self.assertEqual(board_shot.excerpt, "source excerpt")
        self.assertEqual(json.loads(board_shot.selected_card_ids), [
            self.role_card_id,
            self.scene_card_id,
            self.prop_card_id,
            self.sound_card_id,
        ])
        self.assertEqual(sub_shot.sub_shot_index, 1)
        self.assertEqual(sub_shot.scene_override, "Roommoon room")

    def test_serialize_board_sorts_subjects_uses_previews_and_video_state(self):
        db = self.Session()
        try:
            storyboard2_shot = models.Storyboard2Shot(
                episode_id=self.episode_id,
                source_shot_id=self.source_shot_id,
                shot_number=1,
                excerpt="board excerpt",
                selected_card_ids="[]",
                display_order=1,
            )
            db.add(storyboard2_shot)
            db.flush()
            sub_shot = models.Storyboard2SubShot(
                storyboard2_shot_id=storyboard2_shot.id,
                sub_shot_index=1,
                time_range="0-3s",
                visual_text="visual",
                audio_text="audio",
                sora_prompt="prompt",
                selected_card_ids="[]",
            )
            db.add(sub_shot)
            db.flush()

            first_image = models.Storyboard2SubShotImage(
                sub_shot_id=sub_shot.id,
                image_url="https://cdn.example.com/first.png",
                size="1:2",
            )
            current_image = models.Storyboard2SubShotImage(
                sub_shot_id=sub_shot.id,
                image_url="https://cdn.example.com/current.png",
                size="2:1",
            )
            db.add_all([first_image, current_image])
            db.flush()
            sub_shot.current_image_id = current_image.id

            db.add_all([
                models.GeneratedImage(
                    card_id=self.role_card_id,
                    image_path="https://cdn.example.com/hero-ref.png",
                    model_name="seedream",
                    is_reference=True,
                    status="completed",
                ),
                models.CardImage(
                    card_id=self.prop_card_id,
                    image_path="https://cdn.example.com/key-upload.png",
                    order=4,
                ),
                models.Storyboard2SubShotVideo(
                    sub_shot_id=sub_shot.id,
                    task_id="video-task",
                    model_name="grok",
                    duration=6,
                    aspect_ratio="2:1",
                    status="running",
                    progress=27,
                    video_url="",
                    thumbnail_url="",
                    cdn_uploaded=False,
                ),
            ])
            db.commit()

            payload = storyboard2_board.serialize_storyboard2_board(self.episode_id, db)
        finally:
            db.close()

        self.assertEqual(payload["episode_id"], self.episode_id)
        self.assertEqual(
            [item["name"] for item in payload["available_subjects"]],
            ["Hero", "Room", "Key"],
        )
        self.assertEqual(payload["available_subjects"][0]["preview_image"], "https://cdn.example.com/hero-ref.png")
        self.assertEqual(payload["available_subjects"][2]["preview_image"], "https://cdn.example.com/key-upload.png")

        shot_payload = payload["shots"][0]
        self.assertEqual(shot_payload["selected_card_ids"], [
            self.role_card_id,
            self.scene_card_id,
            self.prop_card_id,
        ])
        sub_payload = shot_payload["sub_shots"][0]
        self.assertEqual(sub_payload["scene_override"], "Roommoon room")
        self.assertEqual(sub_payload["current_image"]["image_url"], "https://cdn.example.com/current.png")
        self.assertEqual(sub_payload["current_image"]["size"], "16:9")
        self.assertEqual(len(sub_payload["candidates"]), 2)
        self.assertEqual(sub_payload["videos"][0]["status"], "processing")
        self.assertEqual(sub_payload["videos"][0]["aspect_ratio"], "16:9")
        self.assertEqual(sub_payload["video_generate_status"], "processing")
        self.assertEqual(sub_payload["video_generate_progress"], 27)


if __name__ == "__main__":
    unittest.main()
