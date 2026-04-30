import argparse
import json
import sys

from sqlalchemy import func, inspect, select
from sqlalchemy.engine import make_url

from postgres_migration_common import (
    build_placeholder_rows,
    build_sqlite_url,
    compute_table_hash,
    create_db_engine,
    get_common_column_names,
    get_sorted_tables,
    reflect_table,
    resolve_sqlite_path,
    timestamped_report_path,
)


def _parse_args():
    parser = argparse.ArgumentParser(description="Verify SQLite -> PostgreSQL migration with row counts and content hashes.")
    parser.add_argument("--source-sqlite", default="", help="Path to source SQLite database file.")
    parser.add_argument("--target-url", default="", help="Target PostgreSQL DATABASE_URL.")
    return parser.parse_args()


def main():
    args = _parse_args()
    source_path = resolve_sqlite_path(args.source_sqlite or None)
    target_url = (args.target_url or "").strip()
    if not target_url:
        print("缺少 --target-url", file=sys.stderr)
        sys.exit(2)
    if make_url(target_url).get_backend_name() != "postgresql":
        print("目标数据库必须是 PostgreSQL", file=sys.stderr)
        sys.exit(2)
    if not source_path.exists():
        print(f"源 SQLite 文件不存在: {source_path}", file=sys.stderr)
        sys.exit(2)

    source_engine = create_db_engine(build_sqlite_url(source_path))
    target_engine = create_db_engine(target_url)
    placeholder_rows = build_placeholder_rows(source_engine)
    source_inspector = inspect(source_engine)
    target_table_names = {table.name for table in get_sorted_tables()}
    source_table_names = {
        name
        for name in source_inspector.get_table_names()
        if not str(name).startswith("sqlite_")
    }
    report = {
        "source_sqlite": str(source_path),
        "target_url_backend": "postgresql",
        "placeholder_row_counts": {
            table_name: len(rows)
            for table_name, rows in placeholder_rows.items()
        },
        "table_reports": [],
    }
    has_mismatch = False

    extra_source_tables = sorted(source_table_names - target_table_names)
    if extra_source_tables:
        has_mismatch = True
        report["extra_source_tables"] = extra_source_tables

    for target_table in get_sorted_tables():
        source_table = reflect_table(source_engine, target_table.name)
        target_reflected = reflect_table(target_engine, target_table.name)

        if source_table is None:
            report["table_reports"].append(
                {"table": target_table.name, "status": "skipped_missing_source_table"}
            )
            continue
        if target_reflected is None:
            report["table_reports"].append(
                {"table": target_table.name, "status": "missing_target_table"}
            )
            has_mismatch = True
            continue

        extra_source_columns = sorted(
            set(source_table.columns.keys()) - set(target_table.columns.keys())
        )
        if extra_source_columns:
            report["table_reports"].append(
                {
                    "table": target_table.name,
                    "status": "extra_source_columns",
                    "extra_source_columns": extra_source_columns,
                }
            )
            has_mismatch = True
            continue

        column_names = get_common_column_names(source_table, target_table)
        with source_engine.connect() as source_conn, target_engine.connect() as target_conn:
            source_rows = source_conn.execute(select(func.count()).select_from(source_table)).scalar_one()
            target_rows = target_conn.execute(select(func.count()).select_from(target_reflected)).scalar_one()

        source_hash = compute_table_hash(source_engine, source_table, column_names)
        table_placeholder_rows = placeholder_rows.get(target_table.name, [])
        placeholder_ids = {
            int(row["id"])
            for row in table_placeholder_rows
            if isinstance(row, dict) and row.get("id") is not None
        }
        expected_target_rows = source_rows + len(table_placeholder_rows)
        target_hash = compute_table_hash(
            target_engine,
            target_reflected,
            column_names,
            exclude_primary_ids=placeholder_ids,
        )
        matched = source_rows == (target_rows - len(table_placeholder_rows)) and expected_target_rows == target_rows and source_hash == target_hash
        if not matched:
            has_mismatch = True

        report["table_reports"].append(
            {
                "table": target_table.name,
                "source_rows": source_rows,
                "target_rows": target_rows,
                "expected_target_rows": expected_target_rows,
                "placeholder_rows": len(table_placeholder_rows),
                "source_hash": source_hash,
                "target_hash": target_hash,
                "status": "ok" if matched else "mismatch",
            }
        )

    report["report_path"] = str(timestamped_report_path("postgres_migration_verify"))
    with open(report["report_path"], "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if has_mismatch:
        sys.exit(1)


if __name__ == "__main__":
    main()
