import json
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

import billing_service
import models


def safe_json_dumps(payload: Any) -> str:
    try:
        return json.dumps(payload or {}, ensure_ascii=False)
    except Exception:
        return ""


def resolve_storyboard_video_billing_model(
    shot: models.StoryboardShot,
    *,
    resolve_model_by_provider: Callable[..., str],
    default_model: str,
) -> str:
    provider = str(getattr(shot, "provider", "") or "").strip().lower()
    if provider == "yijia-grok":
        provider = "yijia"
    return str(
        resolve_model_by_provider(
            provider,
            default_model=getattr(shot, "storyboard_video_model", None)
            or getattr(shot, "provider", None)
            or default_model,
        )
    )


def record_card_image_charge(
    db: Session,
    *,
    card: models.SubjectCard,
    model_name: str,
    provider: str,
    resolution: str = "",
    task_id: str,
    quantity: int,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    return None


def record_storyboard_image_charge(
    db: Session,
    *,
    shot: models.StoryboardShot,
    model_name: str,
    provider: str,
    resolution: str = "",
    task_id: str,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    return None


def record_detail_image_charge(
    db: Session,
    *,
    detail_img: models.ShotDetailImage,
    shot: models.StoryboardShot,
    model_name: str,
    provider: str,
    resolution: str = "",
    task_id: str,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    return None


def record_storyboard_video_charge(
    db: Session,
    *,
    shot: models.StoryboardShot,
    task_id: str,
    model_name: str,
    stage: str = "video_generate",
    detail_payload: Optional[Dict[str, Any]] = None,
):
    context = billing_service.get_shot_episode_context(db, shot_id=int(shot.id))
    if not context:
        return None
    try:
        return billing_service.create_charge_entry(
            db,
            user_id=int(context["user_id"]),
            script_id=int(context["script_id"]),
            episode_id=int(context["episode_id"]),
            category="video",
            stage=stage,
            provider=str(getattr(shot, "provider", "") or ""),
            model_name=str(model_name or ""),
            quantity=max(1, int(getattr(shot, "duration", 0) or 0)),
            billing_key=f"video:shot:{shot.id}:task:{task_id}",
            operation_key=f"video:shot:{shot.id}",
            initial_status="pending",
            shot_id=int(shot.id),
            attempt_index=1,
            external_task_id=str(task_id or ""),
            detail_json=safe_json_dumps(detail_payload),
        )
    except ValueError:
        return None


def record_storyboard2_video_charge(
    db: Session,
    *,
    sub_shot: models.Storyboard2SubShot,
    storyboard2_shot: models.Storyboard2Shot,
    task_id: str,
    model_name: str,
    duration: int,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    context = billing_service.get_storyboard2_sub_shot_context(db, sub_shot_id=int(sub_shot.id))
    if not context:
        return None
    try:
        return billing_service.create_charge_entry(
            db,
            user_id=int(context["user_id"]),
            script_id=int(context["script_id"]),
            episode_id=int(context["episode_id"]),
            category="video",
            stage="storyboard2_video_generate",
            provider="yijia",
            model_name=str(model_name or "grok"),
            quantity=max(1, int(duration or 0)),
            billing_key=f"video:storyboard2:{sub_shot.id}:task:{task_id}",
            operation_key=f"video:storyboard2:{storyboard2_shot.id}:sub{sub_shot.id}",
            initial_status="pending",
            storyboard2_shot_id=int(storyboard2_shot.id),
            sub_shot_id=int(sub_shot.id),
            attempt_index=1,
            external_task_id=str(task_id or ""),
            detail_json=safe_json_dumps(detail_payload),
        )
    except ValueError:
        return None


def record_storyboard2_image_charge(
    db: Session,
    *,
    sub_shot: models.Storyboard2SubShot,
    storyboard2_shot: models.Storyboard2Shot,
    task_id: str,
    model_name: str,
    resolution: str = "",
    quantity: int,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    return None
