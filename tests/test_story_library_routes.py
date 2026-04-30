import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

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


class StoryLibraryRouteTests(unittest.TestCase):
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

    def _seed_users(self):
        db = self.Session()
        try:
            owner = models.User(username="owner", token="owner-token")
            other = models.User(username="other", token="other-token")
            db.add_all([owner, other])
            db.commit()
            return owner, other
        finally:
            db.close()

    def _seed_library(self, user_id, name="Library", description="", created_at=None):
        db = self.Session()
        try:
            library = models.StoryLibrary(
                user_id=user_id,
                name=name,
                description=description,
                created_at=created_at,
            )
            db.add(library)
            db.commit()
            return library
        finally:
            db.close()

    def test_create_library_uses_authenticated_user_and_response_includes_owner(self):
        owner, _ = self._seed_users()

        response = self.client.post(
            "/api/libraries",
            json={"name": "Characters", "description": "Main cast"},
            headers=self._auth_headers(owner.token),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["name"], "Characters")
        self.assertEqual(payload["description"], "Main cast")
        self.assertEqual(payload["user_id"], owner.id)
        self.assertEqual(payload["owner"]["id"], owner.id)
        self.assertEqual(payload["owner"]["username"], "owner")

    def test_my_libraries_returns_only_authenticated_users_libraries_newest_first(self):
        owner, other = self._seed_users()
        self._seed_library(
            owner.id,
            name="Older",
            created_at=datetime(2026, 1, 1, 8, 0, 0),
        )
        self._seed_library(
            owner.id,
            name="Newer",
            created_at=datetime(2026, 1, 2, 8, 0, 0),
        )
        self._seed_library(
            other.id,
            name="Other",
            created_at=datetime(2026, 1, 3, 8, 0, 0),
        )

        response = self.client.get(
            "/api/libraries/my",
            headers=self._auth_headers(owner.token),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["name"] for item in response.json()], ["Newer", "Older"])

    def test_get_library_is_public_and_missing_id_returns_404(self):
        owner, _ = self._seed_users()
        library = self._seed_library(owner.id, name="Public", description="Visible")

        response = self.client.get(f"/api/libraries/{library.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Public")
        self.assertEqual(response.json()["owner"]["username"], "owner")

        missing_response = self.client.get("/api/libraries/9999")

        self.assertEqual(missing_response.status_code, 404)
        self.assertEqual(missing_response.json(), {"detail": "Library not found"})

    def test_update_library_renames_owned_library_and_updates_database(self):
        owner, _ = self._seed_users()
        library = self._seed_library(owner.id, name="Old", description="Before")

        response = self.client.put(
            f"/api/libraries/{library.id}",
            json={"name": "Renamed", "description": "After"},
            headers=self._auth_headers(owner.token),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Renamed")
        self.assertEqual(response.json()["description"], "After")

        db = self.Session()
        try:
            updated = db.query(models.StoryLibrary).filter(
                models.StoryLibrary.id == library.id
            ).one()
            self.assertEqual(updated.name, "Renamed")
            self.assertEqual(updated.description, "After")
        finally:
            db.close()

    def test_update_library_by_non_owner_returns_403_and_does_not_change_database(self):
        owner, other = self._seed_users()
        library = self._seed_library(owner.id, name="Original", description="Stable")

        response = self.client.put(
            f"/api/libraries/{library.id}",
            json={"name": "Blocked", "description": "Blocked"},
            headers=self._auth_headers(other.token),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            {"detail": "You don't have permission to edit this library"},
        )

        db = self.Session()
        try:
            unchanged = db.query(models.StoryLibrary).filter(
                models.StoryLibrary.id == library.id
            ).one()
            self.assertEqual(unchanged.name, "Original")
            self.assertEqual(unchanged.description, "Stable")
        finally:
            db.close()

    def test_protected_route_with_invalid_token_returns_401(self):
        self._seed_users()

        response = self.client.post(
            "/api/libraries",
            json={"name": "Blocked", "description": ""},
            headers=self._auth_headers("invalid-token"),
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Invalid authentication token"})

    def test_delete_owned_library_removes_rows_and_image_file(self):
        owner, _ = self._seed_users()
        image_file = tempfile.NamedTemporaryFile(delete=False)
        image_path = image_file.name
        image_file.write(b"image")
        image_file.close()

        db = self.Session()
        try:
            library = models.StoryLibrary(user_id=owner.id, name="Delete me")
            db.add(library)
            db.flush()
            card = models.SubjectCard(
                library_id=library.id,
                name="Lead",
                card_type="character",
            )
            db.add(card)
            db.flush()
            image = models.CardImage(card_id=card.id, image_path=image_path)
            db.add(image)
            db.commit()
            library_id = library.id
            card_id = card.id
            image_id = image.id
        finally:
            db.close()

        try:
            response = self.client.delete(
                f"/api/libraries/{library_id}",
                headers=self._auth_headers(owner.token),
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"message": "Library deleted successfully"})
            self.assertFalse(os.path.exists(image_path))

            verify_db = self.Session()
            try:
                self.assertEqual(
                    verify_db.query(models.StoryLibrary).filter_by(id=library_id).count(),
                    0,
                )
                self.assertEqual(
                    verify_db.query(models.SubjectCard).filter_by(id=card_id).count(),
                    0,
                )
                self.assertEqual(
                    verify_db.query(models.CardImage).filter_by(id=image_id).count(),
                    0,
                )
            finally:
                verify_db.close()
        finally:
            if os.path.exists(image_path):
                os.remove(image_path)


if __name__ == "__main__":
    unittest.main()
