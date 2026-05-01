from typing import Any, Dict, Optional

import models
from ai_config import DEFAULT_TEXT_MODEL_ID, RELAY_PROVIDER_KEY, resolve_ai_model_option
from text_relay_service import get_cached_models_payload, sync_models_from_upstream


FUNCTION_MODEL_DEFAULTS = [
    {"function_key": "detailed_storyboard_s1", "function_name": "详细分镜生成（Stage 1 初始分析）"},
    {"function_key": "detailed_storyboard_s2", "function_name": "详细分镜生成（Stage 2 主体提示词）"},
    {"function_key": "video_prompt", "function_name": "Sora 视频提示词生成"},
    {"function_key": "opening", "function_name": "精彩开头生成"},
    {"function_key": "narration", "function_name": "旁白/解说剧转换"},
    {"function_key": "managed_prompt_optimize", "function_name": "托管重试提示词优化"},
    {"function_key": "subject_prompt", "function_name": "主体提示词生成"},
]

OBSOLETE_FUNCTION_MODEL_KEYS = {"simple_storyboard"}

LEGACY_TEXT_PROVIDER_KEYS = {"", "openrouter", "yyds"}
LEGACY_TEXT_MODEL_VALUES = {
    "",
    "google/gemini-3.1-pro-preview",
    "google/gemini-3-pro-preview",
    "gemini-3.1-pro-preview",
    "gemini-3.1-pro-high",
    "gemini-3.0-pro",
    "gemini_pro_preview",
    "gemini_pro_high",
    "gemini_pro_3_0",
}


def _get_function_model_default_selection(function_key: str) -> Dict[str, Optional[str]]:
    _ = str(function_key or "").strip()
    return {
        "provider_key": RELAY_PROVIDER_KEY,
        "model_key": DEFAULT_TEXT_MODEL_ID,
        "model_id": DEFAULT_TEXT_MODEL_ID,
    }


def _normalize_function_model_id(row: models.FunctionModelConfig) -> str:
    provider_key = str(getattr(row, "provider_key", None) or "").strip().lower()
    model_id = str(getattr(row, "model_id", None) or "").strip()
    model_key = str(getattr(row, "model_key", None) or "").strip()

    if provider_key and provider_key != RELAY_PROVIDER_KEY:
        return DEFAULT_TEXT_MODEL_ID

    candidate = model_id or model_key
    if not candidate or candidate in LEGACY_TEXT_MODEL_VALUES:
        return DEFAULT_TEXT_MODEL_ID
    return candidate


def _ensure_function_model_configs(db):
    """确保所有功能配置行都存在，并统一迁移到 model_id-only 结构。"""
    if OBSOLETE_FUNCTION_MODEL_KEYS:
        db.query(models.FunctionModelConfig).filter(
            models.FunctionModelConfig.function_key.in_(tuple(OBSOLETE_FUNCTION_MODEL_KEYS))
        ).delete(synchronize_session=False)
    for item in FUNCTION_MODEL_DEFAULTS:
        default_selection = _get_function_model_default_selection(item["function_key"])
        row = db.query(models.FunctionModelConfig).filter(
            models.FunctionModelConfig.function_key == item["function_key"]
        ).first()
        if not row:
            db.add(models.FunctionModelConfig(
                function_key=item["function_key"],
                function_name=item["function_name"],
                provider_key=default_selection["provider_key"],
                model_key=default_selection["model_key"],
                model_id=default_selection["model_id"]
            ))
            continue

        row.function_name = item["function_name"]
        normalized_model_id = _normalize_function_model_id(row)
        row.provider_key = RELAY_PROVIDER_KEY
        row.model_key = normalized_model_id
        row.model_id = normalized_model_id
    db.commit()


def _serialize_function_model_config(row: models.FunctionModelConfig, db) -> Dict[str, Any]:
    resolved = resolve_ai_model_option(RELAY_PROVIDER_KEY, getattr(row, "model_id", None), db=db)
    return {
        "function_key": row.function_key,
        "function_name": row.function_name,
        "model_id": str(getattr(row, "model_id", None) or "").strip() or DEFAULT_TEXT_MODEL_ID,
        "resolved_model_key": resolved["model_key"],
        "resolved_model_id": resolved["model_id"],
        "resolved_model_label": resolved["label"],
    }


def get_model_configs_payload(db) -> Dict[str, Any]:
    _ensure_function_model_configs(db)
    rows = db.query(models.FunctionModelConfig).order_by(
        models.FunctionModelConfig.id.asc()
    ).all()
    cache_payload = get_cached_models_payload(db)
    return {
        "default_model": DEFAULT_TEXT_MODEL_ID,
        "models": cache_payload.get("models", []),
        "last_synced_at": cache_payload.get("last_synced_at"),
        "configs": [
            _serialize_function_model_config(r, db)
            for r in rows
        ]
    }
