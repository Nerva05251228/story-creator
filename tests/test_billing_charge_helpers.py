import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}")

from api.services import billing_charges


class BillingChargeHelperTests(unittest.TestCase):
    def test_image_charge_helpers_remain_noops(self):
        db = object()
        with patch.object(billing_charges.billing_service, "create_charge_entry") as create_charge:
            self.assertIsNone(
                billing_charges.record_card_image_charge(
                    db,
                    card=SimpleNamespace(id=11),
                    model_name="seedream",
                    provider="jimeng",
                    resolution="2k",
                    task_id="card-task",
                    quantity=4,
                    detail_payload={"source": "test"},
                )
            )
            self.assertIsNone(
                billing_charges.record_storyboard_image_charge(
                    db,
                    shot=SimpleNamespace(id=12),
                    model_name="seedream",
                    provider="jimeng",
                    resolution="2k",
                    task_id="shot-image-task",
                    detail_payload={"source": "test"},
                )
            )
            self.assertIsNone(
                billing_charges.record_detail_image_charge(
                    db,
                    detail_img=SimpleNamespace(id=13, sub_shot_index=2),
                    shot=SimpleNamespace(id=14),
                    model_name="seedream",
                    provider="jimeng",
                    resolution="2k",
                    task_id="detail-task",
                    detail_payload={"source": "test"},
                )
            )
            self.assertIsNone(
                billing_charges.record_storyboard2_image_charge(
                    db,
                    sub_shot=SimpleNamespace(id=15),
                    storyboard2_shot=SimpleNamespace(id=16),
                    task_id="storyboard2-image-task",
                    model_name="seedream",
                    resolution="2k",
                    quantity=4,
                    detail_payload={"source": "test"},
                )
            )

        create_charge.assert_not_called()

    def test_storyboard_video_charge_records_pending_video_entry(self):
        db = object()
        created_entry = object()
        shot = SimpleNamespace(id=21, provider="yijia-grok", duration=6)

        with patch.object(
            billing_charges.billing_service,
            "get_shot_episode_context",
            return_value={"user_id": 1, "script_id": 2, "episode_id": 3},
        ) as get_context, patch.object(
            billing_charges.billing_service,
            "create_charge_entry",
            return_value=created_entry,
        ) as create_charge:
            result = billing_charges.record_storyboard_video_charge(
                db,
                shot=shot,
                task_id="video-task",
                model_name="grok",
                stage="video_generate",
                detail_payload={"source": "batch_generate"},
            )

        self.assertIs(result, created_entry)
        get_context.assert_called_once_with(db, shot_id=21)
        self.assertEqual(create_charge.call_args.args, (db,))
        self.assertEqual(
            create_charge.call_args.kwargs,
            {
                "user_id": 1,
                "script_id": 2,
                "episode_id": 3,
                "category": "video",
                "stage": "video_generate",
                "provider": "yijia-grok",
                "model_name": "grok",
                "quantity": 6,
                "billing_key": "video:shot:21:task:video-task",
                "operation_key": "video:shot:21",
                "initial_status": "pending",
                "shot_id": 21,
                "attempt_index": 1,
                "external_task_id": "video-task",
                "detail_json": json.dumps({"source": "batch_generate"}, ensure_ascii=False),
            },
        )

    def test_storyboard2_video_charge_records_pending_video_entry(self):
        db = object()
        created_entry = object()
        sub_shot = SimpleNamespace(id=31)
        storyboard2_shot = SimpleNamespace(id=32)

        with patch.object(
            billing_charges.billing_service,
            "get_storyboard2_sub_shot_context",
            return_value={"user_id": 4, "script_id": 5, "episode_id": 6},
        ) as get_context, patch.object(
            billing_charges.billing_service,
            "create_charge_entry",
            return_value=created_entry,
        ) as create_charge:
            result = billing_charges.record_storyboard2_video_charge(
                db,
                sub_shot=sub_shot,
                storyboard2_shot=storyboard2_shot,
                task_id="storyboard2-video-task",
                model_name="",
                duration=0,
                detail_payload={"video_id_pending": True},
            )

        self.assertIs(result, created_entry)
        get_context.assert_called_once_with(db, sub_shot_id=31)
        self.assertEqual(create_charge.call_args.args, (db,))
        self.assertEqual(create_charge.call_args.kwargs["provider"], "yijia")
        self.assertEqual(create_charge.call_args.kwargs["model_name"], "grok")
        self.assertEqual(create_charge.call_args.kwargs["quantity"], 1)
        self.assertEqual(create_charge.call_args.kwargs["billing_key"], "video:storyboard2:31:task:storyboard2-video-task")
        self.assertEqual(create_charge.call_args.kwargs["operation_key"], "video:storyboard2:32:sub31")
        self.assertEqual(json.loads(create_charge.call_args.kwargs["detail_json"]), {"video_id_pending": True})

    def test_video_charge_skips_missing_context_and_value_errors(self):
        db = object()
        shot = SimpleNamespace(id=41, provider="moti", duration=10)

        with patch.object(
            billing_charges.billing_service,
            "get_shot_episode_context",
            return_value=None,
        ), patch.object(billing_charges.billing_service, "create_charge_entry") as create_charge:
            result = billing_charges.record_storyboard_video_charge(
                db,
                shot=shot,
                task_id="missing-context",
                model_name="Seedance 2.0 Fast",
            )

        self.assertIsNone(result)
        create_charge.assert_not_called()

        with patch.object(
            billing_charges.billing_service,
            "get_shot_episode_context",
            return_value={"user_id": 1, "script_id": 2, "episode_id": 3},
        ), patch.object(
            billing_charges.billing_service,
            "create_charge_entry",
            side_effect=ValueError("duplicate"),
        ):
            result = billing_charges.record_storyboard_video_charge(
                db,
                shot=shot,
                task_id="duplicate",
                model_name="Seedance 2.0 Fast",
            )

        self.assertIsNone(result)

    def test_resolve_storyboard_video_billing_model_normalizes_yijia_grok_provider(self):
        calls = []

        def resolve_model(provider, *, default_model):
            calls.append((provider, default_model))
            return "grok"

        model = billing_charges.resolve_storyboard_video_billing_model(
            SimpleNamespace(provider="yijia-grok", storyboard_video_model="sora-2"),
            resolve_model_by_provider=resolve_model,
            default_model="Seedance 2.0 Fast",
        )

        self.assertEqual(model, "grok")
        self.assertEqual(calls, [("yijia", "sora-2")])

    def test_safe_json_dumps_returns_empty_string_for_unserializable_payloads(self):
        self.assertEqual(billing_charges.safe_json_dumps({"ok": True}), '{"ok": true}')
        self.assertEqual(billing_charges.safe_json_dumps({"bad": object()}), "")


if __name__ == "__main__":
    unittest.main()
