import json
import os
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}")

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402
import models  # noqa: E402


class StoryboardMultiSceneReferenceTests(unittest.TestCase):
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

            episode = models.Episode(script_id=script.id, name="ep")
            db.add(episode)
            db.flush()

            library = models.StoryLibrary(user_id=user.id, episode_id=episode.id, name="library")
            db.add(library)
            db.flush()

            self.role_card = models.SubjectCard(library_id=library.id, name="RoleA", card_type="角色")
            self.scene_card_one = models.SubjectCard(library_id=library.id, name="SceneA", card_type="场景")
            self.scene_card_two = models.SubjectCard(library_id=library.id, name="SceneB", card_type="场景")
            self.prop_card = models.SubjectCard(library_id=library.id, name="PropA", card_type="道具")
            db.add_all([self.role_card, self.scene_card_one, self.scene_card_two, self.prop_card])
            db.flush()

            db.add_all([
                models.GeneratedImage(
                    card_id=self.role_card.id,
                    image_path="https://cdn.example.com/role-ref.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
                models.GeneratedImage(
                    card_id=self.scene_card_one.id,
                    image_path="https://cdn.example.com/scene-ref-1.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
                models.GeneratedImage(
                    card_id=self.scene_card_two.id,
                    image_path="https://cdn.example.com/scene-ref-2.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
                models.GeneratedImage(
                    card_id=self.prop_card.id,
                    image_path="https://cdn.example.com/prop-ref.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
            ])

            shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                selected_card_ids=json.dumps(
                    [self.role_card.id, self.scene_card_one.id, self.scene_card_two.id, self.prop_card.id],
                    ensure_ascii=False,
                ),
            )
            db.add(shot)
            db.commit()

            self.shot_id = int(shot.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_collect_moti_v2_reference_assets_keeps_multiple_scenes_in_order(self):
        db = self.Session()
        try:
            shot = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.id == self.shot_id
            ).first()
            assets = main._collect_moti_v2_reference_assets(
                shot,
                db,
                first_frame_image_url="https://cdn.example.com/first-frame.png",
            )
        finally:
            db.close()

        self.assertEqual(
            assets["image_prefix_parts"],
            [
                "首帧[图片1]",
                "场景[图片2]",
                "场景[图片3]",
                "PropA[图片4]",
                "RoleA[图片5]",
            ],
        )
        self.assertEqual(
            assets["image_urls"],
            [
                "https://cdn.example.com/first-frame.png",
                "https://cdn.example.com/scene-ref-1.png",
                "https://cdn.example.com/scene-ref-2.png",
                "https://cdn.example.com/prop-ref.png",
                "https://cdn.example.com/role-ref.png",
            ],
        )


if __name__ == "__main__":
    unittest.main()
