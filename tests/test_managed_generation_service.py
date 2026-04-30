import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import managed_generation_service
import models


class ManagedGenerationServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _create_session_and_shots(self):
        db = self.Session()
        try:
            managed_session = models.ManagedSession(
                episode_id=1,
                status="running",
                provider="moti",
                variant_count=1,
            )
            db.add(managed_session)
            db.flush()

            original_shot = models.StoryboardShot(
                episode_id=1,
                shot_number=1,
                variant_index=0,
                stable_id="stable-shot-1",
                video_status="idle",
            )
            reserved_shot = models.StoryboardShot(
                episode_id=1,
                shot_number=1,
                variant_index=1,
                stable_id="stable-shot-1",
                video_status="idle",
            )
            db.add(original_shot)
            db.add(reserved_shot)
            db.flush()
            return db, managed_session, original_shot, reserved_shot
        except Exception:
            db.close()
            raise

    def _create_owned_episode_and_shot(self, appoint_account="account-a"):
        db = self.Session()
        try:
            user = models.User(
                username="managed-user",
                token="managed-token",
                password_hash="hash",
                password_plain="123456",
            )
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="managed-script")
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="S01",
                storyboard_video_model="Seedance 2.0",
                storyboard_video_aspect_ratio="1:1",
                storyboard_video_duration=5,
                storyboard_video_appoint_account=appoint_account,
            )
            db.add(episode)
            db.flush()

            shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                variant_index=0,
                provider="moti",
                storyboard_video_model="Seedance 2.0",
                aspect_ratio="1:1",
                duration=5,
            )
            db.add(shot)
            db.commit()
            return db, episode, shot
        except Exception:
            db.close()
            raise

    def test_submit_video_generation_passes_episode_appoint_account(self):
        db, _episode, shot = self._create_owned_episode_and_shot()
        captured = {}

        class FakeSubmitResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {"task_id": "managed-task-1"}

        def fake_post(_url, headers=None, json=None, timeout=None):
            captured["json"] = json
            return FakeSubmitResponse()

        poller = managed_generation_service.ManagedGenerationPoller()
        try:
            with patch.object(
                managed_generation_service.requests,
                "post",
                side_effect=fake_post,
            ):
                task_id, error, _prompt = poller._submit_video_generation(
                    shot,
                    "moti",
                    db,
                    prompt_override="managed prompt",
                )
        finally:
            db.close()

        self.assertEqual(task_id, "managed-task-1")
        self.assertIsNone(error)
        self.assertEqual(captured["json"]["extra"], {"appoint_accounts": ["account-a"]})

    def test_process_pending_task_writes_reserved_shot_task_id(self):
        db, managed_session, _original_shot, reserved_shot = self._create_session_and_shots()
        try:
            task = models.ManagedTask(
                session_id=managed_session.id,
                shot_id=reserved_shot.id,
                shot_stable_id="stable-shot-1",
                status="pending",
            )
            db.add(task)
            db.commit()

            poller = managed_generation_service.ManagedGenerationPoller()
            poller._sync_dashboard_task = lambda task: None
            poller._retry_if_needed = lambda task, db: None
            poller._submit_video_generation = lambda *args, **kwargs: (
                "task-123",
                None,
                "full prompt",
            )

            poller._process_pending_tasks(db)
            db.expire_all()

            updated_task = db.query(models.ManagedTask).filter(
                models.ManagedTask.id == task.id
            ).first()
            updated_reserved_shot = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.id == reserved_shot.id
            ).first()

            self.assertEqual(updated_task.status, "processing")
            self.assertEqual(updated_task.task_id, "task-123")
            self.assertEqual(updated_reserved_shot.video_status, "processing")
            self.assertEqual(updated_reserved_shot.task_id, "task-123")
            self.assertIsNotNone(updated_reserved_shot.video_submitted_at)
        finally:
            db.close()

    def test_process_processing_task_marks_reserved_shot_failed(self):
        db, managed_session, _original_shot, reserved_shot = self._create_session_and_shots()
        try:
            reserved_shot.video_status = "processing"
            reserved_shot.task_id = "task-456"
            db.flush()

            task = models.ManagedTask(
                session_id=managed_session.id,
                shot_id=reserved_shot.id,
                shot_stable_id="stable-shot-1",
                status="processing",
                task_id="task-456",
            )
            db.add(task)
            db.commit()

            poller = managed_generation_service.ManagedGenerationPoller()
            poller._sync_dashboard_task = lambda task: None
            poller._retry_if_needed = lambda task, db: None

            with patch.object(
                managed_generation_service,
                "check_video_status",
                return_value={
                    "status": "failed",
                    "error_message": "音频可能包含不当内容",
                    "video_url": "",
                },
            ):
                poller._process_processing_tasks(db)

            db.expire_all()
            updated_task = db.query(models.ManagedTask).filter(
                models.ManagedTask.id == task.id
            ).first()
            updated_reserved_shot = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.id == reserved_shot.id
            ).first()

            self.assertEqual(updated_task.status, "failed")
            self.assertEqual(updated_reserved_shot.video_status, "failed")
            self.assertEqual(updated_reserved_shot.video_error_message, "音频可能包含不当内容")
            self.assertTrue(updated_reserved_shot.video_path.startswith("error:"))
            self.assertEqual(updated_reserved_shot.task_id, "")
        finally:
            db.close()

    def test_reconcile_reserved_slot_repairs_stale_failed_shot(self):
        db, managed_session, _original_shot, reserved_shot = self._create_session_and_shots()
        try:
            reserved_shot.video_status = "processing"
            reserved_shot.task_id = ""
            db.flush()

            task = models.ManagedTask(
                session_id=managed_session.id,
                shot_id=reserved_shot.id,
                shot_stable_id="stable-shot-1",
                status="failed",
                task_id="task-789",
                error_message="音频可能包含不当内容",
                completed_at=datetime.utcnow(),
            )
            db.add(task)
            db.commit()

            poller = managed_generation_service.ManagedGenerationPoller()
            repaired = poller._reconcile_reserved_slot_shot_states(db)

            db.expire_all()
            updated_reserved_shot = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.id == reserved_shot.id
            ).first()

            self.assertGreaterEqual(repaired, 1)
            self.assertEqual(updated_reserved_shot.video_status, "failed")
            self.assertEqual(updated_reserved_shot.video_error_message, "音频可能包含不当内容")
            self.assertEqual(updated_reserved_shot.task_id, "")
        finally:
            db.close()

    def test_reconcile_marks_stale_orphaned_active_shot_failed(self):
        db, _managed_session, _original_shot, reserved_shot = self._create_session_and_shots()
        try:
            reserved_shot.video_status = "submitting"
            reserved_shot.task_id = ""
            reserved_shot.video_submitted_at = datetime.utcnow() - timedelta(hours=2)
            db.commit()

            poller = managed_generation_service.ManagedGenerationPoller()
            repaired = poller._reconcile_reserved_slot_shot_states(db)

            db.expire_all()
            updated_reserved_shot = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.id == reserved_shot.id
            ).first()

            self.assertGreaterEqual(repaired, 1)
            self.assertEqual(updated_reserved_shot.video_status, "failed")
            self.assertEqual(updated_reserved_shot.video_error_message, "任务提交状态已丢失，请重新生成")
            self.assertTrue(updated_reserved_shot.video_path.startswith("error:"))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
