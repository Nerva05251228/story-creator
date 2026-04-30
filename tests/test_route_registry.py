import os
import sys
import unittest
from collections import defaultdict
from pathlib import Path


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

KNOWN_DUPLICATE_ROUTES = {
    ("GET", "/api/scripts/{script_id}/episodes"): (
        "get_script_episodes",
        "get_script_episodes",
    ),
    ("POST", "/api/scripts/{script_id}/copy"): (
        "copy_script",
        "copy_script",
    ),
    ("POST", "/api/scripts/{script_id}/episodes"): (
        "create_episode",
        "create_episode",
    ),
}


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


class RouteRegistryTests(unittest.TestCase):
    def test_duplicate_routes_match_current_baseline(self):
        registered = _registered_method_paths()
        duplicates = {
            key: tuple(endpoints)
            for key, endpoints in sorted(registered.items())
            if len(endpoints) > 1
        }

        self.assertEqual(duplicates, KNOWN_DUPLICATE_ROUTES)

    def test_route_registry_has_no_unexpected_duplicate_paths(self):
        registered = _registered_method_paths()
        unexpected = {
            key: tuple(endpoints)
            for key, endpoints in sorted(registered.items())
            if len(endpoints) > 1 and key not in KNOWN_DUPLICATE_ROUTES
        }

        self.assertEqual(unexpected, {})


if __name__ == "__main__":
    unittest.main()
