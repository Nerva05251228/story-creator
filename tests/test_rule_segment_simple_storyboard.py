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
import simple_storyboard_rules  # noqa: E402


class RuleSegmentSimpleStoryboardTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_parse_rule_segmented_shots_skips_blank_lines_and_keeps_number_order(self):
        shots = simple_storyboard_rules.parse_rule_segmented_shots(
            "1\n第一段内容\n\n2\n第二段内容\n3\n第三段内容"
        )

        self.assertEqual(
            shots,
            [
                {"shot_number": 1, "original_text": "第一段内容"},
                {"shot_number": 2, "original_text": "第二段内容"},
                {"shot_number": 3, "original_text": "第三段内容"},
            ],
        )

    def test_generate_simple_storyboard_api_uses_rule_segment_mode(self):
        with self.Session() as db:
            user = models.User(username="tester", token="token", password_hash="hash", password_plain="123456")
            db.add(user)
            db.flush()

            script = models.Script(user_id=user.id, name="script")
            db.add(script)
            db.flush()

            episode = models.Episode(
                script_id=script.id,
                name="ep",
                content="1\n第一段\n2\n第二段\n3\n第三段",
                storyboard2_duration=35,
                batch_size=500,
            )
            db.add(episode)
            db.commit()

            payload = asyncio.run(
                main.generate_simple_storyboard_api(
                    episode.id,
                    request=main.SimpleStoryboardRequest(batch_size=500),
                    user=user,
                    db=db,
                )
            )

        self.assertEqual([shot["original_text"] for shot in payload["shots"]], ["第一段", "第二段", "第三段"])
        self.assertEqual(payload["shots"][0]["shot_number"], 1)
        self.assertEqual(payload["shots"][2]["shot_number"], 3)


if __name__ == "__main__":
    unittest.main()
