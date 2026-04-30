import asyncio
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
TEST_APP_DB_PATH = Path(tempfile.gettempdir()) / "text2image_video_tasks_cancel_test.db"

try:
    TEST_APP_DB_PATH.unlink()
except FileNotFoundError:
    pass

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_APP_DB_PATH.as_posix()}"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402
import models  # noqa: E402


class VideoTasksCancelTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _create_user_episode_and_shot(self, username, task_id, video_status="processing"):
        db = self.Session()
        try:
            user = models.User(
                username=username,
                token=f"{username}-token",
                password_hash="hash",
                password_plain="123456",
            )
            db.add(user)
            db.flush()
            script = models.Script(user_id=user.id, name=f"{username}-script")
            db.add(script)
            db.flush()
            episode = models.Episode(script_id=script.id, name="S01")
            db.add(episode)
            db.flush()
            shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                task_id=task_id,
                video_status=video_status,
            )
            db.add(shot)
            db.commit()
            return int(user.id), int(shot.id)
        finally:
            db.close()

    def _get_user(self, db, user_id):
        return db.query(models.User).filter(models.User.id == user_id).first()

    def test_cancel_video_tasks_proxies_task_ids_to_upstream(self):
        user_id, _shot_id = self._create_user_episode_and_shot("tester", "task-a")
        db = self.Session()
        captured = {}
        original_cancel = main._cancel_upstream_video_tasks

        def fake_cancel(task_ids):
            captured["task_ids"] = task_ids
            return {
                "requested_count": len(task_ids),
                "status_code": 200,
                "ok": True,
                "response": {"cancelled": task_ids},
            }

        try:
            main._cancel_upstream_video_tasks = fake_cancel
            other_user_id, _other_shot_id = self._create_user_episode_and_shot("tester-2", "task-b")
            _ = other_user_id
            result = asyncio.run(
                main.cancel_video_tasks(
                    main.CancelVideoTasksRequest(task_ids=["task-a"]),
                    user=self._get_user(db, user_id),
                    db=db,
                )
            )
        finally:
            main._cancel_upstream_video_tasks = original_cancel
            db.close()

        self.assertEqual(captured["task_ids"], ["task-a"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["requested_count"], 1)

    def test_cancel_video_tasks_rejects_unowned_task_ids(self):
        user_id, _shot_id = self._create_user_episode_and_shot("owner", "owner-task")
        _other_user_id, _other_shot_id = self._create_user_episode_and_shot("other", "other-task")
        db = self.Session()
        try:
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    main.cancel_video_tasks(
                        main.CancelVideoTasksRequest(task_ids=["other-task"]),
                        user=self._get_user(db, user_id),
                        db=db,
                    )
                )
        finally:
            db.close()

        self.assertEqual(ctx.exception.status_code, 403)

    def test_cancel_video_tasks_rejects_mixed_owned_and_unowned_task_ids(self):
        user_id, _shot_id = self._create_user_episode_and_shot("owner", "owner-task")
        _other_user_id, _other_shot_id = self._create_user_episode_and_shot("other", "other-task")
        db = self.Session()
        called = False
        original_cancel = main._cancel_upstream_video_tasks

        def fake_cancel(_task_ids):
            nonlocal called
            called = True
            return {"requested_count": 0, "status_code": 200, "ok": True, "response": {}}

        try:
            main._cancel_upstream_video_tasks = fake_cancel
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    main.cancel_video_tasks(
                        main.CancelVideoTasksRequest(task_ids=["owner-task", "other-task"]),
                        user=self._get_user(db, user_id),
                        db=db,
                    )
                )
        finally:
            main._cancel_upstream_video_tasks = original_cancel
            db.close()

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertFalse(called)

    def test_cancel_video_tasks_raises_when_upstream_cancel_fails(self):
        user_id, _shot_id = self._create_user_episode_and_shot("tester", "task-a")
        db = self.Session()
        original_cancel = main._cancel_upstream_video_tasks

        def fake_cancel(_task_ids):
            return {
                "requested_count": 1,
                "status_code": 500,
                "ok": False,
                "response": {"detail": "upstream failed"},
            }

        try:
            main._cancel_upstream_video_tasks = fake_cancel
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(
                    main.cancel_video_tasks(
                        main.CancelVideoTasksRequest(task_ids=["task-a"]),
                        user=self._get_user(db, user_id),
                        db=db,
                    )
                )
        finally:
            main._cancel_upstream_video_tasks = original_cancel
            db.close()

        self.assertEqual(ctx.exception.status_code, 502)

    def test_cancel_video_tasks_runs_upstream_cancel_off_event_loop_thread(self):
        user_id, _shot_id = self._create_user_episode_and_shot("tester", "task-a")
        db = self.Session()
        caller_thread_id = threading.get_ident()
        called_thread_id = None
        original_cancel = main._cancel_upstream_video_tasks

        def fake_cancel(task_ids):
            nonlocal called_thread_id
            called_thread_id = threading.get_ident()
            return {
                "requested_count": len(task_ids),
                "status_code": 200,
                "ok": True,
                "response": {},
            }

        try:
            main._cancel_upstream_video_tasks = fake_cancel
            asyncio.run(
                main.cancel_video_tasks(
                    main.CancelVideoTasksRequest(task_ids=["task-a"]),
                    user=self._get_user(db, user_id),
                    db=db,
                )
            )
        finally:
            main._cancel_upstream_video_tasks = original_cancel
            db.close()

        self.assertIsNotNone(called_thread_id)
        self.assertNotEqual(called_thread_id, caller_thread_id)


if __name__ == "__main__":
    unittest.main()
