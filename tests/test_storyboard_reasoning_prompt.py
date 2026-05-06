import json
import os
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}")

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402
import models  # noqa: E402


class StoryboardReasoningPromptTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_build_reasoning_request_uses_prompt_config_without_json_mode(self):
        with self.Session() as db:
            script = models.Script(user_id=1, name="test-script")
            episode = models.Episode(script=script, name="ep")
            shot = models.StoryboardShot(
                episode=episode,
                shot_number=1,
                script_excerpt="原始镜头段落",
            )
            db.add(models.PromptConfig(
                key="storyboard_reasoning_prompt_prefix",
                name="故事板推理提示词前缀",
                description="desc",
                content="根据这段话想个高级感电影剧本，旁白不要变，融入到画面中，不要片名，15s时间轴\n{script_excerpt}",
            ))
            db.add_all([script, episode, shot])
            db.commit()
            db.refresh(shot)

            request_data, task_payload = main._build_storyboard_reasoning_request_data(
                db,
                shot=shot,
                episode=episode,
                script=script,
            )

        self.assertEqual(request_data["stream"], False)
        self.assertNotIn("response_format", request_data)
        self.assertIn("原始镜头段落", request_data["messages"][0]["content"])
        self.assertEqual(task_payload["shot_id"], shot.id)
        self.assertEqual(task_payload["prompt_key"], "storyboard_reasoning_prompt_prefix")

    def test_reasoning_task_success_updates_script_excerpt(self):
        with self.Session() as db:
            script = models.Script(user_id=1, name="test-script")
            episode = models.Episode(script=script, name="ep")
            shot = models.StoryboardShot(
                episode=episode,
                shot_number=1,
                script_excerpt="旧原文",
                selected_card_ids=json.dumps([], ensure_ascii=False),
            )
            task = models.TextRelayTask(
                task_type="storyboard_reasoning_prompt",
                owner_type="shot",
                owner_id=1,
                stage_key="storyboard_reasoning_prompt_prefix",
                function_key="video_prompt",
                task_payload=json.dumps({"shot_id": 1, "prompt_key": "storyboard_reasoning_prompt_prefix"}, ensure_ascii=False),
            )
            db.add_all([script, episode, shot])
            db.commit()
            task.owner_id = shot.id
            task.task_payload = json.dumps({"shot_id": shot.id, "prompt_key": "storyboard_reasoning_prompt_prefix"}, ensure_ascii=False)
            db.add(task)
            db.commit()

            upstream_payload = {
                "result": {
                    "choices": [
                        {
                            "message": {
                                "content": "新的推理结果"
                            }
                        }
                    ]
                }
            }

            main.handle_text_relay_task_success(db, task, upstream_payload)
            db.commit()
            db.refresh(shot)

        self.assertEqual(shot.script_excerpt, "新的推理结果")
        self.assertEqual(shot.reasoning_prompt_status, "completed")


if __name__ == "__main__":
    unittest.main()
