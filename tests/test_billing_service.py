import json
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import models
import billing_service


class BillingServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        db = self.Session()
        try:
            user = models.User(
                username="tester",
                token="token",
                password_hash="hash",
                password_plain="123456",
            )
            second_user = models.User(
                username="tester-2",
                token="token-2",
                password_hash="hash-2",
                password_plain="654321",
            )
            db.add(user)
            db.add(second_user)
            db.flush()

            script = models.Script(user_id=user.id, name="script")
            db.add(script)
            db.flush()

            second_script = models.Script(user_id=user.id, name="script-2")
            other_user_script = models.Script(user_id=second_user.id, name="other-user-script")
            db.add(second_script)
            db.add(other_user_script)
            db.flush()

            old_episode = models.Episode(script_id=script.id, name="old-episode", billing_version=0)
            new_episode = models.Episode(script_id=script.id, name="new-episode", billing_version=1)
            second_new_episode = models.Episode(script_id=script.id, name="second-new-episode", billing_version=1)
            another_script_episode = models.Episode(script_id=second_script.id, name="other-script-episode", billing_version=1)
            other_user_episode = models.Episode(script_id=other_user_script.id, name="other-user-episode", billing_version=1)
            db.add(old_episode)
            db.add(new_episode)
            db.add(second_new_episode)
            db.add(another_script_episode)
            db.add(other_user_episode)
            db.flush()

            db.add_all(
                [
                    models.BillingPriceRule(
                        rule_name="OpenRouter text",
                        category="text",
                        stage="",
                        provider="openrouter",
                        model_name="",
                        billing_mode="per_call",
                        unit_price_rmb=Decimal("0.18"),
                        is_active=True,
                        priority=100,
                    ),
                    models.BillingPriceRule(
                        rule_name="YYDS text",
                        category="text",
                        stage="",
                        provider="yyds",
                        model_name="",
                        billing_mode="per_call",
                        unit_price_rmb=Decimal("0.18"),
                        is_active=True,
                        priority=100,
                    ),
                    models.BillingPriceRule(
                        rule_name="banana2 image 1k",
                        category="image",
                        stage="",
                        provider="banana",
                        model_name="banana2",
                        resolution="1k",
                        billing_mode="per_image",
                        unit_price_rmb=Decimal("0.06"),
                        is_active=True,
                        priority=100,
                    ),
                    models.BillingPriceRule(
                        rule_name="banana2 image 2k",
                        category="image",
                        stage="",
                        provider="banana",
                        model_name="banana2",
                        resolution="2k",
                        billing_mode="per_image",
                        unit_price_rmb=Decimal("0.07"),
                        is_active=True,
                        priority=100,
                    ),
                    models.BillingPriceRule(
                        rule_name="banana2 image 4k",
                        category="image",
                        stage="",
                        provider="banana",
                        model_name="banana2",
                        resolution="4k",
                        billing_mode="per_image",
                        unit_price_rmb=Decimal("0.08"),
                        is_active=True,
                        priority=100,
                    ),
                    models.BillingPriceRule(
                        rule_name="banana-pro image 1k",
                        category="image",
                        stage="",
                        provider="banana",
                        model_name="banana-pro",
                        resolution="1k",
                        billing_mode="per_image",
                        unit_price_rmb=Decimal("0.12"),
                        is_active=True,
                        priority=100,
                    ),
                    models.BillingPriceRule(
                        rule_name="banana-pro image 2k",
                        category="image",
                        stage="",
                        provider="banana",
                        model_name="banana-pro",
                        resolution="2k",
                        billing_mode="per_image",
                        unit_price_rmb=Decimal("0.14"),
                        is_active=True,
                        priority=100,
                    ),
                    models.BillingPriceRule(
                        rule_name="banana-pro image 4k",
                        category="image",
                        stage="",
                        provider="banana",
                        model_name="banana-pro",
                        resolution="4k",
                        billing_mode="per_image",
                        unit_price_rmb=Decimal("0.20"),
                        is_active=True,
                        priority=100,
                    ),
                    models.BillingPriceRule(
                        rule_name="grok video",
                        category="video",
                        stage="",
                        provider="yijia-grok",
                        model_name="grok",
                        billing_mode="per_second",
                        unit_price_rmb=Decimal("0.049"),
                        is_active=True,
                        priority=100,
                    ),
                ]
            )
            db.commit()

            self.user_id = int(user.id)
            self.second_user_id = int(second_user.id)
            self.script_id = int(script.id)
            self.second_script_id = int(second_script.id)
            self.other_user_script_id = int(other_user_script.id)
            self.old_episode_id = int(old_episode.id)
            self.new_episode_id = int(new_episode.id)
            self.second_new_episode_id = int(second_new_episode.id)
            self.other_script_episode_id = int(another_script_episode.id)
            self.other_user_episode_id = int(other_user_episode.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_charge_is_skipped_for_legacy_episode(self):
        db = self.Session()
        try:
            entry = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.old_episode_id,
                category="text",
                stage="detailed_storyboard_stage1",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("1"),
                billing_key="legacy-text-1",
                operation_key="legacy-op",
            )

            self.assertIsNone(entry)
            self.assertEqual(db.query(models.BillingLedgerEntry).count(), 0)
        finally:
            db.close()

    def test_text_charge_uses_per_call_rule(self):
        db = self.Session()
        try:
            entry = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="text",
                stage="detailed_storyboard_stage1",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("1"),
                billing_key="text-1",
                operation_key="stage1-op",
            )

            self.assertIsNotNone(entry)
            self.assertEqual(entry.billing_mode, "per_call")
            self.assertEqual(entry.unit_price_rmb, Decimal("0.18"))
            self.assertEqual(entry.amount_rmb, Decimal("0.18"))
            self.assertEqual(entry.status, "finalized")
        finally:
            db.close()

    def test_pending_video_charge_can_be_reversed_once(self):
        db = self.Session()
        try:
            charge = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="video",
                stage="video_generate",
                provider="yijia-grok",
                model_name="grok",
                quantity=Decimal("6"),
                billing_key="video-1",
                operation_key="video-op",
                initial_status="pending",
            )

            refund = billing_service.reverse_charge_entry(
                db,
                billing_key="video-1",
                reason="provider_failed",
            )
            duplicate_refund = billing_service.reverse_charge_entry(
                db,
                billing_key="video-1",
                reason="provider_failed",
            )

            self.assertIsNotNone(refund)
            self.assertIsNone(duplicate_refund)

            db.refresh(charge)
            self.assertEqual(charge.amount_rmb, Decimal("0.294"))
            self.assertEqual(charge.status, "reversed")
            self.assertEqual(refund.entry_type, "refund")
            self.assertEqual(refund.amount_rmb, Decimal("-0.294"))
        finally:
            db.close()

    def test_image_charge_uses_resolution_specific_rule(self):
        db = self.Session()
        try:
            entry = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="image",
                stage="detail_images",
                provider="banana",
                model_name="banana2",
                resolution="2K",
                quantity=Decimal("2"),
                billing_key="image-resolution-1",
                operation_key="image-resolution-op-1",
            )

            self.assertIsNotNone(entry)
            self.assertEqual(entry.billing_mode, "per_image")
            self.assertEqual(entry.resolution, "2k")
            self.assertEqual(entry.unit_price_rmb, Decimal("0.07"))
            self.assertEqual(entry.amount_rmb, Decimal("0.14"))
        finally:
            db.close()

    def test_cost_based_image_charge_for_shot_uses_upstream_cost(self):
        db = self.Session()
        try:
            shot = models.StoryboardShot(
                episode_id=self.new_episode_id,
                shot_number=1,
            )
            db.add(shot)
            db.flush()

            entry = billing_service.record_image_task_cost_for_shot(
                db,
                shot_id=shot.id,
                stage="detail_images",
                provider="banana",
                model_name="banana-pro",
                resolution="2K",
                cost_rmb=Decimal("0.123456"),
                external_task_id="upstream-image-1",
                billing_key="image-cost-shot-1",
                operation_key="image-cost-shot-op-1",
                detail_payload={"source": "poller"},
            )

            self.assertIsNotNone(entry)
            self.assertEqual(entry.category, "image")
            self.assertEqual(entry.billing_mode, "per_call")
            self.assertEqual(entry.quantity, Decimal("1.00000"))
            self.assertEqual(entry.unit_price_rmb, Decimal("0.12346"))
            self.assertEqual(entry.amount_rmb, Decimal("0.12346"))
            self.assertEqual(entry.provider, "banana")
            self.assertEqual(entry.model_name, "banana-pro")
            self.assertEqual(entry.resolution, "2k")
            self.assertEqual(entry.external_task_id, "upstream-image-1")
            self.assertEqual(entry.shot_id, shot.id)
            self.assertEqual(json.loads(entry.detail_json), {"source": "poller"})
        finally:
            db.close()

    def test_cost_based_image_charge_skips_zero_cost_for_card(self):
        db = self.Session()
        try:
            library = models.StoryLibrary(
                user_id=self.user_id,
                episode_id=self.new_episode_id,
                name="library",
            )
            db.add(library)
            db.flush()
            card = models.SubjectCard(
                library_id=library.id,
                name="hero",
                card_type="character",
            )
            db.add(card)
            db.flush()

            entry = billing_service.record_image_task_cost_for_card(
                db,
                card_id=card.id,
                stage="card_images",
                provider="banana",
                model_name="banana2",
                resolution="1k",
                cost_rmb=Decimal("0"),
                external_task_id="upstream-image-zero",
                billing_key="image-cost-card-zero",
                operation_key="image-cost-card-zero-op",
            )

            self.assertIsNone(entry)
            self.assertEqual(db.query(models.BillingLedgerEntry).count(), 0)
        finally:
            db.close()

    def test_cost_based_image_charge_skips_legacy_episode(self):
        db = self.Session()
        try:
            shot = models.StoryboardShot(
                episode_id=self.old_episode_id,
                shot_number=2,
            )
            db.add(shot)
            db.flush()

            entry = billing_service.record_image_task_cost_for_shot(
                db,
                shot_id=shot.id,
                stage="detail_images",
                provider="banana",
                model_name="banana2",
                resolution="1k",
                cost_rmb=Decimal("0.12"),
                external_task_id="upstream-image-legacy",
                billing_key="image-cost-legacy",
                operation_key="image-cost-legacy-op",
            )

            self.assertIsNone(entry)
            self.assertEqual(db.query(models.BillingLedgerEntry).count(), 0)
        finally:
            db.close()

    def test_cost_based_image_charge_is_idempotent_for_storyboard2_sub_shot(self):
        db = self.Session()
        try:
            storyboard2_shot = models.Storyboard2Shot(
                episode_id=self.new_episode_id,
                shot_number=3,
            )
            db.add(storyboard2_shot)
            db.flush()
            sub_shot = models.Storyboard2SubShot(
                storyboard2_shot_id=storyboard2_shot.id,
                sub_shot_index=1,
            )
            db.add(sub_shot)
            db.flush()

            first = billing_service.record_image_task_cost_for_storyboard2_sub_shot(
                db,
                sub_shot_id=sub_shot.id,
                stage="storyboard2_images",
                provider="banana",
                model_name="banana2",
                resolution="4k",
                cost_rmb=Decimal("0.34"),
                external_task_id="upstream-image-subshot-1",
                billing_key="image-cost-subshot-idempotent",
                operation_key="image-cost-subshot-op",
            )
            second = billing_service.record_image_task_cost_for_storyboard2_sub_shot(
                db,
                sub_shot_id=sub_shot.id,
                stage="storyboard2_images",
                provider="banana",
                model_name="banana2",
                resolution="4k",
                cost_rmb=Decimal("0.99"),
                external_task_id="upstream-image-subshot-2",
                billing_key="image-cost-subshot-idempotent",
                operation_key="image-cost-subshot-op",
            )

            self.assertIsNotNone(first)
            self.assertEqual(first.id, second.id)
            self.assertEqual(db.query(models.BillingLedgerEntry).count(), 1)
            self.assertEqual(second.amount_rmb, Decimal("0.34000"))
            self.assertEqual(second.storyboard2_shot_id, storyboard2_shot.id)
            self.assertEqual(second.sub_shot_id, sub_shot.id)
        finally:
            db.close()

    def test_episode_summary_includes_charges_and_refunds(self):
        db = self.Session()
        try:
            billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="text",
                stage="detailed_storyboard_stage1",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("2"),
                billing_key="text-2",
                operation_key="stage1-op-2",
            )
            billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="image",
                stage="detail_images",
                provider="banana",
                model_name="banana2",
                resolution="4K",
                quantity=Decimal("1"),
                billing_key="image-1",
                operation_key="detail-op",
            )
            billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="video",
                stage="video_generate",
                provider="yijia-grok",
                model_name="grok",
                quantity=Decimal("6"),
                billing_key="video-2",
                operation_key="video-op-2",
                initial_status="pending",
            )
            billing_service.reverse_charge_entry(
                db,
                billing_key="video-2",
                reason="provider_failed",
            )

            summary_rows = billing_service.get_episode_billing_summary(
                db,
                user_id=self.user_id,
            )

            row = next(
                item for item in summary_rows
                if int(item["episode_id"]) == int(self.new_episode_id)
            )
            self.assertEqual(row["episode_id"], self.new_episode_id)
            self.assertEqual(row["text_amount_rmb"], "0.30970")
            self.assertEqual(row["image_amount_rmb"], "0.08000")
            self.assertEqual(row["video_amount_rmb"], "0.29400")
            self.assertEqual(row["refund_amount_rmb"], "-0.29400")
            self.assertEqual(row["net_amount_rmb"], "0.38970")
        finally:
            db.close()

    def test_create_charge_entry_matches_openrouter_text_rule(self):
        db = self.Session()
        try:
            entry = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="text",
                stage="simple_storyboard",
                provider="openrouter",
                model_name="google/gemini-3.1-pro-preview",
                quantity=Decimal("1"),
                billing_key="openrouter-text-1",
                operation_key="openrouter-text-op-1",
            )
            db.commit()

            self.assertEqual(entry.provider, "openrouter")
            self.assertEqual(str(entry.amount_rmb), "0.18000")
        finally:
            db.close()

        db = self.Session()
        try:
            billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="text",
                stage="simple_storyboard",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("1"),
                billing_key="script-list-1",
                operation_key="script-list-op-1",
            )
            billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.second_new_episode_id,
                category="image",
                stage="detail_images",
                provider="banana",
                model_name="banana2",
                resolution="2K",
                quantity=Decimal("2"),
                billing_key="script-list-2",
                operation_key="script-list-op-2",
            )
            billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.second_script_id,
                episode_id=self.other_script_episode_id,
                category="text",
                stage="simple_storyboard",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("1"),
                billing_key="script-list-3",
                operation_key="script-list-op-3",
            )

            rows = billing_service.get_billing_script_list(db, user_id=self.user_id)

            self.assertEqual(len(rows), 2)
            first_script_row = next(
                item for item in rows
                if int(item["script_id"]) == int(self.script_id)
            )
            second_script_row = next(
                item for item in rows
                if int(item["script_id"]) == int(self.second_script_id)
            )
            self.assertEqual(first_script_row["episode_count"], 2)
            self.assertEqual(first_script_row["request_count"], 2)
            self.assertEqual(first_script_row["text_amount_rmb"], "0.18000")
            self.assertEqual(first_script_row["image_amount_rmb"], "0.14000")
            self.assertEqual(first_script_row["net_amount_rmb"], "0.32000")
            self.assertEqual(second_script_row["episode_count"], 1)
        finally:
            db.close()

    def test_script_detail_includes_episode_rollup(self):
        db = self.Session()
        try:
            billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="text",
                stage="simple_storyboard",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("1"),
                billing_key="script-detail-1",
                operation_key="script-detail-op-1",
            )
            billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.second_new_episode_id,
                category="video",
                stage="video_generate",
                provider="yijia-grok",
                model_name="grok",
                quantity=Decimal("6"),
                billing_key="script-detail-2",
                operation_key="script-detail-op-2",
            )

            detail = billing_service.get_script_billing_detail(
                db,
                script_id=self.script_id,
                user_id=self.user_id,
            )

            self.assertIsNotNone(detail)
            self.assertEqual(detail["script_id"], self.script_id)
            self.assertEqual(detail["summary"]["request_count"], 2)
            self.assertEqual(detail["summary"]["net_amount_rmb"], "0.47400")
            self.assertEqual(len(detail["episodes"]), 2)
            self.assertEqual(
                sorted(item["episode_id"] for item in detail["episodes"]),
                sorted([self.new_episode_id, self.second_new_episode_id]),
            )
            self.assertEqual(
                sorted(item["stage"] for item in detail["stage_summary"]),
                ["simple_storyboard", "video_generate"],
            )
        finally:
            db.close()

    def test_admin_script_list_returns_multiple_users(self):
        db = self.Session()
        try:
            billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="text",
                stage="simple_storyboard",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("1"),
                billing_key="global-script-1",
                operation_key="global-script-op-1",
            )
            billing_service.create_charge_entry(
                db,
                user_id=self.second_user_id,
                script_id=self.other_user_script_id,
                episode_id=self.other_user_episode_id,
                category="video",
                stage="video_generate",
                provider="yijia-grok",
                model_name="grok",
                quantity=Decimal("6"),
                billing_key="global-script-2",
                operation_key="global-script-op-2",
            )

            rows = billing_service.get_billing_script_list(db)

            self.assertEqual(len(rows), 2)
            usernames = sorted(item["username"] for item in rows)
            self.assertEqual(usernames, ["tester", "tester-2"])
            self.assertTrue(any(int(item["script_id"]) == int(self.other_user_script_id) for item in rows))
        finally:
            db.close()

    def test_reimbursement_export_groups_monthly_net_amounts_by_script_and_user(self):
        db = self.Session()
        try:
            first_charge = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="text",
                stage="reimbursement-month-1",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("1"),
                billing_key="reimbursement-script-1",
                operation_key="reimbursement-script-op-1",
            )
            second_charge = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.second_new_episode_id,
                category="image",
                stage="reimbursement-month-2",
                provider="banana",
                model_name="banana2",
                resolution="4k",
                quantity=Decimal("1"),
                billing_key="reimbursement-script-2",
                operation_key="reimbursement-script-op-2",
            )
            other_user_charge = billing_service.create_charge_entry(
                db,
                user_id=self.second_user_id,
                script_id=self.other_user_script_id,
                episode_id=self.other_user_episode_id,
                category="text",
                stage="reimbursement-user-1",
                provider="openrouter",
                model_name="gpt-4.1",
                quantity=Decimal("1"),
                billing_key="reimbursement-user-1",
                operation_key="reimbursement-user-op-1",
            )
            billing_service.reverse_charge_entry(
                db,
                billing_key="reimbursement-script-2",
                reason="refund-month-2",
            )

            first_charge.created_at = datetime(2026, 1, 15, 8, 30, 0)
            second_charge.created_at = datetime(2026, 2, 3, 9, 0, 0)
            other_user_charge.created_at = datetime(2026, 2, 8, 10, 15, 0)
            refund = db.query(models.BillingLedgerEntry).filter(
                models.BillingLedgerEntry.billing_key == "reimbursement-script-2:refund"
            ).first()
            refund.created_at = datetime(2026, 2, 4, 11, 0, 0)
            db.commit()

            script_rows = billing_service.get_billing_reimbursement_rows(db, group_by="script")
            user_rows = billing_service.get_billing_reimbursement_rows(db, group_by="user")

            self.assertEqual(
                script_rows,
                [
                    {
                        "month": "2026-01",
                        "group_by": "script",
                        "script_id": self.script_id,
                        "script_name": "script",
                        "user_id": self.user_id,
                        "username": "tester",
                        "amount_rmb": "0.18000",
                    },
                    {
                        "month": "2026-02",
                        "group_by": "script",
                        "script_id": self.other_user_script_id,
                        "script_name": "other-user-script",
                        "user_id": self.second_user_id,
                        "username": "tester-2",
                        "amount_rmb": "0.18000",
                    },
                ],
            )
            self.assertEqual(
                user_rows,
                [
                    {
                        "month": "2026-01",
                        "group_by": "user",
                        "user_id": self.user_id,
                        "username": "tester",
                        "amount_rmb": "0.18000",
                    },
                    {
                        "month": "2026-02",
                        "group_by": "user",
                        "user_id": self.second_user_id,
                        "username": "tester-2",
                        "amount_rmb": "0.18000",
                    },
                ],
            )
        finally:
            db.close()

    def test_reimbursement_export_filters_selected_month(self):
        db = self.Session()
        try:
            march_charge = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="text",
                stage="month-filter-march",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("1"),
                billing_key="month-filter-march",
                operation_key="month-filter-march-op",
            )
            april_charge = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.second_new_episode_id,
                category="text",
                stage="month-filter-april",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("1"),
                billing_key="month-filter-april",
                operation_key="month-filter-april-op",
            )
            march_charge.created_at = datetime(2026, 3, 20, 12, 0, 0)
            april_charge.created_at = datetime(2026, 4, 10, 12, 0, 0)
            db.commit()

            april_rows = billing_service.get_billing_reimbursement_rows(
                db,
                group_by="script",
                month="2026-04",
            )
            april_scripts = billing_service.get_billing_script_list(
                db,
                user_id=self.user_id,
                month="2026-04",
            )

            self.assertEqual(
                april_rows,
                [
                    {
                        "month": "2026-04",
                        "group_by": "script",
                        "script_id": self.script_id,
                        "script_name": "script",
                        "user_id": self.user_id,
                        "username": "tester",
                        "amount_rmb": "0.18000",
                    }
                ],
            )
            self.assertEqual(len(april_scripts), 1)
            self.assertEqual(april_scripts[0]["request_count"], 1)
            self.assertEqual(april_scripts[0]["net_amount_rmb"], "0.18000")
        finally:
            db.close()

    def test_reimbursement_month_uses_shanghai_calendar_month(self):
        db = self.Session()
        try:
            boundary_charge = billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="text",
                stage="month-boundary",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("1"),
                billing_key="month-boundary-charge",
                operation_key="month-boundary-op",
            )
            boundary_charge.created_at = datetime(2026, 3, 31, 16, 30, 0)
            db.commit()

            rows = billing_service.get_billing_reimbursement_rows(db, group_by="script")
            march_rows = billing_service.get_billing_reimbursement_rows(
                db,
                group_by="script",
                month="2026-03",
            )
            april_rows = billing_service.get_billing_reimbursement_rows(
                db,
                group_by="script",
                month="2026-04",
            )

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["month"], "2026-04")
            self.assertEqual(march_rows, [])
            self.assertEqual(len(april_rows), 1)
            self.assertEqual(april_rows[0]["month"], "2026-04")
        finally:
            db.close()

    def test_legacy_ledger_rows_use_fallback_names(self):
        db = self.Session()
        try:
            legacy_entry = models.BillingLedgerEntry(
                user_id=999,
                script_id=888,
                episode_id=777,
                category="text",
                stage="legacy_stage",
                provider="legacy",
                model_name="legacy-model",
                billing_mode="per_call",
                quantity=Decimal("1"),
                unit_price_rmb=Decimal("1.23000"),
                amount_rmb=Decimal("1.23000"),
                entry_type="charge",
                status="finalized",
                billing_key="legacy-ledger-only",
                operation_key="legacy-op-only",
            )
            db.add(legacy_entry)
            db.commit()

            users = billing_service.get_billing_user_list(db)
            scripts = billing_service.get_billing_script_list(db)
            episodes = billing_service.get_billing_episode_list(db)
            script_detail = billing_service.get_script_billing_detail(db, script_id=888)
            episode_detail = billing_service.get_episode_billing_detail(db, episode_id=777)

            legacy_user = next(item for item in users if int(item["user_id"]) == 999)
            legacy_script = next(item for item in scripts if int(item["script_id"]) == 888)
            legacy_episode = next(item for item in episodes if int(item["episode_id"]) == 777)

            self.assertEqual(legacy_user["username"], "用户 #999")
            self.assertEqual(legacy_script["script_name"], "剧本 #888")
            self.assertEqual(legacy_episode["episode_name"], "剧集 #777")
            self.assertIsNotNone(script_detail)
            self.assertEqual(script_detail["username"], "用户 #999")
            self.assertIsNotNone(episode_detail)
            self.assertEqual(episode_detail["script_name"], "剧本 #888")
        finally:
            db.close()

    def test_billing_excludes_test_username(self):
        db = self.Session()
        try:
            billing_service.create_charge_entry(
                db,
                user_id=self.second_user_id,
                script_id=self.other_user_script_id,
                episode_id=self.other_user_episode_id,
                category="text",
                stage="simple_storyboard",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("1"),
                billing_key="test-hidden-op-1",
                operation_key="test-hidden-group-1",
                detail_json='{"creator_username":"test"}',
            )
            db.commit()

            users = billing_service.get_billing_user_list(db)
            scripts = billing_service.get_billing_script_list(db)
            episodes = billing_service.get_billing_episode_list(db)

            self.assertEqual(users, [])
            self.assertEqual(scripts, [])
            self.assertEqual(episodes, [])
        finally:
            db.close()

    def test_billing_user_list_skips_entries_filtered_as_test_after_fallback_meta(self):
        db = self.Session()
        try:
            db.add(
                models.BillingLedgerEntry(
                    user_id=7,
                    script_id=700,
                    episode_id=7000,
                    category="text",
                    stage="legacy_stage",
                    provider="legacy",
                    model_name="legacy-model",
                    billing_mode="per_call",
                    quantity=Decimal("1"),
                    unit_price_rmb=Decimal("0.50000"),
                    amount_rmb=Decimal("0.50000"),
                    entry_type="charge",
                    status="finalized",
                    billing_key="legacy-test-filtered",
                    operation_key="legacy-test-filtered-op",
                    detail_json='{"creator_username":"test"}',
                )
            )
            db.commit()

            users = billing_service.get_billing_user_list(db)
            scripts = billing_service.get_billing_script_list(db)
            episodes = billing_service.get_billing_episode_list(db)

            self.assertEqual(users, [])
            self.assertEqual(scripts, [])
            self.assertEqual(episodes, [])
        finally:
            db.close()

        db = self.Session()
        try:
            billing_service.create_charge_entry(
                db,
                user_id=self.user_id,
                script_id=self.script_id,
                episode_id=self.new_episode_id,
                category="text",
                stage="simple_storyboard",
                provider="yyds",
                model_name="gemini-3.1-pro-high",
                quantity=Decimal("1"),
                billing_key="deleted-script-op-1",
                operation_key="deleted-script-group-1",
                detail_json='{"creator_username":"tester","script_name":"script","episode_name":"new-episode"}',
            )
            billing_service.ensure_deleted_billing_name_snapshots(
                db,
                script_id=self.script_id,
                username="tester",
                script_name="script",
            )
            script = db.query(models.Script).filter(models.Script.id == self.script_id).first()
            db.delete(script)
            db.commit()

            scripts = billing_service.get_billing_script_list(db)
            episodes = billing_service.get_billing_episode_list(db)
            script_detail = billing_service.get_script_billing_detail(db, script_id=self.script_id)

            deleted_script = next(item for item in scripts if int(item["script_id"]) == int(self.script_id))
            deleted_episode = next(item for item in episodes if int(item["script_id"]) == int(self.script_id))

            self.assertEqual(deleted_script["script_name"], "script（已删除）")
            self.assertTrue(bool(deleted_script["script_deleted"]))
            self.assertEqual(deleted_script["username"], "tester（已删除）")
            self.assertEqual(deleted_episode["episode_name"], "new-episode（已删除）")
            self.assertIsNotNone(script_detail)
            self.assertEqual(script_detail["script_name"], "script（已删除）")
        finally:
            db.close()
