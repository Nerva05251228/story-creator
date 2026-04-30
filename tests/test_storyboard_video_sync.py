import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main
import models


class StoryboardVideoSyncTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _create_processing_shot(self):
        db = self.Session()
        try:
            user = models.User(
                username="sync-user",
                token="sync-token",
            )
            db.add(user)
            db.flush()

            script = models.Script(
                user_id=user.id,
                name="同步测试剧本",
            )
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="第1集",
            )
            db.add(episode)
            db.flush()

            shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                variant_index=0,
                video_status="processing",
                task_id="completed-task-1",
            )
            db.add(shot)
            db.commit()
            return db, episode, shot
        except Exception:
            db.close()
            raise

    def test_sync_processing_videos_marks_completed_shot(self):
        db, episode, shot = self._create_processing_shot()
        try:
            with patch.object(
                main,
                "check_video_status",
                return_value={
                    "status": "completed",
                    "video_url": "https://example.com/video.mp4",
                },
            ):
                updated_count = main._sync_processing_storyboard_videos_for_episode(
                    episode.id,
                    db,
                    max_count=10,
                )

            db.expire_all()
            updated_shot = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.id == shot.id
            ).first()

            self.assertGreaterEqual(updated_count, 1)
            self.assertEqual(updated_shot.video_status, "completed")
            self.assertEqual(updated_shot.video_path, "https://example.com/video.mp4")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
