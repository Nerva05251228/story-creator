import hashlib
import json
import os
import sqlite3
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, inspect, select, func

import models


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SQLITE_PATH = BASE_DIR / "story_creator.db"
BACKUP_DIR = BASE_DIR / "migration_backups"
REPORT_DIR = BASE_DIR / "migration_reports"


def normalize_database_url(raw_url: str) -> str:
    value = (raw_url or "").strip()
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql://", 1)
    return value


def build_sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def resolve_sqlite_path(raw_path: str | None = None) -> Path:
    if raw_path:
        return Path(raw_path).expanduser().resolve()
    return DEFAULT_SQLITE_PATH.resolve()


def create_db_engine(database_url: str):
    normalized_url = normalize_database_url(database_url)
    if normalized_url.startswith("sqlite:"):
        return create_engine(
            normalized_url,
            connect_args={"check_same_thread": False, "timeout": 60},
        )
    return create_engine(
        normalized_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 15},
    )


def backup_sqlite_database(source_path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"{source_path.stem}_{timestamp}.db"

    source_conn = sqlite3.connect(str(source_path))
    try:
        backup_conn = sqlite3.connect(str(backup_path))
        try:
            source_conn.backup(backup_conn)
        finally:
            backup_conn.close()
    finally:
        source_conn.close()

    return backup_path


def get_sorted_tables():
    return list(models.Base.metadata.sorted_tables)


def reflect_table(engine, table_name: str):
    if not inspect(engine).has_table(table_name):
        return None
    return Table(table_name, MetaData(), autoload_with=engine, resolve_fks=False)


def get_common_column_names(source_table, target_table) -> list[str]:
    source_columns = set(source_table.columns.keys())
    return [column.name for column in target_table.columns if column.name in source_columns]


def build_order_by_columns(table, column_names: list[str]):
    primary_keys = [column.name for column in table.primary_key.columns if column.name in column_names]
    if primary_keys:
        return [table.c[name] for name in primary_keys]
    return [table.c[name] for name in column_names]


def iter_table_rows(source_conn, source_table, column_names: list[str], batch_size: int = 1000):
    selectable_columns = [source_table.c[name] for name in column_names]
    stmt = select(*selectable_columns)
    order_by_columns = build_order_by_columns(source_table, column_names)
    if order_by_columns:
        stmt = stmt.order_by(*order_by_columns)

    result = source_conn.execution_options(stream_results=True).execute(stmt)
    batch = []
    for row in result.mappings():
        batch.append({name: row[name] for name in column_names})
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def normalize_value(value):
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="microseconds")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def compute_table_hash(engine, table, column_names: list[str], exclude_primary_ids: set[int] | None = None) -> str:
    if not column_names:
        return hashlib.sha256(b"").hexdigest()

    digest = hashlib.sha256()
    exclude_primary_ids = exclude_primary_ids or set()
    with engine.connect() as conn:
        stmt = select(*[table.c[name] for name in column_names])
        if exclude_primary_ids and "id" in table.columns:
            stmt = stmt.where(table.c.id.not_in(sorted(exclude_primary_ids)))
        order_by_columns = build_order_by_columns(table, column_names)
        if order_by_columns:
            stmt = stmt.order_by(*order_by_columns)
        result = conn.execution_options(stream_results=True).execute(stmt)
        for row in result.mappings():
            normalized = {name: normalize_value(row[name]) for name in column_names}
            digest.update(
                json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            )
            digest.update(b"\n")
    return digest.hexdigest()


