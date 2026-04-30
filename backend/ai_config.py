"""
Centralized text-model configuration for the LLM Relay server.

The admin surface now configures only `function -> model_id`.
Provider selection is kept as a single fixed logical provider: `relay`.
"""

import os
from typing import Any, Dict, List, Optional

from env_config import get_first_env, get_int_env, load_app_env, require_real_env


load_app_env()


RELAY_PROVIDER_KEY = "relay"
DEFAULT_AI_PROVIDER = RELAY_PROVIDER_KEY
PUBLIC_AI_PROVIDER_KEYS = (RELAY_PROVIDER_KEY,)
DEFAULT_TEXT_MODEL_ID = "gemini-3.1-pro"

RELAY_BASE_URL = str(
    get_first_env("TEXT_RELAY_BASE_URL", "LLM_RELAY_BASE_URL", default="")
).rstrip("/")
RELAY_API_KEY = str(
    get_first_env("TEXT_RELAY_API_KEY", "LLM_RELAY_API_KEY", default="")
).strip()
RELAY_TIMEOUT = get_int_env("TEXT_RELAY_TIMEOUT_SECONDS", get_int_env("LLM_RELAY_TIMEOUT_SECONDS", 120))

RELAY_CHAT_COMPLETIONS_URL = f"{RELAY_BASE_URL}/v1/chat/completions" if RELAY_BASE_URL else ""
RELAY_MODELS_URL = f"{RELAY_BASE_URL}/v1/models" if RELAY_BASE_URL else ""
RELAY_TASKS_URL_PREFIX = f"{RELAY_BASE_URL}/v1/tasks" if RELAY_BASE_URL else ""


def _require_relay_base_url() -> str:
    return require_real_env("TEXT_RELAY_BASE_URL", "LLM_RELAY_BASE_URL").rstrip("/")


def _require_relay_api_key() -> str:
    return require_real_env("TEXT_RELAY_API_KEY", "LLM_RELAY_API_KEY")


def get_relay_base_url() -> str:
    return _require_relay_base_url()


def get_relay_api_key() -> str:
    return _require_relay_api_key()


def get_relay_chat_completions_url() -> str:
    return f"{get_relay_base_url()}/v1/chat/completions"


def get_relay_models_url() -> str:
    return f"{get_relay_base_url()}/v1/models"


def get_relay_tasks_url_prefix() -> str:
    return f"{get_relay_base_url()}/v1/tasks"


FUNCTION_AI_DEFAULTS: Dict[str, Dict[str, Optional[str]]] = {
    "video_prompt": {
        "model_id": DEFAULT_TEXT_MODEL_ID,
    }
}

