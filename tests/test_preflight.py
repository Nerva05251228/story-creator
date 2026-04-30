import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import preflight  # noqa: E402


class PreflightTests(unittest.TestCase):
    def test_module_does_not_import_main_at_import_time(self):
        source = (BACKEND_DIR / "preflight.py").read_text(encoding="utf-8")

        self.assertNotIn("import main", source.split("def run_startup_preflight", 1)[0])
        self.assertNotIn("from main", source.split("def run_startup_preflight", 1)[0])

    def test_migrate_skips_when_version_already_recorded(self):
        calls = []
        fake_engine = object()

        with mock.patch.object(preflight, "engine", fake_engine), \
            mock.patch.object(preflight, "startup_migration_advisory_lock") as lock_factory, \
            mock.patch.object(preflight, "has_migration", return_value=True), \
            mock.patch.object(preflight, "record_migration") as record_migration, \
            mock.patch.object(preflight, "_run_legacy_bootstrap") as run_bootstrap:
            lock_factory.return_value.__enter__.return_value = None
            lock_factory.return_value.__exit__.return_value = None

            result = preflight.run_startup_preflight(mode="migrate", print_fn=calls.append)

        self.assertEqual(result, 0)
        run_bootstrap.assert_not_called()
        record_migration.assert_not_called()
        self.assertTrue(any("already applied" in item for item in calls))

    def test_migrate_records_version_after_bootstrap(self):
        fake_engine = object()

        with mock.patch.object(preflight, "engine", fake_engine), \
            mock.patch.object(preflight, "startup_migration_advisory_lock") as lock_factory, \
            mock.patch.object(preflight, "has_migration", return_value=False), \
            mock.patch.object(preflight, "record_migration") as record_migration, \
            mock.patch.object(preflight, "_run_legacy_bootstrap") as run_bootstrap:
            lock_factory.return_value.__enter__.return_value = None
            lock_factory.return_value.__exit__.return_value = None

            result = preflight.run_startup_preflight(mode="migrate", print_fn=lambda _: None)

        self.assertEqual(result, 0)
        run_bootstrap.assert_called_once_with()
        record_migration.assert_called_once()

    def test_migrate_does_not_record_when_legacy_bootstrap_prints_failure(self):
        fake_engine = object()

        for message in (
            "检查/迁移 episodes 失败: boom",
            "???? storyboard_shots ??: boom",
            "[pricing] ensure_video_model_pricing: boom",
            "初始化风格模板出错: boom",
        ):
            with self.subTest(message=message):
                def noisy_bootstrap():
                    print(message)

                with mock.patch.object(preflight, "engine", fake_engine), \
                    mock.patch.object(preflight, "startup_migration_advisory_lock") as lock_factory, \
                    mock.patch.object(preflight, "has_migration", return_value=False), \
                    mock.patch.object(preflight, "record_migration") as record_migration, \
                    mock.patch.object(preflight, "_run_legacy_bootstrap", side_effect=noisy_bootstrap):
                    lock_factory.return_value.__enter__.return_value = None
                    lock_factory.return_value.__exit__.return_value = None

                    with self.assertRaisesRegex(RuntimeError, "legacy startup bootstrap reported failures"):
                        preflight.run_startup_preflight(mode="migrate", print_fn=lambda _: None)

                record_migration.assert_not_called()

    def test_check_fails_when_required_version_missing(self):
        fake_engine = object()

        with mock.patch.object(preflight, "engine", fake_engine), \
            mock.patch.object(preflight, "startup_migration_advisory_lock") as lock_factory, \
            mock.patch.object(preflight, "has_migration", return_value=False) as has_migration:
            lock_factory.return_value.__enter__.return_value = None
            lock_factory.return_value.__exit__.return_value = None
            result = preflight.run_startup_preflight(mode="check", print_fn=lambda _: None)

        self.assertEqual(result, 1)
        lock_factory.assert_called_once_with(fake_engine)
        has_migration.assert_called_once_with(
            fake_engine,
            preflight.STARTUP_BOOTSTRAP_VERSION,
            preflight.STARTUP_BOOTSTRAP_CHECKSUM,
            create_table=False,
        )

    def test_check_passes_when_required_version_exists(self):
        fake_engine = object()

        with mock.patch.object(preflight, "engine", fake_engine), \
            mock.patch.object(preflight, "startup_migration_advisory_lock") as lock_factory, \
            mock.patch.object(preflight, "has_migration", return_value=True) as has_migration:
            lock_factory.return_value.__enter__.return_value = None
            lock_factory.return_value.__exit__.return_value = None
            result = preflight.run_startup_preflight(mode="check", print_fn=lambda _: None)

        self.assertEqual(result, 0)
        lock_factory.assert_called_once_with(fake_engine)
        has_migration.assert_called_once_with(
            fake_engine,
            preflight.STARTUP_BOOTSTRAP_VERSION,
            preflight.STARTUP_BOOTSTRAP_CHECKSUM,
            create_table=False,
        )

    def test_legacy_bootstrap_seeds_function_model_configs(self):
        source = (BACKEND_DIR / "preflight.py").read_text(encoding="utf-8")

        self.assertIn("_ensure_function_model_configs", source)

    def test_web_startup_does_not_seed_function_model_configs(self):
        source = (BACKEND_DIR / "main.py").read_text(encoding="utf-8")
        startup_body = source.split("async def startup_event():", 1)[1].split("# 关闭事件", 1)[0]

        self.assertNotIn("_ensure_function_model_configs", startup_body)


if __name__ == "__main__":
    unittest.main()
