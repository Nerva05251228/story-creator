import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


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
from api.services import voiceover_generation  # noqa: E402


class VoiceoverGenerationServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()

        self.user = models.User(username="owner", token="owner-token", password_hash="hash", password_plain="123456")
        self.db.add(self.user)
        self.db.flush()

        self.script = models.Script(user_id=self.user.id, name="script")
        self.db.add(self.script)
        self.db.flush()

        self.episode = models.Episode(script_id=self.script.id, name="episode")
        self.db.add(self.episode)
        self.db.flush()

        self.script.voiceover_shared_data = json.dumps(
            {
                "initialized": True,
                "voice_references": [
                    {"id": "voice-1", "name": "Voice 1", "url": "", "local_path": ""},
                ],
                "vector_presets": [],
                "emotion_audio_presets": [
                    {"id": "emotion-1", "name": "Emotion 1", "url": "", "local_path": ""},
                ],
                "setting_templates": [],
            },
            ensure_ascii=False,
        )
        self.episode.voiceover_data = json.dumps(
            {
                "shots": [
                    {
                        "shot_number": "1",
                        "narration": {
                            "line_id": "line-1",
                            "text": "Narration line",
                            "emotion": "soft",
                            "tts": {
                                "voice_reference_id": "voice-1",
                                "emotion_control_method": voiceover_generation.VOICEOVER_TTS_METHOD_SAME,
                            },
                        },
                        "dialogue": [
                            {
                                "line_id": "line-2",
                                "text": "Dialogue line",
                                "emotion": "angry",
                                "tts": {
                                    "voice_reference_id": "voice-1",
                                    "emotion_control_method": voiceover_generation.VOICEOVER_TTS_METHOD_AUDIO,
                                    "emotion_audio_preset_id": "emotion-1",
                                    "vector_preset_id": "vector-1",
                                },
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_enqueue_voiceover_line_generate_creates_pending_task_and_updates_line_tts(self):
        with mock.patch.object(
            voiceover_generation,
            "sync_voiceover_tts_task_to_dashboard",
        ) as sync_dashboard:
            result = asyncio.run(
                voiceover_generation.enqueue_voiceover_line_generate(
                    self.episode.id,
                    "line-1",
                    {"text": "Updated narration"},
                    self.user,
                    self.db,
                )
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["line_id"], "line-1")
        self.assertEqual(result["status"], "pending")
        self.assertGreaterEqual(result["queue_position"], 1)

        task = self.db.query(models.VoiceoverTtsTask).filter_by(line_id="line-1").one()
        payload = json.loads(task.request_json)
        self.assertEqual(payload["text"], "Updated narration")
        self.assertEqual(payload["voice_reference_id"], "voice-1")
        sync_dashboard.assert_called_once_with(task.id)

    def test_enqueue_voiceover_generate_all_enqueues_valid_lines_and_skips_invalid_or_busy_lines(self):
        payload = json.loads(self.episode.voiceover_data)
        payload["shots"][0]["dialogue"].append(
            {
                "line_id": "line-3",
                "text": "",
                "tts": {"voice_reference_id": "voice-1"},
            }
        )
        payload["shots"][0]["dialogue"][0]["tts"]["generate_status"] = "processing"
        self.episode.voiceover_data = json.dumps(payload, ensure_ascii=False)
        self.db.commit()

        with mock.patch.object(
            voiceover_generation,
            "sync_voiceover_tts_task_to_dashboard",
        ) as sync_dashboard:
            result = asyncio.run(
                voiceover_generation.enqueue_voiceover_generate_all(
                    self.episode.id,
                    self.user,
                    self.db,
                )
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["enqueued_count"], 1)
        self.assertEqual(result["enqueued_line_ids"], ["line-1"])
        self.assertEqual(result["skipped_count"], 2)
        self.assertEqual(result["queue"]["pending"], 1)
        sync_dashboard.assert_called_once()

    def test_get_voiceover_tts_status_returns_line_states_and_queue_counts(self):
        self.db.add(
            models.VoiceoverTtsTask(
                episode_id=self.episode.id,
                line_id="line-1",
                status="pending",
                request_json="{}",
                result_json="",
                error_message="",
            )
        )
        self.db.add(
            models.VoiceoverTtsTask(
                episode_id=self.episode.id,
                line_id="line-2",
                status="processing",
                request_json="{}",
                result_json="",
                error_message="",
            )
        )
        self.db.commit()

        result = voiceover_generation.get_voiceover_tts_status(
            self.episode.id,
            self.user,
            self.db,
        )

        self.assertTrue(result["success"])
        self.assertEqual(len(result["line_states"]), 2)
        self.assertEqual(result["queue"]["pending"], 1)
        self.assertEqual(result["queue"]["processing"], 1)


if __name__ == "__main__":
    unittest.main()
