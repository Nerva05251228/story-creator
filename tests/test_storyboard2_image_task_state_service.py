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
from api.services import storyboard2_image_task_state  # noqa: E402


class Storyboard2ImageTaskStateServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        storyboard2_image_task_state.storyboard2_active_image_tasks.clear()

        db = self.Session()
        try:
            user = models.User(username="owner", token="token", password_hash="hash", password_plain="123456")
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="script")
            db.add(script)
            db.flush()

            episode = models.Episode(script_id=script.id, name="episode")
            db.add(episode)
            db.flush()

            storyboard2_shot = models.Storyboard2Shot(
                episode_id=episode.id,
                shot_number=1,
                source_shot_id=0,
                excerpt="excerpt",
            )
            db.add(storyboard2_shot)
            db.flush()

            orphan_sub_shot = models.Storyboard2SubShot(
                storyboard2_shot_id=storyboard2_shot.id,
                sub_shot_index=1,
                image_generate_status="processing",
                image_generate_progress="queued",
                image_generate_error="",
            )
            preserved_error_sub_shot = models.Storyboard2SubShot(
                storyboard2_shot_id=storyboard2_shot.id,
                sub_shot_index=2,
                image_generate_status="processing",
                image_generate_progress="queued",
                image_generate_error="kept error",
            )
            active_sub_shot = models.Storyboard2SubShot(
                storyboard2_shot_id=storyboard2_shot.id,
                sub_shot_index=3,
                image_generate_status="processing",
                image_generate_progress="working",
                image_generate_error="",
            )
            completed_sub_shot = models.Storyboard2SubShot(
                storyboard2_shot_id=storyboard2_shot.id,
                sub_shot_index=4,
                image_generate_status="completed",
                image_generate_progress="done",
                image_generate_error="",
            )
            db.add_all(
                [
                    orphan_sub_shot,
                    preserved_error_sub_shot,
                    active_sub_shot,
                    completed_sub_shot,
                ]
            )
            db.commit()

            self.episode_id = int(episode.id)
            self.orphan_sub_shot_id = int(orphan_sub_shot.id)
            self.preserved_error_sub_shot_id = int(preserved_error_sub_shot.id)
            self.active_sub_shot_id = int(active_sub_shot.id)
            self.completed_sub_shot_id = int(completed_sub_shot.id)
        finally:
            db.close()

    def tearDown(self):
        storyboard2_image_task_state.storyboard2_active_image_tasks.clear()
        self.engine.dispose()

    def test_mark_active_and_inactive_track_integer_task_ids(self):
        storyboard2_image_task_state.mark_storyboard2_image_task_active("7")
        storyboard2_image_task_state.mark_storyboard2_image_task_active("bad")

        self.assertTrue(storyboard2_image_task_state.is_storyboard2_image_task_active(7))
        self.assertFalse(storyboard2_image_task_state.is_storyboard2_image_task_active("bad"))

        storyboard2_image_task_state.mark_storyboard2_image_task_inactive("7")
        storyboard2_image_task_state.mark_storyboard2_image_task_inactive("bad")

        self.assertFalse(storyboard2_image_task_state.is_storyboard2_image_task_active(7))

    def test_recover_orphan_storyboard2_image_tasks_marks_only_inactive_processing_rows(self):
        storyboard2_image_task_state.mark_storyboard2_image_task_active(self.active_sub_shot_id)

        db = self.Session()
        try:
            recovered_count = storyboard2_image_task_state.recover_orphan_storyboard2_image_tasks(
                self.episode_id,
                db,
            )
            self.assertEqual(recovered_count, 2)
        finally:
            db.close()

        verify_db = self.Session()
        try:
            orphan_sub_shot = verify_db.query(models.Storyboard2SubShot).filter_by(id=self.orphan_sub_shot_id).one()
            preserved_error_sub_shot = (
                verify_db.query(models.Storyboard2SubShot).filter_by(id=self.preserved_error_sub_shot_id).one()
            )
            active_sub_shot = verify_db.query(models.Storyboard2SubShot).filter_by(id=self.active_sub_shot_id).one()
            completed_sub_shot = verify_db.query(models.Storyboard2SubShot).filter_by(id=self.completed_sub_shot_id).one()

            self.assertEqual(orphan_sub_shot.image_generate_status, "failed")
            self.assertEqual(orphan_sub_shot.image_generate_progress, "")
            self.assertTrue(orphan_sub_shot.image_generate_error)

            self.assertEqual(preserved_error_sub_shot.image_generate_status, "failed")
            self.assertEqual(preserved_error_sub_shot.image_generate_progress, "")
            self.assertEqual(preserved_error_sub_shot.image_generate_error, "kept error")

            self.assertEqual(active_sub_shot.image_generate_status, "processing")
            self.assertEqual(active_sub_shot.image_generate_progress, "working")

            self.assertEqual(completed_sub_shot.image_generate_status, "completed")
            self.assertEqual(completed_sub_shot.image_generate_progress, "done")
        finally:
            verify_db.close()


if __name__ == "__main__":
    unittest.main()
