import os
from urllib.parse import urlsplit, urlunsplit


DEFAULT_VIDEO_API_BASE_URL = "https://ne.mocatter.cn/api/video"
VIDEO_API_BASE_URL = ""
VIDEO_API_TOKEN = (
    os.getenv("SORA_VIDEO_API_TOKEN")
    or os.getenv("VIDEO_API_TOKEN")
    or "sk-Zv2THcS1J7KDZkQ-griUI6UlRSNcgQhvTXu70tuvRBw"
)


def normalize_video_api_base_url(url: str) -> str:
    raw_url = str(url or "").strip() or DEFAULT_VIDEO_API_BASE_URL
    parts = urlsplit(raw_url)
    path = str(parts.path or "").rstrip("/")

    for suffix in ("/docs/openapi.json", "/openapi.json", "/swagger.json", "/docs"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break

    normalized = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return normalized.rstrip("/") or DEFAULT_VIDEO_API_BASE_URL


def get_configured_video_api_base_url() -> str:
    configured = (
        os.getenv("SORA_VIDEO_API_BASE_URL")
        or os.getenv("VIDEO_API_BASE_URL")
        or DEFAULT_VIDEO_API_BASE_URL
    )
    return normalize_video_api_base_url(configured)


VIDEO_API_BASE_URL = get_configured_video_api_base_url()


def get_video_api_headers() -> dict:
    return {
        "Authorization": f"Bearer {VIDEO_API_TOKEN}",
        "Content-Type": "application/json",
    }


def get_video_task_create_url() -> str:
    return f"{VIDEO_API_BASE_URL}/tasks"


def get_video_task_status_url(task_id: str) -> str:
    return f"{VIDEO_API_BASE_URL}/tasks/{str(task_id or '').strip()}"


def get_video_task_urls_update_url(task_id: str) -> str:
    return f"{VIDEO_API_BASE_URL}/tasks/{str(task_id or '').strip()}/urls"


def get_video_tasks_cancel_url() -> str:
    return f"{VIDEO_API_BASE_URL}/tasks/cancel"


def get_video_models_url() -> str:
    return f"{VIDEO_API_BASE_URL}/models"


def get_video_provider_accounts_url(provider: str) -> str:
    normalized_provider = str(provider or "").strip().lower()
    return f"{VIDEO_API_BASE_URL}/providers/{normalized_provider}/accounts"


def get_video_provider_stats_url() -> str:
    parts = urlsplit(VIDEO_API_BASE_URL)
    return urlunsplit((parts.scheme, parts.netloc, "/api/video/stats/providers", "", ""))
