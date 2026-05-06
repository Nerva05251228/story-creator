import time

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


SQLITE_LOCK_RETRY_DELAYS = (0.3, 0.8, 1.5, 3.0)


def rollback_quietly(db: Session):
    try:
        db.rollback()
    except Exception:
        pass


def is_sqlite_lock_error(db: Session, exc: Exception) -> bool:
    dialect = getattr(getattr(db, "bind", None), "dialect", None)
    dialect_name = getattr(dialect, "name", "")
    return dialect_name == "sqlite" and "database is locked" in str(exc).lower()


def commit_with_retry(
    db: Session,
    prepare_fn=None,
    context: str = "db commit",
):
    max_retries = len(SQLITE_LOCK_RETRY_DELAYS)

    for attempt in range(max_retries + 1):
        if prepare_fn:
            prepare_fn()
        try:
            db.commit()
            return
        except OperationalError as exc:
            rollback_quietly(db)
            if not is_sqlite_lock_error(db, exc) or attempt >= max_retries:
                raise
            delay = SQLITE_LOCK_RETRY_DELAYS[attempt]
            print(f"[db] {context} 遇到 SQLite 写锁，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries})")
            time.sleep(delay)
        except Exception:
            rollback_quietly(db)
            raise
