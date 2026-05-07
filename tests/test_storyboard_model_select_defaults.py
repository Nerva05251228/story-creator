import asyncio
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


class StoryboardModelSelectDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_get_model_configs_includes_storyboard_defaults(self):
        with self.Session() as db:
            payload = asyncio.run(main.get_model_configs(db=db))

        self.assertIn("storyboard_defaults", payload)
        defaults = payload["storyboard_defaults"]
        self.assertIn("detail_images_provider", defaults)
        self.assertIn("detail_images_model", defaults)
        self.assertIn("storyboard_video_model", defaults)

    def test_build_episode_storyboard_defaults_uses_global_settings(self):
        with self.Session() as db:
            script = models.Script(user_id=1, name="test-script")
            db.add(script)
            db.add_all([
                models.GlobalSettings(
                    key="storyboard_default_detail_images_provider",
                    value="momo",
                ),
                models.GlobalSettings(
                    key="storyboard_default_detail_images_model",
                    value="banana-pro",
                ),
                models.GlobalSettings(
                    key="storyboard_default_video_model",
                    value="Seedance 2.0 Fast",
                ),
            ])
            db.commit()
            db.refresh(script)

            episode_payload = main.EpisodeCreate(name="E01", content="")
            values = main._build_episode_storyboard_sora_create_values(
                script.id,
                episode_payload,
                db,
            )

        self.assertEqual(values["detail_images_provider"], "momo")
        self.assertEqual(values["detail_images_model"], "nano-banana-pro")
        self.assertEqual(values["storyboard_video_model"], "Seedance 2.0 Fast")


if __name__ == "__main__":
    unittest.main()
