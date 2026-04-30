from fastapi import APIRouter, Depends, HTTPException

import models
from auth import get_current_user
from video_provider_accounts import get_cached_video_provider_accounts


router = APIRouter()


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
