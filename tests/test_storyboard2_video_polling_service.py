import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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
from api.services import storyboard2_video_polling  # noqa: E402


class _FakeThread:
    created = []

    def __init__(self, target=None, args=None, kwargs=None):
        self.target = target
        self.args = tuple(args or ())
        self.kwargs = dict(kwargs or {})
        self.daemon = False
        self.started = False
        _FakeThread.created.append(self)

    def start(self):
        self.started = True


class Storyboard2VideoPollingServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        _FakeThread.created = []

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

            sub_shot = models.Storyboard2SubShot(
                storyboard2_shot_id=storyboard2_shot.id,
                sub_shot_index=1,
                time_range="0s-3s",
            )
            db.add(sub_shot)
            db.flush()

            processing_video = models.Storyboard2SubShotVideo(
                sub_shot_id=sub_shot.id,
                task_id="task-1",
                status="processing",
                progress=25,
                is_deleted=False,
            )
            recovery_video = models.Storyboard2SubShotVideo(
                sub_shot_id=sub_shot.id,
                task_id="task-2",
                status="submitted",
                progress=0,
                is_deleted=False,
            )
            missing_task_video = models.Storyboard2SubShotVideo(
                sub_shot_id=sub_shot.id,
                task_id="",
                status="processing",
                progress=15,
                is_deleted=False,
            )
            db.add_all([processing_video, recovery_video, missing_task_video])
            db.commit()

            self.episode_id = int(episode.id)
            self.processing_video_id = int(processing_video.id)
            self.recovery_video_id = int(recovery_video.id)
            self.missing_task_video_id = int(missing_task_video.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_poll_storyboard2_sub_shot_video_status_marks_completed_video_and_saves_debug(self):
        debug_calls = []

        with patch.object(storyboard2_video_polling, "SessionLocal", self.Session), patch.object(
            storyboard2_video_polling,
            "check_video_status",
            return_value={
                "status": "completed",
                "video_url": "https://cdn.example.test/video.mp4",
                "progress": 100,
                "cdn_uploaded": True,
            },
        ), patch.object(
            storyboard2_video_polling.billing_service,
            "finalize_charge_entry",
        ) as finalize_charge, patch.object(
            storyboard2_video_polling.billing_service,
            "reverse_charge_entry",
        ) as reverse_charge:
            storyboard2_video_polling.poll_storyboard2_sub_shot_video_status(
                self.processing_video_id,
                "task-1",
                debug_dir="debug-dir",
                save_debug_fn=lambda debug_dir, filename, payload: debug_calls.append((debug_dir, filename, payload)),
            )

        db = self.Session()
        try:
            video = db.query(models.Storyboard2SubShotVideo).filter_by(id=self.processing_video_id).one()
            self.assertEqual(video.status, "completed")
            self.assertEqual(video.video_url, "https://cdn.example.test/video.mp4")
            self.assertEqual(video.thumbnail_url, "https://cdn.example.test/video.mp4")
            self.assertEqual(int(video.progress or 0), 100)
            self.assertTrue(video.cdn_uploaded)
        finally:
            db.close()

        finalize_charge.assert_called_once()
        reverse_charge.assert_not_called()
        self.assertEqual([call[1] for call in debug_calls], ["output.json", "polling_history.json"])

    def test_recover_storyboard2_video_polling_starts_threads_for_processing_records_with_task_ids(self):
        with patch.object(storyboard2_video_polling, "SessionLocal", self.Session), patch.object(
            storyboard2_video_polling,
            "Thread",
            _FakeThread,
        ):
            storyboard2_video_polling.recover_storyboard2_video_polling()

        created = [(thread.args, thread.started, thread.daemon) for thread in _FakeThread.created]
        self.assertIn(((self.processing_video_id, "task-1"), True, True), created)
        self.assertIn(((self.recovery_video_id, "task-2"), True, True), created)
        self.assertNotIn(((self.missing_task_video_id, ""), True, True), created)

    def test_sync_storyboard2_processing_videos_skips_transient_status_errors(self):
        db = self.Session()
        try:
            missing_task_video = db.query(models.Storyboard2SubShotVideo).filter_by(id=self.missing_task_video_id).one()
            missing_task_video.status = "failed"
            db.commit()

            with patch.object(
                storyboard2_video_polling,
                "check_video_status",
                return_value={"status": "query_failed", "error_message": "retry later"},
            ), patch.object(
                storyboard2_video_polling,
                "is_transient_video_status_error",
                return_value=True,
            ):
                updated_count = storyboard2_video_polling.sync_storyboard2_processing_videos(
                    self.episode_id,
                    db,
                )

            self.assertEqual(updated_count, 0)
            db.expire_all()
            video = db.query(models.Storyboard2SubShotVideo).filter_by(id=self.processing_video_id).one()
            self.assertEqual(video.status, "processing")
            self.assertEqual(int(video.progress or 0), 25)
        finally:
            db.close()

    def test_sync_storyboard2_processing_videos_marks_missing_task_id_failed(self):
        db = self.Session()
        try:
            updated_count = storyboard2_video_polling.sync_storyboard2_processing_videos(
                self.episode_id,
                db,
            )
            self.assertGreaterEqual(updated_count, 1)

            db.expire_all()
            video = db.query(models.Storyboard2SubShotVideo).filter_by(id=self.missing_task_video_id).one()
            self.assertEqual(video.status, "failed")
            self.assertEqual(int(video.progress or 0), 0)
            self.assertTrue(video.error_message)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
