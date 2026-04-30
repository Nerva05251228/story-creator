import asyncio
import json
import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}")

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.env_defaults import TEST_LLM_RELAY_BASE_URL, apply_test_env_defaults  # noqa: E402

apply_test_env_defaults()

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import ai_config  # noqa: E402
import billing_service  # noqa: E402
import dashboard_service  # noqa: E402
import main  # noqa: E402
import models  # noqa: E402
import text_relay_service  # noqa: E402


class RelayAiConfigTests(unittest.TestCase):
    def test_default_ai_config_uses_relay_default_model(self):
        with patch.object(ai_config, "_query_relay_model_rows", return_value=[]):
            config = ai_config.get_ai_config()

        self.assertEqual(config["provider_key"], "relay")
        self.assertEqual(config["model"], "gemini-3.1-pro")
        self.assertEqual(
            str(config["api_url"]),
            f"{TEST_LLM_RELAY_BASE_URL}/v1/chat/completions",
        )
        self.assertEqual(
            ai_config.RELAY_MODELS_URL,
            f"{TEST_LLM_RELAY_BASE_URL}/v1/models",
        )
        self.assertEqual(
            ai_config.RELAY_TASKS_URL_PREFIX,
            f"{TEST_LLM_RELAY_BASE_URL}/v1/tasks",
        )


class RelayModelConfigTests(unittest.TestCase):
    ADMIN_PASSWORD = "test-admin-password"

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_main_session_local = main.SessionLocal
        self.original_relay_session_local = text_relay_service.SessionLocal
        main.SessionLocal = self.Session
        text_relay_service.SessionLocal = self.Session

    def tearDown(self):
        main.SessionLocal = self.original_main_session_local
        text_relay_service.SessionLocal = self.original_relay_session_local
        self.engine.dispose()

    def test_model_configs_rewrite_legacy_provider_rows_to_single_model_id(self):
        with self.Session() as db:
            db.add(
                models.FunctionModelConfig(
                    function_key="video_prompt",
                    function_name="旧配置",
                    provider_key="yyds",
                    model_key="gemini_pro_high",
                    model_id="gemini-3.1-pro-high",
                )
            )
            db.add(
                models.RelayModel(
                    model_id="gemini-3.1-pro",
                    owned_by="google",
                    raw_metadata=json.dumps({"id": "gemini-3.1-pro"}, ensure_ascii=False),
                )
            )
            db.commit()

            main._ensure_function_model_configs(db)
            with patch.object(main, "ADMIN_PANEL_PASSWORD", self.ADMIN_PASSWORD):
                payload = asyncio.run(
                    main.get_model_configs(
                        x_admin_password=self.ADMIN_PASSWORD,
                        db=db,
                    )
                )

            rows = {
                row.function_key: row
                for row in db.query(models.FunctionModelConfig).all()
            }
            self.assertIn("subject_prompt", rows)
            self.assertEqual(rows["subject_prompt"].model_id, "gemini-3.1-pro")
            self.assertEqual(rows["video_prompt"].model_id, "gemini-3.1-pro")

            self.assertIn("models", payload)
            self.assertIn("configs", payload)
            self.assertNotIn("providers", payload)
            self.assertNotIn("provider_catalogs", payload)

            config_map = {item["function_key"]: item for item in payload["configs"]}
            self.assertEqual(config_map["video_prompt"]["model_id"], "gemini-3.1-pro")
            self.assertNotIn("provider_key", config_map["video_prompt"])

    def test_model_configs_drop_obsolete_simple_storyboard_function(self):
        with self.Session() as db:
            db.add(
                models.FunctionModelConfig(
                    function_key="simple_storyboard",
                    function_name="简单分镜生成",
                    provider_key="relay",
                    model_key="gemini-3.1-pro",
                    model_id="gemini-3.1-pro",
                )
            )
            db.commit()

            main._ensure_function_model_configs(db)
            with patch.object(main, "ADMIN_PANEL_PASSWORD", self.ADMIN_PASSWORD):
                payload = asyncio.run(
                    main.get_model_configs(
                        x_admin_password=self.ADMIN_PASSWORD,
                        db=db,
                    )
                )

            rows = db.query(models.FunctionModelConfig).all()
            self.assertNotIn(
                "simple_storyboard",
                {row.function_key for row in rows},
            )
            self.assertNotIn(
                "simple_storyboard",
                {item["function_key"] for item in payload["configs"]},
            )


