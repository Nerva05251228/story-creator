import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import requests
from sqlalchemy.orm import Session
from urllib.parse import quote

import models
from auth import get_current_user
from database import get_db
from managed_generation_service import ACTIVE_MANAGED_SESSION_STATUSES
from video_api_config import (
    get_required_video_api_base_url,
    get_video_api_headers,
    get_video_provider_stats_url,
    get_video_tasks_cancel_url,
)
from video_provider_accounts import get_cached_video_provider_accounts
from video_service import check_video_status


router = APIRouter()
_video_task_executor = ThreadPoolExecutor(max_workers=10)


class CancelVideoTasksRequest(BaseModel):
    task_ids: List[str]


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


@router.get("/api/tasks/{task_id}/status")
async def query_task_status(
    task_id: str,
    user: models.User = Depends(get_current_user)
):
    """根据task_id查询Sora任务状态（返回服务商原始响应）"""
    _ = user
    try:
        # return_raw=True 表示返回服务商的原始JSON响应
        return check_video_status(task_id, return_raw=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


def _normalize_video_task_ids(task_ids: List[str]) -> List[str]:
    normalized_task_ids = []
    seen_task_ids = set()
    for task_id in task_ids or []:
        normalized_task_id = str(task_id or "").strip()
        if normalized_task_id and normalized_task_id not in seen_task_ids:
            normalized_task_ids.append(normalized_task_id)
            seen_task_ids.add(normalized_task_id)
    return normalized_task_ids


def _get_user_cancelable_video_task_ids(
    task_ids: List[str],
    user: models.User,
    db: Session
) -> set:
    if not task_ids or not user:
        return set()

    active_shot_statuses = ["submitting", "preparing", "processing"]
    active_managed_task_statuses = ["pending", "processing"]

    owned_task_ids = {
        task_id
        for (task_id,) in db.query(models.StoryboardShot.task_id).join(
            models.Episode,
            models.StoryboardShot.episode_id == models.Episode.id
        ).join(
            models.Script,
            models.Episode.script_id == models.Script.id
        ).filter(
            models.Script.user_id == user.id,
            models.StoryboardShot.task_id.in_(task_ids),
            models.StoryboardShot.video_status.in_(active_shot_statuses),
        ).all()
        if task_id
    }

    owned_task_ids.update({
        task_id
        for (task_id,) in db.query(models.ManagedTask.task_id).join(
            models.ManagedSession,
            models.ManagedTask.session_id == models.ManagedSession.id
        ).join(
            models.Episode,
            models.ManagedSession.episode_id == models.Episode.id
        ).join(
            models.Script,
            models.Episode.script_id == models.Script.id
        ).filter(
            models.Script.user_id == user.id,
            models.ManagedTask.task_id.in_(task_ids),
            models.ManagedTask.status.in_(active_managed_task_statuses),
            models.ManagedSession.status.in_(ACTIVE_MANAGED_SESSION_STATUSES),
        ).all()
        if task_id
    })

    return owned_task_ids


@router.post("/api/video/tasks/cancel")
async def cancel_video_tasks(
    request: CancelVideoTasksRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return await _cancel_video_tasks_impl(request, user, db, _cancel_upstream_video_tasks)


async def _cancel_video_tasks_impl(
    request: CancelVideoTasksRequest,
    user: models.User,
    db: Session,
    cancel_upstream_video_tasks,
):
    """代理取消上游视频生成任务。"""
    task_ids = _normalize_video_task_ids(request.task_ids)
    if not task_ids:
        raise HTTPException(status_code=400, detail="缺少任务ID")

    owned_task_ids = _get_user_cancelable_video_task_ids(task_ids, user, db)
    unauthorized_task_ids = [
        task_id for task_id in task_ids
        if task_id not in owned_task_ids
    ]
    if unauthorized_task_ids:
        raise HTTPException(status_code=403, detail="无权取消任务")

    try:
        loop = asyncio.get_event_loop()
        cancel_result = await loop.run_in_executor(
            _video_task_executor,
            cancel_upstream_video_tasks,
            task_ids
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"取消任务失败: {str(e)}")

    if not cancel_result.get("ok", False):
        response_payload = cancel_result.get("response") or {}
        detail = response_payload.get("detail") if isinstance(response_payload, dict) else None
        raise HTTPException(status_code=502, detail=detail or "取消任务失败")

    return cancel_result


def _cancel_upstream_video_tasks(task_ids: List[str]) -> dict:
    normalized_task_ids = [
        str(task_id or "").strip()
        for task_id in (task_ids or [])
        if str(task_id or "").strip()
    ]
    if not normalized_task_ids:
        return {
            "requested_count": 0,
            "status_code": None,
            "ok": True,
            "response": {}
        }

    response = requests.post(
        get_video_tasks_cancel_url(),
        headers=get_video_api_headers(),
        json={"task_ids": normalized_task_ids},
        timeout=30
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"raw_text": response.text}

    return {
        "requested_count": len(normalized_task_ids),
        "status_code": response.status_code,
        "ok": response.status_code == 200,
        "response": payload
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
