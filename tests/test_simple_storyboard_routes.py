import os
import sys
import unittest
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

from api.routers import simple_storyboard  # noqa: E402


EXPECTED_SIMPLE_STORYBOARD_ROUTES = {
    ("POST", "/api/episodes/{episode_id}/generate-simple-storyboard"),
    ("GET", "/api/episodes/{episode_id}/simple-storyboard"),
    ("GET", "/api/episodes/{episode_id}/simple-storyboard/status"),
    ("POST", "/api/episodes/{episode_id}/simple-storyboard/retry-failed-batches"),
    ("PUT", "/api/episodes/{episode_id}/simple-storyboard"),
}


class SimpleStoryboardRouterTests(unittest.TestCase):
    def test_router_owns_only_simple_storyboard_routes(self):
        registered = set()
        for route in simple_storyboard.router.routes:
            methods = getattr(route, "methods", set()) or set()
            path = getattr(route, "path", "")
            for method in methods:
                if method not in {"HEAD", "OPTIONS"}:
                    registered.add((method, path))

        self.assertEqual(registered, EXPECTED_SIMPLE_STORYBOARD_ROUTES)


if __name__ == "__main__":
    unittest.main()
