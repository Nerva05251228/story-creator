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
from api.services import storyboard2_video_tasks  # noqa: E402


class Storyboard2VideoTasksServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

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
                shot_number=7,
            )
            db.add(storyboard2_shot)
            db.flush()

            sub_shot = models.Storyboard2SubShot(
                storyboard2_shot_id=storyboard2_shot.id,
                sub_shot_index=3,
            )
            db.add(sub_shot)
            db.flush()

            video = models.Storyboard2SubShotVideo(
                sub_shot_id=sub_shot.id,
                task_id="stored-task",
            )
            missing_owner_video = models.Storyboard2SubShotVideo(
                sub_shot_id=9999,
                task_id="missing-task",
            )
            db.add_all([video, missing_owner_video])
            db.commit()

            self.video_id = int(video.id)
            self.missing_owner_video_id = int(missing_owner_video.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_build_storyboard2_video_name_tag_uses_shot_and_subshot_labels(self):
        db = self.Session()
        try:
            video = db.query(models.Storyboard2SubShotVideo).filter(
                models.Storyboard2SubShotVideo.id == self.video_id
            ).one()

            self.assertEqual(
                storyboard2_video_tasks.build_storyboard2_video_name_tag(video, db),
                f"storyboard2_shot_7_sub_3_video_{self.video_id}",
            )
        finally:
            db.close()

    def test_build_storyboard2_video_name_tag_falls_back_when_owner_records_are_missing(self):
        db = self.Session()
        try:
            video = db.query(models.Storyboard2SubShotVideo).filter(
                models.Storyboard2SubShotVideo.id == self.missing_owner_video_id
            ).one()

            self.assertEqual(
                storyboard2_video_tasks.build_storyboard2_video_name_tag(video, db),
                f"storyboard2_subshot_9999_video_{self.missing_owner_video_id}",
            )
        finally:
            db.close()

    def test_process_storyboard2_video_cover_and_cdn_returns_empty_url_failure_without_upload(self):
        db = self.Session()
        try:
            video = db.query(models.Storyboard2SubShotVideo).filter(
                models.Storyboard2SubShotVideo.id == self.video_id
            ).one()
            with patch.object(storyboard2_video_tasks, "process_and_upload_video_with_cover") as upload:
                result = storyboard2_video_tasks.process_storyboard2_video_cover_and_cdn(
                    video_record=video,
                    db=db,
                    upstream_video_url=" ",
                    task_id="",
                    debug_dir="ignored",
                )

            self.assertEqual(result, ("", "", False, {"success": False, "error": "empty video url"}))
            upload.assert_not_called()
        finally:
            db.close()

    def test_process_storyboard2_video_cover_and_cdn_uploads_with_name_tag_and_task_id_fallback(self):
        db = self.Session()
        try:
            video = db.query(models.Storyboard2SubShotVideo).filter(
                models.Storyboard2SubShotVideo.id == self.video_id
            ).one()
            process_result = {"success": True, "cdn_url": "https://cdn.example.test/video.mp4"}

            with patch.object(
                storyboard2_video_tasks,
                "process_and_upload_video_with_cover",
                return_value=process_result,
            ) as upload:
                result = storyboard2_video_tasks.process_storyboard2_video_cover_and_cdn(
                    video_record=video,
                    db=db,
                    upstream_video_url=" https://upstream.example.test/video.mp4 ",
                    task_id="",
                    debug_dir=None,
                )

            self.assertEqual(result, ("https://cdn.example.test/video.mp4", "https://cdn.example.test/video.mp4", True, process_result))
            upload.assert_called_once_with(
                remote_url="https://upstream.example.test/video.mp4",
                task_id="stored-task",
                name_tag=f"storyboard2_shot_7_sub_3_video_{self.video_id}",
            )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
