import json
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

import models
from api.schemas.episodes import SimpleStoryboardRequest
from api.services import db_commit_retry
from api.services.simple_storyboard_batches import (
    _get_simple_storyboard_batch_rows,
    _get_simple_storyboard_batch_summary,
    _persist_programmatic_simple_storyboard_batches,
    _refresh_episode_simple_storyboard_from_batches,
)
from auth import get_current_user
from database import get_db
from simple_storyboard_rules import (
    generate_simple_storyboard_shots,
    get_default_rule_config,
    normalize_rule_config,
)


router = APIRouter()


SQLITE_LOCK_RETRY_DELAYS = db_commit_retry.SQLITE_LOCK_RETRY_DELAYS
SIMPLE_STORYBOARD_TIMEOUT_SECONDS = 3600
SIMPLE_STORYBOARD_TIMEOUT_ERROR = "简单分镜生成超时（超过 1 小时），已自动标记为失败，请重新生成。"


_rollback_quietly = db_commit_retry.rollback_quietly
_is_sqlite_lock_error = db_commit_retry.is_sqlite_lock_error
commit_with_retry = db_commit_retry.commit_with_retry


def _count_storyboard_items(raw_data: Optional[str]) -> int:
    if not raw_data:
        return 0
    try:
        parsed = json.loads(raw_data)
    except Exception:
        return 0
    shots = parsed.get("shots") if isinstance(parsed, dict) else None
    return len(shots) if isinstance(shots, list) else 0


def _verify_episode_permission(
    episode_id: int,
    user: models.User,
    db: Session,
) -> models.Episode:
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if not script or script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    return episode


def _mark_simple_storyboard_timeout_if_needed(episode: Optional[models.Episode], db: Session) -> bool:
    if not episode or not bool(getattr(episode, "simple_storyboard_generating", False)):
        return False
    reference_time = getattr(episode, "updated_at", None) or getattr(episode, "created_at", None)
    if not reference_time:
        return False
    if (datetime.utcnow() - reference_time).total_seconds() < SIMPLE_STORYBOARD_TIMEOUT_SECONDS:
        return False
    batch_rows = _get_simple_storyboard_batch_rows(int(getattr(episode, "id", 0) or 0), db)
    if batch_rows:
        for row in batch_rows:
            if str(getattr(row, "status", "") or "").strip() in {"completed", "failed"}:
                continue
            row.status = "failed"
            if not str(getattr(row, "error_message", "") or "").strip():
                row.error_message = SIMPLE_STORYBOARD_TIMEOUT_ERROR
            row.updated_at = datetime.utcnow()
        _refresh_episode_simple_storyboard_from_batches(episode, db)
        if not str(getattr(episode, "simple_storyboard_error", "") or "").strip():
            episode.simple_storyboard_error = SIMPLE_STORYBOARD_TIMEOUT_ERROR
    else:
        episode.simple_storyboard_generating = False
        if not str(getattr(episode, "simple_storyboard_error", "") or "").strip():
            episode.simple_storyboard_error = SIMPLE_STORYBOARD_TIMEOUT_ERROR
    db.commit()
    db.refresh(episode)
    return True


def _load_simple_storyboard_rule_config_for_duration(duration: int, db: Session):
    template = db.query(models.ShotDurationTemplate).filter(
        models.ShotDurationTemplate.duration == int(duration or 15)
    ).first()
    if template:
        raw_text = str(getattr(template, "simple_storyboard_config_json", "") or "").strip()
        if raw_text:
            try:
                return normalize_rule_config(json.loads(raw_text), int(duration or 15))
            except Exception:
                pass
    return get_default_rule_config(duration)


def _refresh_episode_batch_sora_prompt_state(episode_id: int, db: Session):
    remaining = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id,
        models.StoryboardShot.sora_prompt_status == "generating",
    ).count()
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if episode:
        episode.batch_generating_prompts = remaining > 0


