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


class StoryboardSoraPromptTemplateTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _create_context(self, db):
        user = models.User(username="u", token="t", password_hash="", password_plain="")
        script = models.Script(user_id=1, name="script", sora_prompt_style="")
        episode = models.Episode(script=script, name="ep", storyboard_video_duration=15)
        library = models.StoryLibrary(user_id=1, episode=episode, name="lib")
        card = models.SubjectCard(library=library, name="主角", card_type="角色", ai_prompt="角色提示")
        shot = models.StoryboardShot(
            episode=episode,
            shot_number=1,
            script_excerpt="这里是原剧本段落",
            selected_card_ids="[1]",
        )
        db.add(user)
        db.add_all([script, episode, library, card, shot])
        db.flush()
        shot.selected_card_ids = f"[{card.id}]"
        db.commit()
        db.refresh(script)
        db.refresh(episode)
        db.refresh(shot)
        return script, episode, shot

    def test_generate_video_prompt_uses_selected_storyboard_sora_template(self):
        with self.Session() as db:
            script, episode, shot = self._create_context(db)
            db.add(models.ShotDurationTemplate(
                duration=15,
                shot_count_min=4,
                shot_count_max=5,
                time_segments=5,
                simple_storyboard_rule="rule",
                video_prompt_rule="LEGACY_DURATION_TEMPLATE::{script_excerpt}",
                large_shot_prompt_rule="large",
                is_default=True,
            ))
            template = models.StoryboardSoraPromptTemplate(
                name="四镜平稳镜头",
                content="SORA_TEMPLATE::{script_excerpt}::{safe_duration}::{subject_text}",
                is_default=True,
            )
            db.add(template)
            db.commit()
            db.refresh(template)

            request_data, _task_payload = main._build_storyboard_prompt_request_data(
                db,
                shot=shot,
                episode=episode,
                script=script,
                prompt_key="generate_video_prompts",
                duration_template_field="video_prompt_rule",
                storyboard_sora_template_id=template.id,
            )

        prompt = request_data["messages"][0]["content"]
        self.assertTrue(prompt.startswith("SORA_TEMPLATE::这里是原剧本段落::15::"))
        self.assertNotIn("LEGACY_DURATION_TEMPLATE", prompt)

    def test_generate_video_prompt_uses_default_storyboard_sora_template_over_duration_rule(self):
        with self.Session() as db:
            script, episode, shot = self._create_context(db)
            db.add(models.ShotDurationTemplate(
                duration=15,
                shot_count_min=4,
                shot_count_max=5,
                time_segments=5,
                simple_storyboard_rule="rule",
                video_prompt_rule="LEGACY_DURATION_TEMPLATE::{script_excerpt}",
                large_shot_prompt_rule="large",
                is_default=True,
            ))
            db.add(models.StoryboardSoraPromptTemplate(
                name="默认模板",
                content="DEFAULT_SORA_TEMPLATE::{script_excerpt}",
                is_default=True,
            ))
            db.commit()

            request_data, _task_payload = main._build_storyboard_prompt_request_data(
                db,
                shot=shot,
                episode=episode,
                script=script,
                prompt_key="generate_video_prompts",
                duration_template_field="video_prompt_rule",
            )

        prompt = request_data["messages"][0]["content"]
        self.assertTrue(prompt.startswith("DEFAULT_SORA_TEMPLATE::这里是原剧本段落"))
        self.assertNotIn("LEGACY_DURATION_TEMPLATE", prompt)


if __name__ == "__main__":
    unittest.main()
