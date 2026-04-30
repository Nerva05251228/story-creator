import json
import os
import sys
import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}")

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.env_defaults import apply_test_env_defaults  # noqa: E402

apply_test_env_defaults()

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import ai_config  # noqa: E402
import ai_service  # noqa: E402
import dashboard_service  # noqa: E402
import main  # noqa: E402
import models  # noqa: E402
import text_llm_queue  # noqa: E402
import simple_storyboard_rules  # noqa: E402


class RelayAiConfigDefaultsTests(unittest.TestCase):
    def test_default_ai_config_uses_relay_default_model(self):
        with patch.object(ai_config, "_query_relay_model_rows", return_value=[]):
            config = ai_config.get_ai_config()

        self.assertEqual(config["provider_key"], "relay")
        self.assertEqual(config["model"], "gemini-3.1-pro")
        self.assertEqual(config["timeout"], 120)


class ModelConfigPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_session_local = main.SessionLocal
        main.SessionLocal = self.Session

    def tearDown(self):
        main.SessionLocal = self.original_session_local
        self.engine.dispose()

    def test_explicit_relay_selection_persists_after_reloading_configs(self):
        with self.Session() as db:
            asyncio.run(main.update_model_config(
                "video_prompt",
                main.UpdateModelConfigRequest(
                    model_id="gemini-3.1-pro",
                ),
                db=db,
            ))

            payload = asyncio.run(main.get_model_configs(db=db))
            config = next(item for item in payload["configs"] if item["function_key"] == "video_prompt")

            self.assertNotIn("provider_key", config)
            self.assertEqual(config["model_id"], "gemini-3.1-pro")
            self.assertEqual(config["resolved_model_id"], "gemini-3.1-pro")



class SimpleStoryboardBatchSplitTests(unittest.TestCase):
    def test_split_simple_storyboard_batches_preserves_paragraph_groups(self):
        content = "第一段\n第二段更长\n第三段"

        batches = main._split_simple_storyboard_batches(content, 8)

        self.assertEqual(batches, ["第一段", "第二段更长", "第三段"])

    def test_split_simple_storyboard_batches_returns_empty_for_blank_content(self):
        self.assertEqual(main._split_simple_storyboard_batches("\n\n", 100), [])


class ProgrammaticSimpleStoryboardRuleTests(unittest.TestCase):
    def test_split_units_keeps_ellipsis_and_mixed_punctuation_as_single_boundary(self):
        rule = simple_storyboard_rules.get_default_rule_config(15)

        units = simple_storyboard_rules.split_units(
            '她沉默了……“你真的要走？！”他追问。她没回头...',
            rule,
        )

        self.assertEqual(
            [item.text for item in units],
            ['她沉默了……', '“你真的要走？！”', '他追问。', '她没回头...'],
        )

    def test_split_units_keeps_dialogue_turn_together_by_default(self):
        rule = simple_storyboard_rules.get_default_rule_config(15)

        units = simple_storyboard_rules.split_units(
            '悟空：师父，前面有妖气。八戒：俺也去！旁白：风越来越紧。',
            rule,
        )

        self.assertEqual(
            [item.text for item in units],
            ['悟空：师父，前面有妖气。', '八戒：俺也去！', '旁白：风越来越紧。'],
        )

    def test_generate_simple_storyboard_shots_uses_different_size_windows_for_15_and_25(self):
        text = (
            '悟空抬头看向山门。'
            '八戒拎着钉耙跟上来。'
            '沙僧回头确认师父还在。'
            '山风穿过石阶，旗幡猎猎作响。'
            '悟空压低声音说前面不对劲。'
            '八戒嘟囔着问要不要先撤。'
        )

        shots_15 = simple_storyboard_rules.generate_simple_storyboard_shots(text, 15)
        shots_25 = simple_storyboard_rules.generate_simple_storyboard_shots(text, 25)

        self.assertGreater(len(shots_15), len(shots_25))
        self.assertEqual(
            ''.join(item['original_text'] for item in shots_15),
            text,
        )
        self.assertEqual(
            ''.join(item['original_text'] for item in shots_25),
            text,
        )

    def test_generate_simple_storyboard_shots_splits_oversized_dialogue_turn_at_sentence_boundary(self):
        rule = simple_storyboard_rules.get_default_rule_config(15)
        text = (
            '悟空：师父，前面妖气太重，我们不能再往前走了，石阶尽头埋伏的人已经按住了兵器，'
            '只等我们踏进山门就会一起冲出来。'
            '悟空：再走一步，山门后面埋伏的人、屋檐上的弓手、侧墙后的伏兵就会同时现身，'
            '到时候师父和行李都会被困在中间。'
        )

        shots = simple_storyboard_rules.generate_simple_storyboard_shots(text, 15, rule_override=rule)

        self.assertGreaterEqual(len(shots), 2)
        self.assertEqual(''.join(item['original_text'] for item in shots), text)

    def test_generate_simple_storyboard_shots_uses_soft_boundaries_for_single_long_sentence(self):
        rule = simple_storyboard_rules.get_default_rule_config(15)
        text = (
            '嫡母揉了揉额角，似乎有些头疼于我的愚钝，但语气却放缓下来，'
            '她低声提醒我先别慌，再把当年的事情一件件说清楚，'
            '又叮嘱我先把宫里的眼线和账册都重新查一遍，免得一会儿说到关键处还要被外人打断。'
        )

        shots = simple_storyboard_rules.generate_simple_storyboard_shots(text, 15, rule_override=rule)

        self.assertGreaterEqual(len(shots), 2)
        self.assertTrue(all(len(item['original_text']) <= rule.soft_max_chars for item in shots))
        self.assertEqual(''.join(item['original_text'] for item in shots), text)


class ShotDurationTemplateConfigTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_session_local = main.SessionLocal
        main.SessionLocal = self.Session

    def tearDown(self):
        main.SessionLocal = self.original_session_local
        self.engine.dispose()

    def test_ensure_shot_duration_template_config_column_backfills_default_json(self):
        with self.Session() as db:
            db.add(models.ShotDurationTemplate(
                duration=15,
                shot_count_min=4,
                shot_count_max=5,
                time_segments=5,
                simple_storyboard_rule="legacy prompt",
                video_prompt_rule="video",
                large_shot_prompt_rule="large",
                is_default=True,
            ))
            db.commit()

        with patch.object(main, "engine", self.engine):
            main.ensure_shot_duration_template_config_json()

        with self.Session() as db:
            row = db.query(models.ShotDurationTemplate).filter(
                models.ShotDurationTemplate.duration == 15
            ).first()
            self.assertTrue(str(getattr(row, "simple_storyboard_config_json", "") or "").strip())
            payload = json.loads(row.simple_storyboard_config_json)
            self.assertEqual(payload["target_chars_min"], 35)
            self.assertTrue(payload["keep_dialogue_turn_intact"])

    def test_update_shot_duration_template_validates_structured_config(self):
        with self.Session() as db:
            db.add(models.ShotDurationTemplate(
                duration=25,
                shot_count_min=5,
                shot_count_max=6,
                time_segments=6,
                simple_storyboard_rule="legacy prompt",
                video_prompt_rule="video",
                large_shot_prompt_rule="large",
                is_default=False,
            ))
            db.commit()

            with self.assertRaises(Exception):
                asyncio.run(main.update_shot_duration_template(
                    25,
                    {
                        "simple_storyboard_config": {
                            "target_chars_min": 120,
                            "target_chars_max": 80,
                        }
                    },
                    db=db,
                ))


class ProgrammaticSimpleStoryboardApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_session_local = main.SessionLocal
        main.SessionLocal = self.Session

        db = self.Session()
        try:
            user = models.User(
                username="tester",
                token="token",
                password_hash="hash",
                password_plain="123456",
            )
            db.add(user)
            db.flush()
            self.user_id = int(user.id)

            script = models.Script(user_id=user.id, name="script")
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="ep1",
                content=(
                    '悟空抬头看向山门。'
                    '八戒拎着钉耙跟上来。'
                    '沙僧回头确认师父还在。'
                    '山风穿过石阶，旗幡猎猎作响。'
                ),
                storyboard2_duration=15,
                simple_storyboard_generating=True,
                simple_storyboard_error="old error",
            )
            db.add(episode)
            db.commit()
            self.episode_id = int(episode.id)
        finally:
            db.close()

    def tearDown(self):
        main.SessionLocal = self.original_session_local
        self.engine.dispose()

    def test_generate_simple_storyboard_api_completes_synchronously_without_relay_task(self):
        with self.Session() as db:
            user = db.query(models.User).filter(models.User.id == self.user_id).first()
            payload = asyncio.run(main.generate_simple_storyboard_api(
                self.episode_id,
                request=main.SimpleStoryboardRequest(),
                user=user,
                db=db,
            ))

            episode = db.query(models.Episode).filter(models.Episode.id == self.episode_id).first()
            batches = db.query(models.SimpleStoryboardBatch).filter(
                models.SimpleStoryboardBatch.episode_id == self.episode_id
            ).all()
            relay_tasks = db.query(models.TextRelayTask).all()

        self.assertFalse(payload["generating"])
        self.assertEqual(payload["failed_batches"], 0)
        self.assertEqual(payload["submitted_batches"], len(batches))
        self.assertIsInstance(payload.get("shots"), list)
        self.assertGreater(len(payload["shots"]), 0)
        self.assertEqual(payload["completed_batches"], len(batches))
        self.assertEqual(payload["total_batches"], len(batches))
        self.assertEqual(''.join(item["original_text"] for item in payload["shots"]), episode.content)
        self.assertFalse(bool(episode.simple_storyboard_generating))
        self.assertEqual(episode.simple_storyboard_error, "")
        self.assertEqual(len(relay_tasks), 0)
        self.assertTrue(all((row.status or "") == "completed" for row in batches))
        saved = json.loads(episode.simple_storyboard_data)
        self.assertEqual(saved["shots"][0]["shot_number"], 1)
        self.assertEqual(
            ''.join(item["original_text"] for item in saved["shots"]),
            episode.content,
        )

class StartupFunctionModelConfigMigrationTests(unittest.TestCase):
    def test_ensure_function_model_config_columns_keeps_blank_provider_on_openrouter(self):
        class FakeConnection:
            def __init__(self):
                self.executed = []

            def execute(self, stmt):
                self.executed.append(str(stmt))

        class FakeBeginContext:
            def __init__(self, conn):
                self.conn = conn

            def __enter__(self):
                return self.conn

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeEngine:
            def __init__(self, conn):
                self.conn = conn

            def begin(self):
                return FakeBeginContext(self.conn)

        fake_conn = FakeConnection()
        fake_engine = FakeEngine(fake_conn)

        with patch.object(main, "engine", fake_engine), \
             patch.object(main, "get_table_columns", return_value={"provider_key", "model_key"}):
            main.ensure_function_model_config_columns()

        joined_sql = "\n".join(fake_conn.executed)
        self.assertIn("SET provider_key = 'openrouter'", joined_sql)
        self.assertNotIn("SET provider_key = 'yyds'", joined_sql)

    def test_ensure_function_model_configs_rewrites_legacy_yyds_default_to_openrouter(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)

        db = Session()
        try:
            db.add(models.FunctionModelConfig(
                function_key="video_prompt",
                function_name="旧配置",
                provider_key="yyds",
                model_key="gemini_pro_high",
                model_id="gemini-3.1-pro-high",
            ))
            db.commit()

            main._ensure_function_model_configs(db)

            row = db.query(models.FunctionModelConfig).filter(
                models.FunctionModelConfig.function_key == "video_prompt"
            ).first()
            self.assertEqual(row.provider_key, "relay")
            self.assertEqual(row.model_key, "gemini-3.1-pro")
            self.assertEqual(row.model_id, "gemini-3.1-pro")
        finally:
            db.close()
            engine.dispose()


