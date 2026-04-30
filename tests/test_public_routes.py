import asyncio
import os
import sys
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

import main  # noqa: E402
import database  # noqa: E402
import models  # noqa: E402


def _route_endpoints(method, path):
    endpoints = []
    for route in main.app.routes:
        if getattr(route, "path", None) != path:
            continue
        if method not in (getattr(route, "methods", None) or set()):
            continue
        endpoints.append(route.endpoint)
    return endpoints


class PublicUsersRouteTests(unittest.TestCase):
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
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_public_users_route_is_owned_by_public_router(self):
        endpoints = _route_endpoints("GET", "/api/public/users")

        self.assertEqual(
            [(endpoint.__module__, endpoint.__name__) for endpoint in endpoints],
            [("api.routers.public", "get_all_users")],
        )

    def test_public_user_libraries_route_is_owned_by_public_router(self):
        endpoints = _route_endpoints("GET", "/api/public/users/{user_id}/libraries")

        self.assertEqual(
            [(endpoint.__module__, endpoint.__name__) for endpoint in endpoints],
            [("api.routers.public", "get_user_libraries")],
        )

    def test_public_users_counts_libraries_and_subject_cards(self):
        db = self.Session()
        try:
            creator = models.User(username="creator", token="creator-token")
            empty_user = models.User(username="empty", token="empty-token")
            db.add_all([creator, empty_user])
            db.flush()

            library_a = models.StoryLibrary(user_id=creator.id, name="Cast")
            library_b = models.StoryLibrary(user_id=creator.id, name="Places")
            db.add_all([library_a, library_b])
            db.flush()

            db.add_all(
                [
                    models.SubjectCard(
                        library_id=library_a.id,
                        name="Lead",
                        card_type="character",
                    ),
                    models.SubjectCard(
                        library_id=library_a.id,
                        name="Rival",
                        card_type="character",
                    ),
                    models.SubjectCard(
                        library_id=library_b.id,
                        name="Station",
                        card_type="scene",
                    ),
                ]
            )
            db.commit()

            endpoint = _route_endpoints("GET", "/api/public/users")[0]
            payload = asyncio.run(endpoint(db=db))

            users_by_name = {item["username"]: item for item in payload}
            self.assertEqual(users_by_name["creator"]["library_count"], 2)
            self.assertEqual(users_by_name["creator"]["total_cards"], 3)
            self.assertEqual(users_by_name["empty"]["library_count"], 0)
            self.assertEqual(users_by_name["empty"]["total_cards"], 0)
        finally:
            db.close()

    def test_public_user_libraries_are_ordered_by_created_at_descending(self):
        db = self.Session()
        try:
            creator = models.User(username="creator", token="creator-token")
            db.add(creator)
            db.flush()

            older_library = models.StoryLibrary(
                user_id=creator.id,
                name="Older",
                created_at=datetime(2026, 1, 1, 8, 0, 0),
            )
            newer_library = models.StoryLibrary(
                user_id=creator.id,
                name="Newer",
                created_at=datetime(2026, 1, 2, 8, 0, 0),
            )
            db.add_all([older_library, newer_library])
            db.commit()

            endpoint = _route_endpoints("GET", "/api/public/users/{user_id}/libraries")[0]
            payload = asyncio.run(endpoint(user_id=creator.id, db=db))

            self.assertEqual([library.name for library in payload], ["Newer", "Older"])
        finally:
            db.close()

    def test_public_user_libraries_serializes_response_model_json(self):
        db = self.Session()
        try:
            creator = models.User(username="creator", token="creator-token")
            db.add(creator)
            db.flush()

            library = models.StoryLibrary(
                user_id=creator.id,
                name="Serialized",
                description="Visible library",
                created_at=datetime(2026, 1, 3, 8, 0, 0),
            )
            db.add(library)
            db.commit()
            creator_id = creator.id
            creator_created_at = creator.created_at.isoformat()
            library_id = library.id
        finally:
            db.close()

        def override_get_db():
            request_db = self.Session()
            try:
                yield request_db
            finally:
                request_db.close()

        main.app.dependency_overrides[database.get_db] = override_get_db
        try:
            client = TestClient(main.app)
            response = client.get(f"/api/public/users/{creator_id}/libraries")
        finally:
            main.app.dependency_overrides.pop(database.get_db, None)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": library_id,
                    "user_id": creator_id,
                    "name": "Serialized",
                    "description": "Visible library",
                    "created_at": "2026-01-03T08:00:00",
                    "owner": {
                        "id": creator_id,
                        "username": "creator",
                        "created_at": creator_created_at,
                    },
                }
            ],
        )

    def test_public_user_libraries_returns_empty_list_for_user_with_no_libraries(self):
        db = self.Session()
        try:
            empty_user = models.User(username="empty", token="empty-token")
            db.add(empty_user)
            db.commit()

            endpoint = _route_endpoints("GET", "/api/public/users/{user_id}/libraries")[0]
            payload = asyncio.run(endpoint(user_id=empty_user.id, db=db))

            self.assertEqual(payload, [])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