class RelaySubmitTests(unittest.TestCase):
    def test_submit_task_builds_default_poll_url_when_upstream_omits_it(self):
        class DummyResponse:
            status_code = 202

            def json(self):
                return {"id": "task-123"}

        with patch.object(text_relay_service.requests, "post", return_value=DummyResponse()):
            result = text_relay_service.submit_chat_completion_task(
                {
                    "model": "gemini-3.1-pro",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                }
            )

        self.assertEqual(result["external_task_id"], "task-123")
        self.assertEqual(
            result["poll_url"],
            f"{TEST_LLM_RELAY_BASE_URL}/v1/tasks/task-123",
        )

    def test_submit_task_normalizes_relative_poll_url_against_host_root(self):
        class DummyResponse:
            status_code = 202

            def json(self):
                return {
                    "id": "task-456",
                    "poll_url": "/api/llm/v1/tasks/task-456",
                }

        with patch.object(text_relay_service.requests, "post", return_value=DummyResponse()):
            result = text_relay_service.submit_chat_completion_task(
                {
                    "model": "gemini-3.1-pro",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                }
            )

        self.assertEqual(result["external_task_id"], "task-456")
        self.assertEqual(
            result["poll_url"],
            f"{TEST_LLM_RELAY_BASE_URL}/v1/tasks/task-456",
        )

    def test_submit_task_rejects_placeholder_relay_config_before_network(self):
        with patch.dict(
            os.environ,
            {
                "TEXT_RELAY_BASE_URL": "https://relay.example.invalid/api/llm",
                "TEXT_RELAY_API_KEY": "test-api-token",
            },
            clear=False,
        ):
            with patch.object(text_relay_service.requests, "post") as post_mock:
                with self.assertRaisesRegex(RuntimeError, "placeholder"):
                    text_relay_service.submit_chat_completion_task(
                        {
                            "model": "gemini-3.1-pro",
                            "messages": [{"role": "user", "content": "hi"}],
                            "stream": False,
                        }
                    )

        post_mock.assert_not_called()


class RelayBillingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        db = self.Session()
        try:
            user = models.User(
                username="tester",
                token="token",
                password_hash="hash",
                password_plain="123456",
            )
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="script")
            db.add(script)
            db.flush()

            episode = models.Episode(script_id=script.id, name="episode", billing_version=1)
            db.add(episode)
            db.commit()
            self.episode_id = int(episode.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_successful_text_task_records_cost_based_charge(self):
        with self.Session() as db:
            entry = billing_service.record_text_task_cost_for_episode(
                db,
                episode_id=self.episode_id,
                stage="opening",
                model_name="gemini-3.1-pro-high",
                cost_rmb=Decimal("0.52000"),
                external_task_id="task-opening-1",
                billing_key="text:opening:episode:1",
                operation_key="text:opening:episode",
                detail_payload={"task_type": "opening"},
            )
            db.commit()
            db.refresh(entry)

            self.assertEqual(entry.provider, "relay")
            self.assertEqual(entry.quantity, Decimal("1.00000"))
            self.assertEqual(entry.unit_price_rmb, Decimal("0.52000"))
            self.assertEqual(entry.amount_rmb, Decimal("0.52000"))
            self.assertEqual(entry.external_task_id, "task-opening-1")


class RelayPollerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_relay_session_local = text_relay_service.SessionLocal
        self.original_billing_session_local = billing_service.SessionLocal
        self.original_dashboard_session_local = dashboard_service.SessionLocal
        text_relay_service.SessionLocal = self.Session
        billing_service.SessionLocal = self.Session
        dashboard_service.SessionLocal = self.Session

        db = self.Session()
        try:
            user = models.User(
                username="tester",
                token="token",
                password_hash="hash",
                password_plain="123456",
            )
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="script")
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="episode",
                billing_version=1,
                opening_generating=True,
            )
            db.add(episode)
            db.flush()

            db.add(
                models.TextRelayTask(
                    task_type="opening",
                    owner_type="episode",
                    owner_id=episode.id,
                    stage_key="opening",
                    function_key="opening",
                    model_id="gemini-3.1-pro",
                    external_task_id="task-123",
                    poll_url="https://relay.local/v1/tasks/task-123",
                    status="submitted",
                    request_payload="{}",
                    task_payload=json.dumps({"episode_id": episode.id}, ensure_ascii=False),
                )
            )
            db.commit()
            self.episode_id = int(episode.id)
        finally:
            db.close()

    def tearDown(self):
        text_relay_service.SessionLocal = self.original_relay_session_local
        billing_service.SessionLocal = self.original_billing_session_local
        dashboard_service.SessionLocal = self.original_dashboard_session_local
        self.engine.dispose()

    def test_process_pending_tasks_once_recovers_and_finalizes_opening_task(self):
        class DummyPollResponse:
            status_code = 200

            def json(self):
                return {
                    "id": "task-123",
                    "status": "succeeded",
                    "model": "gemini-3.1-pro-high",
                    "cost_usd": 0.52,
                    "result": {
                        "choices": [
                            {
                                "message": {
                                    "content": "这是新的精彩开头",
                                }
                            }
                        ]
                    },
                }

        with patch.object(text_relay_service.requests, "get", return_value=DummyPollResponse()):
            processed = text_relay_service.process_pending_tasks_once()

        self.assertEqual(processed, 1)

        db = self.Session()
        try:
            episode = db.query(models.Episode).filter(models.Episode.id == self.episode_id).first()
            task = db.query(models.TextRelayTask).filter(models.TextRelayTask.external_task_id == "task-123").first()
            ledger_entries = db.query(models.BillingLedgerEntry).filter(
                models.BillingLedgerEntry.external_task_id == "task-123"
            ).all()
            dashboard_rows = db.query(models.DashboardTaskLog).filter(
                models.DashboardTaskLog.external_task_id == "task-123"
            ).all()

            self.assertEqual(episode.opening_content, "这是新的精彩开头")
            self.assertFalse(bool(episode.opening_generating))
            self.assertEqual(task.status, "succeeded")
            self.assertEqual(task.billing_status, "recorded")
            self.assertEqual(task.cost_rmb, Decimal("3.64000"))
            self.assertEqual(len(ledger_entries), 1)
            self.assertEqual(ledger_entries[0].amount_rmb, Decimal("3.64000"))
            self.assertEqual(len(dashboard_rows), 1)
            self.assertEqual(dashboard_rows[0].task_type, "opening")
            self.assertEqual(dashboard_rows[0].status, "completed")
        finally:
            db.close()


class RelayBackfillTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_relay_session_local = text_relay_service.SessionLocal
        self.original_billing_session_local = billing_service.SessionLocal
        self.original_dashboard_session_local = dashboard_service.SessionLocal
        text_relay_service.SessionLocal = self.Session
        billing_service.SessionLocal = self.Session
        dashboard_service.SessionLocal = self.Session

        db = self.Session()
        try:
            user = models.User(
                username="tester",
                token="token",
                password_hash="hash",
                password_plain="123456",
            )
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="script")
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="episode",
                billing_version=1,
            )
            db.add(episode)
            db.flush()

            task = models.TextRelayTask(
                task_type="opening",
                owner_type="episode",
                owner_id=episode.id,
                stage_key="opening",
                function_key="opening",
                model_id="gemini-3.1-pro",
                external_task_id="legacy-task-1",
                poll_url="https://relay.local/v1/tasks/legacy-task-1",
                status="succeeded",
                request_payload=json.dumps(
                    {"model": "gemini-3.1-pro", "messages": [{"role": "user", "content": "hi"}]},
                    ensure_ascii=False,
                ),
                task_payload=json.dumps({"episode_id": episode.id}, ensure_ascii=False),
                result_payload=json.dumps(
                    {
                        "id": "legacy-task-1",
                        "status": "succeeded",
                        "model": "gemini-3.1-pro",
                        "cost_usd": 0.125,
                        "result": {
                            "choices": [
                                {
                                    "message": {
                                        "content": "legacy opening content",
                                    }
                                }
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
                billing_status="skipped",
            )
            db.add(task)
            db.commit()
            self.task_id = int(task.id)
        finally:
            db.close()

    def tearDown(self):
        text_relay_service.SessionLocal = self.original_relay_session_local
        billing_service.SessionLocal = self.original_billing_session_local
        dashboard_service.SessionLocal = self.original_dashboard_session_local
        self.engine.dispose()

    def test_backfill_text_relay_records_repairs_dashboard_and_billing(self):
        repaired = text_relay_service.backfill_text_relay_records()

        self.assertGreaterEqual(repaired["dashboard_synced"], 1)
        self.assertGreaterEqual(repaired["billing_recorded"], 1)

        db = self.Session()
        try:
            task = db.query(models.TextRelayTask).filter(models.TextRelayTask.id == self.task_id).first()
            dashboard_rows = db.query(models.DashboardTaskLog).filter(
                models.DashboardTaskLog.external_task_id == "legacy-task-1"
            ).all()
            ledger_entries = db.query(models.BillingLedgerEntry).filter(
                models.BillingLedgerEntry.external_task_id == "legacy-task-1"
            ).all()

            self.assertEqual(task.billing_status, "recorded")
            self.assertEqual(task.cost_rmb, Decimal("0.87500"))
            self.assertEqual(len(dashboard_rows), 1)
            self.assertEqual(dashboard_rows[0].status, "completed")
            self.assertEqual(len(ledger_entries), 1)
            self.assertEqual(ledger_entries[0].amount_rmb, Decimal("0.87500"))
        finally:
            db.close()


class RelayPendingTaskRobustnessTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_relay_session_local = text_relay_service.SessionLocal
        self.original_billing_session_local = billing_service.SessionLocal
        self.original_dashboard_session_local = dashboard_service.SessionLocal
        text_relay_service.SessionLocal = self.Session
        billing_service.SessionLocal = self.Session
        dashboard_service.SessionLocal = self.Session

    def tearDown(self):
        text_relay_service.SessionLocal = self.original_relay_session_local
        billing_service.SessionLocal = self.original_billing_session_local
        dashboard_service.SessionLocal = self.original_dashboard_session_local
        self.engine.dispose()

    def test_process_pending_tasks_once_marks_terminal_parse_failures_as_failed(self):
        db = self.Session()
        try:
            user = models.User(
                username="relay-pending-user",
                token="relay-pending-token",
                password_hash="hash",
                password_plain="123456",
            )
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="relay-pending-script")
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="relay-pending-episode",
                simple_storyboard_generating=True,
                billing_version=1,
            )
            db.add(episode)
            db.flush()

            batch = models.SimpleStoryboardBatch(
                episode_id=episode.id,
                batch_index=1,
                total_batches=1,
                status="submitting",
                source_text="source",
                shots_data="",
                error_message="",
                last_attempt=0,
                retry_count=0,
            )
            db.add(batch)
            db.flush()

            db.add(
                models.TextRelayTask(
                    task_type="simple_storyboard_batch",
                    owner_type="simple_storyboard_batch",
                    owner_id=batch.id,
                    stage_key="simple_storyboard",
                    function_key="simple_storyboard",
                    model_id="gemini-3.1-pro",
                    external_task_id="pending-batch-1",
                    poll_url="https://relay.local/v1/tasks/pending-batch-1",
                    status="submitted",
                    request_payload="{}",
                    task_payload=json.dumps(
                        {"episode_id": episode.id, "batch_row_id": batch.id, "batch_index": 1},
                        ensure_ascii=False,
                    ),
                )
            )
            db.commit()
        finally:
            db.close()

        class DummyPollResponse:
            status_code = 200

            def json(self):
                return {
                    "id": "pending-batch-1",
                    "status": "succeeded",
                    "model": "gemini-3.1-pro",
                    "result": {
                        "choices": [
                            {
                                "message": {
                                    "content": "not-json",
                                }
                            }
                        ]
                    },
                }

        with patch.object(text_relay_service.requests, "get", return_value=DummyPollResponse()):
            processed = text_relay_service.process_pending_tasks_once(limit=10)

        self.assertEqual(processed, 1)

        db = self.Session()
        try:
            task = db.query(models.TextRelayTask).filter(models.TextRelayTask.external_task_id == "pending-batch-1").first()
            batch = db.query(models.SimpleStoryboardBatch).first()
            episode = db.query(models.Episode).first()

            self.assertEqual(task.status, "failed")
            self.assertTrue(bool(task.completed_at))
            self.assertIn("本地程序生成", task.error_message)
            self.assertEqual(batch.status, "failed")
            self.assertFalse(bool(episode.simple_storyboard_generating))
            self.assertIn("本地程序生成", episode.simple_storyboard_error)
        finally:
            db.close()

    def test_process_pending_tasks_once_prioritizes_oldest_update_not_oldest_create(self):
        db = self.Session()
        try:
            user = models.User(
                username="relay-order-user",
                token="relay-order-token",
                password_hash="hash",
                password_plain="123456",
            )
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="relay-order-script")
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="relay-order-episode",
                opening_generating=True,
                billing_version=1,
            )
            db.add(episode)
            db.flush()

            older_task = models.TextRelayTask(
                task_type="opening",
                owner_type="episode",
                owner_id=episode.id,
                stage_key="opening",
                function_key="opening",
                model_id="gemini-3.1-pro",
                external_task_id="older-task",
                poll_url="https://relay.local/v1/tasks/older-task",
                status="submitted",
                request_payload="{}",
                task_payload=json.dumps({"episode_id": episode.id}, ensure_ascii=False),
            )
            newer_task = models.TextRelayTask(
                task_type="opening",
                owner_type="episode",
                owner_id=episode.id,
                stage_key="opening",
                function_key="opening",
                model_id="gemini-3.1-pro",
                external_task_id="newer-task",
                poll_url="https://relay.local/v1/tasks/newer-task",
                status="submitted",
                request_payload="{}",
                task_payload=json.dumps({"episode_id": episode.id}, ensure_ascii=False),
            )
            db.add(older_task)
            db.flush()
            db.add(newer_task)
            db.flush()

            older_task.created_at = main.datetime.utcnow()
            older_task.updated_at = main.datetime.utcnow()
            newer_task.created_at = main.datetime.utcnow()
            newer_task.updated_at = main.datetime(2026, 1, 1)
            db.commit()
        finally:
            db.close()

        class DummyPollResponse:
            status_code = 200

            def __init__(self, task_id: str):
                self.task_id = task_id

            def json(self):
                return {
                    "id": self.task_id,
                    "status": "succeeded",
                    "model": "gemini-3.1-pro",
                    "cost_usd": 0.01,
                    "result": {
                        "choices": [
                            {
                                "message": {
                                    "content": "opening content",
                                }
                            }
                        ]
                    },
                }

        def fake_get(url, headers=None, timeout=None):
            task_id = str(url).rsplit("/", 1)[-1]
            return DummyPollResponse(task_id)

        with patch.object(text_relay_service.requests, "get", side_effect=fake_get):
            processed = text_relay_service.process_pending_tasks_once(limit=1)

        self.assertEqual(processed, 1)

        db = self.Session()
        try:
            older_task = db.query(models.TextRelayTask).filter(models.TextRelayTask.external_task_id == "older-task").first()
            newer_task = db.query(models.TextRelayTask).filter(models.TextRelayTask.external_task_id == "newer-task").first()

            self.assertEqual(older_task.status, "submitted")
            self.assertEqual(newer_task.status, "succeeded")
        finally:
            db.close()


class ScriptDeleteCleanupTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(self.engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        db = self.Session()
        try:
            user = models.User(
                username="deleter",
                token="delete-token",
                password_hash="hash",
                password_plain="123456",
            )
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="script-to-delete")
            db.add(script)
            db.flush()

            episode = models.Episode(script_id=script.id, name="ep-1", billing_version=1)
            db.add(episode)
            db.flush()

            db.add(
                models.SimpleStoryboardBatch(
                    episode_id=episode.id,
                    batch_index=1,
                    total_batches=1,
                    status="completed",
                    shots_data=json.dumps({"shots": []}, ensure_ascii=False),
                )
            )
            db.add(
                models.ManagedSession(
                    episode_id=episode.id,
                    status="running",
                )
            )
            db.add(
                models.VoiceoverTtsTask(
                    episode_id=episode.id,
                    line_id="line-1",
                    status="completed",
                    request_json="{}",
                    result_json="{}",
                )
            )
            db.commit()

            self.user_id = int(user.id)
            self.script_id = int(script.id)
            self.episode_id = int(episode.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_delete_script_cleans_non_cascaded_episode_dependencies(self):
        db = self.Session()
        try:
            user = db.query(models.User).filter(models.User.id == self.user_id).first()
            result = asyncio.run(
                main.delete_script(
                    script_id=self.script_id,
                    user=user,
                    db=db,
                )
            )

            self.assertEqual(result["script_id"], self.script_id)
            self.assertIsNone(db.query(models.Script).filter(models.Script.id == self.script_id).first())
            self.assertEqual(
                db.query(models.Episode).filter(models.Episode.id == self.episode_id).count(),
                0,
            )
            self.assertEqual(db.query(models.SimpleStoryboardBatch).count(), 0)
            self.assertEqual(db.query(models.ManagedSession).count(), 0)
            self.assertEqual(db.query(models.VoiceoverTtsTask).count(), 0)
        finally:
            db.close()


class EpisodeRuntimeFlagTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        db = self.Session()
        try:
            user = models.User(
                username="runtime-user",
                token="runtime-token",
                password_hash="hash",
                password_plain="123456",
            )
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="runtime-script")
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="runtime-episode",
                batch_generating_prompts=True,
                simple_storyboard_generating=True,
                billing_version=1,
            )
            db.add(episode)
            db.flush()

            db.add(
                models.StoryboardShot(
                    episode_id=episode.id,
                    shot_number=1,
                    sora_prompt_status="completed",
                )
            )
            db.add(
                models.SimpleStoryboardBatch(
                    episode_id=episode.id,
                    batch_index=1,
                    total_batches=1,
                    status="completed",
                    shots_data=json.dumps({"shots": []}, ensure_ascii=False),
                )
            )
            db.commit()
            self.episode_id = int(episode.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_reconcile_episode_runtime_flags_clears_stale_batch_flags(self):
        db = self.Session()
        try:
            episode = db.query(models.Episode).filter(models.Episode.id == self.episode_id).first()
            changed = main._reconcile_episode_runtime_flags(episode, db)

            self.assertTrue(changed)
            self.assertFalse(bool(episode.batch_generating_prompts))
            self.assertFalse(bool(episode.simple_storyboard_generating))
        finally:
            db.close()


class StoryboardPromptRuntimeRepairTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        db = self.Session()
        try:
            user = models.User(
                username="prompt-runtime-user",
                token="prompt-runtime-token",
                password_hash="hash",
                password_plain="123456",
            )
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="prompt-runtime-script")
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="prompt-runtime-episode",
                batch_generating_prompts=True,
                billing_version=1,
            )
            db.add(episode)
            db.flush()

            db.add(
                models.StoryboardShot(
                    episode_id=episode.id,
                    shot_number=1,
                    sora_prompt_status="generating",
                    sora_prompt="已有提示词",
                )
            )
            db.add(
                models.StoryboardShot(
                    episode_id=episode.id,
                    shot_number=2,
                    sora_prompt_status="generating",
                    sora_prompt="",
                    storyboard_video_prompt="",
                    video_status="idle",
                )
            )
            db.commit()
            self.episode_id = int(episode.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_reconcile_episode_runtime_flags_repairs_stale_generating_shots_without_tasks(self):
        db = self.Session()
        try:
            episode = db.query(models.Episode).filter(models.Episode.id == self.episode_id).first()

            changed = main._reconcile_episode_runtime_flags(episode, db)

            shots = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.episode_id == self.episode_id
            ).order_by(models.StoryboardShot.shot_number.asc()).all()

            self.assertTrue(changed)
            self.assertEqual([shot.sora_prompt_status for shot in shots], ["completed", "failed"])
            self.assertFalse(bool(episode.batch_generating_prompts))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