class DetailedStoryboardAiConfigTests(unittest.TestCase):
    def test_generate_detailed_storyboard_uses_stage1_model_config(self):
        config_keys = []

        class DummyResponse:
            status_code = 200
            text = '{"choices":[{"message":{"content":"{\\"shots\\": []}"}}]}'

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": '{"shots": []}',
                            }
                        }
                    ]
                }

        def fake_get_ai_config(function_key):
            config_keys.append(function_key)
            return {
                "model": "test-model",
                "api_url": "https://example.com/v1/chat/completions",
                "api_key": "test-key",
                "timeout": 1200,
            }

        with patch.object(ai_service, "get_prompt_by_key", return_value="镜头列表\n{shots_content}"), \
             patch.object(ai_service, "get_ai_config", side_effect=fake_get_ai_config), \
             patch.object(text_llm_queue.requests, "post", return_value=DummyResponse()) as mock_post:
            result, _ = ai_service.generate_detailed_storyboard(
                [{"shot_number": 1, "original_text": "她攥着铜镜快步离开"}]
            )

        self.assertEqual(result["shots"], [])
        self.assertEqual(config_keys, ["detailed_storyboard_s1"])
        self.assertEqual(mock_post.call_args.kwargs["timeout"], 1200)
        self.assertEqual(
            text_llm_queue.get_text_llm_queue_state(),
            {"running": 0, "waiting": 0, "max": 5},
        )


class DetailedStoryboardPromptUpgradeTests(unittest.TestCase):
    def test_content_analysis_prompt_upgrade_strengthens_prop_rules(self):
        upgraded = main.upgrade_detailed_storyboard_content_analysis_prompt_content(
            "\n".join(
                [
                    "【主体提取原则】",
                    "主体类型只能输出三类：角色、场景、道具。",
                    "主体必须包含至少一个场景（即：故事发生的地点）",
                    "【输出格式】",
                    '{"subjects":[{"name":"角色名","type":"角色"},{"name":"场景名","type":"场景"}]}',
                ]
            )
        )

        self.assertIn("0-2个关键道具", upgraded)
        self.assertIn("必须提取为道具", upgraded)
        self.assertIn("关键道具", upgraded)

    def test_content_analysis_prompt_upgrade_is_idempotent_for_prop_examples(self):
        original = "\n".join(
            [
                "【主体提取原则】",
                "主体类型只能输出三类：角色、场景、道具。",
                "主体必须包含至少一个场景（即：故事发生的地点）",
                "```json",
                '{{"name": "场景名", "type": "场景"}}',
                "```",
            ]
        )

        once = main.upgrade_detailed_storyboard_content_analysis_prompt_content(original)
        twice = main.upgrade_detailed_storyboard_content_analysis_prompt_content(once)

        self.assertEqual(once, twice)


class DetailedStoryboardSubjectNormalizationTests(unittest.TestCase):
    def test_subject_normalization_keeps_prop_cards(self):
        normalized = main._normalize_storyboard_generation_subjects(
            [
                {"name": "陆云熙", "type": "角色"},
                {"name": "卧房", "type": "场景"},
                {"name": "瓜子", "type": "道具"},
                {"name": "瓜子", "type": "道具"},
            ]
        )

        self.assertEqual(
            normalized,
            [
                {"name": "陆云熙", "type": "角色"},
                {"name": "卧房", "type": "场景"},
                {"name": "瓜子", "type": "道具"},
            ],
        )


class DetailedStoryboardDashboardSplitTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_session_local = dashboard_service.SessionLocal
        dashboard_service.SessionLocal = self.Session

    def tearDown(self):
        dashboard_service.SessionLocal = self.original_session_local
        self.engine.dispose()

    def test_detailed_storyboard_stage1_and_stage2_create_separate_dashboard_tasks(self):
        dashboard_service.log_debug_task_event(
            stage="detailed_storyboard",
            task_folder="detailed_storyboard_episode_1_20260403_120000",
            input_data={"shots_content": "镜头1：她攥着铜镜快步离开"},
        )
        dashboard_service.log_debug_task_event(
            stage="stage2",
            task_folder="detailed_storyboard_episode_1_20260403_120000",
            input_data={"full_storyboard_json": json.dumps({"shots": []}, ensure_ascii=False)},
        )

        db = self.Session()
        try:
            records = db.query(models.DashboardTaskLog).order_by(models.DashboardTaskLog.id.asc()).all()
        finally:
            db.close()

        self.assertEqual(len(records), 2)
        self.assertEqual(
            [record.task_type for record in records],
            ["detailed_storyboard_stage1", "detailed_storyboard_stage2"],
        )


