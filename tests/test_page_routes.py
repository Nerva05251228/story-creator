import importlib
import os
import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
import httpx


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


class PageRouteTests(unittest.TestCase):
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

    def test_page_router_module_exists(self):
        pages = importlib.import_module("api.routers.pages")

        self.assertTrue(hasattr(pages, "router"))

    def test_fixed_page_routes_return_html(self):
        client = TestClient(main.app)

        for path in (
            "/",
            "/app",
            "/admin",
            "/model-select",
            "/billing",
            "/billing-rules",
            "/dashboard",
            "/manage",
        ):
            with self.subTest(path=path):
                response = client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["content-type"])
                body = response.text.lower()
                self.assertTrue("<html" in body or "<!doctype html" in body)


if __name__ == "__main__":
    unittest.main()
