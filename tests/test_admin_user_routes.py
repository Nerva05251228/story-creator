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


ADMIN_PASSWORD = "admin-user-test-password"


class AdminUserRouteTests(unittest.TestCase):
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
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def tearDown(self):
        self.router_admin_password_patch.stop()
        self.main_admin_password_patch.stop()
        main.app.dependency_overrides.pop(database.get_db, None)
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _admin_headers(self, password=ADMIN_PASSWORD):
        return {"X-Admin-Password": password}

    def _seed_user(self, username="alice", token="token-alice", password="secret"):
        db = self.Session()
        try:
            user = models.User(
                username=username,
                token=token,
                password_hash=main._hash_password(password),
                password_plain=password,
                created_at=datetime(2026, 1, 2, 3, 4, 5),
            )
            db.add(user)
            db.commit()
            user_id = user.id
        finally:
            db.close()
        return user_id

    def test_admin_user_routes_require_admin_password(self):
        user_id = self._seed_user()
        routes = [
            ("get", "/api/admin/users", None),
            ("post", "/api/admin/users", {"username": "bob"}),
            ("delete", f"/api/admin/users/{user_id}", None),
            ("post", f"/api/admin/users/{user_id}/reset-password", None),
            ("post", f"/api/admin/users/{user_id}/impersonate", None),
        ]

        for method, path, body in routes:
            with self.subTest(method=method, path=path):
                caller = getattr(self.client, method)
                response = caller(path, json=body) if body is not None else caller(path)
                self.assertEqual(response.status_code, 403)

    def test_list_users_hides_reserved_accounts_and_includes_plain_password(self):
        self._seed_user("alice", "token-alice", "alice-password")
        self._seed_user("test", "token-hidden", "hidden-password")

        response = self.client.get("/api/admin/users", headers=self._admin_headers())

        self.assertEqual(response.status_code, 200)
        users = response.json()
        self.assertEqual([item["username"] for item in users], ["alice"])
        self.assertEqual(users[0]["password"], "alice-password")
        self.assertEqual(users[0]["today_video_count"], 0)

    def test_create_user_returns_default_plain_password_and_rejects_duplicates(self):
        with mock.patch("secrets.token_urlsafe", return_value="generated-token"):
            response = self.client.post(
                "/api/admin/users",
                json={"username": "bob"},
                headers=self._admin_headers(),
            )

        duplicate = self.client.post(
            "/api/admin/users",
            json={"username": "bob"},
            headers=self._admin_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "bob")
        self.assertEqual(response.json()["password"], "123456")
        self.assertEqual(duplicate.status_code, 400)

        db = self.Session()
        try:
            user = db.query(models.User).filter_by(username="bob").one()
            self.assertEqual(user.token, "generated-token")
            self.assertEqual(user.password_plain, "123456")
            self.assertEqual(user.password_hash, main._hash_password("123456"))
        finally:
            db.close()

    def test_reset_and_impersonate_reject_hidden_users_but_work_for_visible_user(self):
        visible_id = self._seed_user("alice", "token-alice", "old-password")
        hidden_id = self._seed_user("test", "token-hidden", "hidden-password")

        reset_response = self.client.post(
            f"/api/admin/users/{visible_id}/reset-password",
            headers=self._admin_headers(),
        )
        impersonate_response = self.client.post(
            f"/api/admin/users/{visible_id}/impersonate",
            headers=self._admin_headers(),
        )
        hidden_reset = self.client.post(
            f"/api/admin/users/{hidden_id}/reset-password",
            headers=self._admin_headers(),
        )
        hidden_impersonate = self.client.post(
            f"/api/admin/users/{hidden_id}/impersonate",
            headers=self._admin_headers(),
        )

        self.assertEqual(reset_response.status_code, 200)
        self.assertEqual(impersonate_response.status_code, 200)
        self.assertEqual(impersonate_response.json()["token"], "token-alice")
        self.assertEqual(hidden_reset.status_code, 403)
        self.assertEqual(hidden_impersonate.status_code, 403)

        db = self.Session()
        try:
            user = db.query(models.User).filter_by(id=visible_id).one()
            self.assertEqual(user.password_plain, "123456")
            self.assertEqual(user.password_hash, main._hash_password("123456"))
        finally:
            db.close()

    def test_delete_user_cleans_visible_user_and_rejects_hidden_or_missing_user(self):
        visible_id = self._seed_user("alice", "token-alice")
        hidden_id = self._seed_user("test", "token-hidden")

        hidden = self.client.delete(
            f"/api/admin/users/{hidden_id}",
            headers=self._admin_headers(),
        )
        missing = self.client.delete(
            "/api/admin/users/999999",
            headers=self._admin_headers(),
        )
        deleted = self.client.delete(
            f"/api/admin/users/{visible_id}",
            headers=self._admin_headers(),
        )

        self.assertEqual(hidden.status_code, 403)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(deleted.status_code, 200)

        db = self.Session()
        try:
            self.assertIsNone(db.query(models.User).filter_by(id=visible_id).first())
            self.assertIsNotNone(db.query(models.User).filter_by(id=hidden_id).first())
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
