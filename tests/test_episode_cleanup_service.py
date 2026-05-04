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
from api.services import episode_cleanup  # noqa: E402


class EpisodeCleanupServiceTests(unittest.TestCase):
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

            shot = models.StoryboardShot(episode_id=episode.id, shot_number=1, stable_id="shot-a")
            kept_shot = models.StoryboardShot(episode_id=episode.id, shot_number=2, stable_id="shot-b")
            db.add_all([shot, kept_shot])
            db.flush()

            storyboard2_shot = models.Storyboard2Shot(
                episode_id=episode.id,
                source_shot_id=shot.id,
                shot_number=1,
            )
            managed_session = models.ManagedSession(episode_id=episode.id, total_shots=1)
            db.add_all([storyboard2_shot, managed_session])
            db.flush()

            db.add_all([
                models.ShotCollage(shot_id=shot.id, collage_path="cdn://collage.png"),
                models.ShotVideo(shot_id=shot.id, video_path="cdn://video.mp4"),
                models.ShotDetailImage(shot_id=shot.id, sub_shot_index=1),
                models.ManagedTask(
                    session_id=managed_session.id,
                    shot_id=shot.id,
                    shot_stable_id=shot.stable_id,
                ),
                models.ShotVideo(shot_id=kept_shot.id, video_path="cdn://kept.mp4"),
            ])
            db.commit()

            self.episode_id = int(episode.id)
            self.shot_id = int(shot.id)
            self.kept_shot_id = int(kept_shot.id)
            self.storyboard2_shot_id = int(storyboard2_shot.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_normalize_storyboard_shot_ids_filters_invalid_values_and_preserves_order(self):
        self.assertEqual(
            episode_cleanup.normalize_storyboard_shot_ids([self.shot_id, "0", -1, "bad", self.kept_shot_id, self.shot_id]),
            [self.shot_id, self.kept_shot_id],
        )
        self.assertEqual(
            episode_cleanup.normalize_storyboard_shot_ids([0, "0", self.shot_id], allow_zero=True),
            [0, self.shot_id],
        )

    def test_delete_storyboard_shots_by_ids_cleans_dependencies_and_unlinks_storyboard2(self):
        db = self.Session()
        try:
            deleted = episode_cleanup.delete_storyboard_shots_by_ids(
                [self.shot_id, self.shot_id, -1, "bad"],
                db,
                log_context="test",
            )
            db.commit()

            storyboard2_shot = db.query(models.Storyboard2Shot).filter(
                models.Storyboard2Shot.id == self.storyboard2_shot_id
            ).first()
            remaining_shots = db.query(models.StoryboardShot).order_by(models.StoryboardShot.id.asc()).all()
            remaining_videos = db.query(models.ShotVideo).order_by(models.ShotVideo.shot_id.asc()).all()
            collage_count = db.query(models.ShotCollage).count()
            detail_count = db.query(models.ShotDetailImage).count()
            managed_task_count = db.query(models.ManagedTask).count()
        finally:
            db.close()

        self.assertEqual(deleted, 1)
        self.assertIsNone(storyboard2_shot.source_shot_id)
        self.assertEqual([shot.id for shot in remaining_shots], [self.kept_shot_id])
        self.assertEqual([video.shot_id for video in remaining_videos], [self.kept_shot_id])
        self.assertEqual(collage_count, 0)
        self.assertEqual(detail_count, 0)
        self.assertEqual(managed_task_count, 0)

    def test_delete_episode_storyboard_shots_removes_all_episode_shots(self):
        db = self.Session()
        try:
            deleted = episode_cleanup.delete_episode_storyboard_shots(self.episode_id, db)
            db.commit()
            remaining_shot_count = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.episode_id == self.episode_id
            ).count()
        finally:
            db.close()

        self.assertEqual(deleted, 2)
        self.assertEqual(remaining_shot_count, 0)


if __name__ == "__main__":
    unittest.main()
