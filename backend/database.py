import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SQLITE_PATH = BASE_DIR / "story_creator.db"


def _normalize_database_url(raw_url: str) -> str:
    value = (raw_url or "").strip()
    if not value:
        return f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql://", 1)
    return value


DATABASE_URL = _normalize_database_url(os.getenv("DATABASE_URL", ""))
DATABASE_DIALECT = make_url(DATABASE_URL).get_backend_name()
IS_SQLITE = DATABASE_DIALECT == "sqlite"


def _masked_database_url(url: str) -> str:
    try:
        parsed = make_url(url)
        if parsed.password is None:
            return str(parsed)
        return str(parsed.set(password="***"))
    except Exception:
        return url


def _create_sqlite_engine():
    return create_engine(
        DATABASE_URL,
        connect_args={
            "check_same_thread": False,
            "timeout": int(os.getenv("SQLITE_TIMEOUT_SECONDS", "60")),
        },
        pool_pre_ping=True,
        pool_size=int(os.getenv("SQLITE_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("SQLITE_MAX_OVERFLOW", "5")),
    )


def _create_postgresql_engine():
    connect_args = {}
    connect_timeout = int(os.getenv("DATABASE_CONNECT_TIMEOUT", "15"))
    if connect_timeout > 0:
        connect_args["connect_timeout"] = connect_timeout

    return create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=int(os.getenv("DATABASE_POOL_SIZE", "10")),
        max_overflow=int(os.getenv("DATABASE_MAX_OVERFLOW", "20")),
        pool_recycle=int(os.getenv("DATABASE_POOL_RECYCLE_SECONDS", "1800")),
        connect_args=connect_args,
    )


engine = _create_sqlite_engine() if IS_SQLITE else _create_postgresql_engine()
print(f"[database] dialect={DATABASE_DIALECT} url={_masked_database_url(DATABASE_URL)}")


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    if not IS_SQLITE:
        return
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(
        f"PRAGMA busy_timeout={int(os.getenv('SQLITE_BUSY_TIMEOUT_MS', '60000'))}"
    )
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
