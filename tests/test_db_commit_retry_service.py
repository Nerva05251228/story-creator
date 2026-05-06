import sys
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy.exc import OperationalError


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.services import db_commit_retry  # noqa: E402


class _Dialect:
    def __init__(self, name):
        self.name = name


class _Bind:
    def __init__(self, dialect_name):
        self.dialect = _Dialect(dialect_name)


class _FakeSession:
    def __init__(self, *, dialect_name="sqlite", commit_side_effects=None):
        self.bind = _Bind(dialect_name)
        self.commit_side_effects = list(commit_side_effects or [])
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1
        if self.commit_side_effects:
            effect = self.commit_side_effects.pop(0)
            if isinstance(effect, BaseException):
                raise effect

    def rollback(self):
        self.rollbacks += 1


def _locked_error():
    return OperationalError("COMMIT", {}, Exception("database is locked"))


class DbCommitRetryServiceTests(unittest.TestCase):
    def test_commit_with_retry_rebuilds_state_and_sleeps_for_sqlite_locks(self):
        session = _FakeSession(
            commit_side_effects=[
                _locked_error(),
                _locked_error(),
            ]
        )
        prepare_calls = []
        sleep_calls = []

        with mock.patch.object(db_commit_retry.time, "sleep", side_effect=sleep_calls.append):
            db_commit_retry.commit_with_retry(
                session,
                prepare_fn=lambda: prepare_calls.append("prepare"),
                context="unit test commit",
            )

        self.assertEqual(session.commits, 3)
        self.assertEqual(session.rollbacks, 2)
        self.assertEqual(prepare_calls, ["prepare", "prepare", "prepare"])
        self.assertEqual(sleep_calls, [0.3, 0.8])

    def test_commit_with_retry_raises_non_sqlite_errors_without_sleeping(self):
        session = _FakeSession(
            dialect_name="postgresql",
            commit_side_effects=[_locked_error()],
        )

        with mock.patch.object(db_commit_retry.time, "sleep") as sleep:
            with self.assertRaises(OperationalError):
                db_commit_retry.commit_with_retry(session)

        self.assertEqual(session.commits, 1)
        self.assertEqual(session.rollbacks, 1)
        sleep.assert_not_called()

    def test_commit_with_retry_rolls_back_generic_errors(self):
        session = _FakeSession(commit_side_effects=[ValueError("boom")])

        with self.assertRaises(ValueError):
            db_commit_retry.commit_with_retry(session)

        self.assertEqual(session.commits, 1)
        self.assertEqual(session.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
