import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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

import main  # noqa: E402


class FileServingSecurityTests(unittest.TestCase):
    def test_get_file_serves_files_inside_allowed_roots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "uploads"
            root.mkdir()
            allowed_file = root / "clip.mp4"
            allowed_file.write_text("video", encoding="utf-8")

            with mock.patch.object(main, "FILE_SERVING_ROOTS", (root,)):
                response = asyncio.run(main.get_file("clip.mp4"))

            self.assertEqual(Path(response.path).resolve(), allowed_file.resolve())

    def test_get_file_rejects_parent_directory_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "uploads"
            root.mkdir()
            (base / "secret.txt").write_text("secret", encoding="utf-8")

            with mock.patch.object(main, "FILE_SERVING_ROOTS", (root,)):
                with self.assertRaises(main.HTTPException) as raised:
                    asyncio.run(main.get_file("../secret.txt"))

            self.assertEqual(raised.exception.status_code, 404)

    def test_get_file_rejects_windows_separator_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "uploads"
            root.mkdir()
            (base / "secret.txt").write_text("secret", encoding="utf-8")

            with mock.patch.object(main, "FILE_SERVING_ROOTS", (root,)):
                with self.assertRaises(main.HTTPException) as raised:
                    asyncio.run(main.get_file("..\\secret.txt"))

            self.assertEqual(raised.exception.status_code, 404)

    def test_get_file_rejects_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "uploads"
            root.mkdir()
            secret_file = base / "secret.txt"
            secret_file.write_text("secret", encoding="utf-8")

            with mock.patch.object(main, "FILE_SERVING_ROOTS", (root,)):
                with self.assertRaises(main.HTTPException) as raised:
                    asyncio.run(main.get_file(str(secret_file)))

            self.assertEqual(raised.exception.status_code, 404)

    def test_get_file_rejects_nested_escape_from_specific_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "uploads" / "hit_drama_videos"
            root.mkdir(parents=True)
            (base / "uploads" / "secret.txt").write_text("secret", encoding="utf-8")

            with mock.patch.object(main, "FILE_SERVING_ROOTS", (root,)):
                with self.assertRaises(main.HTTPException) as raised:
                    asyncio.run(main.get_file("../secret.txt"))

            self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
