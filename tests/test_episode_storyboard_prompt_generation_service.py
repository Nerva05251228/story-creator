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
from api.services import episode_storyboard_prompt_generation  # noqa: E402


class EpisodeStoryboardPromptGenerationServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_build_storyboard_prompt_request_data_appends_reference_prompt(self):
        with self.Session() as db:
            script = models.Script(user_id=1, name="script", sora_prompt_style="")
            episode = models.Episode(script=script, name="episode", storyboard_video_duration=15)
            shot = models.StoryboardShot(
                episode=episode,
                shot_number=2,
                script_excerpt="current excerpt",
                duration=15,
            )
            reference_shot = models.StoryboardShot(
                episode=episode,
                shot_number=1,
                script_excerpt="reference excerpt",
                sora_prompt="reference blocking",
                sora_prompt_status="completed",
            )
            db.add(
                models.ShotDurationTemplate(
                    duration=15,
                    shot_count_min=4,
                    shot_count_max=5,
                    time_segments=5,
                    simple_storyboard_rule="simple",
                    video_prompt_rule="template:{script_excerpt}|{subject_text}|{scene_description}|{safe_duration}",
                    large_shot_prompt_rule="large",
                    is_default=True,
                )
            )
            db.add_all([script, episode, shot, reference_shot])
            db.commit()
            db.refresh(shot)
            db.refresh(reference_shot)

            with patch.object(
                episode_storyboard_prompt_generation,
                "get_ai_config",
                return_value={"model": "gemini-3.1-pro"},
            ):
                request_data, task_payload = episode_storyboard_prompt_generation.build_storyboard_prompt_request_data(
                    db,
                    shot=shot,
                    episode=episode,
                    script=script,
                    reference_shot_id=reference_shot.id,
                )

        prompt = request_data["messages"][0]["content"]
        self.assertIn("template:current excerpt", prompt)
        self.assertIn("请你参考这段提示词中的人物站位进行编写新的提示词：", prompt)
        self.assertIn("reference blocking", prompt)
        self.assertEqual(task_payload["reference_shot_id"], reference_shot.id)

    def test_submit_storyboard_prompt_task_builds_sora_prompt_task_payload(self):
        shot = models.StoryboardShot(id=11)
        episode = models.Episode(id=22)
        script = models.Script(id=33)
        db = object()
        relay_task = object()

        with patch.object(
            episode_storyboard_prompt_generation,
            "build_storyboard_prompt_request_data",
            return_value=({"model": "test-model", "messages": [{"role": "user", "content": "prompt"}]}, {"shot_id": 11}),
        ) as build_mock, patch.object(
            episode_storyboard_prompt_generation,
            "submit_and_persist_text_task",
            return_value=relay_task,
        ) as submit_mock:
            result = episode_storyboard_prompt_generation.submit_storyboard_prompt_task(
                db,
                shot=shot,
                episode=episode,
                script=script,
                prompt_key="generate_video_prompts",
            )

        self.assertIs(result, relay_task)
        build_mock.assert_called_once_with(
            db,
            shot=shot,
            episode=episode,
            script=script,
            prompt_key="generate_video_prompts",
            duration_template_field="video_prompt_rule",
            large_shot_template_id=None,
            reference_shot_id=None,
        )
        submit_mock.assert_called_once_with(
            db,
            task_type="sora_prompt",
            owner_type="shot",
            owner_id=11,
            stage_key="generate_video_prompts",
            function_key="video_prompt",
            request_payload={"model": "test-model", "messages": [{"role": "user", "content": "prompt"}]},
            task_payload={"shot_id": 11},
        )


if __name__ == "__main__":
    unittest.main()
