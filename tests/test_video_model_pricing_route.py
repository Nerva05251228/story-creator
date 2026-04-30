import asyncio
import inspect
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}",
)

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.env_defaults import apply_test_env_defaults  # noqa: E402

apply_test_env_defaults()

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402
import models  # noqa: E402
from auth import get_current_user  # noqa: E402
from database import get_db  # noqa: E402


class VideoModelPricingRouteTests(unittest.TestCase):
    def setUp(self):
        self.endpoint, self.route = self._get_registered_endpoint()
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        models.VideoModelPricing.__table__.create(engine)
        self.Session = sessionmaker(bind=engine)

    def test_registered_endpoint_signature_has_no_auth_dependency(self):
        signature = inspect.signature(self.endpoint)

        self.assertTrue(inspect.iscoroutinefunction(self.endpoint))
        self.assertEqual(self.endpoint.__name__, "get_video_model_pricing")
        self.assertEqual(signature.parameters["provider"].default, "yijia")
        dependency_calls = {
            getattr(dependency.call, "__name__", repr(dependency.call))
            for dependency in self.route.dependant.dependencies
        }
        self.assertIn(get_db.__name__, dependency_calls)
        self.assertNotIn(get_current_user.__name__, dependency_calls)

    def test_filters_provider_exactly_and_merges_sora_2_pro(self):
        with self.Session() as db:
            db.add_all(
                [
                    models.VideoModelPricing(
                        id=1,
                        provider="yijia",
                        model_name="sora-2",
                        duration=10,
                        aspect_ratio="16:9",
                        price_yuan=1.25,
                        updated_at=datetime(2026, 1, 2, 3, 4, 5),
                    ),
                    models.VideoModelPricing(
                        id=2,
                        provider="yijia",
                        model_name="sora-2-pro",
                        duration=25,
                        aspect_ratio="16:9",
                        price_yuan=8.75,
                        updated_at=datetime(2026, 1, 2, 3, 4, 6),
                    ),
                    models.VideoModelPricing(
                        id=3,
                        provider=" yijia",
                        model_name="sora-2",
                        duration=6,
                        aspect_ratio="1:1",
                        price_yuan=99.0,
                        updated_at=datetime(2026, 1, 2, 3, 4, 7),
                    ),
                    models.VideoModelPricing(
                        id=4,
                        provider="moti",
                        model_name="sora-2",
                        duration=10,
                        aspect_ratio="9:16",
                        price_yuan=42.0,
                        updated_at=datetime(2026, 1, 2, 3, 4, 8),
                    ),
                ]
            )
            db.commit()

            result = asyncio.run(self.endpoint(provider="yijia", db=db))

        self.assertEqual(result["provider"], "yijia")
        self.assertEqual(set(result["pricing"]), {"sora-2"})
        self.assertNotIn("sora-2-pro", result["pricing"])
        self.assertEqual(
            result["pricing"]["sora-2"],
            {
                "10_16:9": {
                    "duration": 10,
                    "aspect_ratio": "16:9",
                    "price_yuan": 1.25,
                },
                "25_16:9": {
                    "duration": 25,
                    "aspect_ratio": "16:9",
                    "price_yuan": 8.75,
                },
            },
        )

    def test_empty_provider_result_preserves_requested_provider(self):
        with self.Session() as db:
            result = asyncio.run(self.endpoint(provider="missing", db=db))

        self.assertEqual(
            result,
            {
                "pricing": {},
                "provider": "missing",
                "last_updated": None,
            },
        )

    def test_last_updated_returns_isoformat_when_present(self):
        updated_at = datetime(2026, 1, 2, 8, 30, 0)
        with self.Session() as db:
            db.add(
                models.VideoModelPricing(
                    id=1,
                    provider="yijia",
                    model_name="grok",
                    duration=6,
                    aspect_ratio="16:9",
                    price_yuan=1.0,
                    updated_at=updated_at,
                )
            )
            db.commit()

            result = asyncio.run(self.endpoint(provider="yijia", db=db))

        self.assertEqual(result["last_updated"], updated_at.isoformat())

    def test_soft_error_payload_does_not_include_provider(self):
        class FailingDb:
            def query(self, *_args, **_kwargs):
                raise RuntimeError("database unavailable")

        result = asyncio.run(self.endpoint(provider="yijia", db=FailingDb()))

        self.assertEqual(
            result,
            {
                "pricing": {},
                "last_updated": None,
                "error": "database unavailable",
            },
        )

    def _get_registered_endpoint(self):
        for route in main.app.routes:
            methods = getattr(route, "methods", set()) or set()
            if getattr(route, "path", None) == "/api/video-model-pricing" and "GET" in methods:
                return getattr(route, "endpoint"), route

        self.fail("GET /api/video-model-pricing is not registered")


if __name__ == "__main__":
    unittest.main()
