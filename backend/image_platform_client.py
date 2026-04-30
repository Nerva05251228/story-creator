import copy
import os
from typing import Any, Dict, List, Optional

import requests


DEFAULT_IMAGE_PLATFORM_BASE_URL = "https://ne.mocatter.cn/api/image"
DEFAULT_IMAGE_PLATFORM_API_TOKEN = "sk-PhyClrwsJ4OPRff-0xr306P4uwA0kYKam_RL_GxKLtI"

_MODEL_CATALOG_CACHE = None
_MODEL_FIELDS = (
    "key",
    "model",
    "display_name",
    "default_provider",
    "fallback_providers",
    "ratios",
    "resolutions",
    "supports_reference",
    "actions",
)
_PROVIDER_FIELDS = (
    "provider",
    "upstream_model",
    "ratios",
    "resolutions",
    "supports_reference",
    "actions",
    "cost",
    "priority",
    "enabled",
)
_BLOCKED_MODEL_TERMS = ("midjourney", "gpt-image-1.5", "gpt image 1.5")
_MODEL_ALIASES = {
    "jimeng": "seedream-4.0",
    "jimeng-4.0": "seedream-4.0",
    "jimeng-4.1": "seedream-4.1",
    "jimeng-4.5": "seedream-4.5",
    "jimeng-4.6": "seedream-4.6",
    "seedream-4-0": "seedream-4.0",
    "seedream-4-1": "seedream-4.1",
    "seedream-4-5": "seedream-4.5",
    "seedream-4-6": "seedream-4.6",
    "doubao-seedance-4-5": "seedream-4.5",
    "banana2": "nano-banana-2",
    "banana2-moti": "nano-banana-2",
    "banana-pro": "nano-banana-pro",
    "nano banana 2": "nano-banana-2",
    "nano banana pro": "nano-banana-pro",
    "gpt image 2": "gpt-image-2",
}


def _base_url() -> str:
    return os.getenv("IMAGE_PLATFORM_BASE_URL", DEFAULT_IMAGE_PLATFORM_BASE_URL).rstrip("/")


def _api_token() -> str:
    return (
        os.getenv("IMAGE_PLATFORM_API_TOKEN")
        or os.getenv("IMAGE_SERVICE_API_KEY")
        or DEFAULT_IMAGE_PLATFORM_API_TOKEN
    )


def _auth_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_api_token()}"}


def _is_blocked_model(model_entry: Dict[str, Any]) -> bool:
    logical_names = (
        str(model_entry.get("key") or ""),
        str(model_entry.get("model") or ""),
        str(model_entry.get("display_name") or ""),
    )
    searchable = " ".join(logical_names).lower()
    return any(term in searchable for term in _BLOCKED_MODEL_TERMS)


