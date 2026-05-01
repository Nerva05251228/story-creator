import hashlib
import os
import sys
import unittest
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

import database  # noqa: E402
import main  # noqa: E402
import models  # noqa: E402


def _hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


class AuthRouteTests(unittest.TestCase):
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
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def tearDown(self):
        main.app.dependency_overrides.pop(database.get_db, None)
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _auth_headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _seed_user(
        self,
        username="alice",
        password="correct-password",
        token="alice-token",
        password_plain="correct-password",
    ):
        db = self.Session()
        try:
            user = models.User(
                username=username,
                token=token,
                password_hash=_hash_password(password),
                password_plain=password_plain,
            )
            db.add(user)
            db.commit()
            return user
        finally:
            db.close()

    def test_login_returns_user_payload_and_persists_plain_password_for_own_password(self):
        user = self._seed_user(password="correct-password", password_plain="stale")

        response = self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "correct-password"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["id"], user.id)
        self.assertEqual(payload["username"], "alice")
        self.assertEqual(payload["token"], "alice-token")
        self.assertIn("created_at", payload)

        db = self.Session()
        try:
            updated = db.query(models.User).filter_by(id=user.id).one()
            self.assertEqual(updated.password_plain, "correct-password")
        finally:
            db.close()

    def test_login_rejects_wrong_password_and_missing_user(self):
        self._seed_user(password="correct-password")

        wrong_password = self.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong-password"},
        )
        missing_user = self.client.post(
            "/api/auth/login",
            json={"username": "missing", "password": "correct-password"},
        )

        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(wrong_password.json(), {"detail": "密码错误"})
        self.assertEqual(missing_user.status_code, 401)
        self.assertEqual(missing_user.json(), {"detail": "用户不存在"})

    def test_verify_requires_valid_bearer_token(self):
        user = self._seed_user()

        valid = self.client.post(
            "/api/auth/verify",
            headers=self._auth_headers(user.token),
        )
        missing = self.client.post("/api/auth/verify")
        invalid = self.client.post(
            "/api/auth/verify",
            headers=self._auth_headers("invalid-token"),
        )

        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.json()["id"], user.id)
        self.assertEqual(valid.json()["username"], "alice")
        self.assertIn("created_at", valid.json())
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(missing.json(), {"detail": "Not authenticated"})
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(invalid.json(), {"detail": "Invalid authentication token"})

    def test_change_password_updates_hash_and_plain_password(self):
        user = self._seed_user(password="old-password", password_plain="old-password")

        response = self.client.post(
            "/api/auth/change-password",
            json={
                "username": "alice",
                "old_password": "old-password",
                "new_password": "new-password",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"message": "密码修改成功"})

        db = self.Session()
        try:
            updated = db.query(models.User).filter_by(id=user.id).one()
            self.assertEqual(updated.password_hash, _hash_password("new-password"))
            self.assertEqual(updated.password_plain, "new-password")
        finally:
            db.close()

    def test_change_password_rejects_wrong_old_password_and_empty_new_password(self):
        self._seed_user(password="old-password", password_plain="old-password")

        wrong_old_password = self.client.post(
            "/api/auth/change-password",
            json={
                "username": "alice",
                "old_password": "wrong-password",
                "new_password": "new-password",
            },
        )
        empty_new_password = self.client.post(
            "/api/auth/change-password",
            json={
                "username": "alice",
                "old_password": "old-password",
                "new_password": "",
            },
        )

        self.assertEqual(wrong_old_password.status_code, 401)
        self.assertEqual(wrong_old_password.json(), {"detail": "原密码错误"})
        self.assertEqual(empty_new_password.status_code, 400)
        self.assertEqual(empty_new_password.json(), {"detail": "新密码不能为空"})

    def test_verify_nerva_password_uses_configured_non_placeholder_env_password(self):
        with mock.patch.dict(os.environ, {"NERVA_PASSWORD": "configured-secret"}):
            success = self.client.post(
                "/api/auth/verify-nerva-password",
                json={"password": "configured-secret"},
            )

        with mock.patch.dict(os.environ, {"NERVA_PASSWORD": "<set-local-nerva-password>"}):
            placeholder = self.client.post(
                "/api/auth/verify-nerva-password",
                json={"password": "<set-local-nerva-password>"},
            )

        with mock.patch.dict(os.environ, {"NERVA_PASSWORD": ""}):
            missing = self.client.post(
                "/api/auth/verify-nerva-password",
                json={"password": "configured-secret"},
            )

        self.assertEqual(success.status_code, 200)
        self.assertEqual(success.json(), {"success": True})
        self.assertEqual(placeholder.status_code, 401)
        self.assertEqual(placeholder.json(), {"detail": "密码错误"})
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json(), {"detail": "密码错误"})


if __name__ == "__main__":
    unittest.main()
