RUNTIME_POSTGRES_ALTER_SKIP_COLUMNS = {
    ("storyboard_shots", "use_uploaded_scene_image"),
    ("storyboard_shots", "duration_override_enabled"),
}


def should_apply_runtime_postgres_alter(table_name: str, column_name: str) -> bool:
    normalized_key = ((table_name or "").strip().lower(), (column_name or "").strip().lower())
    return normalized_key not in RUNTIME_POSTGRES_ALTER_SKIP_COLUMNS
