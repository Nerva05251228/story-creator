import os
import sys
import unittest
from datetime import datetime, timedelta
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

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tests.env_defaults import apply_test_env_defaults  # noqa: E402

apply_test_env_defaults()

import models  # noqa: E402
from api.services import storyboard_prompt_context  # noqa: E402


class StoryboardPromptContextServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _card(self, **overrides):
        return models.SubjectCard(
            library_id=overrides.pop("library_id", 1),
            name=overrides.pop("name", "角色"),
            card_type=overrides.pop("card_type", "角色"),
            role_personality=overrides.pop("role_personality", ""),
            is_protagonist=overrides.pop("is_protagonist", False),
            protagonist_gender=overrides.pop("protagonist_gender", ""),
            **overrides,
        )

    def test_build_subject_text_groups_protagonists_and_other_subjects(self):
        cards = [
            self._card(name="陆云", role_personality="冷静", is_protagonist=True, protagonist_gender="male"),
            self._card(name="苏晚", is_protagonist=True, protagonist_gender="female"),
            self._card(name="管家", role_personality="狡猾"),
            self._card(name="茶馆", card_type="场景", role_personality="不应追加"),
            self._card(name="铜镜", card_type="道具", role_personality="不应追加"),
            self._card(name="  ", card_type="角色", is_protagonist=True, protagonist_gender="male"),
        ]

        result = storyboard_prompt_context.build_subject_text_for_ai(cards)

        self.assertEqual(
            result,
            "男主1：陆云-冷静，女主1：苏晚，其他角色、场景或道具：管家-狡猾、茶馆、铜镜",
        )

    def test_build_subject_text_returns_none_label_for_empty_subjects(self):
        self.assertEqual(storyboard_prompt_context.build_subject_text_for_ai([]), "无")
        self.assertEqual(
            storyboard_prompt_context.build_subject_text_for_ai([self._card(name="  ")]),
            "无",
        )

    def test_build_storyboard2_subject_text_uses_role_personality_and_newlines(self):
        cards = [
            self._card(name="陆云", role_personality="冷静"),
            self._card(name="茶馆", card_type="场景", role_personality="不应追加"),
            self._card(name="  ", card_type="角色"),
        ]

        result = storyboard_prompt_context.build_storyboard2_subject_text(cards)

        self.assertEqual(result, "陆云-冷静\n茶馆")

    def test_append_sora_reference_prompt_trims_and_inserts_instruction(self):
        result = storyboard_prompt_context.append_sora_reference_prompt(" base ", " ref ")

        self.assertEqual(
            result,
            "base\n\n"
            f"{storyboard_prompt_context.SORA_REFERENCE_PROMPT_INSTRUCTION}ref",
        )
        self.assertEqual(
            storyboard_prompt_context.append_sora_reference_prompt("", " ref "),
            f"{storyboard_prompt_context.SORA_REFERENCE_PROMPT_INSTRUCTION}ref",
        )
        self.assertEqual(
            storyboard_prompt_context.append_sora_reference_prompt(" base ", "  \n "),
            "base",
        )

    def test_resolve_sora_reference_prompt_filters_invalid_or_cross_episode_ids(self):
        script = models.Script(user_id=1, name="Script")
        episode = models.Episode(script=script, name="E01")
        other_episode = models.Episode(script=script, name="E02")
        reference_shot = models.StoryboardShot(
            episode=episode,
            shot_number=1,
            stable_id="ref",
            sora_prompt="  参考站位  ",
            selected_card_ids="[]",
        )
        cross_episode_shot = models.StoryboardShot(
            episode=other_episode,
            shot_number=1,
            stable_id="other",
            sora_prompt="不应使用",
            selected_card_ids="[]",
        )
        blank_prompt_shot = models.StoryboardShot(
            episode=episode,
            shot_number=2,
            stable_id="blank",
            sora_prompt="  ",
            selected_card_ids="[]",
        )
        self.db.add_all([script, episode, other_episode, reference_shot, cross_episode_shot, blank_prompt_shot])
        self.db.commit()

        self.assertEqual(
            storyboard_prompt_context.resolve_sora_reference_prompt(self.db, episode.id, reference_shot.id),
            "参考站位",
        )
        self.assertEqual(storyboard_prompt_context.resolve_sora_reference_prompt(self.db, episode.id, None), "")
        self.assertEqual(storyboard_prompt_context.resolve_sora_reference_prompt(self.db, episode.id, "bad"), "")
        self.assertEqual(
            storyboard_prompt_context.resolve_sora_reference_prompt(self.db, episode.id, cross_episode_shot.id),
            "",
        )
        self.assertEqual(
            storyboard_prompt_context.resolve_sora_reference_prompt(self.db, episode.id, blank_prompt_shot.id),
            "",
        )

    def test_resolve_large_shot_template_prefers_explicit_default_then_oldest(self):
        now = datetime.utcnow()
        oldest = models.LargeShotTemplate(
            name="oldest",
            content="oldest content",
            is_default=False,
            created_at=now - timedelta(days=2),
        )
        default_a = models.LargeShotTemplate(
            name="default-a",
            content="default a",
            is_default=True,
            created_at=now,
        )
        default_b = models.LargeShotTemplate(
            name="default-b",
            content="default b",
            is_default=True,
            created_at=now - timedelta(days=1),
        )
        self.db.add_all([oldest, default_a, default_b])
        self.db.commit()

        self.assertEqual(
            storyboard_prompt_context.resolve_large_shot_template(self.db, oldest.id).id,
            oldest.id,
        )
        self.assertEqual(
            storyboard_prompt_context.resolve_large_shot_template(self.db).id,
            default_a.id,
        )

        default_a.is_default = False
        default_b.is_default = False
        self.db.commit()

        self.assertEqual(
            storyboard_prompt_context.resolve_large_shot_template(self.db).id,
            oldest.id,
        )

        self.db.query(models.LargeShotTemplate).delete()
        self.db.commit()

        self.assertIsNone(storyboard_prompt_context.resolve_large_shot_template(self.db))

    def test_debug_resolve_subject_names_preserves_selection_order_and_library_scope(self):
        cards = [
            self._card(id=1, library_id=10, name="甲"),
            self._card(id=2, library_id=10, name="乙"),
            self._card(id=3, library_id=20, name="丙"),
            self._card(id=4, library_id=10, name=""),
        ]
        self.db.add_all(cards)
        self.db.commit()

        self.assertEqual(
            storyboard_prompt_context.debug_resolve_subject_names(self.db, [2, 99, 1, 4, 3], library_id=10),
            ["乙", "甲"],
        )
        self.assertEqual(
            storyboard_prompt_context.debug_resolve_subject_names(self.db, [3, 1], library_id=None),
            ["丙", "甲"],
        )


if __name__ == "__main__":
    unittest.main()
