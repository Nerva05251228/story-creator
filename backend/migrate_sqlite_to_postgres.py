import argparse
import json
import sys

from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url

import models
from postgres_migration_common import (
    backup_sqlite_database,
    build_placeholder_rows,
    build_sqlite_url,
    create_db_engine,
    get_common_column_names,
    get_sorted_tables,
    iter_table_rows,
    reflect_table,
    resolve_sqlite_path,
    timestamped_report_path,
)


def _parse_args():
    parser = argparse.ArgumentParser(description="Migrate SQLite data into PostgreSQL without losing primary keys.")
    parser.add_argument("--source-sqlite", default="", help="Path to source SQLite database file.")
    parser.add_argument("--target-url", default="", help="Target PostgreSQL DATABASE_URL.")
    parser.add_argument("--batch-size", type=int, default=1000, help="Insert batch size.")
    return parser.parse_args()


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _ensure_empty_target(target_engine, tables):
    non_empty_tables = []
    inspector = inspect(target_engine)
    with target_engine.connect() as conn:
        for table in tables:
            if not inspector.has_table(table.name, schema=table.schema):
                continue
            row_count = conn.execute(select(func.count()).select_from(table)).scalar_one()
            if row_count:
                non_empty_tables.append({"table": table.name, "rows": row_count})
    if non_empty_tables:
        raise RuntimeError(
            "目标 PostgreSQL 不是空库，已拒绝迁移以避免覆盖数据: "
            + json.dumps(non_empty_tables, ensure_ascii=False)
        )


def _reset_postgres_sequences(target_conn, tables):
    for table in tables:
        primary_keys = list(table.primary_key.columns)
        if len(primary_keys) != 1:
            continue

        pk_column = primary_keys[0]
        try:
            if getattr(pk_column.type, "python_type", None) is not int:
                continue
        except NotImplementedError:
            continue

        full_table_name = table.name
        if table.schema:
            full_table_name = f"{table.schema}.{table.name}"

        target_conn.execute(
            text(
                f"""
                SELECT setval(
                    pg_get_serial_sequence(:table_name, :column_name),
                    COALESCE(MAX({_quote_identifier(pk_column.name)}), 1),
                    MAX({_quote_identifier(pk_column.name)}) IS NOT NULL
                )
                FROM {_quote_identifier(table.name)}
                """
            ),
            {
                "table_name": full_table_name,
                "column_name": pk_column.name,
            },
        )


def main():
    args = _parse_args()
    source_path = resolve_sqlite_path(args.source_sqlite or None)
    target_url = (args.target_url or "").strip()
    if not target_url:
        print("缺少 --target-url", file=sys.stderr)
        sys.exit(2)

    target_backend = make_url(target_url).get_backend_name()
    if target_backend != "postgresql":
        print("目标数据库必须是 PostgreSQL", file=sys.stderr)
        sys.exit(2)

    if not source_path.exists():
        print(f"源 SQLite 文件不存在: {source_path}", file=sys.stderr)
        sys.exit(2)

    backup_path = backup_sqlite_database(source_path)
    source_engine = create_db_engine(build_sqlite_url(backup_path))
    target_engine = create_db_engine(target_url)
    tables = get_sorted_tables()
    placeholder_rows = build_placeholder_rows(source_engine)
    source_inspector = inspect(source_engine)
    target_table_names = {table.name for table in tables}
    source_table_names = {
        name
        for name in source_inspector.get_table_names()
        if not str(name).startswith("sqlite_")
    }
    extra_source_tables = sorted(source_table_names - target_table_names)
    if extra_source_tables:
        raise RuntimeError(
            "源 SQLite 存在当前模型未覆盖的表，继续迁移会有丢数据风险: "
            + json.dumps(extra_source_tables, ensure_ascii=False)
        )

    report = {
        "source_sqlite": str(source_path),
        "source_backup": str(backup_path),
        "target_url_backend": target_backend,
        "batch_size": args.batch_size,
        "placeholder_row_counts": {
            table_name: len(rows)
            for table_name, rows in placeholder_rows.items()
        },
        "table_reports": [],
    }

    models.Base.metadata.create_all(bind=target_engine)
    _ensure_empty_target(target_engine, tables)

    with source_engine.connect() as source_conn, target_engine.begin() as target_conn:
        for target_table in tables:
            source_table = reflect_table(source_engine, target_table.name)
            if source_table is None:
                report["table_reports"].append(
                    {
                        "table": target_table.name,
                        "source_rows": 0,
                        "target_rows": 0,
                        "copied_rows": 0,
                        "status": "skipped_missing_source_table",
                    }
                )
                continue

            extra_source_columns = sorted(
                set(source_table.columns.keys()) - set(target_table.columns.keys())
            )
            if extra_source_columns:
                raise RuntimeError(
                    f"表 {target_table.name} 存在当前模型未覆盖的源列，继续迁移会有丢数据风险: "
                    + json.dumps(extra_source_columns, ensure_ascii=False)
                )

            column_names = get_common_column_names(source_table, target_table)
            source_rows = source_conn.execute(
                select(func.count()).select_from(source_table)
            ).scalar_one()

            copied_rows = 0
            if source_rows and not column_names:
                raise RuntimeError(f"表 {target_table.name} 没有可复制的公共列，已中止。")

            for batch in iter_table_rows(source_conn, source_table, column_names, batch_size=args.batch_size):
                target_conn.execute(target_table.insert(), batch)
                copied_rows += len(batch)

            extra_rows = placeholder_rows.get(target_table.name, [])
            if extra_rows:
                target_conn.execute(target_table.insert(), extra_rows)

            target_rows = target_conn.execute(
                select(func.count()).select_from(target_table)
            ).scalar_one()

            expected_target_rows = source_rows + len(extra_rows)
            if expected_target_rows != target_rows or copied_rows != source_rows:
                raise RuntimeError(
                    f"表 {target_table.name} 行数校验失败: source={source_rows}, copied={copied_rows}, "
                    f"placeholder={len(extra_rows)}, target={target_rows}"
                )

            report["table_reports"].append(
                {
                    "table": target_table.name,
                    "source_rows": source_rows,
                    "target_rows": target_rows,
                    "copied_rows": copied_rows,
                    "placeholder_rows": len(extra_rows),
                    "status": "ok",
                }
            )

        _reset_postgres_sequences(target_conn, tables)

    report["report_path"] = str(timestamped_report_path("postgres_migration"))
    with open(report["report_path"], "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
