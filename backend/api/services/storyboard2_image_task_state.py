from threading import Lock

from sqlalchemy.orm import Session

import models


storyboard2_active_image_tasks = set()
storyboard2_active_image_tasks_lock = Lock()


def recover_orphan_storyboard2_image_tasks(episode_id: int, db: Session) -> int:
    processing_rows = (
        db.query(models.Storyboard2SubShot)
        .join(
            models.Storyboard2Shot,
            models.Storyboard2SubShot.storyboard2_shot_id == models.Storyboard2Shot.id,
        )
        .filter(
            models.Storyboard2Shot.episode_id == episode_id,
            models.Storyboard2SubShot.image_generate_status == "processing",
        )
        .all()
    )

    if not processing_rows:
        return 0

    with storyboard2_active_image_tasks_lock:
        active_ids = set(storyboard2_active_image_tasks)

    recovered_count = 0
    for row in processing_rows:
        if row.id in active_ids:
            continue
        row.image_generate_status = "failed"
        row.image_generate_progress = ""
        current_error = str(getattr(row, "image_generate_error", "") or "").strip()
        if not current_error:
            row.image_generate_error = "服务重启后任务中断，请重新生成"
        recovered_count += 1

    if recovered_count > 0:
        db.commit()

    return recovered_count


def mark_storyboard2_image_task_active(sub_shot_id: int):
    try:
        task_id = int(sub_shot_id)
    except Exception:
        return
    with storyboard2_active_image_tasks_lock:
        storyboard2_active_image_tasks.add(task_id)


def mark_storyboard2_image_task_inactive(sub_shot_id: int):
    try:
        task_id = int(sub_shot_id)
    except Exception:
        return
    with storyboard2_active_image_tasks_lock:
        storyboard2_active_image_tasks.discard(task_id)


def is_storyboard2_image_task_active(sub_shot_id: int) -> bool:
    try:
        task_id = int(sub_shot_id)
    except Exception:
        return False
    with storyboard2_active_image_tasks_lock:
        return task_id in storyboard2_active_image_tasks
