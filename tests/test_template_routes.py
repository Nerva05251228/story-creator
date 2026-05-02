import os
import sys
import unittest
from pathlib import Path

from fastapi import FastAPI
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
import models  # noqa: E402


EXPECTED_TEMPLATE_ROUTES = {
    ("GET", "/api/templates"),
    ("POST", "/api/templates"),
    ("GET", "/api/style-templates"),
    ("POST", "/api/style-templates"),
    ("PUT", "/api/style-templates/{template_id}"),
    ("DELETE", "/api/style-templates/{template_id}"),
    ("POST", "/api/style-templates/{template_id}/set-default"),
    ("GET", "/api/style-templates/default"),
    ("GET", "/api/video-style-templates"),
    ("POST", "/api/video-style-templates"),
    ("PUT", "/api/video-style-templates/{template_id}"),
    ("DELETE", "/api/video-style-templates/{template_id}"),
    ("POST", "/api/video-style-templates/{template_id}/set-default"),
    ("GET", "/api/large-shot-templates"),
    ("POST", "/api/large-shot-templates"),
    ("PUT", "/api/large-shot-templates/{template_id}"),
    ("DELETE", "/api/large-shot-templates/{template_id}"),
    ("POST", "/api/large-shot-templates/{template_id}/set-default"),
    ("GET", "/api/storyboard-templates/requirements"),
    ("POST", "/api/storyboard-templates/requirements"),
    ("PUT", "/api/storyboard-templates/requirements/{template_id}"),
    ("DELETE", "/api/storyboard-templates/requirements/{template_id}"),
    ("POST", "/api/storyboard-templates/requirements/{template_id}/set-default"),
    ("GET", "/api/storyboard-templates/styles"),
    ("POST", "/api/storyboard-templates/styles"),
    ("PUT", "/api/storyboard-templates/styles/{template_id}"),
    ("DELETE", "/api/storyboard-templates/styles/{template_id}"),
    ("POST", "/api/storyboard-templates/styles/{template_id}/set-default"),
}


class TemplateRouteTests(unittest.TestCase):
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
        from api.routers import templates

        self.templates = templates
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

        self.app = FastAPI()
        self.app.include_router(templates.router)
        self.app.dependency_overrides[database.get_db] = override_get_db
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_router_registers_all_template_routes(self):
        actual_routes = set()
        for route in self.templates.router.routes:
            for method in getattr(route, "methods", set()):
                if method in {"HEAD", "OPTIONS"}:
                    continue
                actual_routes.add((method, route.path))

        self.assertEqual(actual_routes, EXPECTED_TEMPLATE_ROUTES)

    def test_style_template_crud_preserves_variant_fields(self):
        response = self.client.post(
            "/api/style-templates",
            json={
                "name": "style-a",
                "content": "role style",
                "scene_content": "scene style",
                "prop_content": "prop style",
            },
        )

        self.assertEqual(response.status_code, 200)
        created = response.json()
        self.assertEqual(created["name"], "style-a")
        self.assertEqual(created["content"], "role style")
        self.assertEqual(created["scene_content"], "scene style")
        self.assertEqual(created["prop_content"], "prop style")

        list_response = self.client.get("/api/style-templates")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()[0]["name"], "style-a")

        default_response = self.client.post(
            f"/api/style-templates/{created['id']}/set-default"
        )
        self.assertEqual(default_response.status_code, 200)

        get_default_response = self.client.get("/api/style-templates/default")
        self.assertEqual(get_default_response.status_code, 200)
        self.assertEqual(get_default_response.json()["id"], created["id"])

    def test_large_shot_template_rejects_blank_payload(self):
        response = self.client.post(
            "/api/large-shot-templates",
            json={"name": " ", "content": " "},
        )

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
