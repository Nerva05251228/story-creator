from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

import models
from api.schemas.episodes import ManagedSessionStatusResponse


def _get_next_managed_reserved_variant_index(original_shot: models.StoryboardShot, db: Session) -> int:
    max_variant = db.query(func.max(models.StoryboardShot.variant_index)).filter(
        models.StoryboardShot.episode_id == original_shot.episode_id,
        models.StoryboardShot.shot_number == original_shot.shot_number,
    ).scalar()
    family_count = db.query(func.count(models.StoryboardShot.id)).filter(
        models.StoryboardShot.episode_id == original_shot.episode_id,
        models.StoryboardShot.shot_number == original_shot.shot_number,
    ).scalar()
    return max(int(max_variant or 0), int(family_count or 0)) + 1


def _create_managed_reserved_shot(
    original_shot: models.StoryboardShot,
    provider: str,
    reserved_variant_index: int,
) -> models.StoryboardShot:
    return models.StoryboardShot(
        episode_id=original_shot.episode_id,
        shot_number=original_shot.shot_number,
        stable_id=original_shot.stable_id,
        variant_index=reserved_variant_index,
        prompt_template=original_shot.prompt_template,
        script_excerpt=original_shot.script_excerpt,
        storyboard_video_prompt=original_shot.storyboard_video_prompt,
        storyboard_audio_prompt=original_shot.storyboard_audio_prompt,
        storyboard_dialogue=original_shot.storyboard_dialogue,
        scene_override=original_shot.scene_override,
        scene_override_locked=bool(getattr(original_shot, "scene_override_locked", False)),
        sora_prompt=original_shot.sora_prompt,
        sora_prompt_is_full=bool(getattr(original_shot, "sora_prompt_is_full", False)),
        sora_prompt_status=original_shot.sora_prompt_status,
        selected_card_ids=original_shot.selected_card_ids,
        selected_sound_card_ids=getattr(original_shot, "selected_sound_card_ids", None),
        first_frame_reference_image_url=getattr(original_shot, "first_frame_reference_image_url", ""),
        uploaded_scene_image_url=getattr(original_shot, "uploaded_scene_image_url", ""),
        use_uploaded_scene_image=bool(getattr(original_shot, "use_uploaded_scene_image", False)),
        aspect_ratio=original_shot.aspect_ratio,
        duration=original_shot.duration,
        storyboard_video_model=getattr(original_shot, "storyboard_video_model", ""),
        storyboard_video_model_override_enabled=bool(
            getattr(original_shot, "storyboard_video_model_override_enabled", False)
        ),
        duration_override_enabled=bool(getattr(original_shot, "duration_override_enabled", False)),
        provider=provider,
        video_status="processing",
        video_error_message="托管排队中",
        timeline_json=original_shot.timeline_json,
        detail_image_prompt_overrides=original_shot.detail_image_prompt_overrides,
        storyboard_image_path=original_shot.storyboard_image_path,
        storyboard_image_status=original_shot.storyboard_image_status,
        storyboard_image_task_id=original_shot.storyboard_image_task_id,
        storyboard_image_model=original_shot.storyboard_image_model,
    )


def _reserve_legacy_managed_session_slots(session: models.ManagedSession, db: Session) -> int:
    active_tasks = db.query(models.ManagedTask).filter(
        models.ManagedTask.session_id == session.id,
        models.ManagedTask.status.in_(["pending", "processing"]),
        models.ManagedTask.shot_id <= 0,
    ).order_by(models.ManagedTask.id.asc()).all()

    if not active_tasks:
        return 0

    original_shot_cache: Dict[str, Optional[models.StoryboardShot]] = {}
    reserved_count = 0

    for task in active_tasks:
        stable_id = str(task.shot_stable_id or "").strip()
        if not stable_id:
            continue

        if stable_id not in original_shot_cache:
            original_shot_cache[stable_id] = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.stable_id == stable_id,
                models.StoryboardShot.variant_index == 0,
            ).first()
        original_shot = original_shot_cache.get(stable_id)
        if not original_shot:
            continue

        has_original_video = bool((original_shot.video_path or "").strip()) and not str(original_shot.video_path or "").startswith("error:")
        has_existing_variants = db.query(func.count(models.StoryboardShot.id)).filter(
            models.StoryboardShot.episode_id == original_shot.episode_id,
            models.StoryboardShot.shot_number == original_shot.shot_number,
            models.StoryboardShot.variant_index > 0,
        ).scalar()
        if not has_original_video and not int(has_existing_variants or 0):
            continue

        reserved_variant_index = _get_next_managed_reserved_variant_index(original_shot, db)
        reserved_shot = _create_managed_reserved_shot(
            original_shot,
            session.provider,
            reserved_variant_index,
        )
        db.add(reserved_shot)
        db.flush()

        task.shot_id = reserved_shot.id
        reserved_count += 1

    return reserved_count


def stop_managed_generation(
    episode_id: int,
    user: models.User,
    db: Session,
):
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    session = db.query(models.ManagedSession).filter(
        models.ManagedSession.episode_id == episode_id,
        models.ManagedSession.status == "running",
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="没有正在运行的托管任务")

    reserved_count = _reserve_legacy_managed_session_slots(session, db)

    session.status = "detached"
    session.completed_at = None
    db.commit()

    return {
        "message": (
            f"托管已转为后台继续收尾，已预留的结果槽位会继续完成"
            + (f"（本次补齐 {reserved_count} 个旧任务槽位）" if reserved_count > 0 else "")
        )
    }


def get_managed_tasks(
    session_id: int,
    status_filter: Optional[str],
    user: models.User,
    db: Session,
) -> List[Dict[str, Any]]:
    session = db.query(models.ManagedSession).filter(
        models.ManagedSession.id == session_id
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == session.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    query = db.query(models.ManagedTask).filter(
        models.ManagedTask.session_id == session_id
    )

    if status_filter and status_filter != "all":
        query = query.filter(models.ManagedTask.status == status_filter)

    tasks = query.order_by(models.ManagedTask.created_at.asc()).all()

    result: List[Dict[str, Any]] = []
    for task in tasks:
        shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id == task.shot_id
        ).first() if task.shot_id > 0 else None

        original_shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.stable_id == task.shot_stable_id,
            models.StoryboardShot.variant_index == 0
        ).first()

        result.append({
            "id": task.id,
            "session_id": task.session_id,
            "shot_id": task.shot_id,
            "shot_stable_id": task.shot_stable_id,
            "shot_number": shot.shot_number if shot else 0,
            "variant_index": shot.variant_index if shot else 0,
            "original_shot_number": original_shot.shot_number if original_shot else 0,
            "video_path": task.video_path,
            "status": task.status,
            "error_message": task.error_message,
            "task_id": task.task_id,
            "prompt_text": task.prompt_text or "",
            "created_at": task.created_at,
            "completed_at": task.completed_at
        })

    return result


def get_managed_session_status(
    episode_id: int,
    user: models.User,
    db: Session,
) -> ManagedSessionStatusResponse:
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    session = db.query(models.ManagedSession).filter(
        models.ManagedSession.episode_id == episode_id
    ).order_by(models.ManagedSession.created_at.desc()).first()

    if not session:
        return ManagedSessionStatusResponse(
            session_id=None,
            status="none",
            total_shots=0,
            completed_shots=0,
            created_at=None
        )

    return ManagedSessionStatusResponse(
        session_id=session.id,
        status=session.status,
        total_shots=session.total_shots,
        completed_shots=session.completed_shots,
        created_at=session.created_at
    )
