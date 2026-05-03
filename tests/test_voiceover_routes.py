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

from api.routers import voiceover  # noqa: E402


EXPECTED_VOICEOVER_ROUTES = {
    ("PUT", "/api/episodes/{episode_id}/voiceover"),
    ("GET", "/api/episodes/{episode_id}/voiceover/shared"),
    ("POST", "/api/episodes/{episode_id}/voiceover/shared/voice-references"),
    ("PUT", "/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}"),
    ("GET", "/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}/preview"),
    ("DELETE", "/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}"),
    ("POST", "/api/episodes/{episode_id}/voiceover/shared/vector-presets"),
    ("DELETE", "/api/episodes/{episode_id}/voiceover/shared/vector-presets/{preset_id}"),
    ("POST", "/api/episodes/{episode_id}/voiceover/shared/emotion-audio-presets"),
    ("DELETE", "/api/episodes/{episode_id}/voiceover/shared/emotion-audio-presets/{preset_id}"),
    ("POST", "/api/episodes/{episode_id}/voiceover/shared/setting-templates"),
    ("DELETE", "/api/episodes/{episode_id}/voiceover/shared/setting-templates/{template_id}"),
    ("POST", "/api/episodes/{episode_id}/voiceover/lines/{line_id}/generate"),
    ("POST", "/api/episodes/{episode_id}/voiceover/generate-all"),
    ("GET", "/api/episodes/{episode_id}/voiceover/tts-status"),
}


class VoiceoverRouterTests(unittest.TestCase):
    def test_router_owns_only_voiceover_routes(self):
        registered = set()
        for route in voiceover.router.routes:
            methods = getattr(route, "methods", set()) or set()
            path = getattr(route, "path", "")
            for method in methods:
                if method not in {"HEAD", "OPTIONS"}:
                    registered.add((method, path))

        self.assertEqual(registered, EXPECTED_VOICEOVER_ROUTES)


if __name__ == "__main__":
    unittest.main()
