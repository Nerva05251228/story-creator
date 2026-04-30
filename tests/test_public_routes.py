import asyncio
import os
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


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
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
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


if __name__ == "__main__":
    unittest.main()
