import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}")

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402
import models  # noqa: E402


class ThreeViewPromptTests(unittest.TestCase):
    def test_three_view_prompt_uses_only_configured_prompt(self):
        card = SimpleNamespace(card_type="角色", ai_prompt="角色外观描述")

        original_reader = main._get_optional_prompt_config_content
        try:
            main._get_optional_prompt_config_content = lambda key, fallback="": "三视图固定提示词"
            prompt = main._build_card_image_prompt(card, "电影感风格", "three_view")
        finally:
            main._get_optional_prompt_config_content = original_reader

        self.assertEqual(prompt, "三视图固定提示词")


class ThreeViewReferenceResolutionTests(unittest.TestCase):
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

            library = models.StoryLibrary(user_id=user.id, name="library")
            db.add(library)
            db.flush()

            card = models.SubjectCard(
                library_id=library.id,
                name="角色A",
                card_type="角色",
                ai_prompt="角色A提示词",
            )
            db.add(card)
            db.flush()

            reference_image = models.GeneratedImage(
                card_id=card.id,
                image_path="https://cdn.example.com/reference.png",
                model_name="upload",
                is_reference=True,
                status="completed",
            )
            other_image = models.GeneratedImage(
                card_id=card.id,
                image_path="https://cdn.example.com/other.png",
                model_name="upload",
                is_reference=False,
                status="completed",
            )
            db.add_all([reference_image, other_image])
            db.commit()

            self.card_id = int(card.id)
            self.reference_image_id = int(reference_image.id)
            self.other_image_id = int(other_image.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_three_view_requires_selected_subject_reference_image(self):
        db = self.Session()
        try:
            with self.assertRaises(main.HTTPException) as ctx:
                main._resolve_card_reference_urls(
                    db=db,
                    card_id=self.card_id,
                    reference_image_ids=[],
                    generation_mode="three_view",
                )
        finally:
            db.close()

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("主体素材图", ctx.exception.detail)

    def test_three_view_uses_only_selected_subject_reference_image(self):
        db = self.Session()
        try:
            urls = main._resolve_card_reference_urls(
                db=db,
                card_id=self.card_id,
                reference_image_ids=[self.reference_image_id],
                generation_mode="three_view",
            )
        finally:
            db.close()

        self.assertEqual(urls, ["https://cdn.example.com/reference.png"])


if __name__ == "__main__":
    unittest.main()