def timestamped_report_path(prefix: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return REPORT_DIR / f"{prefix}_{timestamp}.json"


def _get_table_max_id(conn, table) -> int:
    if table is None or "id" not in table.columns:
        return 0
    value = conn.execute(select(func.max(table.c.id))).scalar_one()
    return int(value or 0)


def _fetch_missing_ids(conn, child_table, child_column: str, parent_table, parent_column: str = "id") -> list[int]:
    if child_table is None or parent_table is None:
        return []

    child_col = child_table.c[child_column]
    parent_col = parent_table.c[parent_column]
    rows = conn.execute(
        select(child_col)
        .select_from(child_table.outerjoin(parent_table, child_col == parent_col))
        .where(child_col.is_not(None))
        .where(parent_col.is_(None))
        .distinct()
        .order_by(child_col.asc())
    ).scalars().all()
    return [int(value) for value in rows if value is not None]


def build_placeholder_rows(source_engine) -> dict[str, list[dict]]:
    tables = {
        table.name: reflect_table(source_engine, table.name)
        for table in get_sorted_tables()
    }

    with source_engine.connect() as conn:
        missing_episode_ids = _fetch_missing_ids(
            conn,
            tables.get("managed_sessions"),
            "episode_id",
            tables.get("episodes"),
        )
        missing_style_template_ids = _fetch_missing_ids(
            conn,
            tables.get("subject_cards"),
            "style_template_id",
            tables.get("style_templates"),
        )

        missing_subject_card_ids = sorted({
            *_fetch_missing_ids(conn, tables.get("card_images"), "card_id", tables.get("subject_cards")),
            *_fetch_missing_ids(conn, tables.get("generated_images"), "card_id", tables.get("subject_cards")),
        })

        missing_storyboard_shot_ids = sorted({
            *_fetch_missing_ids(conn, tables.get("managed_tasks"), "shot_id", tables.get("storyboard_shots")),
            *_fetch_missing_ids(conn, tables.get("shot_collages"), "shot_id", tables.get("storyboard_shots")),
            *_fetch_missing_ids(conn, tables.get("shot_detail_images"), "shot_id", tables.get("storyboard_shots")),
            *_fetch_missing_ids(conn, tables.get("storyboard2_shots"), "source_shot_id", tables.get("storyboard_shots")),
            *_fetch_missing_ids(conn, tables.get("shot_videos"), "shot_id", tables.get("storyboard_shots")),
        })

        needs_placeholder_user = bool(
            missing_episode_ids or missing_subject_card_ids or missing_storyboard_shot_ids
        )
        if not needs_placeholder_user and not missing_style_template_ids:
            return {}

        now = datetime.utcnow()
        placeholder_rows: dict[str, list[dict]] = {}

        if needs_placeholder_user:
            users_table = tables["users"]
            scripts_table = tables["scripts"]
            libraries_table = tables["story_libraries"]
            episodes_table = tables["episodes"]

            placeholder_user_id = _get_table_max_id(conn, users_table) + 1
            placeholder_script_id = _get_table_max_id(conn, scripts_table) + 1
            next_episode_seed = max(
                _get_table_max_id(conn, episodes_table),
                max(missing_episode_ids, default=0),
            ) + 1
            placeholder_episode_id = next_episode_seed
            placeholder_library_id = _get_table_max_id(conn, libraries_table) + 1

            placeholder_rows["users"] = [{
                "id": placeholder_user_id,
                "username": "__migration_placeholder_user__",
                "token": "__migration_placeholder_token__",
                "created_at": now,
                "sora_rule": "准则：不要出现字幕",
                "password_hash": "",
                "password_plain": "123456",
            }]
            placeholder_rows["scripts"] = [{
                "id": placeholder_script_id,
                "user_id": placeholder_user_id,
                "name": "__migration_placeholder_script__",
                "created_at": now,
                "sora_prompt_style": "",
                "style_template": "",
                "narration_template": "",
                "voiceover_shared_data": "",
            }]

            episode_rows = [
                {
                    "id": episode_id,
                    "script_id": placeholder_script_id,
                    "name": f"[迁移占位]已删除片段 {episode_id}",
                    "content": "",
                    "created_at": now,
                }
                for episode_id in missing_episode_ids
            ]
            if missing_storyboard_shot_ids:
                episode_rows.append(
                    {
                        "id": placeholder_episode_id,
                        "script_id": placeholder_script_id,
                        "name": "[迁移占位]已删除镜头归档片段",
                        "content": "",
                        "created_at": now,
                    }
                )
            if episode_rows:
                placeholder_rows["episodes"] = episode_rows

            if missing_subject_card_ids:
                placeholder_rows["story_libraries"] = [{
                    "id": placeholder_library_id,
                    "user_id": placeholder_user_id,
                    "episode_id": placeholder_episode_id if missing_storyboard_shot_ids else None,
                    "name": "[迁移占位]已删除主体库",
                    "description": "用于承接 SQLite 历史孤儿主体引用的占位库",
                    "created_at": now,
                }]
                placeholder_rows["subject_cards"] = [
                    {
                        "id": card_id,
                        "library_id": placeholder_library_id,
                        "name": f"[迁移占位]已删除主体 {card_id}",
                        "alias": "",
                        "card_type": "角色",
                        "linked_card_id": None,
                        "ai_prompt": "",
                        "ai_prompt_status": None,
                        "role_personality": "",
                        "style_template_id": None,
                        "is_protagonist": False,
                        "protagonist_gender": "",
                        "is_generating_images": False,
                        "generating_count": 0,
                        "created_at": now,
                    }
                    for card_id in missing_subject_card_ids
                ]

            if missing_storyboard_shot_ids:
                placeholder_rows["storyboard_shots"] = [
                    {
                        "id": shot_id,
                        "episode_id": placeholder_episode_id,
                        "shot_number": int(shot_id),
                        "stable_id": f"migration-placeholder-shot-{shot_id}",
                        "variant_index": 0,
                        "prompt_template": "",
                        "script_excerpt": "",
                        "storyboard_video_prompt": "",
                        "storyboard_audio_prompt": "",
                        "storyboard_dialogue": "",
                        "scene_override": "",
                        "scene_override_locked": False,
                        "sora_prompt": "",
                        "sora_prompt_status": "idle",
                        "selected_card_ids": "[]",
                        "video_path": "",
                        "thumbnail_video_path": "",
                        "video_status": "idle",
                        "task_id": "",
                        "aspect_ratio": "16:9",
                        "duration": 15,
                        "provider": "yijia",
                        "cdn_uploaded": False,
                        "video_submitted_at": None,
                        "video_error_message": "",
                        "price": 0,
                        "timeline_json": "",
                        "detail_image_prompt_overrides": "{}",
                        "storyboard_image_path": "",
                        "storyboard_image_status": "idle",
                        "storyboard_image_task_id": "",
                        "storyboard_image_model": "",
                        "created_at": now,
                    }
                    for shot_id in missing_storyboard_shot_ids
                ]

        if missing_style_template_ids:
            placeholder_rows["style_templates"] = [
                {
                    "id": style_template_id,
                    "name": f"[迁移占位]已删除风格模板 {style_template_id}",
                    "content": "",
                    "is_default": False,
                    "created_at": now,
                }
                for style_template_id in missing_style_template_ids
            ]

        return placeholder_rows
