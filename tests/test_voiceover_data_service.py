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

    def test_normalize_vector_config_clamps_values_and_uses_neutral_fallback(self):
        normalized = voiceover_data.normalize_voiceover_vector_config({
            "weight": "1.5",
            "joy": "-1",
            "anger": "0.25",
            "neutral": "0",
        })

        self.assertEqual(normalized["weight"], 1.0)
        self.assertEqual(normalized["joy"], 0.0)
        self.assertEqual(normalized["anger"], 0.25)
        self.assertEqual(normalized["neutral"], 0.0)

        all_zero = voiceover_data.normalize_voiceover_vector_config({"weight": "bad"})

        self.assertEqual(all_zero["weight"], 0.65)
        self.assertEqual(all_zero["neutral"], 1.0)

    def test_normalize_setting_template_payload_uses_default_reference_and_allowed_method(self):
        normalized = voiceover_data.normalize_voiceover_setting_template_payload(
            {
                "emotion_control_method": "unsupported",
                "voice_reference_id": "",
                "vector_preset_id": " preset-1 ",
                "emotion_audio_preset_id": " audio-1 ",
                "vector_config": {"joy": "0.4"},
            },
            default_voice_reference_id="voice-default",
        )

        self.assertEqual(normalized["emotion_control_method"], voiceover_data.VOICEOVER_TTS_METHOD_SAME)
        self.assertEqual(normalized["voice_reference_id"], "voice-default")
        self.assertEqual(normalized["vector_preset_id"], "preset-1")
        self.assertEqual(normalized["emotion_audio_preset_id"], "audio-1")
        self.assertEqual(normalized["vector_config"]["joy"], 0.4)

    def test_normalize_line_tts_defaults_invalid_status_and_preserves_generated_audio(self):
        normalized = voiceover_data.normalize_voiceover_line_tts(
            {
                "emotion_control_method": voiceover_data.VOICEOVER_TTS_METHOD_VECTOR,
                "voice_reference_id": " voice-1 ",
                "vector_preset_id": " preset-1 ",
                "emotion_audio_preset_id": " audio-1 ",
                "generate_status": "BROKEN",
                "generate_error": " failed ",
                "latest_task_id": " task-1 ",
                "generated_audios": [
                    {
                        "id": " audio-1 ",
                        "name": " Result ",
                        "url": " https://cdn.example.invalid/audio.mp3 ",
                        "task_id": " task-1 ",
                        "created_at": "2026-05-03T00:00:00",
                        "status": "FAILED",
                    },
                    {"url": ""},
                    "bad",
                ],
            },
            default_voice_reference_id="voice-default",
        )

        self.assertEqual(normalized["emotion_control_method"], voiceover_data.VOICEOVER_TTS_METHOD_VECTOR)
        self.assertEqual(normalized["voice_reference_id"], "voice-1")
        self.assertEqual(normalized["vector_preset_id"], "preset-1")
        self.assertEqual(normalized["emotion_audio_preset_id"], "audio-1")
        self.assertEqual(normalized["generate_status"], "idle")
        self.assertEqual(normalized["generate_error"], "failed")
        self.assertEqual(normalized["latest_task_id"], "task-1")
        self.assertEqual(
            normalized["generated_audios"],
            [
                {
                    "id": "audio-1",
                    "name": "Result",
                    "url": "https://cdn.example.invalid/audio.mp3",
                    "task_id": "task-1",
                    "created_at": "2026-05-03T00:00:00",
                    "status": "failed",
                }
            ],
        )

    def test_normalize_shots_for_tts_adds_line_ids_and_extracts_line_states(self):
        shots = [
            {
                "shot_number": "3",
                "narration": {"text": "narration"},
                "dialogue": [
                    {"text": "first"},
                    {
                        "line_id": "custom-dialogue",
                        "text": "second",
                        "tts": {"generate_status": "completed"},
                    },
                ],
            }
        ]

        normalized_shots, changed = voiceover_data.normalize_voiceover_shots_for_tts(
            shots,
            default_voice_reference_id="voice-default",
        )

        self.assertIs(normalized_shots, shots)
        self.assertTrue(changed)
        narration = shots[0]["narration"]
        first_dialogue = shots[0]["dialogue"][0]
        second_dialogue = shots[0]["dialogue"][1]
        self.assertEqual(narration["line_id"], "shot_3_narration")
        self.assertEqual(narration["tts"]["voice_reference_id"], "voice-default")
        self.assertEqual(first_dialogue["line_id"], "shot_3_dialogue_1")
        self.assertEqual(second_dialogue["line_id"], "custom-dialogue")
        self.assertEqual(second_dialogue["tts"]["generate_status"], "completed")

        states = voiceover_data.extract_voiceover_tts_line_states(shots)

        self.assertEqual(
            [item["line_id"] for item in states],
            ["shot_3_narration", "shot_3_dialogue_1", "custom-dialogue"],
        )
        self.assertIs(
            voiceover_data.find_voiceover_line_entry(shots, "custom-dialogue"),
            second_dialogue,
        )
        self.assertEqual(list(voiceover_data.iter_voiceover_lines(shots)), [narration, first_dialogue, second_dialogue])

    def test_parse_episode_voiceover_payload_and_first_reference_id(self):
        class EpisodeStub:
            voiceover_data = json.dumps({"shots": {"not": "a list"}, "keep": True})

        payload = voiceover_data.parse_episode_voiceover_payload(EpisodeStub())

        self.assertEqual(payload, {"shots": [], "keep": True})
        self.assertEqual(
            voiceover_data.voiceover_first_reference_id({
                "voice_references": [{"id": " voice-1 "}, {"id": "voice-2"}],
            }),
            "voice-1",
        )


if __name__ == "__main__":
    unittest.main()
