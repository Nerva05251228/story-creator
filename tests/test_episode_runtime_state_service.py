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
from api.services import episode_runtime_state  # noqa: E402


class EpisodeRuntimeStateServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        db = self.Session()
        try:
            user = models.User(username="owner", token="token", password_hash="hash", password_plain="123456")
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="script")
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="episode",
                batch_generating_prompts=False,
                simple_storyboard_generating=True,
            )
            db.add(episode)
            db.flush()

            completed_shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                sora_prompt_status="generating",
                sora_prompt="ready prompt",
                video_status="idle",
            )
            failed_shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=2,
                sora_prompt_status="generating",
                sora_prompt="",
                storyboard_video_prompt="",
                video_status="idle",
            )
            active_shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=3,
                sora_prompt_status="generating",
                sora_prompt="",
                storyboard_video_prompt="",
                video_status="idle",
            )
            db.add_all([completed_shot, failed_shot, active_shot])
            db.flush()

            db.add(
                models.TextRelayTask(
                    task_type="sora_prompt",
                    owner_type="shot",
                    owner_id=active_shot.id,
                    status="running",
                    external_task_id="task-running",
                    request_payload="{}",
                    task_payload="{}",
                )
            )
            db.commit()

            self.episode_id = int(episode.id)
            self.completed_shot_id = int(completed_shot.id)
            self.failed_shot_id = int(failed_shot.id)
            self.active_shot_id = int(active_shot.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_refresh_episode_batch_sora_prompt_state_sets_episode_flag_from_generating_rows(self):
        db = self.Session()
        try:
            episode_runtime_state.refresh_episode_batch_sora_prompt_state(self.episode_id, db)
            episode = db.query(models.Episode).filter_by(id=self.episode_id).one()
            self.assertTrue(bool(episode.batch_generating_prompts))

            db.query(models.StoryboardShot).filter_by(episode_id=self.episode_id).update(
                {"sora_prompt_status": "completed"}
            )
            db.commit()

            episode_runtime_state.refresh_episode_batch_sora_prompt_state(self.episode_id, db)
            self.assertFalse(bool(episode.batch_generating_prompts))
        finally:
            db.close()

    def test_repair_stale_storyboard_prompt_generation_repairs_only_inactive_generating_shots(self):
        db = self.Session()
        try:
            changed = episode_runtime_state.repair_stale_storyboard_prompt_generation(self.episode_id, db)
            self.assertTrue(changed)

            shots = db.query(models.StoryboardShot).filter_by(episode_id=self.episode_id).order_by(
                models.StoryboardShot.shot_number.asc()
            ).all()
            self.assertEqual(
                [shot.sora_prompt_status for shot in shots],
                ["completed", "failed", "generating"],
            )
        finally:
            db.close()

    def test_reconcile_episode_runtime_flags_clears_stale_batch_and_simple_flags(self):
        db = self.Session()
        try:
            episode = db.query(models.Episode).filter_by(id=self.episode_id).one()
            db.query(models.TextRelayTask).filter(
                models.TextRelayTask.owner_id == self.active_shot_id
            ).update({"status": "failed"})
            db.commit()

            with patch.object(
                episode_runtime_state,
                "_get_simple_storyboard_batch_summary",
                return_value={
                    "submitting_batches": 0,
                    "total_batches": 1,
                    "completed_batches": 1,
                    "failed_batches": 0,
                },
            ):
                changed = episode_runtime_state.reconcile_episode_runtime_flags(episode, db)

            self.assertTrue(changed)
            self.assertFalse(bool(episode.batch_generating_prompts))
            self.assertFalse(bool(episode.simple_storyboard_generating))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
