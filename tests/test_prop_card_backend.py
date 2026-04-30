import json
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


class PropCardBackendTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        db = self.Session()
        try:
            user = models.User(username="tester", token="token", password_hash="hash", password_plain="123456")
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="script")
            db.add(script)
            db.flush()

            episode = models.Episode(script_id=script.id, name="ep")
            db.add(episode)
            db.flush()

            library = models.StoryLibrary(user_id=user.id, episode_id=episode.id, name="library")
            db.add(library)
            db.flush()

            self.scene_card = models.SubjectCard(
                library_id=library.id,
                name="废弃仓库",
                card_type="场景",
                ai_prompt="阴冷破旧的仓库，地面有积水和锈迹",
            )
            self.role_card = models.SubjectCard(
                library_id=library.id,
                name="林七",
                card_type="角色",
                ai_prompt="年轻男性，黑色短发，警惕的眼神",
                role_personality="冷静谨慎",
            )
            self.prop_card = models.SubjectCard(
                library_id=library.id,
                name="青铜匕首",
                card_type="道具",
                ai_prompt="青铜材质，刀刃有磨损，木柄缠着发黑布条",
            )
            db.add_all([self.scene_card, self.role_card, self.prop_card])
            db.flush()

            source_shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                selected_card_ids=json.dumps([self.role_card.id, self.scene_card.id, self.prop_card.id], ensure_ascii=False),
            )
            db.add(source_shot)
            db.flush()

            storyboard2_shot = models.Storyboard2Shot(
                episode_id=episode.id,
                source_shot_id=source_shot.id,
                shot_number=1,
                selected_card_ids=json.dumps([self.role_card.id, self.scene_card.id, self.prop_card.id], ensure_ascii=False),
            )
            db.add(storyboard2_shot)
            db.flush()

            db.add_all([
                models.GeneratedImage(
                    card_id=self.prop_card.id,
                    image_path="https://cdn.example.com/prop-ref.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
                models.GeneratedImage(
                    card_id=self.role_card.id,
                    image_path="https://cdn.example.com/role-ref.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
                models.GeneratedImage(
                    card_id=self.scene_card.id,
                    image_path="https://cdn.example.com/scene-ref.png",
                    model_name="upload",
                    is_reference=True,
                    status="completed",
                ),
            ])
            db.commit()

            self.source_shot_id = int(source_shot.id)
            self.storyboard2_shot_id = int(storyboard2_shot.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_prop_card_type_is_accepted_by_subject_normalization(self):
        subject = {
            "name": "青铜匕首",
            "type": "道具",
            "alias": "带血旧匕首",
            "ai_prompt": "青铜材质，刀刃有磨损，木柄开裂",
            "role_personality": "should be cleared",
        }

        normalized = main._normalize_subject_detail_entry(subject)

        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["type"], "道具")
        self.assertEqual(normalized["role_personality"], "")

    def test_stage2_prompt_upgrade_mentions_prop_card_type(self):
        upgraded = main.upgrade_stage2_refine_shot_prompt_content(
            "1. 主体类型只有两类：角色 / 场景。\n"
            "4. 为每个主体生成绘画提示词与别名。\n"
            "     - 角色 ai_prompt：年龄 + 性别 + 表情 + 眼睛 + 发型 + 配饰 + 衣服 + 细节\n"
            "     - 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征\n"
            '        "type": "角色 或 场景",\n'
            '        "role_personality": "角色性格（中文一句话），场景填空字符串",\n'
        )

        self.assertIn("角色 / 场景 / 道具", upgraded)
        self.assertIn("道具 ai_prompt", upgraded)
        self.assertIn("场景/道具填空字符串", upgraded)

    def test_collect_storyboard2_reference_images_includes_prop_cards(self):
        db = self.Session()
        try:
            storyboard2_shot = db.query(models.Storyboard2Shot).filter(
                models.Storyboard2Shot.id == self.storyboard2_shot_id
            ).first()

            urls = main._collect_storyboard2_reference_images(
                storyboard2_shot=storyboard2_shot,
                db=db,
                include_scene_references=False,
            )
        finally:
            db.close()

        self.assertIn("https://cdn.example.com/prop-ref.png", urls)

    def test_collect_moti_v2_reference_assets_orders_first_frame_scene_prop_then_role(self):
        db = self.Session()
        try:
            shot = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.id == self.source_shot_id
            ).first()
            assets = main._collect_moti_v2_reference_assets(
                shot,
                db,
                first_frame_image_url="https://cdn.example.com/first-frame.png",
            )
        finally:
            db.close()

        self.assertEqual(
            assets["image_prefix_parts"],
            [
                "首帧[图片1]",
                "场景[图片2]",
                "青铜匕首[图片3]",
                "林七[图片4]",
            ],
        )
        self.assertEqual(
            assets["image_urls"],
            [
                "https://cdn.example.com/first-frame.png",
                "https://cdn.example.com/scene-ref.png",
                "https://cdn.example.com/prop-ref.png",
                "https://cdn.example.com/role-ref.png",
            ],
        )

    def test_extract_scene_description_ignores_prop_cards(self):
        db = self.Session()
        try:
            shot = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.id == self.source_shot_id
            ).first()
            description = main.extract_scene_description(shot, db)
        finally:
            db.close()

        self.assertIn("废弃仓库", description)
        self.assertNotIn("青铜匕首", description)


if __name__ == "__main__":
    unittest.main()