def _repair_stale_storyboard_prompt_generation(episode_id: int, db: Session) -> bool:
    shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id,
        models.StoryboardShot.sora_prompt_status == "generating",
    ).all()
    if not shots:
        return False

    shot_ids = [int(getattr(shot, "id", 0) or 0) for shot in shots]
    tasks = db.query(models.TextRelayTask).filter(
        models.TextRelayTask.task_type == "sora_prompt",
        models.TextRelayTask.owner_type == "shot",
        models.TextRelayTask.owner_id.in_(shot_ids),
    ).order_by(
        models.TextRelayTask.owner_id.asc(),
        models.TextRelayTask.id.desc(),
    ).all()

    latest_task_by_shot: Dict[int, models.TextRelayTask] = {}
    active_shot_ids = set()
    for task in tasks:
        shot_id = int(getattr(task, "owner_id", 0) or 0)
        if shot_id <= 0:
            continue
        if shot_id not in latest_task_by_shot:
            latest_task_by_shot[shot_id] = task
        if str(getattr(task, "status", "") or "").strip() in {"submitted", "queued", "running"}:
            active_shot_ids.add(shot_id)

    changed = False
    for shot in shots:
        shot_id = int(getattr(shot, "id", 0) or 0)
        if shot_id in active_shot_ids:
            continue

        next_status = ""
        latest_task = latest_task_by_shot.get(shot_id)
        latest_task_status = str(getattr(latest_task, "status", "") or "").strip() if latest_task else ""
        if latest_task_status == "succeeded":
            next_status = "completed"
        elif latest_task_status == "failed":
            next_status = "failed"
        else:
            has_prompt_content = bool(
                str(getattr(shot, "sora_prompt", "") or "").strip()
                or str(getattr(shot, "storyboard_video_prompt", "") or "").strip()
            )
            has_video_progress = str(getattr(shot, "video_status", "") or "").strip() in {
                "submitting",
                "preparing",
                "processing",
                "completed",
                "failed",
            }
            next_status = "completed" if (has_prompt_content or has_video_progress) else "failed"

        if str(getattr(shot, "sora_prompt_status", "") or "").strip() != next_status:
            shot.sora_prompt_status = next_status
            changed = True

    if changed:
        db.flush()

    return changed


def _reconcile_episode_runtime_flags(episode: Optional[models.Episode], db: Session) -> bool:
    if not episode:
        return False

    episode_id = int(getattr(episode, "id", 0) or 0)
    if episode_id <= 0:
        return False

    changed = False

    changed = _repair_stale_storyboard_prompt_generation(episode_id, db) or changed

    has_generating_sora_prompt = db.query(models.StoryboardShot.id).filter(
        models.StoryboardShot.episode_id == episode_id,
        models.StoryboardShot.sora_prompt_status == "generating",
    ).first() is not None
    if bool(getattr(episode, "batch_generating_prompts", False)) != has_generating_sora_prompt:
        episode.batch_generating_prompts = has_generating_sora_prompt
        changed = True

    simple_summary = _get_simple_storyboard_batch_summary(episode_id, db)
    simple_generating = bool(
        simple_summary.get("submitting_batches", 0) > 0
        or (
            simple_summary.get("total_batches", 0) > 0
            and simple_summary.get("completed_batches", 0) + simple_summary.get("failed_batches", 0)
            < simple_summary.get("total_batches", 0)
        )
    )
    if bool(getattr(episode, "simple_storyboard_generating", False)) != simple_generating:
        episode.simple_storyboard_generating = simple_generating
        changed = True

    if changed:
        db.flush()

    return changed


