from typing import Optional

from sqlalchemy.orm import Session

import models
from video_service import process_and_upload_video_with_cover


def build_storyboard2_video_name_tag(video_record: models.Storyboard2SubShotVideo, db: Session) -> str:
    default_tag = f"storyboard2_subshot_{video_record.sub_shot_id}_video_{video_record.id}"
    try:
        sub_shot = db.query(models.Storyboard2SubShot).filter(
            models.Storyboard2SubShot.id == video_record.sub_shot_id
        ).first()
        if not sub_shot:
            return default_tag

        storyboard2_shot = db.query(models.Storyboard2Shot).filter(
            models.Storyboard2Shot.id == sub_shot.storyboard2_shot_id
        ).first()
        shot_label = str(getattr(storyboard2_shot, "shot_number", "x"))
        sub_index = str(getattr(sub_shot, "sub_shot_index", "x"))
        return f"storyboard2_shot_{shot_label}_sub_{sub_index}_video_{video_record.id}"
    except Exception:
        return default_tag


def process_storyboard2_video_cover_and_cdn(
    video_record: models.Storyboard2SubShotVideo,
    db: Session,
    upstream_video_url: str,
    task_id: str,
    debug_dir: Optional[str] = None,
):
    source_url = str(upstream_video_url or "").strip()
    if not source_url:
        return source_url, source_url, False, {"success": False, "error": "empty video url"}

    name_tag = build_storyboard2_video_name_tag(video_record, db)
    task_id_value = str(task_id or video_record.task_id or "").strip()
    process_result = process_and_upload_video_with_cover(
        remote_url=source_url,
        task_id=task_id_value,
        name_tag=name_tag,
    )

    if process_result.get("success") and str(process_result.get("cdn_url") or "").strip():
        final_url = str(process_result.get("cdn_url")).strip()
        return final_url, final_url, True, process_result

    return source_url, source_url, False, process_result
