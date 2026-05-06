import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
TESTS_DIR = ROOT_DIR / "tests"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}")

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from env_defaults import apply_test_env_defaults  # noqa: E402

apply_test_env_defaults()

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import models  # noqa: E402
from api.services import episode_text_generation  # noqa: E402


class EpisodeTextGenerationTemplateTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_resolve_narration_template_prefers_custom_then_script_then_global_setting(self):
        db = self.Session()
        try:
            user = models.User(username="owner", token="token", password_hash="hash", password_plain="123456")
            db.add(user)
            db.flush()

            script = models.Script(
                user_id=user.id,
                name="Script",
                narration_template="  Script narration template  ",
            )
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="Episode",
            )
            db.add(episode)
            db.add(
                models.GlobalSettings(
                    key="narration_conversion_template",
                    value="  Global narration template  ",
                )
            )
            db.commit()

            db.refresh(episode)

            self.assertEqual(
                episode_text_generation.resolve_narration_template(
                    episode,
                    db,
                    "  Custom narration template  ",
                ),
                "Custom narration template",
            )
            self.assertEqual(
                episode_text_generation.resolve_narration_template(episode, db),
                "Script narration template",
            )

            episode.script.narration_template = "   "
            db.commit()

            self.assertEqual(
                episode_text_generation.resolve_narration_template(episode, db),
                "Global narration template",
            )
        finally:
            db.close()

    def test_resolve_opening_template_prefers_custom_then_global_then_default(self):
        db = self.Session()
        try:
            db.add(
                models.GlobalSettings(
                    key="opening_generation_template",
                    value="  Global opening template  ",
                )
            )
            db.commit()

            self.assertEqual(
                episode_text_generation.resolve_opening_template(
                    db,
                    "  Custom opening template  ",
                ),
                "Custom opening template",
            )
            self.assertEqual(
                episode_text_generation.resolve_opening_template(db),
                "Global opening template",
            )

            db.query(models.GlobalSettings).delete()
            db.commit()

            self.assertEqual(
                episode_text_generation.resolve_opening_template(db),
                "我想把这个片段做成一个短视频，需要一个精彩吸引人的开头，请你帮我写一个开头",
            )
        finally:
            db.close()


class EpisodeTextGenerationSubmitTests(unittest.TestCase):
    def test_submit_episode_text_relay_task_builds_episode_payload_and_merges_extra_task_payload(self):
        episode = models.Episode(id=12, script_id=3, name="Episode")
        db = object()
        relay_task = object()

        with patch.object(
            episode_text_generation,
            "get_ai_config",
            return_value={"model": "relay-model"},
        ) as get_ai_config_mock, patch.object(
            episode_text_generation,
            "submit_and_persist_text_task",
            return_value=relay_task,
        ) as submit_mock:
            result = episode_text_generation.submit_episode_text_relay_task(
                db,
                episode=episode,
                task_type="opening",
                function_key="opening",
                prompt="Prompt body",
                response_format_json=True,
                extra_task_payload={"simple_shots": [{"shot_number": 1}]},
            )

        self.assertIs(result, relay_task)
        get_ai_config_mock.assert_called_once_with("opening")
        submit_mock.assert_called_once()
        self.assertIs(submit_mock.call_args.args[0], db)
        self.assertEqual(
            submit_mock.call_args.kwargs,
            {
                "task_type": "opening",
                "owner_type": "episode",
                "owner_id": 12,
                "stage_key": "opening",
                "function_key": "opening",
                "request_payload": {
                    "model": "relay-model",
                    "messages": [
                        {
                            "role": "user",
                            "content": "Prompt body",
                        }
                    ],
                    "response_format": {"type": "json_object"},
                    "stream": False,
                },
                "task_payload": {
                    "simple_shots": [{"shot_number": 1}],
                    "episode_id": 12,
                    "task_type": "opening",
                    "function_key": "opening",
                },
            },
        )

    def test_submit_detailed_storyboard_stage1_task_builds_expected_prompt_and_task_payload(self):
        db = object()
        relay_task = object()
        simple_shots = [
            {"shot_number": "1", "original_text": "她攥着铜镜快步离开"},
            {"shot_number": 2, "original_text": "回廊尽头传来脚步声"},
        ]

        with patch.object(
            episode_text_generation,
            "get_prompt_by_key",
            return_value="Analyze:\n{shots_content}",
        ) as get_prompt_mock, patch.object(
            episode_text_generation,
            "get_ai_config",
            return_value={"model": "storyboard-model"},
        ) as get_ai_config_mock, patch.object(
            episode_text_generation,
            "submit_and_persist_text_task",
            return_value=relay_task,
        ) as submit_mock:
            result = episode_text_generation.submit_detailed_storyboard_stage1_task(
                db,
                episode_id=34,
                simple_shots=simple_shots,
            )

        self.assertIs(result, relay_task)
        get_prompt_mock.assert_called_once_with("detailed_storyboard_content_analysis")
        get_ai_config_mock.assert_called_once_with("detailed_storyboard_s1")
        submit_mock.assert_called_once()
        self.assertIs(submit_mock.call_args.args[0], db)
        self.assertEqual(
            submit_mock.call_args.kwargs,
            {
                "task_type": "detailed_storyboard_stage1",
                "owner_type": "episode",
                "owner_id": 34,
                "stage_key": "detailed_storyboard_stage1",
                "function_key": "detailed_storyboard_s1",
                "request_payload": {
                    "model": "storyboard-model",
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Analyze:\n"
                                "镜头1:\n她攥着铜镜快步离开\n\n"
                                "镜头2:\n回廊尽头传来脚步声\n\n"
                            ),
                        }
                    ],
                    "response_format": {"type": "json_object"},
                    "stream": False,
                },
                "task_payload": {
                    "episode_id": 34,
                    "simple_shots": simple_shots,
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