@router.post("/api/episodes/{episode_id}/generate-simple-storyboard")
async def generate_simple_storyboard_api(
    episode_id: int,
    request: SimpleStoryboardRequest = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    if request and request.content:
        episode_content = request.content
    else:
        episode_content = episode.content

    if request and request.batch_size:
        batch_size = request.batch_size
    else:
        batch_size = episode.batch_size or 500

    duration = 25 if int(episode.storyboard2_duration or 15) == 25 else 15

    def mark_simple_storyboard_request_started():
        episode.batch_size = batch_size
        episode.simple_storyboard_data = None
        episode.simple_storyboard_generating = True
        episode.simple_storyboard_error = ""

    commit_with_retry(
        db,
        prepare_fn=mark_simple_storyboard_request_started,
        context=f"simple_storyboard_request episode={episode_id}",
    )

    try:
        rule_config = _load_simple_storyboard_rule_config_for_duration(duration, db)
        shots = generate_simple_storyboard_shots(
            episode_content,
            duration,
            rule_override=rule_config,
        )
        _persist_programmatic_simple_storyboard_batches(
            episode_id,
            shots,
            batch_size,
            db,
        )
        episode.simple_storyboard_data = json.dumps({"shots": shots}, ensure_ascii=False)
        episode.simple_storyboard_generating = False
        episode.simple_storyboard_error = ""
        summary = _refresh_episode_simple_storyboard_from_batches(episode, db)
        db.commit()
        print(
            f"[SimpleStoryboard][generate] episode_id={episode_id} duration={duration} "
            f"content_len={len(str(episode_content or ''))} shots={len(shots)} "
            f"total_batches={int(summary.get('total_batches') or 0)} "
            f"completed_batches={int(summary.get('completed_batches') or 0)} "
            f"failed_batches={int(summary.get('failed_batches') or 0)}"
        )
    except Exception as exc:
        db.rollback()
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            episode.simple_storyboard_generating = False
            episode.simple_storyboard_error = str(exc)
            db.commit()
        raise HTTPException(status_code=500, detail=f"简单分镜生成失败: {str(exc)}")

    return {
        "message": "简单分镜生成完成",
        "generating": False,
        "submitted_batches": int(summary.get("total_batches") or 0),
        "error": episode.simple_storyboard_error or "",
        "shots": summary.get("shots") or [],
        "batch_size": int(episode.batch_size or batch_size or 500),
        "total_batches": int(summary.get("total_batches") or 0),
        "completed_batches": int(summary.get("completed_batches") or 0),
        "failed_batches": int(summary.get("failed_batches") or 0),
        "submitting_batches": int(summary.get("submitting_batches") or 0),
        "has_failures": bool(summary.get("has_failures")),
        "failed_batch_errors": summary.get("failed_batch_errors") or [],
        "batches": summary.get("batches") or [],
    }


@router.get("/api/episodes/{episode_id}/simple-storyboard")
def get_simple_storyboard(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    _mark_simple_storyboard_timeout_if_needed(episode, db)
    if _reconcile_episode_runtime_flags(episode, db):
        db.commit()

    shots = []
    if episode.simple_storyboard_data:
        try:
            data = json.loads(episode.simple_storyboard_data)
            shots = data.get("shots", [])
        except Exception:
            shots = []

    summary = _get_simple_storyboard_batch_summary(episode_id, db)
    print(
        f"[SimpleStoryboard][fetch] episode_id={episode_id} generating={bool(episode.simple_storyboard_generating)} "
        f"error={bool(episode.simple_storyboard_error)} shots={len(shots)} "
        f"total_batches={int(summary.get('total_batches') or 0)} "
        f"completed_batches={int(summary.get('completed_batches') or 0)} "
        f"failed_batches={int(summary.get('failed_batches') or 0)} "
        f"submitting_batches={int(summary.get('submitting_batches') or 0)}"
    )
    return {
        "generating": episode.simple_storyboard_generating,
        "error": episode.simple_storyboard_error or "",
        "shots": shots,
        "batch_size": episode.batch_size or 500,
        "total_batches": int(summary.get("total_batches") or 0),
        "completed_batches": int(summary.get("completed_batches") or 0),
        "failed_batches": int(summary.get("failed_batches") or 0),
        "submitting_batches": int(summary.get("submitting_batches") or 0),
        "has_failures": bool(summary.get("has_failures")),
        "failed_batch_errors": summary.get("failed_batch_errors") or [],
        "batches": summary.get("batches") or [],
    }


@router.get("/api/episodes/{episode_id}/simple-storyboard/status")
def get_simple_storyboard_status(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    episode = _verify_episode_permission(episode_id, user, db)
    _mark_simple_storyboard_timeout_if_needed(episode, db)
    if _reconcile_episode_runtime_flags(episode, db):
        db.commit()
    summary = _get_simple_storyboard_batch_summary(episode_id, db)
    return {
        "generating": bool(episode.simple_storyboard_generating),
        "error": episode.simple_storyboard_error or "",
        "shots_count": _count_storyboard_items(episode.simple_storyboard_data),
        "total_batches": int(summary.get("total_batches") or 0),
        "completed_batches": int(summary.get("completed_batches") or 0),
        "failed_batches": int(summary.get("failed_batches") or 0),
        "submitting_batches": int(summary.get("submitting_batches") or 0),
        "failed_batch_errors": summary.get("failed_batch_errors") or [],
        "batches": summary.get("batches") or [],
    }


@router.post("/api/episodes/{episode_id}/simple-storyboard/retry-failed-batches")
async def retry_failed_simple_storyboard_batches_api(
    episode_id: int,
    background_tasks: BackgroundTasks = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _verify_episode_permission(episode_id, user, db)
    raise HTTPException(status_code=400, detail="失败批次重试已移除，请重新发起整次简单分镜生成")


@router.put("/api/episodes/{episode_id}/simple-storyboard")
async def update_simple_storyboard(
    episode_id: int,
    data: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    batch_rows = _get_simple_storyboard_batch_rows(episode_id, db)
    for row in batch_rows:
        row.status = "completed"
        row.error_message = ""
        row.shots_data = ""
    episode.simple_storyboard_data = json.dumps(data, ensure_ascii=False)
    episode.simple_storyboard_generating = False
    episode.simple_storyboard_error = ""
    db.commit()

    return {"message": "简单分镜数据已更新"}
