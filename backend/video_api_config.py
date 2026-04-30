import os
from urllib.parse import urlsplit, urlunsplit

from env_config import get_first_env, load_app_env, require_real_env


load_app_env()

DEFAULT_VIDEO_API_BASE_URL = ""
VIDEO_API_BASE_URL = ""
VIDEO_API_TOKEN = get_first_env("VIDEO_API_TOKEN", "SORA_VIDEO_API_TOKEN", default="")


def normalize_video_api_base_url(url: str) -> str:
    raw_url = str(url or "").strip()
    if not raw_url:
        return ""
    parts = urlsplit(raw_url)
    path = str(parts.path or "").rstrip("/")

    for suffix in ("/docs/openapi.json", "/openapi.json", "/swagger.json", "/docs"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break

    normalized = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return normalized.rstrip("/")


def get_configured_video_api_base_url() -> str:
    configured = (
        get_first_env("VIDEO_API_BASE_URL", "SORA_VIDEO_API_BASE_URL", default="")
    )
    return normalize_video_api_base_url(configured)


VIDEO_API_BASE_URL = get_configured_video_api_base_url()


def get_video_api_headers() -> dict:
    token = require_real_env("VIDEO_API_TOKEN", "SORA_VIDEO_API_TOKEN")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _require_video_api_base_url() -> str:
    configured = require_real_env("VIDEO_API_BASE_URL", "SORA_VIDEO_API_BASE_URL")
    return normalize_video_api_base_url(configured)


def get_required_video_api_base_url() -> str:
    return _require_video_api_base_url()


def get_video_task_create_url() -> str:
    return f"{_require_video_api_base_url()}/tasks"


def get_video_task_status_url(task_id: str) -> str:
    return f"{_require_video_api_base_url()}/tasks/{str(task_id or '').strip()}"


def get_video_task_urls_update_url(task_id: str) -> str:
    return f"{_require_video_api_base_url()}/tasks/{str(task_id or '').strip()}/urls"


def get_video_tasks_cancel_url() -> str:
    return f"{_require_video_api_base_url()}/tasks/cancel"


def get_video_models_url() -> str:
    return f"{_require_video_api_base_url()}/models"


def get_video_provider_accounts_url(provider: str) -> str:
    normalized_provider = str(provider or "").strip().lower()
    return f"{_require_video_api_base_url()}/providers/{normalized_provider}/accounts"


def get_video_provider_stats_url() -> str:
    return f"{_require_video_api_base_url()}/stats/providers"
