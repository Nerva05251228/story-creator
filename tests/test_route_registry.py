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
