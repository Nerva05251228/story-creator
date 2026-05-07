import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}")

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402
import models  # noqa: E402


class StoryboardVideoMotiAccountsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_accounts_endpoint_refreshes_when_cache_is_empty_after_restart(self):
        refreshed_payload = {
            "provider": "moti",
            "total": 1,
            "records": [
                {"account_id": "罗西剧场", "robot_id": "2429291451132548"},
            ],
            "loaded": True,
        }

        with self.Session() as db, patch.object(
            main,
            "get_cached_video_provider_accounts",
            return_value={"provider": "moti", "total": 0, "records": [], "loaded": False},
        ) as cached_mock, patch.object(
            main,
            "refresh_video_provider_accounts",
            return_value=refreshed_payload,
            create=True,
        ) as refresh_mock:
            payload = asyncio.run(
                main.get_video_provider_accounts("moti", user=object(), db=db)
            )

        cached_mock.assert_called_once_with("moti")
        refresh_mock.assert_called_once_with("moti")
        self.assertEqual(payload, refreshed_payload)

    def test_accounts_endpoint_persists_last_successful_snapshot(self):
        refreshed_payload = {
            "provider": "moti",
            "total": 1,
            "records": [
                {"account_id": "罗西剧场", "robot_id": "2429291451132548"},
            ],
            "loaded": True,
        }

        with self.Session() as db, patch.object(
            main,
            "get_cached_video_provider_accounts",
            return_value={"provider": "moti", "total": 0, "records": [], "loaded": False},
        ), patch.object(
            main,
            "refresh_video_provider_accounts",
            return_value=refreshed_payload,
            create=True,
        ):
            payload = asyncio.run(
                main.get_video_provider_accounts("moti", user=object(), db=db)
            )
            snapshot = main._load_video_provider_accounts_snapshot(db, "moti")

        self.assertEqual(payload["records"][0]["account_id"], "罗西剧场")
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["records"][0]["robot_id"], "2429291451132548")

    def test_accounts_endpoint_returns_last_successful_snapshot_when_refresh_fails(self):
        stale_payload = {
            "provider": "moti",
            "total": 1,
            "records": [
                {"account_id": "罗西剧场", "robot_id": "2429291451132548"},
            ],
            "loaded": True,
        }
        failed_payload = {
            "provider": "moti",
            "total": 0,
            "records": [],
            "loaded": True,
            "error": "HTTP 500",
        }

        with self.Session() as db:
            main._persist_video_provider_accounts_snapshot(db, "moti", stale_payload)

            with patch.object(
                main,
                "get_cached_video_provider_accounts",
                return_value={"provider": "moti", "total": 0, "records": [], "loaded": False},
            ), patch.object(
                main,
                "refresh_video_provider_accounts",
                return_value=failed_payload,
                create=True,
            ):
                payload = asyncio.run(
                    main.get_video_provider_accounts("moti", user=object(), db=db)
                )

        self.assertEqual(payload["records"][0]["account_id"], "罗西剧场")
        self.assertTrue(payload["stale"])
        self.assertEqual(payload["error"], "HTTP 500")

    def test_moti_payload_maps_account_id_to_robot_id_when_cache_has_match(self):
        with patch.object(
            main,
            "get_cached_video_provider_accounts",
            return_value={
                "records": [
                    {"account_id": "罗西剧场", "robot_id": "2429291451132548"},
                ]
            },
        ):
            payload = main._build_unified_storyboard_video_task_payload(
                shot=None,
                db=None,
                username="alex",
                model_name="Seedance 2.0",
                provider="moti",
                full_prompt="prompt",
                aspect_ratio="1:1",
                duration=5,
                first_frame_image_url="https://example.com/frame.png",
                appoint_account="罗西剧场",
            )

        self.assertEqual(payload["provider"], "moti")
        self.assertEqual(payload["extra"], {"appoint_accounts": ["2429291451132548"]})

    def test_moti_payload_includes_selected_appoint_account(self):
        payload = main._build_unified_storyboard_video_task_payload(
            shot=None,
            db=None,
            username="alex",
            model_name="Seedance 2.0",
            provider="moti",
            full_prompt="手握枪柄",
            aspect_ratio="1:1",
            duration=5,
            first_frame_image_url="https://example.com/frame.png",
            appoint_account="罗西剧场",
        )

        self.assertEqual(payload["provider"], "moti")
        self.assertEqual(payload["extra"], {"appoint_accounts": ["罗西剧场"]})

    def test_moti_payload_omits_extra_when_account_is_blank(self):
        payload = main._build_unified_storyboard_video_task_payload(
            shot=None,
            db=None,
            username="alex",
            model_name="Seedance 2.0",
            provider="moti",
            full_prompt="手握枪柄",
            aspect_ratio="1:1",
            duration=5,
            first_frame_image_url="https://example.com/frame.png",
            appoint_account="  ",
        )

        self.assertNotIn("extra", payload)

    def test_effective_video_settings_keep_episode_default_appoint_account(self):
        episode = models.Episode(
            storyboard_video_model="Seedance 2.0",
            storyboard_video_aspect_ratio="1:1",
            storyboard_video_duration=5,
            storyboard_video_appoint_account="account-a",
        )
        shot = models.StoryboardShot(
            storyboard_video_model="",
            storyboard_video_model_override_enabled=False,
            duration_override_enabled=False,
        )

        settings = main._get_effective_storyboard_video_settings_for_shot(shot, episode)

        self.assertEqual(settings["appoint_account"], "account-a")


if __name__ == "__main__":
    unittest.main()
