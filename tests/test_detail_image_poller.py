import json
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

import image_generation_service
import models


class DetailImagePollerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_session_local = image_generation_service.SessionLocal
        image_generation_service.SessionLocal = self.Session

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
            shot = models.StoryboardShot(episode_id=episode.id, shot_number=1)
            db.add(shot)
            db.flush()
            detail = models.ShotDetailImage(
                shot_id=shot.id,
                sub_shot_index=1,
                status="processing",
                task_id="detail-task-1",
                provider="banana",
                model_name="banana2",
                submit_api_url="https://submit.example.com",
                status_api_url="https://status.example.com",
            )
            db.add(detail)
            db.commit()
            self.shot_id = int(shot.id)
            self.detail_id = int(detail.id)
        finally:
            db.close()

    def tearDown(self):
        image_generation_service.SessionLocal = self.original_session_local
        self.engine.dispose()

    def _load_detail(self):
        db = self.Session()
        try:
            detail = db.query(models.ShotDetailImage).filter(models.ShotDetailImage.id == self.detail_id).first()
            shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == self.shot_id).first()
            return detail, shot
        finally:
            db.close()

    @patch("image_generation_service.sync_external_task_status_to_dashboard")
    @patch("image_generation_service.download_and_upload_image")
    @patch("image_generation_service.get_image_task_status")
    def test_detail_image_poller_recovers_after_transient_query_failures(
        self,
        mock_get_status,
        mock_download,
        mock_dashboard_sync,
    ):
        mock_get_status.side_effect = [
            {
                "status": "query_failed",
                "error_message": "查询异常: EOF",
                "query_ok": False,
                "query_transient": True,
            },
            {
                "status": "query_failed",
                "error_message": "查询异常: EOF",
                "query_ok": False,
                "query_transient": True,
            },
            {
                "status": "completed",
                "images": ["https://upstream.example.com/output.png"],
                "raw_status": "SUCCESS",
            },
        ]
        mock_download.return_value = "https://cdn.example.com/output.png"

        poller = image_generation_service.ImageGenerationPoller()
        poller._poll_once()
        detail, shot = self._load_detail()
        self.assertEqual(detail.status, "processing")
        self.assertEqual(detail.query_error_count, 1)
        self.assertIn("EOF", detail.last_query_error)

        poller._poll_once()
        detail, shot = self._load_detail()
        self.assertEqual(detail.status, "processing")
        self.assertEqual(detail.query_error_count, 2)

        poller._poll_once()
        detail, shot = self._load_detail()
        self.assertEqual(detail.status, "completed")
        self.assertEqual(detail.query_error_count, 0)
        self.assertEqual(json.loads(detail.images_json), ["https://cdn.example.com/output.png"])
        self.assertEqual(shot.storyboard_image_path, "https://cdn.example.com/output.png")
        self.assertEqual(shot.storyboard_image_status, "completed")
        mock_dashboard_sync.assert_called()

    @patch("image_generation_service.sync_external_task_status_to_dashboard")
    @patch("image_generation_service.download_and_upload_image")
    @patch("image_generation_service.get_image_task_status")
    def test_detail_image_poller_persists_all_upstream_images(
        self,
        mock_get_status,
        mock_download,
        mock_dashboard_sync,
    ):
        upstream_images = [
            "https://upstream.example.com/output-1.png",
            "https://upstream.example.com/output-2.png",
            "https://upstream.example.com/output-3.png",
            "https://upstream.example.com/output-4.png",
        ]
        mock_get_status.return_value = {
            "status": "completed",
            "images": upstream_images,
            "raw_status": "SUCCESS",
        }
        mock_download.side_effect = [
            "https://cdn.example.com/output-1.png",
            "https://cdn.example.com/output-2.png",
            "https://cdn.example.com/output-3.png",
            "https://cdn.example.com/output-4.png",
        ]

        poller = image_generation_service.ImageGenerationPoller()
        poller._poll_once()

        detail, shot = self._load_detail()
        self.assertEqual(detail.status, "completed")
        self.assertEqual(
            json.loads(detail.images_json),
            [
                "https://cdn.example.com/output-1.png",
                "https://cdn.example.com/output-2.png",
                "https://cdn.example.com/output-3.png",
                "https://cdn.example.com/output-4.png",
            ],
        )
        self.assertEqual(shot.storyboard_image_path, "https://cdn.example.com/output-1.png")
        self.assertEqual(mock_download.call_count, 4)
        mock_dashboard_sync.assert_called()

    @patch("image_generation_service.sync_external_task_status_to_dashboard")
    @patch("image_generation_service.get_image_task_status")
    def test_detail_image_poller_marks_failed_after_ten_transient_query_failures(
        self,
        mock_get_status,
        mock_dashboard_sync,
    ):
        mock_get_status.return_value = {
            "status": "query_failed",
            "error_message": "查询异常: TLS EOF",
            "query_ok": False,
            "query_transient": True,
        }

        poller = image_generation_service.ImageGenerationPoller()
        for _ in range(10):
            poller._poll_once()

        detail, _shot = self._load_detail()
        self.assertEqual(detail.status, "failed")
        self.assertEqual(detail.query_error_count, 10)
        self.assertIn("连续查询异常 10 次", detail.error_message)
        mock_dashboard_sync.assert_called()


if __name__ == "__main__":
    unittest.main()