def _sanitize_provider(provider_entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not provider_entry.get("enabled"):
        return None
    return {
        field: copy.deepcopy(provider_entry[field])
        for field in _PROVIDER_FIELDS
        if field in provider_entry
    }


def _sanitize_model(model_entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if _is_blocked_model(model_entry):
        return None

    providers = []
    for provider_entry in model_entry.get("providers") or []:
        if isinstance(provider_entry, dict):
            sanitized_provider = _sanitize_provider(provider_entry)
            if sanitized_provider:
                providers.append(sanitized_provider)

    if not providers:
        return None

    sanitized = {
        field: copy.deepcopy(model_entry[field])
        for field in _MODEL_FIELDS
        if field in model_entry
    }
    sanitized["providers"] = providers
    return sanitized


def _sanitize_catalog(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("Image model catalog response must be a list")
    if not payload:
        raise ValueError("Image model catalog response is empty")

    catalog = []
    for model_entry in payload:
        if isinstance(model_entry, dict):
            sanitized_model = _sanitize_model(model_entry)
            if sanitized_model:
                catalog.append(sanitized_model)

    if not catalog:
        raise ValueError("Image model catalog has no available models")
    return catalog


def fetch_image_model_catalog(timeout: int = 60) -> List[Dict[str, Any]]:
    response = requests.get(
        f"{_base_url()}/models",
        headers=_auth_headers(),
        timeout=timeout,
    )
    response.raise_for_status()
    return _sanitize_catalog(response.json())


def refresh_image_model_catalog() -> List[Dict[str, Any]]:
    global _MODEL_CATALOG_CACHE

    catalog = fetch_image_model_catalog()
    _MODEL_CATALOG_CACHE = catalog
    return copy.deepcopy(catalog)


def get_image_model_catalog() -> List[Dict[str, Any]]:
    global _MODEL_CATALOG_CACHE

    if _MODEL_CATALOG_CACHE is None:
        _MODEL_CATALOG_CACHE = fetch_image_model_catalog()
    return copy.deepcopy(_MODEL_CATALOG_CACHE)


def get_image_model_catalog_public() -> List[Dict[str, Any]]:
    return get_image_model_catalog()


def _matches_model(model_entry: Dict[str, Any], model: str) -> bool:
    expected = str(model).strip().lower()
    expected = _MODEL_ALIASES.get(expected, expected)
    return any(
        str(model_entry.get(field) or "").strip().lower() == expected
        for field in ("key", "model", "display_name")
    )


def resolve_image_route(model: str, provider: Optional[str] = None) -> Dict[str, Any]:
    catalog = get_image_model_catalog()
    model_entry = next((entry for entry in catalog if _matches_model(entry, model)), None)
    if not model_entry:
        raise ValueError(f"Image model is unavailable: {model}")

    providers = model_entry.get("providers") or []
    selected_provider = None
    if provider:
        selected_provider = next(
            (
                route
                for route in providers
                if str(route.get("provider") or "").lower() == str(provider).lower()
            ),
            None,
        )
        if not selected_provider:
            raise ValueError(f"Image provider is unavailable for {model}: {provider}")
    else:
        default_provider = model_entry.get("default_provider")
        if default_provider:
            selected_provider = next(
                (
                    route
                    for route in providers
                    if str(route.get("provider") or "").lower()
                    == str(default_provider).lower()
                ),
                None,
            )
        selected_provider = selected_provider or providers[0] if providers else None

    if not selected_provider:
        raise ValueError(f"Image model has no enabled provider route: {model}")

    route = {
        field: copy.deepcopy(model_entry.get(field))
        for field in _MODEL_FIELDS
        if field in model_entry
    }
    route.update(copy.deepcopy(selected_provider))
    return route


def submit_image_task(
    prompt: str,
    model: str,
    username: str,
    provider: Optional[str] = None,
    action: str = "text2image",
    ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    reference_images: Optional[List[str]] = None,
    extra: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    payload = {
        "prompt": prompt,
        "model": model,
        "username": username,
        "action": action,
    }
    optional_fields = {
        "provider": provider,
        "ratio": ratio,
        "resolution": resolution,
        "reference_images": reference_images,
        "extra": extra,
        "metadata": metadata,
    }
    payload.update(
        {key: value for key, value in optional_fields.items() if value is not None}
    )

    response = requests.post(
        f"{_base_url()}/tasks",
        json=payload,
        headers=_auth_headers(),
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def get_image_task(task_id: str, timeout: int = 60) -> Dict[str, Any]:
    response = requests.get(
        f"{_base_url()}/tasks/{task_id}",
        headers=_auth_headers(),
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _normalize_images(payload: Dict[str, Any]) -> List[str]:
    final_images = payload.get("final_image_urls")
    if final_images:
        return list(final_images)
    upstream_images = payload.get("upstream_image_urls")
    if upstream_images:
        return list(upstream_images)
    return []


def _normalize_error_message(payload: Dict[str, Any]) -> Optional[str]:
    if payload.get("error_message"):
        return str(payload.get("error_message"))
    error = payload.get("error")
    if isinstance(error, dict):
        return error.get("message") or error.get("error") or str(error)
    if error:
        return str(error)
    return None


def normalize_task_status_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": payload.get("status"),
        "progress": payload.get("progress"),
        "images": _normalize_images(payload),
        "cost": payload.get("cost"),
        "provider": payload.get("provider"),
        "model": payload.get("model"),
        "raw_response": payload.get("raw_response"),
        "error": payload.get("error"),
        "error_message": _normalize_error_message(payload),
    }
