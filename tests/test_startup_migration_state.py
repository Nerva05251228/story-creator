import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import startup_migration_state  # noqa: E402


class StartupMigrationStateTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )

    def tearDown(self):
        self.engine.dispose()

    def test_ensure_schema_migrations_table_is_idempotent_and_creates_required_columns(self):
        startup_migration_state.ensure_schema_migrations_table(self.engine)
        startup_migration_state.ensure_schema_migrations_table(self.engine)

        inspector = inspect(self.engine)
        self.assertTrue(inspector.has_table("schema_migrations"))

        columns = {
            column["name"]
            for column in inspector.get_columns("schema_migrations")
        }
        self.assertGreaterEqual(
            columns,
            {"version", "checksum", "description", "applied_at", "duration_ms"},
        )

    def test_has_migration_detects_recorded_versions(self):
        self.assertFalse(
            startup_migration_state.has_migration(self.engine, "202604300001")
        )

        startup_migration_state.record_migration(
            self.engine,
            version="202604300001",
            checksum="sha256:first",
            description="create example table",
            duration_ms=42,
        )

        self.assertTrue(
            startup_migration_state.has_migration(self.engine, "202604300001")
        )
        self.assertTrue(
            startup_migration_state.has_migration(
                self.engine,
                "202604300001",
                checksum="sha256:first",
            )
        )

    def test_has_migration_can_run_read_only_when_table_is_missing(self):
        self.assertFalse(
            startup_migration_state.has_migration(
                self.engine,
                "202604300001",
                create_table=False,
            )
        )

        inspector = inspect(self.engine)
        self.assertFalse(inspector.has_table("schema_migrations"))

    def test_has_migration_rejects_checksum_mismatch(self):
        startup_migration_state.record_migration(
            self.engine,
            version="202604300001",
            checksum="sha256:first",
            description="create example table",
            duration_ms=42,
        )

        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            startup_migration_state.has_migration(
                self.engine,
                "202604300001",
                checksum="sha256:changed",
            )

    def test_record_migration_is_idempotent_only_for_same_checksum(self):
        startup_migration_state.record_migration(
            self.engine,
            version="202604300001",
            checksum="sha256:first",
            description="create example table",
            duration_ms=42,
        )
        startup_migration_state.record_migration(
            self.engine,
            version="202604300001",
            checksum="sha256:first",
            description="create example table",
            duration_ms=42,
        )

        with self.engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar_one()
        self.assertEqual(count, 1)

        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            startup_migration_state.record_migration(
                self.engine,
                version="202604300001",
                checksum="sha256:changed",
                description="create example table",
                duration_ms=42,
            )

    def test_sqlite_advisory_lock_is_noop_context_manager(self):
        with startup_migration_state.startup_migration_advisory_lock(self.engine):
            with self.engine.connect() as conn:
                self.assertEqual(conn.execute(text("SELECT 1")).scalar_one(), 1)

    def test_helper_module_does_not_import_main(self):
        source = (BACKEND_DIR / "startup_migration_state.py").read_text(encoding="utf-8")

        self.assertNotIn("import main", source)
        self.assertNotIn("from main", source)


if __name__ == "__main__":
    unittest.main()
