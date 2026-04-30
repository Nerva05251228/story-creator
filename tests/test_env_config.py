import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class EnvConfigTests(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("env_config", None)

    def _load_module(self):
        return importlib.import_module("env_config")

    def test_load_app_env_reads_dotenv_without_overriding_existing_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "DATABASE_URL=postgresql://user:secret@127.0.0.1:5432/app",
                        "PORT=10001",
                        "QUOTED='hello world'",
                    ]
                ),
                encoding="utf-8",
            )

            with mock.patch.dict(
                os.environ,
                {
                    "DATABASE_URL": "postgresql://user:secret@127.0.0.1:5432/app",
                    "PORT": "20002",
                },
                clear=True,
            ):
                env_config = self._load_module()
                loaded = env_config.load_app_env(env_path)

                self.assertTrue(loaded)
                self.assertEqual(
                    os.environ["DATABASE_URL"],
                    "postgresql://user:secret@127.0.0.1:5432/app",
                )
                self.assertEqual(os.environ["PORT"], "20002")
                self.assertEqual(os.environ["QUOTED"], "hello world")

    def test_get_first_env_supports_aliases_and_blank_values(self):
        with mock.patch.dict(
            os.environ,
            {"PRIMARY": "", "SECONDARY": "fallback-value"},
            clear=True,
        ):
            env_config = self._load_module()

            self.assertEqual(
                env_config.get_first_env("PRIMARY", "SECONDARY"),
                "fallback-value",
            )

    def test_require_env_raises_clear_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            env_config = self._load_module()

            with self.assertRaisesRegex(RuntimeError, "MISSING_VALUE"):
                env_config.require_env("MISSING_VALUE")

    def test_placeholder_env_values_are_detected(self):
        env_config = self._load_module()

        self.assertTrue(env_config.is_placeholder_env_value("<set-local-text-relay-key>"))
        self.assertTrue(env_config.is_placeholder_env_value("https://relay.example.invalid/api/llm"))
        self.assertFalse(env_config.is_placeholder_env_value("https://llm.example.test/api/llm"))

    def test_mask_url_redacts_password(self):
        env_config = self._load_module()

        self.assertEqual(
            env_config.mask_url("postgresql://user:secret@127.0.0.1:5432/app"),
            "postgresql://user:***@127.0.0.1:5432/app",
        )


if __name__ == "__main__":
    unittest.main()
