from contextlib import contextmanager

from sqlalchemy import inspect
from sqlalchemy import text


SCHEMA_MIGRATIONS_TABLE = "schema_migrations"
STARTUP_MIGRATION_LOCK_ID = 2026043001


def _dialect_name(engine) -> str:
    return getattr(getattr(engine, "dialect", None), "name", "")


def _timestamp_type_sql(engine) -> str:
    if _dialect_name(engine) == "postgresql":
        return "TIMESTAMP"
    return "DATETIME"


def ensure_schema_migrations_table(engine) -> None:
    timestamp_type = _timestamp_type_sql(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE} (
                    version VARCHAR(255) PRIMARY KEY,
                    checksum VARCHAR(255) NOT NULL,
                    description TEXT NOT NULL,
                    applied_at {timestamp_type} NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    duration_ms INTEGER NOT NULL
                )
                """
            )
        )


def _get_migration(conn, version: str):
    return conn.execute(
        text(
            f"""
            SELECT version, checksum
            FROM {SCHEMA_MIGRATIONS_TABLE}
            WHERE version = :version
            """
        ),
        {"version": version},
    ).mappings().first()


def _raise_checksum_mismatch(version: str, recorded_checksum: str, requested_checksum: str) -> None:
    raise RuntimeError(
        "schema migration checksum mismatch for "
        f"version {version}: recorded {recorded_checksum!r}, requested {requested_checksum!r}"
    )


def schema_migrations_table_exists(engine) -> bool:
    return inspect(engine).has_table(SCHEMA_MIGRATIONS_TABLE)


def has_migration(engine, version, checksum=None, create_table: bool = True) -> bool:
    if create_table:
        ensure_schema_migrations_table(engine)
    elif not schema_migrations_table_exists(engine):
        return False
    normalized_version = str(version)
    with engine.connect() as conn:
        row = _get_migration(conn, normalized_version)

    if row is None:
        return False
    if checksum is not None and row["checksum"] != str(checksum):
        _raise_checksum_mismatch(normalized_version, row["checksum"], str(checksum))
    return True


def record_migration(engine, version, checksum, description, duration_ms) -> None:
    ensure_schema_migrations_table(engine)
    normalized_version = str(version)
    normalized_checksum = str(checksum)

    with engine.begin() as conn:
        row = _get_migration(conn, normalized_version)
        if row is not None:
            if row["checksum"] != normalized_checksum:
                _raise_checksum_mismatch(
                    normalized_version,
                    row["checksum"],
                    normalized_checksum,
                )
            return

        conn.execute(
            text(
                f"""
                INSERT INTO {SCHEMA_MIGRATIONS_TABLE}
                    (version, checksum, description, duration_ms)
                VALUES
                    (:version, :checksum, :description, :duration_ms)
                """
            ),
            {
                "version": normalized_version,
                "checksum": normalized_checksum,
                "description": str(description),
                "duration_ms": int(duration_ms),
            },
        )


@contextmanager
def startup_migration_advisory_lock(engine, lock_id: int = STARTUP_MIGRATION_LOCK_ID):
    if _dialect_name(engine) != "postgresql":
        yield
        return

    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": int(lock_id)})
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": int(lock_id)})


__all__ = [
    "SCHEMA_MIGRATIONS_TABLE",
    "STARTUP_MIGRATION_LOCK_ID",
    "ensure_schema_migrations_table",
    "has_migration",
    "record_migration",
    "schema_migrations_table_exists",
    "startup_migration_advisory_lock",
]
