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


EXPECTED_SETTINGS_ROUTES = {
    ("GET", "/api/video-generation-rules"),
    ("PUT", "/api/video-generation-rules"),
    ("GET", "/api/sora-rule"),
    ("PUT", "/api/sora-rule"),
    ("GET", "/api/users/{user_id}/sora-rule"),
    ("PUT", "/api/users/{user_id}/sora-rule"),
    ("GET", "/api/global-settings/prompt_template"),
    ("PUT", "/api/global-settings/prompt_template"),
    ("GET", "/api/global-settings/narration_conversion_template"),
    ("PUT", "/api/global-settings/narration_conversion_template"),
    ("GET", "/api/global-settings/opening_generation_template"),
    ("PUT", "/api/global-settings/opening_generation_template"),
    ("GET", "/api/prompt-configs"),
    ("GET", "/api/prompt-configs/{config_id}"),
    ("PUT", "/api/prompt-configs/{config_id}"),
    ("POST", "/api/prompt-configs/{config_id}/reset"),
    ("GET", "/api/shot-duration-templates"),
    ("GET", "/api/shot-duration-templates/{duration}"),
    ("PUT", "/api/shot-duration-templates/{duration}"),
}


class SettingsRouteTests(unittest.TestCase):
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
        from api.routers import settings

        self.settings = settings
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
        self.app.include_router(settings.router)
        self.app.dependency_overrides[database.get_db] = override_get_db
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_router_registers_all_settings_routes(self):
        actual_routes = set()
        for route in self.settings.router.routes:
            for method in getattr(route, "methods", set()):
                if method in {"HEAD", "OPTIONS"}:
                    continue
                actual_routes.add((method, route.path))

        self.assertEqual(actual_routes, EXPECTED_SETTINGS_ROUTES)

    def test_video_generation_rules_round_trip(self):
        response = self.client.put(
            "/api/video-generation-rules",
            json={"sora_rule": "sora rule", "grok_rule": "grok rule"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sora_rule"], "sora rule")
        self.assertEqual(response.json()["grok_rule"], "grok rule")

        get_response = self.client.get("/api/video-generation-rules")
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json()["sora_rule"], "sora rule")
        self.assertEqual(get_response.json()["grok_rule"], "grok rule")

    def test_global_prompt_template_round_trip(self):
        response = self.client.put(
            "/api/global-settings/prompt_template",
            json={"value": "prompt value"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["value"], "prompt value")

        get_response = self.client.get("/api/global-settings/prompt_template")
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json()["value"], "prompt value")

    def test_shot_duration_template_validates_structured_config(self):
        db = self.Session()
        try:
            db.add(models.ShotDurationTemplate(
                duration=25,
                shot_count_min=5,
                shot_count_max=6,
                time_segments=6,
                simple_storyboard_rule="legacy prompt",
                video_prompt_rule="video",
                large_shot_prompt_rule="large",
                is_default=False,
            ))
            db.commit()
        finally:
            db.close()

        response = self.client.put(
            "/api/shot-duration-templates/25",
            json={
                "simple_storyboard_config": {
                    "target_chars_min": 120,
                    "target_chars_max": 80,
                }
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_prompt_configs_use_display_overrides_and_order(self):
        db = self.Session()
        try:
            db.add_all([
                models.PromptConfig(
                    key="stage2_refine_shot",
                    name="stage2",
                    description="stage2",
                    content="content-2",
                ),
                models.PromptConfig(
                    key="generate_subject_ai_prompt",
                    name="single",
                    description="single",
                    content="content-single",
                ),
                models.PromptConfig(
                    key="detailed_storyboard_content_analysis",
                    name="analysis",
                    description="analysis",
                    content="content-analysis",
                ),
                models.PromptConfig(
                    key="stage1_initial_storyboard",
                    name="stage1",
                    description="stage1",
                    content="content-1",
                ),
            ])
            db.commit()
        finally:
            db.close()

        response = self.client.get("/api/prompt-configs")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["key"] for item in response.json()[:4]],
            [
                "stage1_initial_storyboard",
                "detailed_storyboard_content_analysis",
                "stage2_refine_shot",
                "generate_subject_ai_prompt",
            ],
        )


if __name__ == "__main__":
    unittest.main()
