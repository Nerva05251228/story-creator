import asyncio
import os
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'story_creator.db').as_posix()}")

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402
import models  # noqa: E402


class StoryboardFirstFrameUploadTests(unittest.TestCase):
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

            episode = models.Episode(script_id=script.id, name="S01")
            db.add(episode)
            db.flush()

            shot = models.StoryboardShot(
                episode_id=episode.id,
                shot_number=1,
                storyboard_image_path="https://cdn.example.com/storyboard.png",
                first_frame_reference_image_url="",
            )
            db.add(shot)
            db.commit()

            self.user_id = int(user.id)
            self.shot_id = int(shot.id)
        finally:
            db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_upload_first_frame_reference_image_persists_uploaded_candidate_without_selecting(self):
        db = self.Session()
        try:
            user = db.query(models.User).filter(models.User.id == self.user_id).first()
            upload = UploadFile(filename="frame.png", file=BytesIO(b"frame-bytes"))

            with patch.object(main, "save_and_upload_to_cdn", return_value="https://cdn.example.com/uploaded-first-frame.png"):
                result = asyncio.run(
                    main.upload_shot_first_frame_reference_image(
                        shot_id=self.shot_id,
                        file=upload,
                        user=user,
                        db=db,
                    )
                )

            shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == self.shot_id).first()
            self.assertEqual(
                result["uploaded_first_frame_reference_image_url"],
                "https://cdn.example.com/uploaded-first-frame.png",
            )
            self.assertEqual(result["first_frame_reference_image_url"], "")
            self.assertIn(
                "https://cdn.example.com/uploaded-first-frame.png",
                result["candidate_urls"],
            )
            self.assertEqual(
                getattr(shot, "uploaded_first_frame_reference_image_url", ""),
                "https://cdn.example.com/uploaded-first-frame.png",
            )
            self.assertEqual(shot.first_frame_reference_image_url, "")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
