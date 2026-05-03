import json
import os
import sys
import unittest
from pathlib import Path


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

from api.services import voiceover_data  # noqa: E402


class VoiceoverDataServiceTests(unittest.TestCase):
    def test_merge_line_preserves_existing_tts_when_incoming_has_no_tts(self):
        existing = {
            "line_id": "line-1",
            "text": "old",
            "tts": {
                "voice_reference_id": "voice-1",
                "generate_status": "completed",
            },
        }
        incoming = {"text": "new"}

        merged = voiceover_data.merge_voiceover_line_preserving_tts(existing, incoming)

        self.assertEqual(merged["text"], "new")
        self.assertEqual(
            merged["tts"],
            {
                "voice_reference_id": "voice-1",
                "generate_status": "completed",
            },
        )
        self.assertEqual(merged["line_id"], "line-1")

    def test_merge_line_combines_tts_when_both_sides_provide_tts(self):
        existing = {
            "line_id": "",
            "tts": {
                "voice_reference_id": "voice-1",
                "generate_status": "completed",
            },
        }
        incoming = {
            "tts": {
                "generate_status": "pending",
                "latest_task_id": "task-2",
            },
        }

        merged = voiceover_data.merge_voiceover_line_preserving_tts(
            existing,
            incoming,
            fallback_line_id="fallback-line",
        )

        self.assertEqual(
            merged["tts"],
            {
                "voice_reference_id": "voice-1",
                "generate_status": "pending",
                "latest_task_id": "task-2",
            },
        )
        self.assertEqual(merged["line_id"], "fallback-line")

    def test_merge_shots_preserves_extensions_and_matches_dialogue_by_line_id(self):
        existing_payload = {
            "custom_root": "keep",
            "shots": [
                {
                    "shot_number": "1",
                    "custom_shot": "keep-shot",
                    "voice_type": "old-type",
                    "narration": {
                        "line_id": "narration-1",
                        "text": "old narration",
                        "tts": {"voice_reference_id": "voice-1"},
                    },
                    "dialogue": [
                        {
                            "line_id": "dialogue-2",
                            "text": "old second",
                            "tts": {"voice_reference_id": "voice-2"},
                        },
                    ],
                }
            ],
        }
        incoming_shots = [
            {
                "shot_number": "1",
                "voice_type": "new-type",
                "narration": {"text": "new narration"},
                "dialogue": [
                    {"line_id": "dialogue-2", "text": "new second"},
                    {"text": "new fallback"},
                ],
            }
        ]

        merged = voiceover_data.merge_voiceover_shots_preserving_extensions(
            json.dumps(existing_payload),
            incoming_shots,
        )

        self.assertEqual(merged["custom_root"], "keep")
        shot = merged["shots"][0]
        self.assertEqual(shot["custom_shot"], "keep-shot")
        self.assertEqual(shot["voice_type"], "new-type")
        self.assertEqual(shot["narration"]["text"], "new narration")
        self.assertEqual(shot["narration"]["line_id"], "narration-1")
        self.assertEqual(shot["narration"]["tts"], {"voice_reference_id": "voice-1"})
        self.assertEqual(shot["dialogue"][0]["text"], "new second")
        self.assertEqual(shot["dialogue"][0]["tts"], {"voice_reference_id": "voice-2"})
        self.assertEqual(shot["dialogue"][1]["line_id"], "shot_1_dialogue_2")

    def test_merge_shots_tolerates_invalid_existing_payload_and_non_list_incoming(self):
        merged = voiceover_data.merge_voiceover_shots_preserving_extensions(
            "not json",
            {"not": "a list"},
        )

        self.assertEqual(merged, {"shots": []})


if __name__ == "__main__":
    unittest.main()
