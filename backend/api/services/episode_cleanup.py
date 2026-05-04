from typing import Dict, List

from sqlalchemy.orm import Session

import models


def normalize_storyboard_shot_ids(shot_ids: List[int], allow_zero: bool = False) -> List[int]:
    normalized_ids = []
    seen_ids = set()
    for raw_shot_id in shot_ids or []:
        try:
            shot_id = int(raw_shot_id or 0)
        except (TypeError, ValueError):
            continue
        if shot_id < 0 or (shot_id == 0 and not allow_zero) or shot_id in seen_ids:
            continue
        seen_ids.add(shot_id)
        normalized_ids.append(shot_id)
    return normalized_ids


def clear_storyboard_shot_dependencies(
    shot_ids: List[int],
    db: Session,
    allow_zero: bool = False,
) -> Dict[str, int]:
    normalized_shot_ids = normalize_storyboard_shot_ids(shot_ids, allow_zero=allow_zero)
    if not normalized_shot_ids:
        return {
            "storyboard2_unlinked": 0,
            "deleted_collages": 0,
            "deleted_videos": 0,
            "deleted_detail_images": 0,
            "deleted_managed_tasks": 0,
        }

    storyboard2_unlinked = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.source_shot_id.in_(normalized_shot_ids)
    ).update(
        {models.Storyboard2Shot.source_shot_id: None},
        synchronize_session=False,
    )

    deleted_collages = db.query(models.ShotCollage).filter(
        models.ShotCollage.shot_id.in_(normalized_shot_ids)
    ).delete(synchronize_session=False)
    deleted_videos = db.query(models.ShotVideo).filter(
        models.ShotVideo.shot_id.in_(normalized_shot_ids)
    ).delete(synchronize_session=False)
    deleted_detail_images = db.query(models.ShotDetailImage).filter(
        models.ShotDetailImage.shot_id.in_(normalized_shot_ids)
    ).delete(synchronize_session=False)
    deleted_managed_tasks = db.query(models.ManagedTask).filter(
        models.ManagedTask.shot_id.in_(normalized_shot_ids)
    ).delete(synchronize_session=False)

    return {
        "storyboard2_unlinked": int(storyboard2_unlinked or 0),
        "deleted_collages": int(deleted_collages or 0),
        "deleted_videos": int(deleted_videos or 0),
        "deleted_detail_images": int(deleted_detail_images or 0),
        "deleted_managed_tasks": int(deleted_managed_tasks or 0),
    }


def delete_storyboard_shots_by_ids(
    shot_ids: List[int],
    db: Session,
    log_context: str = "",
    allow_zero: bool = False,
) -> int:
    normalized_shot_ids = normalize_storyboard_shot_ids(shot_ids, allow_zero=allow_zero)
    if not normalized_shot_ids:
        return 0

    cleanup_stats = clear_storyboard_shot_dependencies(
        normalized_shot_ids,
        db,
        allow_zero=allow_zero,
    )
    deleted_shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.id.in_(normalized_shot_ids)
    ).delete(synchronize_session=False)

    print(
        "[分镜删除清理] "
        f"{log_context} shots={deleted_shots} "
        f"collages={cleanup_stats['deleted_collages']} "
        f"videos={cleanup_stats['deleted_videos']} "
        f"detail_images={cleanup_stats['deleted_detail_images']} "
        f"managed_tasks={cleanup_stats['deleted_managed_tasks']} "
        f"storyboard2_unlinked={cleanup_stats['storyboard2_unlinked']}"
    )
    return deleted_shots


def delete_episode_storyboard_shots(episode_id: int, db: Session) -> int:
    shot_ids = [
        shot_id
        for shot_id, in db.query(models.StoryboardShot.id).filter(
            models.StoryboardShot.episode_id == episode_id
        ).all()
    ]
    return delete_storyboard_shots_by_ids(
        shot_ids,
        db,
        log_context=f"episode_id={episode_id}",
        allow_zero=True,
    )


def clear_episode_dependencies(episode_ids: List[int], db: Session) -> Dict[str, int]:
    normalized_episode_ids = []
    seen_ids = set()
    for raw_episode_id in episode_ids or []:
        try:
            episode_id = int(raw_episode_id or 0)
        except (TypeError, ValueError):
            continue
        if episode_id <= 0 or episode_id in seen_ids:
            continue
        seen_ids.add(episode_id)
        normalized_episode_ids.append(episode_id)

    if not normalized_episode_ids:
        return {
            "unlinked_libraries": 0,
            "deleted_simple_storyboard_batches": 0,
            "deleted_managed_tasks": 0,
            "deleted_managed_sessions": 0,
            "deleted_voiceover_tts_tasks": 0,
        }

    unlinked_libraries = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id.in_(normalized_episode_ids)
    ).update(
        {models.StoryLibrary.episode_id: None},
        synchronize_session=False,
    )

    deleted_simple_storyboard_batches = db.query(models.SimpleStoryboardBatch).filter(
        models.SimpleStoryboardBatch.episode_id.in_(normalized_episode_ids)
    ).delete(synchronize_session=False)

    managed_session_ids = [
        session_id
        for session_id, in db.query(models.ManagedSession.id).filter(
            models.ManagedSession.episode_id.in_(normalized_episode_ids)
        ).all()
    ]
    deleted_managed_tasks = 0
    if managed_session_ids:
        deleted_managed_tasks = db.query(models.ManagedTask).filter(
            models.ManagedTask.session_id.in_(managed_session_ids)
        ).delete(synchronize_session=False)

    deleted_managed_sessions = db.query(models.ManagedSession).filter(
        models.ManagedSession.episode_id.in_(normalized_episode_ids)
    ).delete(synchronize_session=False)
    deleted_voiceover_tts_tasks = db.query(models.VoiceoverTtsTask).filter(
        models.VoiceoverTtsTask.episode_id.in_(normalized_episode_ids)
    ).delete(synchronize_session=False)

    return {
        "unlinked_libraries": int(unlinked_libraries or 0),
        "deleted_simple_storyboard_batches": int(deleted_simple_storyboard_batches or 0),
        "deleted_managed_tasks": int(deleted_managed_tasks or 0),
        "deleted_managed_sessions": int(deleted_managed_sessions or 0),
        "deleted_voiceover_tts_tasks": int(deleted_voiceover_tts_tasks or 0),
    }
