import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from requests.exceptions import ReadTimeout
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import ai_service
import text_llm_queue
import models


class SimpleStoryboardLoggingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_session_local = ai_service.SessionLocal
        ai_service.SessionLocal = self.Session
        text_llm_queue.reset_text_llm_queue_state_for_tests()

        db = self.Session()
        try:
            db.add(
                models.ShotDurationTemplate(
                    duration=15,
                    shot_count_min=5,
                    shot_count_max=7,
                    time_segments=3,
                    simple_storyboard_rule="prompt {content}",
                    video_prompt_rule="video prompt",
                    large_shot_prompt_rule="",
                    is_default=True,
                )
            )
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        ai_service.SessionLocal = self.original_session_local
        self.engine.dispose()

    def test_timeout_attempt_logs_failure_debug_event(self):
        debug_calls = []

        def fake_save_ai_debug(*args, **kwargs):
            debug_calls.append({
                "stage": args[0] if len(args) > 0 else None,
                "input_data": args[1] if len(args) > 1 else None,
                "output_data": args[2] if len(args) > 2 else None,
                "kwargs": kwargs,
            })
            return kwargs.get("task_folder") or "simple_storyboard_episode_1"

        fake_main = types.ModuleType("main")
        fake_main.save_ai_debug = fake_save_ai_debug

        with patch.dict(sys.modules, {"main": fake_main}), \
             patch.object(ai_service, "get_ai_config", return_value={
                 "provider_key": "yyds",
                 "provider_name": "YYDS",
                 "api_url": "https://example.com/v1/chat/completions",
                 "api_key": "test-key",
                 "timeout": 10,
                 "model": "gemini-3.1-pro-high",
                 "model_id": "gemini-3.1-pro-high",
                 "model_key": "gemini_pro_high",
             }), \
             patch.object(ai_service, "build_ai_debug_config", side_effect=lambda config: dict(config)), \
             patch.object(text_llm_queue.requests, "post", side_effect=ReadTimeout("timed out")), \
             patch.object(ai_service.time, "sleep", return_value=None):
            with self.assertRaises(Exception):
                ai_service.generate_simple_storyboard(
                    "第一段文本\n\n第二段文本",
                    batch_size=500,
                    duration=15,
                    episode_id=1,
                    task_folder="simple_storyboard_episode_1",
                )

        failure_events = [
            call for call in debug_calls
            if call["stage"] == "simple_storyboard"
            and isinstance(call["output_data"], dict)
            and call["output_data"].get("exception_type") == "ReadTimeout"
        ]

        self.assertTrue(failure_events, "timeout attempts should write failed debug events")
        self.assertIn("timed out", failure_events[0]["output_data"].get("error", ""))
        self.assertEqual(
            text_llm_queue.get_text_llm_queue_state(),
            {"running": 0, "waiting": 0, "max": 5},
        )


if __name__ == "__main__":
    unittest.main()
