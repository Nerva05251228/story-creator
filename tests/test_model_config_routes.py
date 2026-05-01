import importlib
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

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
import api.services.admin_auth as admin_auth  # noqa: E402
import main  # noqa: E402
import models  # noqa: E402


ADMIN_PASSWORD = "model-config-test-admin"
MODEL_CONFIGS_SERVICE_MODULE = "api.services.model_configs"

try:
    model_configs_service = importlib.import_module(MODEL_CONFIGS_SERVICE_MODULE)
except ModuleNotFoundError:
    model_configs_service = main
    sys.modules.setdefault(MODEL_CONFIGS_SERVICE_MODULE, model_configs_service)


class ModelConfigRouteTests(unittest.TestCase):
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

        main.app.dependency_overrides[database.get_db] = override_get_db
        self.admin_password_patch = mock.patch.object(
            admin_auth,
            "ADMIN_PANEL_PASSWORD",
            ADMIN_PASSWORD,
        )
        self.admin_password_patch.start()
        self.client = TestClient(main.app, raise_server_exceptions=False)

    def tearDown(self):
        self.admin_password_patch.stop()
        main.app.dependency_overrides.pop(database.get_db, None)
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _admin_headers(self, password=ADMIN_PASSWORD):
        return {"X-Admin-Password": password}

    def _model_config_patch_target(self, route_path, function_name):
        _ = route_path
        return f"{MODEL_CONFIGS_SERVICE_MODULE}.{function_name}"

    def _seed_config(self, function_key="video_prompt"):
        db = self.Session()
        try:
            row = models.FunctionModelConfig(
                function_key=function_key,
                function_name="Sora video prompt",
                provider_key="openrouter",
                model_key="old-model",
                model_id="old-model",
            )
            db.add(row)
            db.commit()
        finally:
            db.close()

    def test_model_config_routes_require_admin_password(self):
        routes = [
            ("get", "/api/admin/model-configs", None),
            ("post", "/api/admin/model-configs/sync-models", None),
            ("put", "/api/admin/model-config/video_prompt", {"model_id": "model-a"}),
        ]

        for method, path, body in routes:
            with self.subTest(method=method, path=path, auth="missing"):
                caller = getattr(self.client, method)
                response = caller(path, json=body) if body is not None else caller(path)
                self.assertEqual(response.status_code, 403)

            with self.subTest(method=method, path=path, auth="wrong"):
                caller = getattr(self.client, method)
                if body is None:
                    response = caller(path, headers=self._admin_headers("wrong-password"))
                else:
                    response = caller(
                        path,
                        json=body,
                        headers=self._admin_headers("wrong-password"),
                    )
                self.assertEqual(response.status_code, 403)

    def test_get_model_configs_returns_models_and_function_configs(self):
        self._seed_config()
        cache_payload = {
            "models": [
                {
                    "model_id": "model-a",
                    "owned_by": "provider-a",
                    "available_providers_count": 1,
                    "raw_metadata": {"id": "model-a"},
                    "synced_at": "2026-01-02T03:04:05",
                }
            ],
            "last_synced_at": "2026-01-02T03:04:05",
        }
        resolved_payload = {
            "model_key": "old-model",
            "model_id": "old-model",
            "label": "Old Model",
        }

        with mock.patch(
            self._model_config_patch_target(
                "/api/admin/model-configs",
                "get_cached_models_payload",
            ),
            return_value=cache_payload,
        ) as get_cached_models:
            with mock.patch(
                self._model_config_patch_target(
                    "/api/admin/model-configs",
                    "resolve_ai_model_option",
                ),
                return_value=resolved_payload,
            ):
                response = self.client.get(
                    "/api/admin/model-configs",
                    headers=self._admin_headers(),
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("default_model", payload)
        self.assertEqual(payload["models"], cache_payload["models"])
        self.assertEqual(payload["last_synced_at"], cache_payload["last_synced_at"])
        configs_by_key = {
            item["function_key"]: item for item in payload["configs"]
        }
        self.assertIn("video_prompt", configs_by_key)
        self.assertEqual(
            set(configs_by_key["video_prompt"]),
            {
                "function_key",
                "function_name",
                "model_id",
                "resolved_model_key",
                "resolved_model_id",
                "resolved_model_label",
            },
        )
        self.assertEqual(configs_by_key["video_prompt"]["resolved_model_label"], "Old Model")
        self.assertEqual(get_cached_models.call_count, 1)

    def test_sync_models_returns_cache_payload(self):
        cache_payload = {
            "models": [
                {
                    "model_id": "model-b",
                    "owned_by": "",
                    "available_providers_count": 2,
                    "raw_metadata": {"id": "model-b"},
                    "synced_at": "2026-02-03T04:05:06",
                }
            ],
            "last_synced_at": "2026-02-03T04:05:06",
        }

        with mock.patch(
            self._model_config_patch_target(
                "/api/admin/model-configs/sync-models",
                "sync_models_from_upstream",
            ),
            return_value={"count": 1, "synced_at": "2026-02-03T04:05:06"},
        ) as sync_models:
            with mock.patch(
                self._model_config_patch_target(
                    "/api/admin/model-configs/sync-models",
                    "get_cached_models_payload",
                ),
                return_value=cache_payload,
            ):
                response = self.client.post(
                    "/api/admin/model-configs/sync-models",
                    headers=self._admin_headers(),
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "message": "模型缓存已同步",
                "count": 1,
                "last_synced_at": cache_payload["last_synced_at"],
                "models": cache_payload["models"],
            },
        )
        self.assertEqual(sync_models.call_count, 1)

    def test_update_model_config_returns_resolved_config_and_persists_model_id(self):
        self._seed_config()
        resolved_payload = {
            "model_key": "model-new",
            "model_id": "model-new",
            "label": "Model New",
        }

        with mock.patch(
            self._model_config_patch_target(
                "/api/admin/model-config/{function_key}",
                "resolve_ai_model_option",
            ),
            return_value=resolved_payload,
        ):
            response = self.client.put(
                "/api/admin/model-config/video_prompt",
                json={"model_id": "model-new"},
                headers=self._admin_headers(),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["function_key"], "video_prompt")
        self.assertEqual(payload["model_id"], "model-new")
        self.assertEqual(payload["resolved_model_key"], "model-new")
        self.assertEqual(payload["resolved_model_id"], "model-new")
        self.assertEqual(payload["resolved_model_label"], "Model New")

        db = self.Session()
        try:
            row = db.query(models.FunctionModelConfig).filter_by(
                function_key="video_prompt"
            ).first()
            self.assertEqual(row.model_id, "model-new")
            self.assertEqual(row.model_key, "model-new")
        finally:
            db.close()

    def test_update_model_config_returns_404_for_missing_function_key(self):
        response = self.client.put(
            "/api/admin/model-config/missing-function",
            json={"model_id": "model-new"},
            headers=self._admin_headers(),
        )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
