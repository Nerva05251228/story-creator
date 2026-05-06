import asyncio
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException, UploadFile
from fastapi.responses import RedirectResponse
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
from api.services import voiceover_data, voiceover_resources  # noqa: E402


class VoiceoverResourcesServiceTests(unittest.TestCase):
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

    def _seed_user_script_episode(self):
        user = models.User(username="alice", token="token-alice")
        self.db.add(user)
        self.db.flush()

        script = models.Script(user_id=user.id, name="Script 1")
        self.db.add(script)
        self.db.flush()

        episode = models.Episode(script_id=script.id, name="E01")
        self.db.add(episode)
        self.db.commit()

        return user, script, episode

    def test_ensure_voiceover_permission_enforces_missing_and_ownership_checks(self):
        owner, script, episode = self._seed_user_script_episode()
        outsider = models.User(username="bob", token="token-bob")
        self.db.add(outsider)
        self.db.commit()

        resolved_episode, resolved_script = voiceover_resources.ensure_voiceover_permission(
            episode.id,
            owner,
            self.db,
        )

        self.assertEqual(resolved_episode.id, episode.id)
        self.assertEqual(resolved_script.id, script.id)

        with self.assertRaises(HTTPException) as missing_ctx:
            voiceover_resources.ensure_voiceover_permission(episode.id + 999, owner, self.db)
        self.assertEqual(missing_ctx.exception.status_code, 404)

        with self.assertRaises(HTTPException) as denied_ctx:
            voiceover_resources.ensure_voiceover_permission(episode.id, outsider, self.db)
        self.assertEqual(denied_ctx.exception.status_code, 403)

    def test_module_exports_only_requested_public_helpers(self):
        self.assertEqual(
            voiceover_resources.__all__,
            [
                "ensure_voiceover_permission",
                "replace_voice_reference_for_script_episodes",
                "clear_tts_field_for_script_episodes",
                "resolve_voiceover_audio_source",
                "create_voiceover_voice_reference",
                "rename_voiceover_voice_reference",
                "preview_voiceover_voice_reference",
                "delete_voiceover_voice_reference",
                "upsert_voiceover_vector_preset",
                "delete_voiceover_vector_preset",
                "create_voiceover_emotion_audio_preset",
                "delete_voiceover_emotion_audio_preset",
                "upsert_voiceover_setting_template",
                "delete_voiceover_setting_template",
            ],
        )

    def test_replace_and_clear_helpers_update_matching_tts_fields(self):
        user, script, episode = self._seed_user_script_episode()
        another = models.Episode(script_id=script.id, name="E02")
        self.db.add(another)
        self.db.flush()

        payload = {
            "shots": [
                {
                    "shot_number": "1",
                    "narration": {
                        "text": "Narration",
                        "tts": {
                            "voice_reference_id": "voice-old",
                            "vector_preset_id": "vector-old",
                        },
                    },
                    "dialogue": [
                        {
                            "text": "Dialogue",
                            "tts": {
                                "voice_reference_id": "voice-old",
                                "vector_preset_id": "vector-old",
                            },
                        }
                    ],
                }
            ]
        }
        episode.voiceover_data = json.dumps(payload, ensure_ascii=False)
        another.voiceover_data = json.dumps(payload, ensure_ascii=False)
        self.db.commit()

        replaced = voiceover_resources.replace_voice_reference_for_script_episodes(
            self.db,
            script.id,
            "voice-old",
            "voice-new",
        )
        cleared = voiceover_resources.clear_tts_field_for_script_episodes(
            self.db,
            script.id,
            "vector_preset_id",
            "vector-old",
        )
        self.db.commit()

        self.assertEqual(replaced, 4)
        self.assertEqual(cleared, 4)

        refreshed = self.db.query(models.Episode).filter(models.Episode.id == episode.id).one()
        parsed = voiceover_data.parse_episode_voiceover_payload(refreshed)
        shots, _ = voiceover_data.normalize_voiceover_shots_for_tts(parsed["shots"], "")
        line_states = voiceover_data.extract_voiceover_tts_line_states(shots)

        self.assertEqual(len(line_states), 2)
        self.assertEqual(
            [item["tts"]["voice_reference_id"] for item in line_states],
            ["voice-new", "voice-new"],
        )
        self.assertEqual(
            [item["tts"]["vector_preset_id"] for item in line_states],
            ["", ""],
        )

    def test_resolve_voiceover_audio_source_prefers_url_and_resolves_relative_local_paths(self):
        self.assertEqual(
            voiceover_resources.resolve_voiceover_audio_source({"url": " https://cdn.example/audio.mp3 "}),
            "https://cdn.example/audio.mp3",
        )

        expected = os.path.abspath(
            os.path.join(
                os.path.dirname(voiceover_resources.__file__),
                "..",
                "tmp",
                "audio.mp3",
            )
        )
        self.assertEqual(
            voiceover_resources.resolve_voiceover_audio_source({"local_path": "tmp/audio.mp3"}),
            expected,
        )
        self.assertEqual(voiceover_resources.resolve_voiceover_audio_source({}), "")

    def test_create_rename_and_preview_voice_reference(self):
        user, script, episode = self._seed_user_script_episode()
        upload = UploadFile(filename="voice.mp3", file=io.BytesIO(b"voice-data"))

        with mock.patch.object(
            voiceover_resources,
            "save_and_upload_to_cdn",
            return_value="https://cdn.example/voice.mp3",
        ):
            created = asyncio.run(
                voiceover_resources.create_voiceover_voice_reference(
                    episode.id,
                    name="Hero",
                    file=upload,
                    user=user,
                    db=self.db,
                )
            )

        self.assertTrue(created["success"])
        item = created["item"]
        self.assertEqual(item["name"], "Hero")
        self.assertEqual(item["file_name"], "voice.mp3")
        self.assertEqual(item["url"], "https://cdn.example/voice.mp3")

        renamed = asyncio.run(
            voiceover_resources.rename_voiceover_voice_reference(
                episode.id,
                item["id"],
                {"name": "Heroine"},
                user=user,
                db=self.db,
            )
        )

        self.assertEqual(renamed["item"]["name"], "Heroine")
        self.assertIn("updated_at", renamed["item"])

        preview = asyncio.run(
            voiceover_resources.preview_voiceover_voice_reference(
                episode.id,
                item["id"],
                user=user,
                db=self.db,
            )
        )

        self.assertIsInstance(preview, RedirectResponse)
        self.assertEqual(preview.status_code, 307)
        self.assertEqual(preview.headers["location"], "https://cdn.example/voice.mp3")

    def test_delete_voice_reference_rewrites_episode_lines_to_fallback_reference(self):
        user, script, episode = self._seed_user_script_episode()
        voiceover_data.save_script_voiceover_shared_data(
            script,
            {
                "initialized": True,
                "voice_references": [
                    {"id": "voice-old", "name": "Old"},
                    {"id": "voice-fallback", "name": "Fallback"},
                ],
            },
        )
        episode.voiceover_data = json.dumps(
            {
                "shots": [
                    {
                        "shot_number": "2",
                        "narration": {"text": "Narration", "tts": {"voice_reference_id": "voice-old"}},
                        "dialogue": [{"text": "Dialogue", "tts": {"voice_reference_id": "voice-old"}}],
                    }
                ]
            },
            ensure_ascii=False,
        )
        self.db.commit()

        deleted = asyncio.run(
            voiceover_resources.delete_voiceover_voice_reference(
                episode.id,
                "voice-old",
                user=user,
                db=self.db,
            )
        )

        self.assertTrue(deleted["success"])
        self.assertEqual(deleted["fallback_voice_reference_id"], "voice-fallback")
        self.assertEqual(deleted["updated_line_count"], 2)
        self.assertEqual(
            [item["id"] for item in deleted["shared"]["voice_references"]],
            ["voice-fallback"],
        )

        refreshed = self.db.query(models.Episode).filter(models.Episode.id == episode.id).one()
        parsed = voiceover_data.parse_episode_voiceover_payload(refreshed)
        line_states = voiceover_data.extract_voiceover_tts_line_states(parsed["shots"])
        self.assertEqual(
            [item["tts"]["voice_reference_id"] for item in line_states],
            ["voice-fallback", "voice-fallback"],
        )

    def test_upsert_and_delete_vector_preset_clear_episode_references(self):
        user, script, episode = self._seed_user_script_episode()

        created = asyncio.run(
            voiceover_resources.upsert_voiceover_vector_preset(
                episode.id,
                {
                    "name": "Warm",
                    "description": "desc",
                    "vector_config": {"joy": "0.4"},
                },
                user=user,
                db=self.db,
            )
        )
        preset_id = created["preset_id"]

        episode.voiceover_data = json.dumps(
            {
                "shots": [
                    {
                        "shot_number": "5",
                        "narration": {"text": "Narration", "tts": {"vector_preset_id": preset_id}},
                        "dialogue": [{"text": "Dialogue", "tts": {"vector_preset_id": preset_id}}],
                    }
                ]
            },
            ensure_ascii=False,
        )
        self.db.commit()

        deleted = asyncio.run(
            voiceover_resources.delete_voiceover_vector_preset(
                episode.id,
                preset_id,
                user=user,
                db=self.db,
            )
        )

        self.assertTrue(deleted["success"])
        self.assertEqual(deleted["updated_line_count"], 2)
        self.assertEqual(deleted["shared"]["vector_presets"], [])

        refreshed = self.db.query(models.Episode).filter(models.Episode.id == episode.id).one()
        parsed = voiceover_data.parse_episode_voiceover_payload(refreshed)
        line_states = voiceover_data.extract_voiceover_tts_line_states(parsed["shots"])
        self.assertEqual(
            [item["tts"]["vector_preset_id"] for item in line_states],
            ["", ""],
        )

    def test_create_and_delete_emotion_audio_preset_clear_episode_references(self):
        user, script, episode = self._seed_user_script_episode()
        upload = UploadFile(filename="emotion.mp3", file=io.BytesIO(b"emotion-data"))

        with mock.patch.object(
            voiceover_resources,
            "save_and_upload_to_cdn",
            return_value="https://cdn.example/emotion.mp3",
        ):
            created = asyncio.run(
                voiceover_resources.create_voiceover_emotion_audio_preset(
                    episode.id,
                    name="Angry",
                    description="sharp",
                    file=upload,
                    user=user,
                    db=self.db,
                )
            )

        preset_id = created["item"]["id"]
        episode.voiceover_data = json.dumps(
            {
                "shots": [
                    {
                        "shot_number": "7",
                        "narration": {
                            "text": "Narration",
                            "tts": {"emotion_audio_preset_id": preset_id},
                        },
                        "dialogue": [
                            {
                                "text": "Dialogue",
                                "tts": {"emotion_audio_preset_id": preset_id},
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )
        self.db.commit()

        deleted = asyncio.run(
            voiceover_resources.delete_voiceover_emotion_audio_preset(
                episode.id,
                preset_id,
                user=user,
                db=self.db,
            )
        )

        self.assertTrue(deleted["success"])
        self.assertEqual(deleted["updated_line_count"], 2)
        self.assertEqual(deleted["shared"]["emotion_audio_presets"], [])

        refreshed = self.db.query(models.Episode).filter(models.Episode.id == episode.id).one()
        parsed = voiceover_data.parse_episode_voiceover_payload(refreshed)
        line_states = voiceover_data.extract_voiceover_tts_line_states(parsed["shots"])
        self.assertEqual(
            [item["tts"]["emotion_audio_preset_id"] for item in line_states],
            ["", ""],
        )

    def test_upsert_and_delete_setting_template_preserve_normalized_settings(self):
        user, script, episode = self._seed_user_script_episode()
        voiceover_data.save_script_voiceover_shared_data(
            script,
            {
                "initialized": True,
                "voice_references": [{"id": "voice-default", "name": "Default"}],
            },
        )
        self.db.commit()

        created = asyncio.run(
            voiceover_resources.upsert_voiceover_setting_template(
                episode.id,
                {
                    "name": "Template A",
                    "settings": {
                        "emotion_control_method": "unsupported",
                        "voice_reference_id": "",
                        "vector_preset_id": " preset-1 ",
                    },
                },
                user=user,
                db=self.db,
            )
        )
        template_id = created["template_id"]
        template = created["shared"]["setting_templates"][0]

        self.assertEqual(template["name"], "Template A")
        self.assertEqual(
            template["settings"]["emotion_control_method"],
            voiceover_data.VOICEOVER_TTS_METHOD_SAME,
        )
        self.assertEqual(template["settings"]["voice_reference_id"], "voice-default")
        self.assertEqual(template["settings"]["vector_preset_id"], "preset-1")

        updated = asyncio.run(
            voiceover_resources.upsert_voiceover_setting_template(
                episode.id,
                {
                    "id": template_id,
                    "name": "Template A",
                    "settings": {
                        "emotion_control_method": voiceover_data.VOICEOVER_TTS_METHOD_AUDIO,
                        "voice_reference_id": "voice-default",
                    },
                },
                user=user,
                db=self.db,
            )
        )
        self.assertEqual(updated["template_id"], template_id)
        self.assertEqual(
            updated["shared"]["setting_templates"][0]["settings"]["emotion_control_method"],
            voiceover_data.VOICEOVER_TTS_METHOD_AUDIO,
        )

        deleted = asyncio.run(
            voiceover_resources.delete_voiceover_setting_template(
                episode.id,
                template_id,
                user=user,
                db=self.db,
            )
        )

        self.assertTrue(deleted["success"])
        self.assertEqual(deleted["shared"]["setting_templates"], [])


if __name__ == "__main__":
    unittest.main()
