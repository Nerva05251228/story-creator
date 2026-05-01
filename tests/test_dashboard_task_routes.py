import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}",
)

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tests.env_defaults import apply_test_env_defaults  # noqa: E402

apply_test_env_defaults()

import api.services.admin_auth as admin_auth  # noqa: E402
import database  # noqa: E402
import main  # noqa: E402
import models  # noqa: E402


ADMIN_PASSWORD = "dashboard-test-admin"


class DashboardTaskRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._httpx_client_init = httpx.Client.__init__
        if "app" not in cls._httpx_client_init.__code__.co_varnames:

            def compatible_client_init(self, *args, app=None, **kwargs):
                return cls._httpx_client_init(self, *args, **kwargs)

            httpx.Client.__init__ = compatible_client_init

    @classmethod
    def tearDownClass(cls):
        httpx.Client.__init__ = cls._httpx_client_init

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

        def override_get_db():
            request_db = self.Session()
            try:
                yield request_db
            finally:
                request_db.close()

        main.app.dependency_overrides[database.get_db] = override_get_db
        self.admin_password_patch = mock.patch.object(
            admin_auth,
            "ADMIN_PANEL_PASSWORD",
            ADMIN_PASSWORD,
        )
        self.admin_password_patch.start()
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.task_key_counter = 0

    def tearDown(self):
        self.admin_password_patch.stop()
        main.app.dependency_overrides.pop(database.get_db, None)
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _admin_headers(self, password=ADMIN_PASSWORD):
        return {"X-Admin-Password": password}

    def _seed_task(self, **overrides):
        self.task_key_counter += 1
        values = {
            "task_key": f"task-{self.task_key_counter}",
            "task_folder": "folder-a",
            "source_type": "debug",
            "source_record_type": "episode",
            "source_record_id": 10,
            "task_type": "image_generation",
            "stage": "stage-a",
            "title": "Alpha task",
            "status": "completed",
            "creator_user_id": 1,
            "creator_username": "alice",
            "script_id": 2,
            "script_name": "Script Alpha",
            "episode_id": 3,
            "episode_name": "Episode One",
            "shot_id": 4,
            "shot_number": 5,
            "batch_id": "batch-a",
            "provider": "seedream",
            "model_name": "model-a",
            "api_url": "https://image.example.test/create",
            "status_api_url": "https://image.example.test/status/task-a",
            "external_task_id": "external-a",
            "input_payload": json.dumps({"prompt": "hello"}),
            "output_payload": json.dumps({"images": ["https://cdn.example.test/a.png"]}),
            "raw_response_payload": json.dumps({"status": "success"}),
            "result_payload": json.dumps({"result": "ok"}),
            "latest_event_payload": json.dumps({"filename": "output.json"}),
            "events_json": json.dumps([{"event": "completed", "output": {"ok": True}}]),
            "result_summary": "generated image",
            "created_at": datetime(2024, 1, 2, 10, 0, 0),
            "updated_at": datetime(2024, 1, 2, 10, 5, 0),
        }
        values.update(overrides)
        db = self.Session()
        try:
            row = models.DashboardTaskLog(**values)
            db.add(row)
            db.commit()
            return row.id
        finally:
            db.close()

    def _query_status_patch_target(self):
        for route in main.app.routes:
            if (
                getattr(route, "path", None)
                == "/api/dashboard/tasks/{task_id}/query-status"
            ):
                endpoint = getattr(route, "endpoint", None)
                module_name = getattr(endpoint, "__module__", "main")
                return f"{module_name}.query_dashboard_task"
        return "main.query_dashboard_task"

    def test_dashboard_task_routes_require_admin_password(self):
        task_id = self._seed_task()
        routes = [
            ("get", "/api/dashboard/tasks", None),
            ("get", f"/api/dashboard/tasks/{task_id}", None),
            ("post", f"/api/dashboard/tasks/{task_id}/query-status", None),
            ("delete", f"/api/dashboard/tasks/{task_id}", None),
            ("post", "/api/dashboard/tasks/bulk-delete", {"ids": [task_id]}),
        ]

        for method, path, body in routes:
            with self.subTest(method=method, path=path, auth="missing"):
                caller = getattr(self.client, method)
                response = caller(path, json=body) if body is not None else caller(path)
                self.assertEqual(response.status_code, 403)

            with self.subTest(method=method, path=path, auth="wrong"):
                caller = getattr(self.client, method)
                if body is None:
                    response = caller(path, headers=self._admin_headers("wrong-password"))
                else:
                    response = caller(
                        path,
                        json=body,
                        headers=self._admin_headers("wrong-password"),
                    )
                self.assertEqual(response.status_code, 403)

    def test_list_dashboard_tasks_applies_filters_and_pagination(self):
        older_matching = self._seed_task(
            task_key="matching-older",
            title="Alpha older",
            creator_username="alice",
            task_type="image_generation",
            status="completed",
            created_at=datetime(2024, 1, 1, 9, 0, 0),
        )
        newer_matching = self._seed_task(
            task_key="matching-newer",
            title="Alpha newer",
            creator_username="alice",
            task_type="image_generation",
            status="completed",
            created_at=datetime(2024, 1, 2, 9, 0, 0),
        )
        self._seed_task(
            task_key="wrong-status",
            title="Alpha failed",
            creator_username="alice",
            task_type="image_generation",
            status="failed",
            created_at=datetime(2024, 1, 3, 9, 0, 0),
        )
        self._seed_task(
            task_key="wrong-keyword",
            title="Beta task",
            creator_username="bob",
            task_type="video_generate",
            status="completed",
            created_at=datetime(2024, 1, 4, 9, 0, 0),
        )

        response = self.client.get(
            "/api/dashboard/tasks",
            params={
                "status": "completed",
                "task_type": "image_generation",
                "creator_username": "ali",
                "keyword": "Alpha",
                "date_from": "2024-01-01",
                "date_to": "2024-01-02",
                "page": 2,
                "size": 1,
            },
            headers=self._admin_headers(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["page"], 2)
        self.assertEqual(payload["size"], 1)
        self.assertEqual([item["id"] for item in payload["items"]], [older_matching])
        self.assertEqual(payload["items"][0]["id"], older_matching)
        self.assertNotEqual(payload["items"][0]["id"], newer_matching)
        self.assertIn("completed", payload["status_options"])
        self.assertIn("failed", payload["status_options"])
        self.assertIn("image_generation", payload["task_type_options"])
        self.assertIn("video_generate", payload["task_type_options"])
        self.assertIn("completed", payload["status_labels"])
        self.assertIn("image_generation", payload["task_type_labels"])

    def test_get_dashboard_task_detail_includes_parsed_payloads_and_events(self):
        task_id = self._seed_task(
            input_payload=json.dumps({"prompt": "detail prompt"}),
            output_payload=json.dumps({"images": ["image-a"]}),
            raw_response_payload=json.dumps({"raw": {"status": "ok"}}),
            result_payload=json.dumps({"final": True}),
            latest_event_payload=json.dumps({"event": "latest"}),
            events_json=json.dumps([{"event": "submitted"}, {"event": "completed"}]),
        )

        response = self.client.get(
            f"/api/dashboard/tasks/{task_id}",
            headers=self._admin_headers(),
        )
        missing = self.client.get(
            "/api/dashboard/tasks/999999",
            headers=self._admin_headers(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["id"], task_id)
        self.assertEqual(payload["input_payload"], {"prompt": "detail prompt"})
        self.assertEqual(payload["output_payload"], {"images": ["image-a"]})
        self.assertEqual(payload["raw_response_payload"], {"raw": {"status": "ok"}})
        self.assertEqual(payload["result_payload"], {"final": True})
        self.assertEqual(payload["latest_event_payload"], {"event": "latest"})
        self.assertEqual(payload["events"], [{"event": "submitted"}, {"event": "completed"}])
        self.assertEqual(missing.status_code, 404)

    def test_query_dashboard_task_status_returns_service_payload_and_maps_value_error(self):
        task_id = self._seed_task(task_type="image_generation", external_task_id="external-a")
        service_payload = {
            "task_id": task_id,
            "task_type": "image_generation",
            "query_result": {"status": "completed"},
        }

        with mock.patch(self._query_status_patch_target(), return_value=service_payload) as query:
            response = self.client.post(
                f"/api/dashboard/tasks/{task_id}/query-status",
                headers=self._admin_headers(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), service_payload)
        self.assertEqual(query.call_count, 1)

        with mock.patch(
            self._query_status_patch_target(),
            side_effect=ValueError("unsupported task"),
        ):
            error_response = self.client.post(
                f"/api/dashboard/tasks/{task_id}/query-status",
                headers=self._admin_headers(),
            )

        missing = self.client.post(
            "/api/dashboard/tasks/999999/query-status",
            headers=self._admin_headers(),
        )

        self.assertEqual(error_response.status_code, 400)
        self.assertEqual(error_response.json(), {"detail": "unsupported task"})
        self.assertEqual(missing.status_code, 404)

    def test_delete_dashboard_task_removes_row_and_missing_id_returns_404(self):
        task_id = self._seed_task()

        response = self.client.delete(
            f"/api/dashboard/tasks/{task_id}",
            headers=self._admin_headers(),
        )
        missing = self.client.delete(
            "/api/dashboard/tasks/999999",
            headers=self._admin_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted_count"], 1)
        db = self.Session()
        try:
            self.assertEqual(
                db.query(models.DashboardTaskLog).filter_by(id=task_id).count(),
                0,
            )
        finally:
            db.close()
        self.assertEqual(missing.status_code, 404)

    def test_bulk_delete_rejects_no_conditions_deletes_by_ids_and_filter(self):
        first = self._seed_task(task_key="bulk-first", status="failed")
        second = self._seed_task(task_key="bulk-second", status="completed")
        filter_match = self._seed_task(
            task_key="bulk-filter-match",
            status="processing",
            task_type="video_generate",
            creator_username="carol",
        )
        keep = self._seed_task(
            task_key="bulk-keep",
            status="processing",
            task_type="image_generation",
            creator_username="carol",
        )

        no_conditions = self.client.post(
            "/api/dashboard/tasks/bulk-delete",
            json={},
            headers=self._admin_headers(),
        )
        by_ids = self.client.post(
            "/api/dashboard/tasks/bulk-delete",
            json={"ids": [first, second]},
            headers=self._admin_headers(),
        )
        by_filter = self.client.post(
            "/api/dashboard/tasks/bulk-delete",
            json={
                "status": "processing",
                "task_type": "video_generate",
                "creator_username": "car",
            },
            headers=self._admin_headers(),
        )

        self.assertEqual(no_conditions.status_code, 400)
        self.assertEqual(by_ids.status_code, 200)
        self.assertEqual(by_ids.json()["deleted_count"], 2)
        self.assertEqual(by_filter.status_code, 200)
        self.assertEqual(by_filter.json()["deleted_count"], 1)

        db = self.Session()
        try:
            remaining_ids = {
                row.id
                for row in db.query(models.DashboardTaskLog).order_by(
                    models.DashboardTaskLog.id.asc()
                )
            }
        finally:
            db.close()
        self.assertEqual(remaining_ids, {keep})
        self.assertNotIn(filter_match, remaining_ids)


if __name__ == "__main__":
    unittest.main()
