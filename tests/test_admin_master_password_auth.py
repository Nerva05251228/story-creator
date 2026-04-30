import asyncio
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


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

import main  # noqa: E402


class _FakeQuery:
    def __init__(self, user):
        self._user = user

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._user


class _FakeDb:
    def __init__(self, user):
        self._user = user
        self.commits = 0

    def query(self, _model):
        return _FakeQuery(self._user)

    def commit(self):
        self.commits += 1


class AdminMasterPasswordAuthTests(unittest.TestCase):
    def _user(self, *, password: str = "user-secret"):
        return SimpleNamespace(
            id=1,
            username="alice",
            token="token-1",
            created_at=datetime(2026, 1, 1),
            password_hash=main._hash_password(password),
            password_plain=password,
        )

    def test_blank_master_password_does_not_authenticate_empty_password(self):
        db = _FakeDb(self._user())
        request = main.LoginRequest(username="alice", password="")

        with mock.patch.object(main, "MASTER_PASSWORD", ""):
            with self.assertRaises(main.HTTPException) as raised:
                asyncio.run(main.login(request, db))

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(db.commits, 0)

    def test_blank_admin_panel_password_rejects_missing_and_empty_header(self):
        with mock.patch.object(main, "ADMIN_PANEL_PASSWORD", ""):
            for header in (None, ""):
                with self.subTest(header=header):
                    with self.assertRaises(main.HTTPException) as raised:
                        main._verify_admin_panel_password(header)

                    self.assertEqual(raised.exception.status_code, 403)

    def test_user_password_login_still_works_when_master_password_is_blank(self):
        db = _FakeDb(self._user(password="user-secret"))
        request = main.LoginRequest(username="alice", password="user-secret")

        with mock.patch.object(main, "MASTER_PASSWORD", ""):
            response = asyncio.run(main.login(request, db))

        self.assertEqual(response["username"], "alice")
        self.assertEqual(db.commits, 0)

    def test_placeholder_master_password_does_not_authenticate(self):
        db = _FakeDb(self._user())
        request = main.LoginRequest(username="alice", password="<set-local-master-password>")

        with mock.patch.dict(os.environ, {"MASTER_PASSWORD": "<set-local-master-password>"}):
            master_password = main._get_private_password_env("MASTER_PASSWORD")
        self.assertEqual(master_password, "")

        with mock.patch.object(main, "MASTER_PASSWORD", master_password):
            with self.assertRaises(main.HTTPException) as raised:
                asyncio.run(main.login(request, db))

        self.assertEqual(raised.exception.status_code, 401)

    def test_placeholder_admin_panel_password_rejects_matching_header(self):
        with mock.patch.dict(os.environ, {"ADMIN_PANEL_PASSWORD": "<set-local-admin-password>"}):
            admin_password = main._get_private_password_env("ADMIN_PANEL_PASSWORD")
        self.assertEqual(admin_password, "")

        with mock.patch.object(main, "ADMIN_PANEL_PASSWORD", admin_password):
            with self.assertRaises(main.HTTPException) as raised:
                main._verify_admin_panel_password("<set-local-admin-password>")

        self.assertEqual(raised.exception.status_code, 403)

    def test_blank_nerva_password_env_rejects_legacy_hardcoded_password(self):
        request = main.PasswordVerifyRequest(password="any-submitted-password")

        with mock.patch.dict(os.environ, {"NERVA_PASSWORD": ""}):
            with self.assertRaises(main.HTTPException) as raised:
                asyncio.run(main.verify_nerva_password(request))

        self.assertEqual(raised.exception.status_code, 401)

    def test_configured_nerva_password_env_authenticates(self):
        request = main.PasswordVerifyRequest(password="configured-nerva-secret")

        with mock.patch.dict(os.environ, {"NERVA_PASSWORD": "configured-nerva-secret"}):
            response = asyncio.run(main.verify_nerva_password(request))

        self.assertEqual(response, {"success": True})

    def test_placeholder_nerva_password_env_rejects_matching_password(self):
        request = main.PasswordVerifyRequest(password="<set-local-nerva-password>")

        with mock.patch.dict(os.environ, {"NERVA_PASSWORD": "<set-local-nerva-password>"}):
            with self.assertRaises(main.HTTPException) as raised:
                asyncio.run(main.verify_nerva_password(request))

        self.assertEqual(raised.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
