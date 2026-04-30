import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import dashboard_query_service


class DashboardQueryServiceTests(unittest.TestCase):
    def test_card_image_task_uses_image_status_query(self):
        record = SimpleNamespace(
            id=7,
            task_type="card_image_generate",
            provider="banana",
            model_name="banana2",
            external_task_id="img-task-1",
            api_url="https://submit.example.com",
            status_api_url="https://status.example.com",
        )

        with patch("dashboard_query_service.query_image_task_status_raw", return_value={"status": "RUNNING"}) as mock_query:
            result = dashboard_query_service.query_dashboard_task(record)

        mock_query.assert_called_once_with(
            "img-task-1",
            model_name="banana2",
            provider="banana",
        )
        self.assertEqual(result["query_kind"], "image")
        self.assertEqual(result["query_result"], {"status": "RUNNING"})

    def test_missing_external_task_id_is_rejected(self):
        record = SimpleNamespace(
            id=8,
            task_type="video_generate",
            provider="yijia",
            model_name="",
            external_task_id="",
            api_url="",
            status_api_url="",
        )

        with self.assertRaises(ValueError):
            dashboard_query_service.query_dashboard_task(record)


if __name__ == "__main__":
    unittest.main()
