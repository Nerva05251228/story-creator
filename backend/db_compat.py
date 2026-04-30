from sqlalchemy import inspect, text


def get_dialect_name(engine) -> str:
    return getattr(getattr(engine, "dialect", None), "name", "")


def is_sqlite_engine(engine) -> bool:
    return get_dialect_name(engine) == "sqlite"


def is_postgresql_engine(engine) -> bool:
    return get_dialect_name(engine) == "postgresql"


def table_exists(engine, table_name: str, schema: str = None) -> bool:
    return inspect(engine).has_table(table_name, schema=schema)


def get_table_columns(engine, table_name: str, schema: str = None) -> set[str]:
    if not table_exists(engine, table_name, schema=schema):
        return set()
    return {
        column["name"]
        for column in inspect(engine).get_columns(table_name, schema=schema)
    }


def boolean_sql(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def datetime_sql_for_dialect(dialect_name: str) -> str:
    normalized = (dialect_name or "").strip().lower()
    if normalized == "postgresql":
        return "TIMESTAMP"
    return "DATETIME"


def datetime_sql(engine) -> str:
    return datetime_sql_for_dialect(get_dialect_name(engine))


def quoted_table_name(table_name: str, schema: str = None) -> str:
    safe_table = table_name.replace('"', '""')
    if schema:
        safe_schema = schema.replace('"', '""')
        return f'"{safe_schema}"."{safe_table}"'
    return f'"{safe_table}"'


def rename_column_if_needed(
    engine,
    table_name: str,
    old_name: str,
    new_name: str,
    schema: str = None
) -> bool:
    columns = get_table_columns(engine, table_name, schema=schema)
    if old_name not in columns or new_name in columns:
        return False
    safe_old = old_name.replace('"', '""')
    safe_new = new_name.replace('"', '""')
    with engine.begin() as conn:
        conn.execute(
            text(
                f"ALTER TABLE {quoted_table_name(table_name, schema=schema)} "
                f'RENAME COLUMN "{safe_old}" TO "{safe_new}"'
            )
        )
    return True
