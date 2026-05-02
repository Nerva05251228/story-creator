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
import billing_service  # noqa: E402
import database  # noqa: E402
import main  # noqa: E402
import models  # noqa: E402


ADMIN_PASSWORD = "billing-test-admin"
AUTH_TOKEN = "billing-user-token"


class BillingRouteTests(unittest.TestCase):
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
        self.main_admin_password_patch = mock.patch.object(
            main,
            "ADMIN_PANEL_PASSWORD",
            ADMIN_PASSWORD,
        )
        self.router_admin_password_patch = mock.patch.object(
            admin_auth,
            "ADMIN_PANEL_PASSWORD",
            ADMIN_PASSWORD,
        )
        self.main_admin_password_patch.start()
        self.router_admin_password_patch.start()
        self._seed_user()
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def tearDown(self):
        self.router_admin_password_patch.stop()
        self.main_admin_password_patch.stop()
        main.app.dependency_overrides.pop(database.get_db, None)
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed_user(self):
        db = self.Session()
        try:
            user = models.User(
                username="billing-admin",
                token=AUTH_TOKEN,
                password_hash=main._hash_password("secret"),
                password_plain="secret",
                created_at=datetime(2026, 1, 1, 0, 0, 0),
            )
            db.add(user)
            db.commit()
        finally:
            db.close()

    def _auth_headers(self, admin_password=ADMIN_PASSWORD):
        return {
            "Authorization": f"Bearer {AUTH_TOKEN}",
            "X-Admin-Password": admin_password,
        }

    def test_billing_routes_require_bearer_and_admin_password(self):
        no_bearer = self.client.get(
            "/api/billing/users",
            headers={"X-Admin-Password": ADMIN_PASSWORD},
        )
        no_admin = self.client.get(
            "/api/billing/users",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        wrong_admin = self.client.get(
            "/api/billing/users",
            headers=self._auth_headers("wrong-password"),
        )

        self.assertEqual(no_bearer.status_code, 403)
        self.assertEqual(no_admin.status_code, 403)
        self.assertEqual(wrong_admin.status_code, 403)

    def test_summary_routes_delegate_filters_and_preserve_response_shape(self):
        with mock.patch.object(
            billing_service,
            "get_billing_user_list",
            return_value=[{"user_id": 1}],
        ) as users:
            users_response = self.client.get(
                "/api/billing/users",
                params={"month": "2026-04"},
                headers=self._auth_headers(),
            )

        with mock.patch.object(
            billing_service,
            "get_billing_episode_list",
            return_value=[{"episode_id": 2}],
        ) as episodes:
            episodes_response = self.client.get(
                "/api/billing/episodes",
                params={
                    "group_by": "episode",
                    "user_id": 1,
                    "script_id": 2,
                    "month": "2026-04",
                },
                headers=self._auth_headers(),
            )

        with mock.patch.object(
            billing_service,
            "get_billing_script_list",
            return_value=[{"script_id": 3}],
        ) as scripts:
            scripts_response = self.client.get(
                "/api/billing/scripts",
                params={"group_by": "user", "user_id": 1, "month": "2026-04"},
                headers=self._auth_headers(),
            )

        self.assertEqual(users_response.status_code, 200)
        self.assertEqual(users_response.json(), {"users": [{"user_id": 1}]})
        users.assert_called_once()
        self.assertEqual(users.call_args.kwargs, {"month": "2026-04"})

        self.assertEqual(episodes_response.status_code, 200)
        self.assertEqual(
            episodes_response.json(),
            {"group_by": "episode", "episodes": [{"episode_id": 2}]},
        )
        episodes.assert_called_once()
        self.assertEqual(
            episodes.call_args.kwargs,
            {"user_id": 1, "script_id": 2, "month": "2026-04"},
        )

        self.assertEqual(scripts_response.status_code, 200)
        self.assertEqual(
            scripts_response.json(),
            {"group_by": "user", "scripts": [{"script_id": 3}]},
        )
        scripts.assert_called_once()
        self.assertEqual(scripts.call_args.kwargs, {"user_id": 1, "month": "2026-04"})

    def test_detail_routes_return_service_payload_or_404(self):
        script_payload = {"script_id": 7, "total_amount_rmb": 1.23}
        episode_payload = {"episode_id": 8, "total_amount_rmb": 2.34}

        with mock.patch.object(
            billing_service,
            "get_script_billing_detail",
            side_effect=[script_payload, None],
        ) as script_detail:
            script_response = self.client.get(
                "/api/billing/scripts/7",
                params={"month": "2026-04"},
                headers=self._auth_headers(),
            )
            missing_script = self.client.get(
                "/api/billing/scripts/999",
                headers=self._auth_headers(),
            )

        with mock.patch.object(
            billing_service,
            "get_episode_billing_detail",
            side_effect=[episode_payload, None],
        ) as episode_detail:
            episode_response = self.client.get(
                "/api/billing/episodes/8",
                params={"month": "2026-04"},
                headers=self._auth_headers(),
            )
            missing_episode = self.client.get(
                "/api/billing/episodes/999",
                headers=self._auth_headers(),
            )

        self.assertEqual(script_response.status_code, 200)
        self.assertEqual(script_response.json(), script_payload)
        self.assertEqual(missing_script.status_code, 404)
        self.assertEqual(script_detail.call_args_list[0].kwargs, {"script_id": 7, "month": "2026-04"})

        self.assertEqual(episode_response.status_code, 200)
        self.assertEqual(episode_response.json(), episode_payload)
        self.assertEqual(missing_episode.status_code, 404)
        self.assertEqual(episode_detail.call_args_list[0].kwargs, {"episode_id": 8, "month": "2026-04"})

    def test_reimbursement_export_normalizes_group_by_and_passes_month(self):
        with mock.patch.object(
            billing_service,
            "get_billing_reimbursement_rows",
            return_value=[{"name": "row"}],
        ) as reimbursement_rows:
            response = self.client.get(
                "/api/billing/reimbursement-export",
                params={"group_by": "USER", "month": "2026-04"},
                headers=self._auth_headers(),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["group_by"], "user")
        self.assertEqual(payload["month"], "2026-04")
        self.assertEqual(payload["rows"], [{"name": "row"}])
        reimbursement_rows.assert_called_once()
        self.assertEqual(
            reimbursement_rows.call_args.kwargs,
            {"group_by": "user", "month": "2026-04"},
        )

    def test_billing_rules_create_update_and_rollback_bad_datetime(self):
        create_payload = {
            "rule_name": "Text rule",
            "category": "text",
            "stage": "opening",
            "provider": "relay",
            "model_name": "model-a",
            "resolution": "",
            "billing_mode": "per_call",
            "unit_price_rmb": 0.25,
            "is_active": True,
            "priority": 10,
            "effective_from": "2026-04-01T00:00:00Z",
            "effective_to": None,
        }
        create_response = self.client.post(
            "/api/billing/rules",
            json=create_payload,
            headers=self._auth_headers(),
        )

        self.assertEqual(create_response.status_code, 200)
        created = create_response.json()
        self.assertEqual(created["rule_name"], "Text rule")
        self.assertEqual(created["effective_from"], "2026-04-01T08:00:00")

        update_payload = dict(create_payload)
        update_payload["rule_name"] = "Updated text rule"
        update_payload["unit_price_rmb"] = 0.5
        update_response = self.client.put(
            f"/api/billing/rules/{created['id']}",
            json=update_payload,
            headers=self._auth_headers(),
        )
        missing_update = self.client.put(
            "/api/billing/rules/999999",
            json=update_payload,
            headers=self._auth_headers(),
        )
        invalid_payload = dict(create_payload)
        invalid_payload["rule_name"] = "Invalid datetime"
        invalid_payload["effective_from"] = "not-a-date"
        invalid_response = self.client.post(
            "/api/billing/rules",
            json=invalid_payload,
            headers=self._auth_headers(),
        )

        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["rule_name"], "Updated text rule")
        self.assertEqual(missing_update.status_code, 404)
        self.assertEqual(invalid_response.status_code, 400)

        db = self.Session()
        try:
            names = {
                row.rule_name
                for row in db.query(models.BillingPriceRule).order_by(
                    models.BillingPriceRule.id.asc()
                )
            }
            self.assertEqual(names, {"Updated text rule"})
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
