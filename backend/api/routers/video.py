from fastapi import APIRouter, Depends, HTTPException
import requests
from urllib.parse import quote

import models
from auth import get_current_user
from video_api_config import (
    get_required_video_api_base_url,
    get_video_api_headers,
    get_video_provider_stats_url,
)
from video_provider_accounts import get_cached_video_provider_accounts


router = APIRouter()


@router.get("/api/video/provider-stats")
def get_video_provider_stats(
    user: models.User = Depends(get_current_user),
):
    _ = user
    try:
        response = requests.get(
            get_video_provider_stats_url(),
            headers=get_video_api_headers(),
            timeout=5,
        )
        try:
            payload = response.json()
        except Exception:
            payload = {"raw_text": getattr(response, "text", "")}
        if int(getattr(response, "status_code", 0) or 0) >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {payload}")
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return {"providers": payload}
        return {"providers": []}
    except Exception as exc:
        print(f"[video-provider-stats] refresh failed: {str(exc)}")
        return {"providers": [], "error": str(exc)}


@router.get("/api/video/quota/{username}")
def get_video_quota(
    username: str,
    user: models.User = Depends(get_current_user),
):
    _ = user
    encoded_username = quote(str(username or "").strip(), safe="")
    if not encoded_username:
        return {}
    base_url = get_required_video_api_base_url().rstrip("/")
    try:
        response = requests.get(
            f"{base_url}/quota/{encoded_username}",
            headers=get_video_api_headers(),
            timeout=5,
        )
        try:
            payload = response.json()
        except Exception:
            payload = {"raw_text": getattr(response, "text", "")}
        if int(getattr(response, "status_code", 0) or 0) >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {payload}")
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        print(f"[video-quota] refresh failed for {encoded_username}: {str(exc)}")
        return {}


@router.get("/api/video/providers/{provider}/accounts")
async def get_video_provider_accounts(
    provider: str,
    user: models.User = Depends(get_current_user),
):
    _ = user
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider != "moti":
        raise HTTPException(status_code=404, detail="不支持该视频服务商账号列表")
    return get_cached_video_provider_accounts(normalized_provider)
