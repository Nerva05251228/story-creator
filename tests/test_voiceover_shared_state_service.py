import json
import os
import sys
import unittest
from pathlib import Path

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
from api.services import voiceover_data  # noqa: E402

try:  # noqa: E402
    from api.services import voiceover_shared_state  # type: ignore
except ImportError:  # pragma: no cover - exercised in the red TDD step
    voiceover_shared_state = None


class VoiceoverSharedStateServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()

        self.user = models.User(
            username="owner",
            token="owner-token",
            password_hash="hash",
            password_plain="123456",
        )
        self.db.add(self.user)
        self.db.flush()

        self.script = models.Script(user_id=self.user.id, name="script")
        self.db.add(self.script)
        self.db.flush()

        self.episode = models.Episode(script_id=self.script.id, name="episode")
        self.db.add(self.episode)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        models.Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _require_service(self):
        self.assertIsNotNone(
            voiceover_shared_state,
            "api.services.voiceover_shared_state is missing",
        )
        return voiceover_shared_state

    def test_update_voiceover_data_merges_existing_payload_and_normalizes_new_lines(self):
        service = self._require_service()
        self.script.voiceover_shared_data = json.dumps(
            {
                "initialized": True,
                "voice_references": [{"id": "voice-default", "name": "Default"}],
            },
            ensure_ascii=False,
        )
        self.episode.voiceover_data = json.dumps(
            {
                "custom_root": "keep",
                "shots": [
                    {
                        "shot_number": "1",
                        "custom_shot": "keep-shot",
                        "voice_type": "narration",
                        "narration": {
                            "line_id": "shot_1_narration",
                            "text": "Old narration",
                            "tts": {
                                "voice_reference_id": "voice-old",
                                "generate_status": "completed",
                            },
                        },
                    }
                ],
            },
            ensure_ascii=False,
        )
        self.db.commit()

        result = service.update_voiceover_data(
            self.episode.id,
            {
                "shots": [
                    {
                        "shot_number": "1",
                        "voice_type": "dialogue",
                        "narration": {"text": "New narration"},
                        "dialogue": [{"text": "New dialogue"}],
                    }
                ]
            },
            self.user,
            self.db,
        )

        self.assertTrue(result["success"])
        shot = result["shots"][0]
        self.assertEqual(shot["custom_shot"], "keep-shot")
        self.assertEqual(shot["voice_type"], "dialogue")
        self.assertEqual(shot["narration"]["line_id"], "shot_1_narration")
        self.assertEqual(shot["narration"]["tts"]["voice_reference_id"], "voice-old")
        self.assertEqual(shot["dialogue"][0]["line_id"], "shot_1_dialogue_1")
        self.assertEqual(shot["dialogue"][0]["tts"]["voice_reference_id"], "voice-default")

        saved = json.loads(self.episode.voiceover_data)
        self.assertEqual(saved["custom_root"], "keep")
        self.assertEqual(saved["shots"][0]["custom_shot"], "keep-shot")

    def test_get_voiceover_shared_data_returns_normalized_shared_payload(self):
        service = self._require_service()
        self.script.voiceover_shared_data = json.dumps(
            {
                "initialized": True,
                "voice_references": [
                    {
                        "id": " voice-1 ",
                        "name": " Voice 1 ",
                    }
                ],
                "setting_templates": [
                    {
                        "id": " template-1 ",
                        "name": " Template 1 ",
                        "settings": {
                            "emotion_control_method": "unsupported",
                            "voice_reference_id": "",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        )
        self.db.commit()

        result = service.get_voiceover_shared_data(
            self.episode.id,
            self.user,
            self.db,
        )

        self.assertTrue(result["success"])
        shared = result["shared"]
        self.assertEqual(shared["voice_references"][0]["id"], "voice-1")
        self.assertEqual(shared["voice_references"][0]["name"], "Voice 1")
        self.assertEqual(
            shared["setting_templates"][0]["settings"]["emotion_control_method"],
            voiceover_data.VOICEOVER_TTS_METHOD_SAME,
        )
        self.assertEqual(
            shared["setting_templates"][0]["settings"]["voice_reference_id"],
            "voice-1",
        )

    def test_get_detailed_storyboard_falls_back_to_storyboard_data_and_persists_normalized_shots(self):
        service = self._require_service()
        self.script.voiceover_shared_data = json.dumps(
            {
                "initialized": True,
                "voice_references": [{"id": "voice-default", "name": "Default"}],
            },
            ensure_ascii=False,
        )
        self.episode.storyboard_generating = True
        self.episode.storyboard_error = "storyboard error"
        self.episode.storyboard_data = json.dumps(
            {
                "subjects": [
                    {
                        "name": "Hero",
                        "type": "角色",
                        "ai_prompt": "Stored hero prompt",
                        "role_personality": "Calm",
                        "alias": "Lead",
                    }
                ],
                "shots": [
                    {
                        "shot_number": "1",
                        "voice_type": "narration",
                        "narration": {"text": "Narration line"},
                        "dialogue": [{"text": "Dialogue line"}],
                        "extra": "ignored",
                    }
                ],
            },
            ensure_ascii=False,
        )
        library = models.StoryLibrary(
            user_id=self.user.id,
            episode_id=self.episode.id,
            name="library",
        )
        self.db.add(library)
        self.db.flush()
        card = models.SubjectCard(
            library_id=library.id,
            name="Hero",
            card_type="角色",
            alias="",
            ai_prompt="",
            role_personality="",
        )
        self.db.add(card)
        self.db.commit()

        result = service.get_detailed_storyboard(
            self.episode.id,
            self.user,
            self.db,
        )

        self.assertTrue(result["generating"])
        self.assertEqual(result["error"], "storyboard error")
        self.assertEqual(
            result["subjects"],
            [
                {
                    "id": card.id,
                    "name": "Hero",
                    "card_type": "角色",
                    "type": "角色",
                    "ai_prompt": "Stored hero prompt",
                    "role_personality": "Calm",
                    "alias": "Lead",
                }
            ],
        )
        self.assertEqual(result["tts_shared"]["voice_references"][0]["id"], "voice-default")
        self.assertEqual(result["shots"][0]["shot_number"], "1")
        self.assertEqual(result["shots"][0]["narration"]["line_id"], "shot_1_narration")
        self.assertEqual(
            result["shots"][0]["narration"]["tts"]["voice_reference_id"],
            "voice-default",
        )
        self.assertEqual(result["shots"][0]["dialogue"][0]["line_id"], "shot_1_dialogue_1")
        self.assertNotIn("extra", result["shots"][0])

        saved = voiceover_data.parse_episode_voiceover_payload(self.episode)
        self.assertEqual(saved["shots"], result["shots"])


if __name__ == "__main__":
    unittest.main()