LEGACY_DEFAULT_MODEL_VALUES = {
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


def normalize_ai_provider_key(provider_key: Optional[str]) -> str:
    _ = provider_key
    return RELAY_PROVIDER_KEY


def get_default_ai_provider_key() -> str:
    return RELAY_PROVIDER_KEY


def get_function_ai_default(function_key: Optional[str]) -> Dict[str, Optional[str]]:
    normalized_key = str(function_key or "").strip()
    if normalized_key in FUNCTION_AI_DEFAULTS:
        return dict(FUNCTION_AI_DEFAULTS[normalized_key])
    return {"model_id": DEFAULT_TEXT_MODEL_ID}


def get_ai_provider_runtime_config(provider_key: Optional[str]) -> Dict[str, Any]:
    _ = provider_key
    relay_base_url = _require_relay_base_url()
    return {
        "provider_key": RELAY_PROVIDER_KEY,
        "provider_name": "LLM Relay",
        "api_url": f"{relay_base_url}/v1/chat/completions",
        "api_key": _require_relay_api_key(),
        "timeout": RELAY_TIMEOUT,
        "request_mode": "relay_async_submit",
        "supports_response_format_json_object": True,
    }


def build_ai_debug_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    debug_source = dict(config or {})
    return {
        "provider_key": RELAY_PROVIDER_KEY,
        "provider_name": str(debug_source.get("provider_name") or "LLM Relay"),
        "api_url": str(debug_source.get("api_url") or "").strip(),
        "model": str(debug_source.get("model") or "").strip(),
        "model_id": str(debug_source.get("model_id") or debug_source.get("model") or "").strip(),
        "model_key": str(debug_source.get("model_key") or debug_source.get("model_id") or debug_source.get("model") or "").strip(),
        "timeout": debug_source.get("timeout"),
        "request_mode": str(debug_source.get("request_mode") or "").strip(),
    }


def _query_relay_model_rows(db=None):
    try:
        from database import SessionLocal
        import models as _models
    except ImportError:
        from .database import SessionLocal
        from . import models as _models

    created_db = False
    session = db
    if session is None:
        session = SessionLocal()
        created_db = True

    try:
        return session.query(_models.RelayModel).order_by(_models.RelayModel.model_id.asc()).all()
    finally:
        if created_db:
            session.close()


def _serialize_relay_model_row(row, *, is_default: bool = False) -> Dict[str, Any]:
    raw_metadata = str(getattr(row, "raw_metadata", "") or "")
    description = ""
    try:
        import json

        raw_json = json.loads(raw_metadata) if raw_metadata else {}
        description = str(raw_json.get("description") or raw_json.get("name") or "")
    except Exception:
        description = ""

    model_id = str(getattr(row, "model_id", "") or "").strip()
    return {
        "provider_key": RELAY_PROVIDER_KEY,
        "model_key": model_id,
        "model_id": model_id,
        "label": model_id,
        "description": description,
        "context_length": 0,
        "pricing_prompt": "0",
        "pricing_completion": "0",
        "modality": "text->text",
        "source": "cache",
        "is_default": bool(is_default),
        "owned_by": str(getattr(row, "owned_by", "") or "").strip(),
        "available_providers_count": int(getattr(row, "available_providers_count", 0) or 0),
    }


def _build_fallback_model_option(model_id: Optional[str] = None) -> Dict[str, Any]:
    normalized_model_id = str(model_id or DEFAULT_TEXT_MODEL_ID).strip() or DEFAULT_TEXT_MODEL_ID
    return {
        "provider_key": RELAY_PROVIDER_KEY,
        "model_key": normalized_model_id,
        "model_id": normalized_model_id,
        "label": normalized_model_id,
        "description": "Relay default fallback",
        "context_length": 0,
        "pricing_prompt": "0",
        "pricing_completion": "0",
        "modality": "text->text",
        "source": "fallback",
        "is_default": normalized_model_id == DEFAULT_TEXT_MODEL_ID,
        "owned_by": "",
        "available_providers_count": 0,
    }


def get_provider_model_options(provider_key: Optional[str], db=None) -> List[Dict[str, Any]]:
    _ = provider_key
    rows = _query_relay_model_rows(db=db)
    options = [
        _serialize_relay_model_row(row, is_default=str(getattr(row, "model_id", "") or "").strip() == DEFAULT_TEXT_MODEL_ID)
        for row in rows
    ]
    if not any(item["model_id"] == DEFAULT_TEXT_MODEL_ID for item in options):
        options.append(_build_fallback_model_option(DEFAULT_TEXT_MODEL_ID))
    return options


def resolve_ai_model_option(provider_key: Optional[str], model_key: Optional[str] = None, db=None) -> Dict[str, Any]:
    _ = provider_key
    requested_key = str(model_key or "").strip()
    if requested_key in LEGACY_DEFAULT_MODEL_VALUES:
        requested_key = DEFAULT_TEXT_MODEL_ID

    options = get_provider_model_options(RELAY_PROVIDER_KEY, db=db)
    if requested_key:
        for option in options:
            if option["model_key"] == requested_key or option["model_id"] == requested_key:
                return option
        return _build_fallback_model_option(requested_key)

    for option in options:
        if option.get("is_default"):
            return option

    return _build_fallback_model_option(DEFAULT_TEXT_MODEL_ID)


def get_ai_provider_public_configs() -> List[Dict[str, Any]]:
    return [
        {
            "provider_key": RELAY_PROVIDER_KEY,
            "provider_name": "LLM Relay",
            "supports_model_sync": True,
            "catalog_source": "database",
            "supports_response_format_json_object": True,
            "default_model_key": DEFAULT_TEXT_MODEL_ID,
            "default_model_id": DEFAULT_TEXT_MODEL_ID,
        }
    ]


def get_ai_provider_catalog(provider_key: Optional[str], db=None) -> Dict[str, Any]:
    _ = provider_key
    models = get_provider_model_options(RELAY_PROVIDER_KEY, db=db)
    synced_at = None
    rows = _query_relay_model_rows(db=db)
    if rows:
        latest = max((row.synced_at for row in rows if getattr(row, "synced_at", None)), default=None)
        synced_at = latest.isoformat() if latest else None
    return {
        "provider_key": RELAY_PROVIDER_KEY,
        "provider_name": "LLM Relay",
        "supports_model_sync": True,
        "default_model_key": DEFAULT_TEXT_MODEL_ID,
        "default_model_id": DEFAULT_TEXT_MODEL_ID,
        "synced_at": synced_at,
        "total": len(models),
        "models": models,
    }


def get_ai_config(function_key: Optional[str] = None) -> Dict[str, Any]:
    function_default = get_function_ai_default(function_key)
    selected_model_id = str(function_default.get("model_id") or DEFAULT_TEXT_MODEL_ID).strip() or DEFAULT_TEXT_MODEL_ID

    if function_key:
        try:
            try:
                from database import SessionLocal
                import models as _models
            except ImportError:
                from .database import SessionLocal
                from . import models as _models

            db = SessionLocal()
            try:
                row = db.query(_models.FunctionModelConfig).filter(
                    _models.FunctionModelConfig.function_key == function_key
                ).first()
                if row:
                    stored_model_id = str(getattr(row, "model_id", None) or "").strip()
                    stored_provider_key = str(getattr(row, "provider_key", None) or "").strip().lower()
                    if stored_provider_key and stored_provider_key != RELAY_PROVIDER_KEY:
                        stored_model_id = DEFAULT_TEXT_MODEL_ID
                    if stored_model_id:
                        selected_model_id = stored_model_id
            finally:
                db.close()
        except Exception:
            selected_model_id = str(function_default.get("model_id") or DEFAULT_TEXT_MODEL_ID).strip() or DEFAULT_TEXT_MODEL_ID

    runtime = get_ai_provider_runtime_config(RELAY_PROVIDER_KEY)
    resolved_model = resolve_ai_model_option(RELAY_PROVIDER_KEY, selected_model_id)
    runtime.update(
        {
            "model": resolved_model["model_id"],
            "model_id": resolved_model["model_id"],
            "model_key": resolved_model["model_key"],
            "model_label": resolved_model["label"],
        }
    )
    return runtime


if __name__ == "__main__":
    config = get_ai_config()
    print("=" * 60)
    print(f"Current provider: {config['provider_key']}")
    print("=" * 60)
    print(f"API URL: {config['api_url']}")
    print(f"Model: {config['model']}")
    print(f"Timeout: {config['timeout']}s")
    print("API Key: [set]")
    print("=" * 60)
