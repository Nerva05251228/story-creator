import ast
import os
import sys
import inspect
import textwrap
import unittest
from collections import defaultdict
from pathlib import Path

from fastapi.params import Header as HeaderParam


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


IGNORED_AUTOMATIC_METHODS = {"HEAD", "OPTIONS"}

def _registered_method_paths():
    registered = defaultdict(list)
    for route in main.app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        endpoint = getattr(route, "endpoint", None)
        if not methods or not path:
            continue
        endpoint_name = getattr(endpoint, "__name__", repr(endpoint))
        for method in methods:
            if method in IGNORED_AUTOMATIC_METHODS:
                continue
            registered[(method, path)].append(endpoint_name)
    return registered


def _function_calls_name(function, expected_name):
    source = textwrap.dedent(inspect.getsource(function))
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Name) and callee.id == expected_name:
            return True
        if isinstance(callee, ast.Attribute) and callee.attr == expected_name:
            return True
    return False


class RouteRegistryTests(unittest.TestCase):
    def test_route_registry_has_no_duplicate_paths(self):
        registered = _registered_method_paths()
        duplicates = {
            key: tuple(endpoints)
            for key, endpoints in sorted(registered.items())
            if len(endpoints) > 1
        }

        self.assertEqual(duplicates, {})

    def test_media_file_route_is_registered_once(self):
        registered = _registered_method_paths()

        self.assertEqual(registered[("GET", "/files/{filename:path}")], ["get_file"])

    def test_public_users_route_is_registered_once(self):
        registered = _registered_method_paths()

        self.assertEqual(registered[("GET", "/api/public/users")], ["get_all_users"])

    def test_public_user_libraries_route_is_registered_once(self):
        registered = _registered_method_paths()

        self.assertEqual(
            registered[("GET", "/api/public/users/{user_id}/libraries")],
            ["get_user_libraries"],
        )

    def test_image_generation_models_route_is_registered_once(self):
        registered = _registered_method_paths()

        self.assertEqual(
            registered[("GET", "/api/image-generation/models")],
            ["get_image_models"],
        )

    def test_video_provider_accounts_route_is_owned_by_video_router(self):
        self._assert_get_route_owned_by(
            "/api/video/providers/{provider}/accounts",
            "api.routers.video.get_video_provider_accounts",
        )

    def test_video_provider_stats_route_is_owned_by_video_router(self):
        self._assert_get_route_owned_by(
            "/api/video/provider-stats",
            "api.routers.video.get_video_provider_stats",
        )

    def test_video_quota_route_is_owned_by_video_router(self):
        self._assert_get_route_owned_by(
            "/api/video/quota/{username}",
            "api.routers.video.get_video_quota",
        )

    def test_video_model_pricing_route_is_owned_by_video_router(self):
        self._assert_get_route_owned_by(
            "/api/video-model-pricing",
            "api.routers.video.get_video_model_pricing",
        )

    def _assert_get_route_owned_by(self, path, expected_qualified_name):
        for route in main.app.routes:
            methods = getattr(route, "methods", set()) or set()
            if getattr(route, "path", None) == path and "GET" in methods:
                endpoint = getattr(route, "endpoint", None)
                qualified_name = (
                    f"{getattr(endpoint, '__module__', '')}."
                    f"{getattr(endpoint, '__name__', '')}"
                )
                self.assertEqual(qualified_name, expected_qualified_name)
                return

        self.fail(f"GET {path} is not registered")

    def test_admin_routes_verify_admin_password_header(self):
        missing = []
        for route in main.app.routes:
            path = getattr(route, "path", "")
            endpoint = getattr(route, "endpoint", None)
            if not path.startswith("/api/admin") or endpoint is None:
                continue

            signature = inspect.signature(endpoint)
            password_parameter = signature.parameters.get("x_admin_password")
            if (
                password_parameter is None
                or not isinstance(password_parameter.default, HeaderParam)
                or getattr(password_parameter.default, "alias", None) != "X-Admin-Password"
                or not _function_calls_name(endpoint, "_verify_admin_panel_password")
            ):
                endpoint_name = getattr(endpoint, "__name__", repr(endpoint))
                missing.append(f"{path} -> {endpoint_name}")

        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
