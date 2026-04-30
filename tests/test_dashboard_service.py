import sys
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import dashboard_service
import models


class DashboardStatusInferenceTests(unittest.TestCase):
    def test_request_received_stays_submitting(self):
        self.assertEqual(
            dashboard_service._infer_debug_status(
                {"status": "request_received"},
                None,
            ),
            "submitting",
        )

    def test_task_submission_stays_processing(self):
        self.assertEqual(
            dashboard_service._infer_debug_status(
                {"task_id": "task-123"},
                None,
            ),
            "processing",
        )

    def test_error_output_maps_to_failed(self):
        self.assertEqual(
            dashboard_service._infer_debug_status(
                {"error": "boom"},
                None,
            ),
            "failed",
        )


class DashboardBatchSummaryTests(unittest.TestCase):
    def test_batch_summary_tracks_latest_status_per_batch(self):
        summary = dashboard_service.summarize_dashboard_batch_events(
            [
                {
                    "timestamp": "2026-04-03T10:00:00",
                    "status": "submitting",
                    "input": {"batch_idx": 1, "attempt": 1},
                },
                {
                    "timestamp": "2026-04-03T10:00:30",
                    "status": "failed",
                    "input": {"batch_idx": 1, "attempt": 1},
                    "output": {"attempt": 1, "error": "provider timeout"},
                },
                {
                    "timestamp": "2026-04-03T10:01:00",
                    "status": "submitting",
                    "input": {"batch_idx": 2, "attempt": 1},
                },
                {
                    "timestamp": "2026-04-03T10:01:10",
                    "status": "completed",
                    "input": {"batch_idx": 2, "attempt": 1},
                    "output": {"attempt": 1, "shots_count": 7},
                },
                {
                    "timestamp": "2026-04-03T10:02:00",
                    "status": "submitting",
                    "input": {"batch_idx": 1, "attempt": 2},
                },
            ]
        )

        self.assertTrue(summary["has_batches"])
        self.assertEqual(summary["overall_status"], "submitting")
        self.assertEqual(summary["counts"]["total"], 2)
        self.assertEqual(summary["counts"]["completed"], 1)
        self.assertEqual(summary["counts"]["submitting"], 1)

        batch1, batch2 = summary["items"]
        self.assertEqual(batch1["batch_id"], "1")
        self.assertEqual(batch1["latest_status"], "submitting")
        self.assertEqual(batch1["latest_attempt"], 2)
        self.assertEqual(batch1["failed_attempts"], [1])
        self.assertEqual(batch1["last_error"], "provider timeout")

        self.assertEqual(batch2["batch_id"], "2")
        self.assertEqual(batch2["latest_status"], "completed")
        self.assertEqual(batch2["latest_attempt"], 1)
        self.assertEqual(batch2["shots_count"], 7)

    def test_batch_summary_marks_task_completed_when_all_batches_complete(self):
        summary = dashboard_service.summarize_dashboard_batch_events(
            [
                {
                    "timestamp": "2026-04-03T10:00:00",
                    "status": "completed",
                    "input": {"batch_idx": 1, "attempt": 1},
                    "output": {"attempt": 1, "shots_count": 6},
                },
                {
                    "timestamp": "2026-04-03T10:00:01",
                    "status": "completed",
                    "input": {"batch_idx": 2, "attempt": 2},
                    "output": {"attempt": 2, "shots_count": 5},
                },
            ]
        )

        self.assertEqual(summary["overall_status"], "completed")
        self.assertEqual(summary["counts"]["completed"], 2)


class DashboardExternalTaskSyncTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_session_local = dashboard_service.SessionLocal
        dashboard_service.SessionLocal = self.Session

    def tearDown(self):
        dashboard_service.SessionLocal = self.original_session_local
        self.engine.dispose()

    def _create_record(self, *, task_key: str, external_task_id: str, task_type: str) -> int:
        db = self.Session()
        try:
            record = models.DashboardTaskLog(
                task_key=task_key,
                task_folder=task_key,
                task_type=task_type,
                stage=task_type,
                status="processing",
                external_task_id=external_task_id,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(record)
            db.commit()
            return int(record.id)
        finally:
            db.close()

    def test_sync_external_task_status_updates_completed_result(self):
        record_id = self._create_record(
            task_key="card_image_task_1",
            external_task_id="task-123",
            task_type="card_image_generate",
        )

        dashboard_service.sync_external_task_status_to_dashboard(
            external_task_id="task-123",
            status="completed",
            output_data={
                "task_id": "task-123",
                "images": ["https://cdn.example.com/image.png"],
            },
            stage="card_image_generate",
        )

        db = self.Session()
        try:
            record = db.query(models.DashboardTaskLog).filter(
                models.DashboardTaskLog.id == record_id
            ).first()
            self.assertIsNotNone(record)
            self.assertEqual(record.status, "completed")
            self.assertIn("cdn.example.com/image.png", record.result_payload)
            self.assertIn("cdn.example.com/image.png", record.result_summary)
            self.assertIn("\"images\"", record.result_summary)
            self.assertEqual(record.error_message, "")
        finally:
            db.close()

    def test_sync_external_task_status_updates_failed_error(self):
        record_id = self._create_record(
            task_key="video_task_1",
            external_task_id="task-456",
            task_type="video_generate",
        )

        dashboard_service.sync_external_task_status_to_dashboard(
            external_task_id="task-456",
            status="failed",
            raw_response={
                "task_id": "task-456",
                "error": "provider timeout",
            },
            stage="video_generate",
        )

        db = self.Session()
        try:
            record = db.query(models.DashboardTaskLog).filter(
                models.DashboardTaskLog.id == record_id
            ).first()
            self.assertIsNotNone(record)
            self.assertEqual(record.status, "failed")
            self.assertEqual(record.error_message, "provider timeout")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
