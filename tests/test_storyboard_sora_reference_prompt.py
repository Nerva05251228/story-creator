import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}")

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.env_defaults import apply_test_env_defaults  # noqa: E402

apply_test_env_defaults()

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402
import models  # noqa: E402

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class StoryboardSoraReferencePromptTests(unittest.TestCase):
    def test_append_reference_prompt_adds_standing_instruction_after_base_prompt(self):
        base_prompt = "原来的Sora提示词规则"
        reference_prompt = "镜头1：甲站左侧，乙站右侧。"

        result = main._append_sora_reference_prompt(base_prompt, reference_prompt)

        self.assertTrue(result.startswith(base_prompt))
        self.assertIn("请你参考这段提示词中的人物站位进行编写新的提示词：", result)
        self.assertTrue(result.endswith(reference_prompt))

    def test_append_reference_prompt_ignores_blank_reference(self):
        base_prompt = "原来的Sora提示词规则"

        result = main._append_sora_reference_prompt(base_prompt, "  \n ")

        self.assertEqual(result, base_prompt)


class StoryboardSoraReferenceRequestDataTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_build_request_data_appends_selected_reference_prompt(self):
        with self.Session() as db:
            script = models.Script(user_id=1, name="测试剧本", sora_prompt_style="")
            episode = models.Episode(
                script=script,
                name="测试片段",
                storyboard_video_duration=15,
            )
            shot = models.StoryboardShot(
                episode=episode,
                shot_number=2,
                script_excerpt="当前原剧本段落",
                duration=15,
            )
            reference_shot = models.StoryboardShot(
                episode=episode,
                shot_number=1,
                script_excerpt="参考原剧本段落",
                sora_prompt="参考镜头站位：甲在左，乙在右。",
                sora_prompt_status="completed",
            )
            db.add(models.ShotDurationTemplate(
                duration=15,
                shot_count_min=4,
                shot_count_max=5,
                time_segments=5,
                simple_storyboard_rule="simple",
                video_prompt_rule="模板：{script_excerpt}|{subject_text}|{scene_description}|{safe_duration}",
                large_shot_prompt_rule="large",
                is_default=True,
            ))
            db.add_all([script, episode, shot, reference_shot])
            db.commit()
            db.refresh(shot)
            db.refresh(reference_shot)

            with patch.object(
                main,
                "get_ai_config",
                return_value={
                    "provider_key": "relay",
                    "model": "gemini-3.1-pro",
                    "model_id": "gemini-3.1-pro",
                    "api_url": "https://llm.example.test/api/llm/v1/chat/completions",
                    "api_key": "test-api-token",
                    "timeout": 120,
                },
            ):
                request_data, task_payload = main._build_storyboard_prompt_request_data(
                    db,
                    shot=shot,
                    episode=episode,
                    script=script,
                    reference_shot_id=reference_shot.id,
                )

        prompt = request_data["messages"][0]["content"]
        self.assertIn("模板：当前原剧本段落", prompt)
        self.assertIn("请你参考这段提示词中的人物站位进行编写新的提示词：", prompt)
        self.assertIn("参考镜头站位：甲在左，乙在右。", prompt)
        self.assertEqual(task_payload["reference_shot_id"], reference_shot.id)
