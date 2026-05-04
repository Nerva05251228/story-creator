import os
import sys
import unittest
from pathlib import Path

from fastapi import HTTPException
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
from api.services import storyboard_video_generation_limits  # noqa: E402


class StoryboardVideoGenerationLimitTests(unittest.TestCase):
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

    def _shot(self, **overrides):
        shot = models.StoryboardShot(
            episode_id=overrides.pop("episode_id", 1),
            shot_number=overrides.pop("shot_number", 1),
            stable_id=overrides.pop("stable_id", "stable-a"),
            variant_index=overrides.pop("variant_index", 0),
            video_status=overrides.pop("video_status", "idle"),
            selected_card_ids="[]",
            **overrides,
        )
        self.db.add(shot)
        self.db.flush()
        return shot

    def _managed_task(self, shot, *, status="processing", shot_stable_id=None):
        session = models.ManagedSession(
            episode_id=shot.episode_id,
            status="running",
            total_shots=1,
            completed_shots=0,
            variant_count=1,
        )
        self.db.add(session)
        self.db.flush()
        task = models.ManagedTask(
            session_id=session.id,
            shot_id=shot.id,
            shot_stable_id=shot_stable_id if shot_stable_id is not None else (shot.stable_id or ""),
            status=status,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def test_counts_active_video_status_across_stable_id_family(self):
        original = self._shot(stable_id="family-a", variant_index=0, video_status="idle")
        self._shot(stable_id="family-a", variant_index=1, video_status="processing")
        self._shot(stable_id="family-b", variant_index=0, video_status="processing")
        self.db.commit()

        count = storyboard_video_generation_limits.count_active_video_generations_for_shot_family(
            original,
            self.db,
        )

        self.assertEqual(count, 1)

    def test_stable_id_family_includes_legacy_empty_stable_id_rows_for_same_shot_number(self):
        original = self._shot(stable_id="family-a", shot_number=3, video_status="idle")
        self._shot(stable_id="", shot_number=3, video_status="processing")
        self._shot(stable_id="", shot_number=4, video_status="processing")
        self.db.commit()

        count = storyboard_video_generation_limits.count_active_video_generations_for_shot_family(
            original,
            self.db,
        )

        self.assertEqual(count, 1)

    def test_managed_task_counts_when_reserved_shot_is_not_already_active(self):
        shot = self._shot(stable_id="family-a", video_status="idle")
        self._managed_task(shot, status="processing", shot_stable_id="family-a")
        self.db.commit()

        count = storyboard_video_generation_limits.count_active_video_generations_for_shot_family(
            shot,
            self.db,
        )

        self.assertEqual(count, 1)

    def test_duplicate_requested_family_entries_are_aggregated_before_limit_check(self):
        shot = self._shot(stable_id="family-a", video_status="idle")
        self.db.commit()

        with self.assertRaises(HTTPException) as raised:
            storyboard_video_generation_limits.ensure_storyboard_video_generation_slots_available(
                [shot, shot],
                self.db,
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("镜头1", raised.exception.detail)

    def test_builds_multi_shot_blocked_message(self):
        shot_a = self._shot(stable_id="family-a", shot_number=1, video_status="processing")
        shot_b = self._shot(stable_id="family-b", shot_number=2, video_status="processing")
        self.db.commit()

        with self.assertRaises(HTTPException) as raised:
            storyboard_video_generation_limits.ensure_storyboard_video_generation_slots_available(
                [shot_a, shot_b],
                self.db,
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("镜头1、镜头2", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
