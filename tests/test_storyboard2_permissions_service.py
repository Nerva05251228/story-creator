import os
import sys
import unittest
from pathlib import Path

from fastapi import HTTPException
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
from api.services import storyboard2_permissions  # noqa: E402


class Storyboard2PermissionsServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        db = self.Session()
        try:
            owner = models.User(username="owner", token="owner-token", password_hash="hash", password_plain="123456")
            other = models.User(username="other", token="other-token", password_hash="hash", password_plain="123456")
            db.add_all([owner, other])
            db.flush()

            script = models.Script(user_id=owner.id, name="script")
            db.add(script)
            db.flush()

            episode = models.Episode(script_id=script.id, name="episode")
            db.add(episode)
            db.flush()

            storyboard2_shot = models.Storyboard2Shot(
                episode_id=episode.id,
                shot_number=1,
                source_shot_id=0,
                excerpt="excerpt",
            )
            db.add(storyboard2_shot)
            db.flush()

            sub_shot = models.Storyboard2SubShot(
                storyboard2_shot_id=storyboard2_shot.id,
                sub_shot_index=1,
                time_range="0s-3s",
            )
            db.add(sub_shot)
            db.commit()

            self.owner_id = int(owner.id)
            self.other_id = int(other.id)
            self.episode_id = int(episode.id)
            self.storyboard2_shot_id = int(storyboard2_shot.id)
            self.sub_shot_id = int(sub_shot.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_verify_episode_permission_returns_owned_episode(self):
        db = self.Session()
        try:
            user = db.query(models.User).filter(models.User.id == self.owner_id).one()
            episode = storyboard2_permissions.verify_episode_permission(self.episode_id, user, db)
            self.assertEqual(episode.id, self.episode_id)
        finally:
            db.close()

    def test_verify_episode_permission_rejects_missing_and_other_owner(self):
        db = self.Session()
        try:
            owner = db.query(models.User).filter(models.User.id == self.owner_id).one()
            other = db.query(models.User).filter(models.User.id == self.other_id).one()

            with self.assertRaises(HTTPException) as missing_context:
                storyboard2_permissions.verify_episode_permission(9999, owner, db)
            self.assertEqual(missing_context.exception.status_code, 404)

            with self.assertRaises(HTTPException) as forbidden_context:
                storyboard2_permissions.verify_episode_permission(self.episode_id, other, db)
            self.assertEqual(forbidden_context.exception.status_code, 403)
        finally:
            db.close()

    def test_shot_and_sub_shot_permission_helpers_return_owned_records(self):
        db = self.Session()
        try:
            user = db.query(models.User).filter(models.User.id == self.owner_id).one()

            storyboard2_shot = storyboard2_permissions.get_storyboard2_shot_with_permission(
                self.storyboard2_shot_id,
                user,
                db,
            )
            sub_shot, owner_shot = storyboard2_permissions.get_storyboard2_sub_shot_with_permission(
                self.sub_shot_id,
                user,
                db,
            )

            self.assertEqual(storyboard2_shot.id, self.storyboard2_shot_id)
            self.assertEqual(sub_shot.id, self.sub_shot_id)
            self.assertEqual(owner_shot.id, self.storyboard2_shot_id)
        finally:
            db.close()

    def test_shot_permission_helpers_reject_missing_records(self):
        db = self.Session()
        try:
            user = db.query(models.User).filter(models.User.id == self.owner_id).one()

            with self.assertRaises(HTTPException) as shot_context:
                storyboard2_permissions.get_storyboard2_shot_with_permission(9999, user, db)
            self.assertEqual(shot_context.exception.status_code, 404)

            with self.assertRaises(HTTPException) as sub_shot_context:
                storyboard2_permissions.get_storyboard2_sub_shot_with_permission(9999, user, db)
            self.assertEqual(sub_shot_context.exception.status_code, 404)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
