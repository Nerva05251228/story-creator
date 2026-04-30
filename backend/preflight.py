import argparse
import io
import os
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from typing import Callable

import models
from database import engine
from startup_migration_state import (
    has_migration,
    record_migration,
    startup_migration_advisory_lock,
)


STARTUP_BOOTSTRAP_VERSION = "202604300001_startup_bootstrap_baseline"
STARTUP_BOOTSTRAP_CHECKSUM = "sha256:startup-bootstrap-baseline-20260430"
STARTUP_BOOTSTRAP_DESCRIPTION = "Run legacy startup schema/data bootstrap once before workers start."
LEGACY_BOOTSTRAP_FAILURE_MARKERS = (
    "失败",
    "出错",
    "错误",
    "????",
    "ensure_",
    "traceback",
    "exception",
    " failed",
    " failure",
    " error",
)


def _ensure_runtime_directories() -> None:
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("videos", exist_ok=True)
    os.makedirs("../frontend", exist_ok=True)


def run_startup_preflight(mode: str = "migrate", print_fn: Callable[[str], None] = print) -> int:
    normalized_mode = str(mode or "migrate").strip().lower()
    if normalized_mode not in {"migrate", "check"}:
        raise ValueError("mode must be 'migrate' or 'check'")

    if normalized_mode == "check":
        with startup_migration_advisory_lock(engine):
            if has_migration(
                engine,
                STARTUP_BOOTSTRAP_VERSION,
                STARTUP_BOOTSTRAP_CHECKSUM,
                create_table=False,
            ):
                print_fn(f"[preflight] {STARTUP_BOOTSTRAP_VERSION} applied")
                return 0
            print_fn(f"[preflight] missing required migration: {STARTUP_BOOTSTRAP_VERSION}")
            return 1

    with startup_migration_advisory_lock(engine):
        if has_migration(engine, STARTUP_BOOTSTRAP_VERSION, STARTUP_BOOTSTRAP_CHECKSUM):
            print_fn(f"[preflight] {STARTUP_BOOTSTRAP_VERSION} already applied")
            return 0

        started_at = time.monotonic()
        _run_legacy_bootstrap_checked(print_fn)
        duration_ms = int((time.monotonic() - started_at) * 1000)
        record_migration(
            engine,
            version=STARTUP_BOOTSTRAP_VERSION,
            checksum=STARTUP_BOOTSTRAP_CHECKSUM,
            description=STARTUP_BOOTSTRAP_DESCRIPTION,
            duration_ms=duration_ms,
        )
        print_fn(f"[preflight] {STARTUP_BOOTSTRAP_VERSION} applied in {duration_ms}ms")
        return 0


def _run_legacy_bootstrap_checked(print_fn: Callable[[str], None]) -> None:
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        _run_legacy_bootstrap()

    captured = output.getvalue()
    if captured:
        for line in captured.rstrip().splitlines():
            print_fn(line)
    _raise_if_legacy_bootstrap_reported_failure(captured)


def _raise_if_legacy_bootstrap_reported_failure(output: str) -> None:
    for line in str(output or "").splitlines():
        lowered_line = line.lower()
        if any(marker in line or marker in lowered_line for marker in LEGACY_BOOTSTRAP_FAILURE_MARKERS):
            raise RuntimeError(f"legacy startup bootstrap reported failures: {line}")


def _run_legacy_bootstrap() -> None:
    import importlib

    models.Base.metadata.create_all(bind=engine)
    _ensure_runtime_directories()
    main = importlib.import_module("main")
    main.run_startup_bootstrap()
    db = main.SessionLocal()
    try:
        main._ensure_function_model_configs(db)
    finally:
        db.close()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run startup preflight before web or poller processes.")
    parser.add_argument(
        "mode",
        nargs="?",
        default="migrate",
        choices=("migrate", "check"),
        help="Use migrate before web startup and check before poller startup.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    return run_startup_preflight(mode=args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