class DashboardAdditionalTextTaskTypeTests(unittest.TestCase):
    def test_derive_task_type_recognizes_new_text_stages(self):
        self.assertEqual(dashboard_service._derive_task_type("opening", "", "", []), "opening")
        self.assertEqual(dashboard_service._derive_task_type("narration", "", "", []), "narration")
        self.assertEqual(dashboard_service._derive_task_type("managed_prompt_optimize", "", "", []), "managed_prompt_optimize")
        self.assertEqual(dashboard_service._derive_task_type("subject_prompt", "", "", []), "subject_prompt")


class StoryboardShotSubjectBindingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_create_shots_reconciles_canonical_scene_and_keeps_unmapped_scene(self):
        db = self.Session()
        try:
            user = models.User(
                username="tester",
                token="token",
                password_hash="hash",
                password_plain="123456",
            )
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="script")
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="S01",
                storyboard_data=json.dumps(
                    {
                        "subjects": [
                            {
                                "name": "陆云熙",
                                "type": "角色",
                                "alias": "四小姐",
                                "ai_prompt": "角色提示词",
                                "role_personality": "聪慧",
                            },
                            {
                                "name": "侯府花园",
                                "type": "场景",
                                "alias": "静谧幽冷的古代侯府花园，夜色朦胧",
                                "ai_prompt": "场景提示词",
                                "role_personality": "",
                            },
                        ],
                        "shots": [
                            {
                                "shot_number": 1,
                                "subjects": [
                                    {"name": "陆云熙", "type": "角色"},
                                    {"name": "花园偏僻角落", "type": "场景"},
                                ],
                                "original_text": "我借着偶遇，在花园偏僻角落撞见了陆弘轩。",
                                "voice_type": "narration",
                                "narration": {
                                    "speaker": "陆云熙",
                                    "gender": "女",
                                    "emotion": "好奇",
                                    "text": "我借着偶遇，在花园偏僻角落撞见了陆弘轩。",
                                },
                                "dialogue": None,
                            },
                            {
                                "shot_number": 2,
                                "subjects": [
                                    {"name": "陆云熙", "type": "角色"},
                                    {"name": "回去的路上", "type": "场景"},
                                ],
                                "original_text": "回去的路上，我终于想明白了。",
                                "voice_type": "narration",
                                "narration": {
                                    "speaker": "陆云熙",
                                    "gender": "女",
                                    "emotion": "震惊",
                                    "text": "回去的路上，我终于想明白了。",
                                },
                                "dialogue": None,
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
            )
            db.add(episode)
            db.flush()

            library = models.StoryLibrary(
                user_id=user.id,
                episode_id=episode.id,
                name="S01 - 主体库",
            )
            db.add(library)
            db.commit()

            main._create_shots_from_storyboard_data(episode.id, db)

            cards = db.query(models.SubjectCard).filter(
                models.SubjectCard.library_id == library.id
            ).all()
            card_names = {(card.name, card.card_type) for card in cards}
            self.assertIn(("侯府花园", "场景"), card_names)
            self.assertIn(("回去的路上", "场景"), card_names)
            self.assertNotIn(("花园偏僻角落", "场景"), card_names)

            cards_by_id = {card.id: (card.name, card.card_type) for card in cards}
            shots = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.episode_id == episode.id
            ).order_by(models.StoryboardShot.shot_number.asc()).all()

            shot1_subjects = [cards_by_id[card_id] for card_id in json.loads(shots[0].selected_card_ids or "[]")]
            shot2_subjects = [cards_by_id[card_id] for card_id in json.loads(shots[1].selected_card_ids or "[]")]

            self.assertEqual(
                shot1_subjects,
                [("陆云熙", "角色"), ("侯府花园", "场景")],
            )
            self.assertEqual(
                shot2_subjects,
                [("陆云熙", "角色"), ("回去的路上", "场景")],
            )
        finally:
            db.close()

    def test_get_storyboard_merges_missing_scene_from_storyboard_data(self):
        db = self.Session()
        try:
            user = models.User(
                username="tester",
                token="token",
                password_hash="hash",
                password_plain="123456",
            )
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="script")
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="S01",
                storyboard_data=json.dumps(
                    {
                        "subjects": [
                            {
                                "name": "陆云熙",
                                "type": "角色",
                                "alias": "四小姐",
                                "ai_prompt": "角色提示词",
                                "role_personality": "聪慧",
                            },
                            {
                                "name": "侯府花园",
                                "type": "场景",
                                "alias": "静谧幽冷的古代侯府花园，夜色朦胧",
                                "ai_prompt": "场景提示词",
                                "role_personality": "",
                            },
                        ],
                        "shots": [
                            {
                                "shot_number": 1,
                                "subjects": [
                                    {"name": "陆云熙", "type": "角色"},
                                    {"name": "花园偏僻角落", "type": "场景"},
                                ],
                                "original_text": "我借着偶遇，在花园偏僻角落撞见了陆弘轩。",
                                "voice_type": "narration",
                                "narration": {
                                    "speaker": "陆云熙",
                                    "gender": "女",
                                    "emotion": "好奇",
                                    "text": "我借着偶遇，在花园偏僻角落撞见了陆弘轩。",
                                },
                                "dialogue": None,
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
            )
            db.add(episode)
            db.flush()

            library = models.StoryLibrary(
                user_id=user.id,
                episode_id=episode.id,
                name="S01 - 主体库",
            )
            db.add(library)
            db.flush()

            role_card = models.SubjectCard(
                library_id=library.id,
                name="陆云熙",
                card_type="角色",
                alias="四小姐",
                ai_prompt="角色提示词",
                role_personality="聪慧",
            )
            scene_card = models.SubjectCard(
                library_id=library.id,
                name="侯府花园",
                card_type="场景",
                alias="静谧幽冷的古代侯府花园，夜色朦胧",
                ai_prompt="场景提示词",
            )
            db.add_all([role_card, scene_card])
            db.flush()

            shot_record = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                variant_index=0,
                selected_card_ids=json.dumps([role_card.id], ensure_ascii=False),
            )
            db.add(shot_record)
            db.commit()

            payload = main.get_episode_storyboard(episode.id, user=user, db=db)
            shot = payload["shots"][0]
            subjects = [(item["name"], item["type"]) for item in shot["subjects"]]
            selected_ids = json.loads(shot["selected_card_ids"])

            self.assertEqual(
                subjects,
                [("陆云熙", "角色"), ("侯府花园", "场景")],
            )
            self.assertEqual(selected_ids, [role_card.id, scene_card.id])
        finally:
            db.close()


class PromptConfigDisplayOrderTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_manage_prompt_configs_use_display_overrides_and_order(self):
        db = self.Session()
        try:
            configs = [
                models.PromptConfig(
                    key="stage2_refine_shot",
                    name="阶段2：主体绘画提示词",
                    description="stage2",
                    content="content-2",
                ),
                models.PromptConfig(
                    key="generate_subject_ai_prompt",
                    name="生成主体绘画提示词",
                    description="single",
                    content="content-single",
                ),
                models.PromptConfig(
                    key="detailed_storyboard_content_analysis",
                    name="详细分镜：内容分析",
                    description="analysis",
                    content="content-analysis",
                ),
                models.PromptConfig(
                    key="stage1_initial_storyboard",
                    name="阶段1：初步分镜生成",
                    description="stage1",
                    content="content-1",
                ),
            ]
            db.add_all(configs)
            db.commit()

            payload = asyncio.run(main.get_prompt_configs(db=db))
        finally:
            db.close()

        ordered_keys = [item["key"] for item in payload[:4]]
        ordered_names = [item["name"] for item in payload[:4]]

        self.assertEqual(
            ordered_keys,
            [
                "stage1_initial_storyboard",
                "detailed_storyboard_content_analysis",
                "stage2_refine_shot",
                "generate_subject_ai_prompt",
            ],
        )
        self.assertEqual(
            ordered_names,
            [
                "阶段1：初步分镜生成",
                "阶段2-1：详细分镜内容分析",
                "阶段2-2：详细分镜提取主体与去重",
                "生成主体绘画提示词",
            ],
        )


if __name__ == "__main__":
    unittest.main()
