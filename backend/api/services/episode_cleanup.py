from typing import Dict, List

from sqlalchemy.orm import Session

import models


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
