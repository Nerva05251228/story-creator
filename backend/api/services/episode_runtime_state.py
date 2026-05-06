from typing import Dict, Optional

from sqlalchemy.orm import Session

import models
from api.services.simple_storyboard_batches import _get_simple_storyboard_batch_summary


def refresh_episode_batch_sora_prompt_state(episode_id: int, db: Session):
    remaining = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id,
        models.StoryboardShot.sora_prompt_status == "generating",
    ).count()
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if episode:
        episode.batch_generating_prompts = remaining > 0


def repair_stale_storyboard_prompt_generation(episode_id: int, db: Session) -> bool:
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


def reconcile_episode_runtime_flags(episode: Optional[models.Episode], db: Session) -> bool:
    if not episode:
        return False

    episode_id = int(getattr(episode, "id", 0) or 0)
    if episode_id <= 0:
        return False

    changed = False

    changed = repair_stale_storyboard_prompt_generation(episode_id, db) or changed

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
