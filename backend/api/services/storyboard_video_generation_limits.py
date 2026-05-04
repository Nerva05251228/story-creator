from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

import models


ACTIVE_VIDEO_GENERATION_STATUSES = ("submitting", "preparing", "processing")
ACTIVE_MANAGED_TASK_STATUSES = ("pending", "processing")
MAX_ACTIVE_VIDEO_GENERATIONS_PER_SHOT = 1


def get_storyboard_shot_family_identity(shot: models.StoryboardShot) -> str:
    stable_id = str(getattr(shot, "stable_id", "") or "").strip()
    if stable_id:
        return f"stable:{int(getattr(shot, 'episode_id', 0) or 0)}:{stable_id}"
    return f"shot_number:{int(getattr(shot, 'episode_id', 0) or 0)}:{int(getattr(shot, 'shot_number', 0) or 0)}"


def get_storyboard_shot_family_filters(shot: models.StoryboardShot):
    stable_id = str(getattr(shot, "stable_id", "") or "").strip()
    if stable_id:
        return [
            models.StoryboardShot.episode_id == shot.episode_id,
            or_(
                models.StoryboardShot.stable_id == stable_id,
                and_(
                    models.StoryboardShot.shot_number == shot.shot_number,
                    or_(
                        models.StoryboardShot.stable_id.is_(None),
                        models.StoryboardShot.stable_id == "",
                    ),
                ),
            ),
        ]
    return [
        models.StoryboardShot.episode_id == shot.episode_id,
        models.StoryboardShot.shot_number == shot.shot_number,
    ]


def count_active_video_generations_for_shot_family(
    shot: models.StoryboardShot,
    db: Session,
) -> int:
    family_rows = db.query(
        models.StoryboardShot.id,
        models.StoryboardShot.video_status,
    ).filter(
        *get_storyboard_shot_family_filters(shot)
    ).all()

    family_shot_ids = []
    active_shot_ids = set()
    for shot_id, video_status in family_rows:
        numeric_shot_id = int(shot_id or 0)
        if numeric_shot_id <= 0:
            continue
        family_shot_ids.append(numeric_shot_id)
        if str(video_status or "").strip().lower() in ACTIVE_VIDEO_GENERATION_STATUSES:
            active_shot_ids.add(numeric_shot_id)

    active_count = len(active_shot_ids)
    stable_id = str(getattr(shot, "stable_id", "") or "").strip()

    if stable_id:
        managed_tasks = db.query(
            models.ManagedTask.id,
            models.ManagedTask.shot_id,
        ).filter(
            models.ManagedTask.shot_stable_id == stable_id,
            models.ManagedTask.status.in_(ACTIVE_MANAGED_TASK_STATUSES),
        ).all()
    elif family_shot_ids:
        managed_tasks = db.query(
            models.ManagedTask.id,
            models.ManagedTask.shot_id,
        ).filter(
            models.ManagedTask.shot_id.in_(family_shot_ids),
            models.ManagedTask.status.in_(ACTIVE_MANAGED_TASK_STATUSES),
        ).all()
    else:
        managed_tasks = []

    for _, managed_shot_id in managed_tasks:
        numeric_shot_id = int(managed_shot_id or 0)
        if numeric_shot_id <= 0 or numeric_shot_id not in active_shot_ids:
            active_count += 1

    return active_count


def is_storyboard_shot_generation_active(
    shot: models.StoryboardShot,
    db: Session,
) -> bool:
    if str(getattr(shot, "video_status", "") or "").strip().lower() in ACTIVE_VIDEO_GENERATION_STATUSES:
        return True

    shot_id = int(getattr(shot, "id", 0) or 0)
    if shot_id <= 0:
        return False

    active_task = db.query(models.ManagedTask.id).filter(
        models.ManagedTask.shot_id == shot_id,
        models.ManagedTask.status.in_(ACTIVE_MANAGED_TASK_STATUSES),
    ).first()
    return active_task is not None


def build_active_video_generation_limit_message(
    blocked_entries: List[Dict[str, Any]],
) -> str:
    if not blocked_entries:
        return ""

    if len(blocked_entries) == 1:
        entry = blocked_entries[0]
        shot = entry["shot"]
        current_active = int(entry["current_active"] or 0)
        remaining = max(0, MAX_ACTIVE_VIDEO_GENERATIONS_PER_SHOT - current_active)
        if remaining <= 0:
            return f"镜头{shot.shot_number}已有{current_active}个正在生成中的视频，请等待完成"
        return (
            f"镜头{shot.shot_number}当前已有{current_active}个正在生成中的视频，"
            f"本次最多还能再提交{remaining}个，请等待完成"
        )

    labels = []
    for entry in blocked_entries[:6]:
        shot = entry["shot"]
        labels.append(f"镜头{shot.shot_number}")
    labels_text = "、".join(labels)
    if len(blocked_entries) > 6:
        labels_text += "等"
    return (
        f"{labels_text}已达到同时生成上限或本次提交后会超出上限，"
        f"当前每个镜头最多只能有{MAX_ACTIVE_VIDEO_GENERATIONS_PER_SHOT}个正在生成中的视频，请等待完成"
    )


def ensure_storyboard_video_generation_slots_available(
    shots: List[models.StoryboardShot],
    db: Session,
    requested_count_per_shot: int = 1,
) -> None:
    blocked_entries = []
    family_entries: Dict[str, Dict[str, Any]] = {}
    requested_count = max(1, int(requested_count_per_shot or 1))

    for shot in shots or []:
        if not shot:
            continue

        family_key = get_storyboard_shot_family_identity(shot)
        entry = family_entries.get(family_key)
        if not entry:
            entry = {
                "shot": shot,
                "requested_count": 0,
            }
            family_entries[family_key] = entry
        entry["requested_count"] += requested_count

    for entry in family_entries.values():
        shot = entry["shot"]
        current_active = count_active_video_generations_for_shot_family(shot, db)
        if current_active + int(entry["requested_count"] or 0) > MAX_ACTIVE_VIDEO_GENERATIONS_PER_SHOT:
            blocked_entries.append({
                "shot": shot,
                "current_active": current_active,
            })

    if blocked_entries:
        raise HTTPException(
            status_code=400,
            detail=build_active_video_generation_limit_message(blocked_entries),
        )
