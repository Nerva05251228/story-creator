from fastapi import APIRouter, Depends, HTTPException
import requests
from sqlalchemy.orm import Session
from urllib.parse import quote

import models
from auth import get_current_user
from database import get_db
from video_api_config import (
    get_required_video_api_base_url,
    get_video_api_headers,
    get_video_provider_stats_url,
)
from video_provider_accounts import get_cached_video_provider_accounts


router = APIRouter()


@router.get("/api/video-model-pricing")
async def get_video_model_pricing(provider: str = "yijia", db: Session = Depends(get_db)):
    """Get video model pricing from database.

    Returns pricing grouped by front-end model name.
    sora-2-pro prices are merged into sora-2 (since 25s auto-maps to sora-2-pro).
    """
    try:
        pricing_records = db.query(models.VideoModelPricing).filter(
            models.VideoModelPricing.provider == provider
        ).all()

        # Group by front-end model name
        # sora-2-pro -> merge into sora-2 (front-end only knows sora-2)
        pricing_map = {}
        updated_at = None

        for record in pricing_records:
            # Map sora-2-pro back to sora-2 for front-end display
            display_model = record.model_name
            if display_model == "sora-2-pro":
                display_model = "sora-2"

            if display_model not in pricing_map:
                pricing_map[display_model] = {}

            key = f"{record.duration}_{record.aspect_ratio}"
            pricing_map[display_model][key] = {
                "duration": record.duration,
                "aspect_ratio": record.aspect_ratio,
                "price_yuan": record.price_yuan
            }

            if record.updated_at:
                updated_at = record.updated_at

        return {
            "pricing": pricing_map,
            "provider": provider,
            "last_updated": updated_at.isoformat() if updated_at else None
        }
    except Exception as e:
        return {
            "pricing": {},
            "last_updated": None,
            "error": str(e)
        }


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
