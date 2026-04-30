import asyncio
import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


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


def _route_endpoints(method, path):
    endpoints = []
    for route in main.app.routes:
        if getattr(route, "path", None) != path:
            continue
        if method not in (getattr(route, "methods", None) or set()):
            continue
        endpoints.append(route.endpoint)
    return endpoints


class ImageGenerationRouteTests(unittest.TestCase):
    def _import_image_generation_router(self):
        try:
            return importlib.import_module("api.routers.image_generation")
        except ImportError as exc:
            self.fail(f"image_generation router module should import: {exc}")

    def test_image_models_route_is_owned_by_image_generation_router(self):
        endpoints = _route_endpoints("GET", "/api/image-generation/models")

        self.assertEqual(
            [(endpoint.__module__, endpoint.__name__) for endpoint in endpoints],
            [("api.routers.image_generation", "get_image_models")],
        )

    def test_image_models_returns_router_catalog_payload(self):
        image_generation = self._import_image_generation_router()

        with patch.object(
            image_generation.image_platform_client,
            "get_image_model_catalog_public",
            return_value=[{"key": "mock-model", "name": "Mock Model"}],
        ) as get_catalog:
            payload = asyncio.run(image_generation.get_image_models())

        self.assertEqual(
            payload,
            {"models": [{"key": "mock-model", "name": "Mock Model"}]},
        )
        get_catalog.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
