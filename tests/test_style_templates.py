import asyncio
import os
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}")

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402
import models  # noqa: E402
from api.routers import templates  # noqa: E402


class StyleTemplateBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_build_card_image_prompt_uses_card_type_specific_style_content(self):
        template = models.StyleTemplate(
            name="日漫风格",
            content="角色风格",
            scene_content="场景风格",
            prop_content="道具风格",
        )
        role_card = models.SubjectCard(card_type="角色", ai_prompt="角色描述")
        scene_card = models.SubjectCard(card_type="场景", ai_prompt="场景描述")
        prop_card = models.SubjectCard(card_type="道具", ai_prompt="道具描述")

        role_style = main._resolve_style_template_content_for_card_type(template, role_card.card_type)
        scene_style = main._resolve_style_template_content_for_card_type(template, scene_card.card_type)
        prop_style = main._resolve_style_template_content_for_card_type(template, prop_card.card_type)

        self.assertEqual(role_style, "角色风格")
        self.assertEqual(scene_style, "场景风格")
        self.assertEqual(prop_style, "道具风格")

    def test_get_style_templates_returns_all_three_prompt_variants(self):
        db = self.Session()
        try:
            db.add(
                models.StyleTemplate(
                    name="厚涂国风",
                    content="角色版本",
                    scene_content="场景版本",
                    prop_content="道具版本",
                    is_default=True,
                )
            )
            db.commit()

            payload = asyncio.run(templates.get_style_templates(db=db))
        finally:
            db.close()

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["content"], "角色版本")
        self.assertEqual(payload[0]["scene_content"], "场景版本")
        self.assertEqual(payload[0]["prop_content"], "道具版本")

    def test_ensure_style_template_variant_columns_backfills_scene_and_prop_without_changing_role_content(self):
        original_engine = main.engine
        original_session_local = main.SessionLocal
        main.engine = self.engine
        main.SessionLocal = self.Session

        db = self.Session()
        try:
            template = models.StyleTemplate(
                name="日漫风格",
                content="日漫，古风，平涂，赛璐璐上色",
                scene_content="",
                prop_content="",
            )
            db.add(template)
            db.commit()
            template_id = template.id
        finally:
            db.close()

        try:
            main.ensure_style_template_variant_columns()

            db = self.Session()
            try:
                refreshed = db.query(models.StyleTemplate).filter(models.StyleTemplate.id == template_id).first()
                self.assertIsNotNone(refreshed)
                self.assertEqual(refreshed.content, "日漫，古风，平涂，赛璐璐上色")
                self.assertIn("日漫，古风，平涂，赛璐璐上色", refreshed.scene_content)
                self.assertIn("日漫，古风，平涂，赛璐璐上色", refreshed.prop_content)
                self.assertIn("环境设计", refreshed.scene_content)
                self.assertIn("道具材质", refreshed.prop_content)
            finally:
                db.close()
        finally:
            main.engine = original_engine
            main.SessionLocal = original_session_local

    def test_ensure_style_template_variant_columns_rebuilds_corrupted_question_mark_variants(self):
        original_engine = main.engine
        original_session_local = main.SessionLocal
        main.engine = self.engine
        main.SessionLocal = self.Session

        db = self.Session()
        try:
            template = models.StyleTemplate(
                name="3D漫画（国风）",
                content="3D国漫风格，bjd质感，韩式厚涂插画",
                scene_content="???????????????????????3D国漫风格",
                prop_content="???????????????????????3D国漫风格",
            )
            db.add(template)
            db.commit()
            template_id = template.id
        finally:
            db.close()

        try:
            main.ensure_style_template_variant_columns()

            db = self.Session()
            try:
                refreshed = db.query(models.StyleTemplate).filter(models.StyleTemplate.id == template_id).first()
                self.assertIsNotNone(refreshed)
                self.assertEqual(refreshed.content, "3D国漫风格，bjd质感，韩式厚涂插画")
                self.assertNotIn("??????????", refreshed.scene_content)
                self.assertNotIn("??????????", refreshed.prop_content)
                self.assertIn("环境设计", refreshed.scene_content)
                self.assertIn("道具材质", refreshed.prop_content)
            finally:
                db.close()
        finally:
            main.engine = original_engine
            main.SessionLocal = original_session_local

    def test_scene_and_prop_template_variants_drop_role_only_descriptions(self):
        source = "3D国漫风格，bjd质感＋人物高清画质＋细腻渲染＋极致清晰＋韩式厚涂插画＋人物冷白皮＋冷调光，全身，白底"

        scene = main._build_scene_style_template_content(source)
        prop = main._build_prop_style_template_content(source)

        self.assertNotIn("保持与该风格角色模板一致", scene)
        self.assertNotIn("保持与该风格角色模板一致", prop)
        self.assertNotIn("人物高清画质", scene)
        self.assertNotIn("人物高清画质", prop)
        self.assertNotIn("冷白皮", scene)
        self.assertNotIn("冷白皮", prop)
        self.assertNotIn("全身", scene)
        self.assertNotIn("全身", prop)
        self.assertNotIn("白底", scene)
        self.assertNotIn("白底", prop)
        self.assertNotIn("＋＋", scene)
        self.assertNotIn("＋＋", prop)
        self.assertIn("3D国漫风格", scene)
        self.assertIn("韩式厚涂插画", scene)
        self.assertIn("3D国漫风格", prop)
        self.assertIn("韩式厚涂插画", prop)

    def test_scene_and_prop_template_variants_strip_portrait_template_labels(self):
        source = (
            "- 核心风格：照片级写实肖像，真实人类，电影级摄影，专业人像\n"
            "- 建模技术：专业人像摄影，数码单反相机质量，八十五毫米镜头，清晰对焦\n"
            "- 皮肤质感：真实皮肤纹理，可见毛孔，自然皮肤瑕疵，皮肤细节，次表面散射\n"
            "- 光影效果：自然光照，工作室光照，柔光箱光照，轮廓光，真实情感，写实凝视"
        )

        scene = main._build_scene_style_template_content(source)
        prop = main._build_prop_style_template_content(source)

        self.assertNotIn("核心风格", scene)
        self.assertNotIn("建模技术", scene)
        self.assertNotIn("皮肤", scene)
        self.assertNotIn("情感", scene)
        self.assertNotIn("凝视", scene)
        self.assertNotIn("核心风格", prop)
        self.assertNotIn("建模技术", prop)
        self.assertNotIn("皮肤", prop)
        self.assertNotIn("情感", prop)
        self.assertNotIn("凝视", prop)
        self.assertIn("电影级摄影", scene)
        self.assertIn("数码单反相机质量", scene)
        self.assertIn("电影级摄影", prop)
        self.assertIn("数码单反相机质量", prop)


if __name__ == "__main__":
    unittest.main()
