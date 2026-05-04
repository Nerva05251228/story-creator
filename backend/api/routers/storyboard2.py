import asyncio
import json
import os
import re
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Lock, Thread
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

import billing_service
import image_platform_client
import models
from ai_config import get_ai_config
from ai_service import get_prompt_by_key
from auth import get_current_user
from dashboard_service import log_file_task_event
from database import SessionLocal, get_db
from image_generation_service import (
    download_and_upload_image,
    get_image_status_api_url,
    get_image_submit_api_url,
    jimeng_generate_image_with_polling,
)
from text_relay_service import submit_and_persist_text_task
from video_api_config import get_video_api_headers, get_video_task_create_url, get_video_task_status_url
from video_service import check_video_status, is_transient_video_status_error, process_and_upload_video_with_cover
from api.schemas.episodes import (
    Storyboard2BatchGenerateSoraPromptsRequest,
    Storyboard2GenerateImagesRequest,
    Storyboard2GenerateVideoRequest,
    Storyboard2SetCurrentImageRequest,
    Storyboard2UpdateShotRequest,
    Storyboard2UpdateSubShotRequest,
)
from api.services import (
    billing_charges,
    storyboard2_reference_images,
    storyboard_defaults,
    storyboard_prompt_context,
    storyboard_sync,
    storyboard_video_settings,
)


router = APIRouter()

executor = ThreadPoolExecutor(max_workers=10)

storyboard2_active_image_tasks = set()
storyboard2_active_image_tasks_lock = Lock()

STORYBOARD2_IMAGE_PROMPT_KEY = "storyboard2_image_prompt_prefix"
STORYBOARD2_IMAGE_PROMPT_DEFAULT = "\u751f\u6210\u52a8\u6f2b\u98ce\u683c\u7684\u56fe\u7247"
STORYBOARD2_VIDEO_PROMPT_KEY = "generate_storyboard2_video_prompts"
GROK_RULE_DEFAULT = "\u4e25\u683c\u6309\u7167\u63d0\u793a\u8bcd\u751f\u89c6\u9891\uff0c\u4e0d\u8981\u51fa\u73b0\u5176\u4ed6\u4eba\u7269"
ALLOWED_CARD_TYPES = storyboard_sync.ALLOWED_CARD_TYPES
SOUND_CARD_TYPE = "\u58f0\u97f3"

_DETAIL_IMAGES_MODEL_CONFIG = {
    "seedream-4.0": {},
    "seedream-4.1": {},
    "seedream-4.5": {},
    "seedream-4.6": {},
    "nano-banana-2": {},
    "nano-banana-pro": {},
    "gpt-image-2": {},
}

_record_storyboard2_video_charge = billing_charges.record_storyboard2_video_charge
_record_storyboard2_image_charge = billing_charges.record_storyboard2_image_charge
_normalize_detail_images_provider = storyboard_defaults.normalize_detail_images_provider
_resolve_episode_detail_images_provider = storyboard_defaults.resolve_episode_detail_images_provider
_normalize_detail_images_model = storyboard_defaults.normalize_detail_images_model
_normalize_storyboard2_image_cw = storyboard_defaults.normalize_storyboard2_image_cw
_normalize_storyboard2_video_duration = storyboard_defaults.normalize_storyboard2_video_duration
_normalize_storyboard_video_resolution_name = storyboard_video_settings.normalize_storyboard_video_resolution_name

def _build_image_generation_debug_meta(
    model_key: Optional[str],
    provider: Optional[str] = None,
    actual_model: Optional[str] = None,
    has_reference_images: bool = False,
) -> dict:
    normalized_model = _normalize_detail_images_model(model_key, default_model="seedream-4.0")
    try:
        route = image_platform_client.resolve_image_route(normalized_model, provider=provider)
    except Exception:
        route = {}
    resolved_provider = str(
        provider
        or route.get("provider")
        or (_DETAIL_IMAGES_MODEL_CONFIG.get(normalized_model) or {}).get("provider")
        or ""
    ).strip().lower()
    resolved_actual_model = str(
        actual_model
        or route.get("model")
        or (_DETAIL_IMAGES_MODEL_CONFIG.get(normalized_model) or {}).get("actual_model")
        or normalized_model
    ).strip()
    return {
        "requested_model": normalized_model,
        "provider": resolved_provider,
        "actual_model": resolved_actual_model,
        "submit_api_url": get_image_submit_api_url(
            model_name=normalized_model,
            provider=resolved_provider,
            has_reference_images=has_reference_images,
        ),
        "status_api_url_template": get_image_status_api_url(
            task_id="{task_id}",
            model_name=normalized_model,
            provider=resolved_provider,
        ),
    }

_build_storyboard2_subject_text = storyboard_prompt_context.build_storyboard2_subject_text

def _refresh_storyboard2_prompt_batch_state(episode_id: int, db: Session):
    pending_count = db.query(models.TextRelayTask).filter(
        models.TextRelayTask.task_type == "storyboard2_sora_prompt",
        models.TextRelayTask.status.in_(["submitted", "queued", "running"]),
    ).all()
    pending_task_ids = [int(row.owner_id or 0) for row in pending_count]
    active = False
    if pending_task_ids:
        active = db.query(models.Storyboard2Shot).filter(
            models.Storyboard2Shot.id.in_(pending_task_ids),
            models.Storyboard2Shot.episode_id == episode_id,
        ).count() > 0
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if episode:
        episode.batch_generating_storyboard2_prompts = active

def _submit_storyboard2_prompt_task(
    db: Session,
    *,
    storyboard2_shot: models.Storyboard2Shot,
):
    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == storyboard2_shot.episode_id
    ).first()
    all_subject_cards = []
    if library:
        all_subject_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library.id,
            models.SubjectCard.card_type.in_(ALLOWED_CARD_TYPES)
        ).all()
        all_subject_cards.sort(
            key=lambda card: (
                _subject_type_sort_key(card.card_type),
                (card.name or ""),
                card.id
            )
        )
    subject_names = [card.name for card in all_subject_cards if card and (card.name or "").strip()]
    subject_text = _build_storyboard2_subject_text(all_subject_cards)

    excerpt = (storyboard2_shot.excerpt or "").strip()
    if not excerpt:
        raise ValueError("镜头原文为空")

    source_shot = None
    if storyboard2_shot.source_shot_id:
        source_shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id == storyboard2_shot.source_shot_id
        ).first()
    duration = int(source_shot.duration or 10) if source_shot else 10
    if duration not in (10, 15):
        duration = 10 if duration < 13 else 15

    prompt_template = get_prompt_by_key(STORYBOARD2_VIDEO_PROMPT_KEY)
    prompt = prompt_template.format(
        script_excerpt=excerpt,
        scene_description="",
        subject_text=subject_text,
        safe_duration=duration,
        extra_style="",
    )
    config = get_ai_config("video_prompt")
    request_data = {
        "model": config["model"],
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    task_payload = {
        "episode_id": int(storyboard2_shot.episode_id),
        "storyboard2_shot_id": int(storyboard2_shot.id),
        "duration": int(duration),
        "subject_names": subject_names,
    }
    return submit_and_persist_text_task(
        db,
        task_type="storyboard2_sora_prompt",
        owner_type="storyboard2_shot",
        owner_id=int(storyboard2_shot.id),
        stage_key=STORYBOARD2_VIDEO_PROMPT_KEY,
        function_key="video_prompt",
        request_payload=request_data,
        task_payload=task_payload,
    )

def _normalize_jimeng_ratio(value: Optional[str], default_ratio: str = "9:16") -> str:
    allowed_ratios = {"21:9", "16:9", "3:2", "4:3", "1:1", "3:4", "2:3", "9:16"}
    legacy_map = {
        "1:2": "9:16",
        "2:1": "16:9"
    }
    raw = (value or "").strip()
    normalized = legacy_map.get(raw, raw)
    if normalized in allowed_ratios:
        return normalized
    fallback = legacy_map.get((default_ratio or "").strip(), (default_ratio or "").strip())
    return fallback if fallback in allowed_ratios else "9:16"

def _build_storyboard_video_text_and_images_content(full_prompt: str, image_urls: List[str]) -> list:
    content = [{"type": "text", "text": full_prompt}]
    for url in image_urls or []:
        image_url = str(url or "").strip()
        if image_url:
            content.append({"type": "image_url", "image_url": image_url})
    return content

_parse_storyboard2_card_ids = storyboard2_reference_images.parse_storyboard2_card_ids
_resolve_storyboard2_selected_card_ids = storyboard2_reference_images.resolve_storyboard2_selected_card_ids
_is_scene_subject_card_type = storyboard2_reference_images.is_scene_subject_card_type
_collect_storyboard2_reference_images = storyboard2_reference_images.collect_storyboard2_reference_images

def _verify_episode_permission(episode_id: int, user: models.User, db: Session) -> models.Episode:
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if not script or script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    return episode

def _get_storyboard2_sub_shot_with_permission(sub_shot_id: int, user: models.User, db: Session):
    sub_shot = db.query(models.Storyboard2SubShot).filter(
        models.Storyboard2SubShot.id == sub_shot_id
    ).first()
    if not sub_shot:
        raise HTTPException(status_code=404, detail="分镜不存在")

    storyboard2_shot = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.id == sub_shot.storyboard2_shot_id
    ).first()
    if not storyboard2_shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    _verify_episode_permission(storyboard2_shot.episode_id, user, db)
    return sub_shot, storyboard2_shot


def _get_storyboard2_shot_with_permission(storyboard2_shot_id: int, user: models.User, db: Session):
    storyboard2_shot = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.id == storyboard2_shot_id
    ).first()
    if not storyboard2_shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    _verify_episode_permission(storyboard2_shot.episode_id, user, db)
    return storyboard2_shot


def _clean_scene_ai_prompt_text(ai_prompt: str) -> str:
    text_value = str(ai_prompt or "")
    if not text_value:
        return ""
    text_value = re.sub(r'生成图片的风格是：[^\n]*\n?', '', text_value)
    text_value = re.sub(r'生成图片中场景的是：', '', text_value)
    return text_value.strip()

def _extract_scene_description_from_card_ids(card_ids: List[int], db: Session) -> str:
    if not card_ids:
        return ""

    try:
        all_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.id.in_(card_ids)
        ).all()
        scene_cards = [card for card in all_cards if _is_scene_subject_card_type(getattr(card, "card_type", ""))]
        if not scene_cards:
            return ""

        card_map = {int(card.id): card for card in scene_cards if card}
        scene_parts: List[str] = []
        for raw_card_id in card_ids:
            try:
                card_id = int(raw_card_id)
            except Exception:
                continue
            card = card_map.get(card_id)
            if not card:
                continue
            clean_prompt = _clean_scene_ai_prompt_text(card.ai_prompt or "")
            if not clean_prompt:
                continue
            scene_parts.append(f"{(card.name or '').strip()}{clean_prompt}")

        return "；".join([part for part in scene_parts if str(part or "").strip()])
    except Exception:
        return ""

def _resolve_storyboard2_scene_override_text(
    sub_shot: models.Storyboard2SubShot,
    storyboard2_shot: models.Storyboard2Shot,
    db: Session,
    fallback_selected_card_ids: Optional[List[int]] = None
) -> str:
    scene_override = str(getattr(sub_shot, "scene_override", "") or "").strip()
    scene_override_locked = bool(getattr(sub_shot, "scene_override_locked", False))
    if scene_override or scene_override_locked:
        return scene_override

    selected_card_ids = _parse_storyboard2_card_ids(getattr(sub_shot, "selected_card_ids", "[]"))
    if not selected_card_ids:
        if fallback_selected_card_ids is not None:
            selected_card_ids = list(fallback_selected_card_ids)
        else:
            selected_card_ids = _resolve_storyboard2_selected_card_ids(storyboard2_shot, db)

    scene_from_cards = _extract_scene_description_from_card_ids(selected_card_ids, db)
    if scene_from_cards:
        return scene_from_cards

    if storyboard2_shot and storyboard2_shot.source_shot_id:
        source_shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id == storyboard2_shot.source_shot_id
        ).first()
        if source_shot and (source_shot.scene_override or "").strip():
            return (source_shot.scene_override or "").strip()

    return ""

def _pick_storyboard2_source_shots(episode_id: int, db: Session):
    all_shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id
    ).order_by(
        models.StoryboardShot.shot_number.asc(),
        models.StoryboardShot.variant_index.asc(),
        models.StoryboardShot.id.asc()
    ).all()

    selected_by_number = {}
    for shot in all_shots:
        shot_number = int(shot.shot_number or 0)
        current = selected_by_number.get(shot_number)
        if not current:
            selected_by_number[shot_number] = shot
            continue

        current_variant = int(current.variant_index or 0)
        this_variant = int(shot.variant_index or 0)
        if current_variant != 0 and this_variant == 0:
            selected_by_number[shot_number] = shot

    ordered_numbers = sorted(selected_by_number.keys())
    return [selected_by_number[num] for num in ordered_numbers]

def _ensure_storyboard2_initialized(episode_id: int, db: Session) -> bool:
    existing_count = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.episode_id == episode_id
    ).count()

    if existing_count > 0:
        return False

    source_shots = _pick_storyboard2_source_shots(episode_id, db)
    if not source_shots:
        return False

    for order_index, source_shot in enumerate(source_shots, start=1):
        excerpt = (
            (source_shot.script_excerpt or "").strip()
            or (source_shot.scene_override or "").strip()
            or (source_shot.storyboard_dialogue or "").strip()
            or f"镜头{source_shot.shot_number}原文描述"
        )
        initial_selected_card_ids = _parse_storyboard2_card_ids(source_shot.selected_card_ids)

        storyboard2_shot = models.Storyboard2Shot(
            episode_id=episode_id,
            source_shot_id=source_shot.id,
            shot_number=int(source_shot.shot_number or order_index),
            excerpt=excerpt,
            selected_card_ids=json.dumps(initial_selected_card_ids, ensure_ascii=False),
            display_order=order_index
        )
        db.add(storyboard2_shot)
        db.flush()

        initial_scene_override = (
            (source_shot.scene_override or "").strip()
            or _extract_scene_description_from_card_ids(initial_selected_card_ids, db)
        )

        # 初始化仅保留一条空分镜，后续由“批量生成Sora提示词”再细化
        db.add(models.Storyboard2SubShot(
            storyboard2_shot_id=storyboard2_shot.id,
            sub_shot_index=1,
            time_range="",
            visual_text="",
            audio_text="",
            sora_prompt="",
            scene_override=initial_scene_override,
            scene_override_locked=False
        ))

    db.commit()
    return True

def _recover_orphan_storyboard2_image_tasks(episode_id: int, db: Session) -> int:
    """回收故事板2镜头图孤儿任务（服务重启后遗留processing）。"""
    processing_rows = db.query(models.Storyboard2SubShot).join(
        models.Storyboard2Shot,
        models.Storyboard2SubShot.storyboard2_shot_id == models.Storyboard2Shot.id
    ).filter(
        models.Storyboard2Shot.episode_id == episode_id,
        models.Storyboard2SubShot.image_generate_status == "processing"
    ).all()

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

def _serialize_storyboard2_board(episode_id: int, db: Session):
    storyboard2_shots = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.episode_id == episode_id
    ).order_by(
        models.Storyboard2Shot.display_order.asc(),
        models.Storyboard2Shot.shot_number.asc(),
        models.Storyboard2Shot.id.asc()
    ).all()

    source_shot_ids = [shot.source_shot_id for shot in storyboard2_shots if shot.source_shot_id]
    source_shot_map = {}
    if source_shot_ids:
        source_shots = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id.in_(source_shot_ids)
        ).all()
        source_shot_map = {item.id: item for item in source_shots}

    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == episode_id
    ).first()
    library_id = library.id if library else None

    all_library_cards = []
    card_map = {}
    if library_id:
        all_library_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library_id,
            models.SubjectCard.card_type.in_(ALLOWED_CARD_TYPES)
        ).all()
        all_library_cards.sort(
            key=lambda card: (
                _subject_type_sort_key(card.card_type),
                (card.name or ""),
                card.id
            )
        )
        card_map = {card.id: card for card in all_library_cards}

    selected_card_ids_by_storyboard2_shot = {}
    for shot in storyboard2_shots:
        selected_ids = _parse_storyboard2_card_ids(shot.selected_card_ids)
        if not selected_ids:
            source_shot = source_shot_map.get(shot.source_shot_id)
            if source_shot:
                selected_ids = _parse_storyboard2_card_ids(source_shot.selected_card_ids)

        if card_map:
            selected_ids = [card_id for card_id in selected_ids if card_id in card_map]

        selected_card_ids_by_storyboard2_shot[shot.id] = selected_ids

    reference_image_map = {}
    uploaded_image_map = {}
    all_card_ids = list(card_map.keys())
    if all_card_ids:
        reference_images = db.query(models.GeneratedImage).filter(
            models.GeneratedImage.card_id.in_(all_card_ids),
            models.GeneratedImage.is_reference == True,
            models.GeneratedImage.status == "completed"
        ).order_by(
            models.GeneratedImage.created_at.desc(),
            models.GeneratedImage.id.desc()
        ).all()
        for image in reference_images:
            if image.card_id not in reference_image_map and image.image_path:
                reference_image_map[image.card_id] = image.image_path

        uploaded_images = db.query(models.CardImage).filter(
            models.CardImage.card_id.in_(all_card_ids)
        ).order_by(
            models.CardImage.order.desc(),
            models.CardImage.created_at.desc(),
            models.CardImage.id.desc()
        ).all()
        for image in uploaded_images:
            if image.card_id not in uploaded_image_map and image.image_path:
                uploaded_image_map[image.card_id] = image.image_path

    all_images = db.query(models.Storyboard2SubShotImage).join(
        models.Storyboard2SubShot,
        models.Storyboard2SubShotImage.sub_shot_id == models.Storyboard2SubShot.id
    ).join(
        models.Storyboard2Shot,
        models.Storyboard2SubShot.storyboard2_shot_id == models.Storyboard2Shot.id
    ).filter(
        models.Storyboard2Shot.episode_id == episode_id
    ).order_by(
        models.Storyboard2SubShotImage.id.desc()
    ).all()

    image_map = {img.id: img for img in all_images}
    images_by_sub_shot = {}
    for img in all_images:
        images_by_sub_shot.setdefault(img.sub_shot_id, []).append(img)

    all_videos = db.query(models.Storyboard2SubShotVideo).join(
        models.Storyboard2SubShot,
        models.Storyboard2SubShotVideo.sub_shot_id == models.Storyboard2SubShot.id
    ).join(
        models.Storyboard2Shot,
        models.Storyboard2SubShot.storyboard2_shot_id == models.Storyboard2Shot.id
    ).filter(
        models.Storyboard2Shot.episode_id == episode_id,
        models.Storyboard2SubShotVideo.is_deleted == False
    ).order_by(
        models.Storyboard2SubShotVideo.created_at.asc(),
        models.Storyboard2SubShotVideo.id.asc()
    ).all()

    videos_by_sub_shot = {}
    for video in all_videos:
        videos_by_sub_shot.setdefault(video.sub_shot_id, []).append(video)

    shot_payload = []
    for shot in storyboard2_shots:
        sub_shots = sorted(list(shot.sub_shots or []), key=lambda x: (x.sub_shot_index, x.id))
        sub_payload = []

        for sub in sub_shots:
            candidates = images_by_sub_shot.get(sub.id, [])
            candidate_count = len(candidates)
            candidate_payload = []
            for idx, candidate in enumerate(candidates, start=1):
                candidate_size = _normalize_jimeng_ratio(getattr(candidate, "size", None), default_ratio="9:16")
                candidate_payload.append({
                    "id": candidate.id,
                    "label": f"候选{idx}",
                    "image_url": candidate.image_url,
                    "size": candidate_size,
                    "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
                    "deletable": candidate_count > 1 and sub.current_image_id != candidate.id
                })

            current_image = image_map.get(sub.current_image_id) if sub.current_image_id else None
            current_payload = None
            if current_image:
                current_size = _normalize_jimeng_ratio(getattr(current_image, "size", None), default_ratio="9:16")
                current_payload = {
                    "id": current_image.id,
                    "image_url": current_image.image_url,
                    "size": current_size,
                    "source_sub_shot_id": current_image.sub_shot_id,
                    "created_at": current_image.created_at.isoformat() if current_image.created_at else None
                }

            sub_videos = videos_by_sub_shot.get(sub.id, [])
            video_payload = []
            for video in sub_videos:
                normalized_video_status = _normalize_storyboard2_video_status(
                    str(video.status or "pending"),
                    default_value="processing"
                )
                video_payload.append({
                    "id": video.id,
                    "task_id": video.task_id or "",
                    "model_name": video.model_name or "grok",
                    "duration": int(video.duration or 6),
                    "aspect_ratio": _normalize_jimeng_ratio(getattr(video, "aspect_ratio", None), default_ratio="9:16"),
                    "status": normalized_video_status,
                    "progress": int(video.progress or 0),
                    "video_url": video.video_url or "",
                    "thumbnail_url": video.thumbnail_url or "",
                    "error_message": video.error_message or "",
                    "cdn_uploaded": bool(video.cdn_uploaded),
                    "created_at": video.created_at.isoformat() if video.created_at else None
                })

            latest_video = sub_videos[-1] if sub_videos else None
            processing_video = next(
                (
                    item for item in reversed(sub_videos)
                    if _is_storyboard2_video_processing(str(item.status or ""))
                ),
                None
            )
            if processing_video:
                video_generate_status = "processing"
                video_generate_progress = max(0, min(int(processing_video.progress or 0), 99))
                video_generate_error = processing_video.error_message or ""
            elif latest_video and _normalize_storyboard2_video_status(str(latest_video.status or ""), default_value="processing") == "failed":
                video_generate_status = "failed"
                video_generate_progress = 0
                video_generate_error = latest_video.error_message or ""
            else:
                video_generate_status = "idle"
                video_generate_progress = 0
                video_generate_error = ""

            sub_selected_card_ids = _parse_storyboard2_card_ids(getattr(sub, "selected_card_ids", "[]"))
            if not sub_selected_card_ids:
                sub_selected_card_ids = list(selected_card_ids_by_storyboard2_shot.get(shot.id, []))
            if card_map:
                sub_selected_card_ids = [card_id for card_id in sub_selected_card_ids if card_id in card_map]
            sub_scene_override_locked = bool(getattr(sub, "scene_override_locked", False))
            sub_scene_override = _resolve_storyboard2_scene_override_text(
                sub_shot=sub,
                storyboard2_shot=shot,
                db=db,
                fallback_selected_card_ids=sub_selected_card_ids
            )

            sub_subjects_payload = []
            for card_id in sub_selected_card_ids:
                card = card_map.get(card_id)
                if not card:
                    continue
                preview_image = reference_image_map.get(card_id) or uploaded_image_map.get(card_id) or ""
                sub_subjects_payload.append({
                    "id": card.id,
                    "name": card.name or "",
                    "alias": card.alias or "",
                    "card_type": card.card_type or "",
                    "preview_image": preview_image
                })

            sub_payload.append({
                "id": sub.id,
                "order": sub.sub_shot_index,
                "time_range": sub.time_range or "",
                "visual_text": sub.visual_text or "",
                "audio_text": sub.audio_text or "",
                "sora_prompt": sub.sora_prompt or "",
                "scene_override": sub_scene_override,
                "scene_override_locked": sub_scene_override_locked,
                "selected_card_ids": sub_selected_card_ids,
                "subjects": sub_subjects_payload,
                "image_generate_status": sub.image_generate_status or "idle",
                "image_generate_progress": sub.image_generate_progress or "",
                "image_generate_error": sub.image_generate_error or "",
                "video_generate_status": video_generate_status,
                "video_generate_progress": video_generate_progress,
                "video_generate_error": video_generate_error,
                "current_image": current_payload,
                "candidates": candidate_payload,
                "videos": video_payload
            })

        subjects_payload = []
        for card_id in selected_card_ids_by_storyboard2_shot.get(shot.id, []):
            card = card_map.get(card_id)
            if not card:
                continue
            preview_image = reference_image_map.get(card_id) or uploaded_image_map.get(card_id) or ""
            subjects_payload.append({
                "id": card.id,
                "name": card.name or "",
                "alias": card.alias or "",
                "card_type": card.card_type or "",
                "preview_image": preview_image
            })

        shot_payload.append({
            "id": shot.id,
            "source_shot_id": shot.source_shot_id,
            "shot_label": str(shot.shot_number),
            "excerpt": shot.excerpt or "",
            "selected_card_ids": selected_card_ids_by_storyboard2_shot.get(shot.id, []),
            "subjects": subjects_payload,
            "sub_shots": sub_payload
        })

    available_subjects = []
    for card in all_library_cards:
        preview_image = reference_image_map.get(card.id) or uploaded_image_map.get(card.id) or ""
        available_subjects.append({
            "id": card.id,
            "name": card.name or "",
            "alias": card.alias or "",
            "card_type": card.card_type or "",
            "preview_image": preview_image
        })

    return {
        "episode_id": episode_id,
        "available_subjects": available_subjects,
        "shots": shot_payload
    }

def _mark_storyboard2_image_task_active(sub_shot_id: int):
    try:
        task_id = int(sub_shot_id)
    except Exception:
        return
    with storyboard2_active_image_tasks_lock:
        storyboard2_active_image_tasks.add(task_id)


def _mark_storyboard2_image_task_inactive(sub_shot_id: int):
    try:
        task_id = int(sub_shot_id)
    except Exception:
        return
    with storyboard2_active_image_tasks_lock:
        storyboard2_active_image_tasks.discard(task_id)


def _is_storyboard2_image_task_active(sub_shot_id: int) -> bool:
    try:
        task_id = int(sub_shot_id)
    except Exception:
        return False
    with storyboard2_active_image_tasks_lock:
        return task_id in storyboard2_active_image_tasks


def _subject_type_sort_key(card_type: str) -> int:
    normalized = str(card_type or "").strip()
    if normalized == "角色":
        return 0
    if normalized == "场景":
        return 1
    if normalized == "道具":
        return 2
    if normalized == SOUND_CARD_TYPE:
        return 3
    return 9

def _normalize_storyboard2_video_status(status: str, default_value: str = "processing") -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"completed", "success", "succeeded", "done"}:
        return "completed"
    if normalized in {"failed", "failure", "error", "cancelled", "canceled", "timeout", "timed_out"}:
        return "failed"
    if normalized in {"submitted", "pending", "queued", "waiting"}:
        return "pending"
    if normalized in {"processing", "running", "in_progress", "preparing", "starting"}:
        return "processing"
    return default_value

def _is_storyboard2_video_processing(status: str) -> bool:
    return _normalize_storyboard2_video_status(status, default_value="processing") in {"pending", "processing"}

def _build_storyboard2_video_name_tag(video_record: models.Storyboard2SubShotVideo, db: Session) -> str:
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

def _process_storyboard2_video_cover_and_cdn(
    video_record: models.Storyboard2SubShotVideo,
    db: Session,
    upstream_video_url: str,
    task_id: str,
    debug_dir: Optional[str] = None
):
    source_url = str(upstream_video_url or "").strip()
    if not source_url:
        return source_url, source_url, False, {"success": False, "error": "empty video url"}

    name_tag = _build_storyboard2_video_name_tag(video_record, db)
    task_id_value = str(task_id or video_record.task_id or "").strip()
    process_result = process_and_upload_video_with_cover(
        remote_url=source_url,
        task_id=task_id_value,
        name_tag=name_tag
    )

    if process_result.get("success") and str(process_result.get("cdn_url") or "").strip():
        final_url = str(process_result.get("cdn_url")).strip()
        return final_url, final_url, True, process_result

    return source_url, source_url, False, process_result


def _get_optional_prompt_config_content(key: str, fallback: str = "") -> str:
    try:
        content = get_prompt_by_key(key)
        content_text = str(content or "").strip()
        if content_text:
            return content_text
    except Exception:
        pass
    return str(fallback or "").strip()


def _save_storyboard2_image_debug(debug_dir: Optional[str], filename: str, payload: dict):
    if not debug_dir:
        return
    try:
        os.makedirs(debug_dir, exist_ok=True)
        file_path = os.path.join(debug_dir, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        log_file_task_event(
            task_folder=os.path.basename(debug_dir),
            file_name=filename,
            payload=payload,
            task_type="storyboard2_image",
            stage="storyboard2_image",
            episode_id=int(payload.get("episode_id")) if isinstance(payload, dict) and payload.get("episode_id") else None,
        )
    except Exception as e:
        print(f"[故事板2镜头图调试] 保存 {filename} 失败: {str(e)}")


def _save_storyboard2_video_debug(debug_dir: Optional[str], filename: str, payload: dict):
    if not debug_dir:
        return
    try:
        os.makedirs(debug_dir, exist_ok=True)
        file_path = os.path.join(debug_dir, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        log_file_task_event(
            task_folder=os.path.basename(debug_dir),
            file_name=filename,
            payload=payload,
            task_type="storyboard2_video",
            stage="storyboard2_video",
            episode_id=int(payload.get("episode_id")) if isinstance(payload, dict) and payload.get("episode_id") else None,
        )
    except Exception as e:
        print(f"[故事板2视频调试] 保存 {filename} 失败: {str(e)}")


def _poll_storyboard2_sub_shot_video_status(
    sub_shot_video_id: int,
    task_id: str,
    debug_dir: Optional[str] = None
):
    """后台轮询故事板2视频任务并落库。"""
    from video_service import check_video_status, is_transient_video_status_error

    polling_history = []
    try:
        while True:
            # 仅在读写数据库时短暂持有会话，避免轮询线程长期占用连接池。
            db = SessionLocal()
            try:
                video_record = db.query(models.Storyboard2SubShotVideo).filter(
                    models.Storyboard2SubShotVideo.id == sub_shot_video_id
                ).first()
                if not video_record:
                    return
                if bool(getattr(video_record, "is_deleted", False)):
                    return

            finally:
                db.close()

            status_info = check_video_status(task_id)
            if is_transient_video_status_error(status_info):
                print(f"[poll] video_id={sub_shot_video_id} task_id={task_id} 上游暂时错误，5秒后重试: {status_info.get('error_message','')}")
                polling_history.append({
                    "polled_at": datetime.now().isoformat(),
                    "status": "query_failed",
                    "progress": 0,
                    "video_url": "",
                    "cdn_uploaded": False,
                    "error_message": str(status_info.get("error_message") or "")
                })
                time.sleep(5)
                continue
            status = _normalize_storyboard2_video_status(
                status_info.get("status"),
                default_value="processing"
            )
            progress_raw = status_info.get("progress")
            error_message = str(status_info.get("error_message") or "").strip()
            video_url = str(status_info.get("video_url") or "").strip()
            cdn_uploaded = bool(status_info.get("cdn_uploaded", False))

            try:
                progress = int(progress_raw) if progress_raw is not None else 0
            except Exception:
                progress = 0

            polling_history.append({
                "polled_at": datetime.now().isoformat(),
                "status": status,
                "progress": progress,
                "video_url": video_url,
                "cdn_uploaded": cdn_uploaded,
                "error_message": error_message
            })
            print(f"[poll] video_id={sub_shot_video_id} task_id={task_id} status={status} progress={progress} video_url={video_url[:60] if video_url else ''}")
            try:
                video_record = db.query(models.Storyboard2SubShotVideo).filter(
                    models.Storyboard2SubShotVideo.id == sub_shot_video_id
                ).first()
                if not video_record:
                    return
                if bool(getattr(video_record, "is_deleted", False)):
                    return

                if _is_storyboard2_video_processing(status):
                    video_record.status = status if status in {"pending", "processing"} else "processing"
                    video_record.progress = max(0, min(progress, 99))
                    video_record.error_message = ""
                    db.commit()
                    should_sleep = True
                elif status == "completed":
                    if not video_url:
                        video_record.status = "failed"
                        video_record.error_message = "任务完成但未返回视频地址"
                        billing_service.reverse_charge_entry(
                            db,
                            billing_key=f"video:storyboard2:{video_record.sub_shot_id}:task:{task_id}",
                            reason="completed_without_video_url",
                        )
                    else:
                        final_video_url = video_url
                        final_thumbnail_url = video_url
                        final_cdn_uploaded = cdn_uploaded

                        if not final_cdn_uploaded:
                            processed_video_url, processed_thumbnail_url, processed_cdn_uploaded, _process_meta = _process_storyboard2_video_cover_and_cdn(
                                video_record=video_record,
                                db=db,
                                upstream_video_url=video_url,
                                task_id=task_id,
                                debug_dir=debug_dir
                            )
                            final_video_url = processed_video_url or final_video_url
                            final_thumbnail_url = processed_thumbnail_url or final_thumbnail_url
                            final_cdn_uploaded = bool(processed_cdn_uploaded)

                        video_record.status = "completed"
                        video_record.video_url = final_video_url
                        if final_thumbnail_url:
                            video_record.thumbnail_url = final_thumbnail_url
                        video_record.progress = 100
                        video_record.error_message = ""
                        video_record.cdn_uploaded = final_cdn_uploaded
                        billing_service.finalize_charge_entry(
                            db,
                            billing_key=f"video:storyboard2:{video_record.sub_shot_id}:task:{task_id}",
                        )
                    db.commit()
                    _save_storyboard2_video_debug(debug_dir, "output.json", {
                        "sub_shot_video_id": sub_shot_video_id,
                        "task_id": task_id,
                        "status": video_record.status,
                        "video_url": video_record.video_url,
                        "thumbnail_url": video_record.thumbnail_url,
                        "cdn_uploaded": video_record.cdn_uploaded,
                        "finished_at": datetime.now().isoformat()
                    })
                    _save_storyboard2_video_debug(debug_dir, "polling_history.json", polling_history)
                    return
                elif status in {"failed", "cancelled"}:
                    video_record.status = "failed"
                    video_record.error_message = error_message or f"任务状态: {status}"
                    billing_service.reverse_charge_entry(
                        db,
                        billing_key=f"video:storyboard2:{video_record.sub_shot_id}:task:{task_id}",
                        reason=f"provider_{status}",
                    )
                    db.commit()
                    _save_storyboard2_video_debug(debug_dir, "error.json", {
                        "sub_shot_video_id": sub_shot_video_id,
                        "task_id": task_id,
                        "status": status,
                        "error_message": video_record.error_message,
                        "failed_at": datetime.now().isoformat()
                    })
                    _save_storyboard2_video_debug(debug_dir, "polling_history.json", polling_history)
                    return
                else:
                    video_record.status = "processing"
                    video_record.progress = max(0, min(progress, 99))
                    db.commit()
                    should_sleep = True
            finally:
                db.close()

            if should_sleep:
                time.sleep(5)
    except Exception as e:
        try:
            db = SessionLocal()
            try:
                db.rollback()
                failed_record = db.query(models.Storyboard2SubShotVideo).filter(
                    models.Storyboard2SubShotVideo.id == sub_shot_video_id
                ).first()
                if failed_record:
                    failed_record.status = "failed"
                    failed_record.error_message = str(e)
                    db.commit()
            finally:
                db.close()
        except Exception:
            pass
        _save_storyboard2_video_debug(debug_dir, "exception.json", {
            "sub_shot_video_id": sub_shot_video_id,
            "task_id": task_id,
            "error": str(e),
            "failed_at": datetime.now().isoformat()
        })
        _save_storyboard2_video_debug(debug_dir, "polling_history.json", polling_history)


def _recover_storyboard2_video_polling():
    """服务重启后，恢复所有处于处理中但无轮询线程的 Storyboard2SubShotVideo 任务。"""
    from threading import Thread
    print("[recover] 开始扫描需要恢复的 storyboard2 视频任务...")
    db = SessionLocal()
    try:
        processing_records = db.query(models.Storyboard2SubShotVideo).filter(
            models.Storyboard2SubShotVideo.is_deleted == False,
            models.Storyboard2SubShotVideo.task_id != "",
            models.Storyboard2SubShotVideo.status.in_(["submitted", "pending", "processing"])
        ).all()
        recovered = [(r.id, r.task_id) for r in processing_records]
    finally:
        db.close()

    print(f"[recover] 扫描完成，找到 {len(recovered)} 条需要恢复的任务")

    for record_id, task_id in recovered:
        print(f"[recover] 恢复轮询: video_id={record_id} task_id={task_id}")
        t = Thread(
            target=_poll_storyboard2_sub_shot_video_status,
            args=(record_id, task_id)
        )
        t.daemon = True
        t.start()

    if recovered:
        print(f"[recover] 已启动 {len(recovered)} 个恢复轮询线程: ids={[r[0] for r in recovered]}")
    else:
        print("[recover] 无需恢复，没有处理中的任务")



def _sync_storyboard2_processing_videos(episode_id: int, db: Session, max_count: int = 20) -> int:
    """
    兜底同步故事板2视频状态。
    作用：当后台轮询线程中断（例如服务重启）时，前端拉取故事板2数据仍能推进状态。
    """
    from video_service import check_video_status

    processing_videos = db.query(models.Storyboard2SubShotVideo).join(
        models.Storyboard2SubShot,
        models.Storyboard2SubShotVideo.sub_shot_id == models.Storyboard2SubShot.id
    ).join(
        models.Storyboard2Shot,
        models.Storyboard2SubShot.storyboard2_shot_id == models.Storyboard2Shot.id
    ).filter(
        models.Storyboard2Shot.episode_id == episode_id,
        models.Storyboard2SubShotVideo.is_deleted == False,
        models.Storyboard2SubShotVideo.status.in_(["submitted", "pending", "processing"])
    ).order_by(
        models.Storyboard2SubShotVideo.created_at.asc(),
        models.Storyboard2SubShotVideo.id.asc()
    ).limit(max_count).all()

    if not processing_videos:
        return 0

    updated_count = 0
    for video in processing_videos:
        task_id = (video.task_id or "").strip()
        if not task_id:
            if (video.status or "").strip().lower() != "failed":
                video.status = "failed"
                video.error_message = "缺少task_id，无法查询任务状态"
                video.progress = 0
                updated_count += 1
            continue

        try:
            status_info = check_video_status(task_id)
        except Exception as e:
            status_info = {
                "status": "query_failed",
                "video_url": "",
                "error_message": f"查询异常: {str(e)}",
                "progress": 0,
                "cdn_uploaded": False,
                "query_ok": False,
                "query_transient": True
            }

        if is_transient_video_status_error(status_info):
            continue

        normalized_status = _normalize_storyboard2_video_status(
            status_info.get("status"),
            default_value="processing"
        )
        try:
            progress = int(status_info.get("progress", 0) or 0)
        except Exception:
            progress = 0
        progress = max(0, min(progress, 100))
        error_message = str(status_info.get("error_message") or "").strip()
        video_url = str(status_info.get("video_url") or "").strip()
        cdn_uploaded = bool(status_info.get("cdn_uploaded", False))

        if normalized_status == "completed":
            if not video_url:
                normalized_status = "failed"
                error_message = error_message or "任务完成但未返回视频地址"
            else:
                final_video_url = video_url
                final_thumbnail_url = video_url
                final_cdn_uploaded = cdn_uploaded

                if not final_cdn_uploaded:
                    processed_video_url, processed_thumbnail_url, processed_cdn_uploaded, _process_meta = _process_storyboard2_video_cover_and_cdn(
                        video_record=video,
                        db=db,
                        upstream_video_url=video_url,
                        task_id=task_id,
                        debug_dir=None
                    )
                    final_video_url = processed_video_url or final_video_url
                    final_thumbnail_url = processed_thumbnail_url or final_thumbnail_url
                    final_cdn_uploaded = bool(processed_cdn_uploaded)

                if (
                    (video.status or "").strip().lower() != "completed"
                    or (video.video_url or "").strip() != final_video_url
                    or int(video.progress or 0) != 100
                    or bool(video.cdn_uploaded) != final_cdn_uploaded
                    or (video.error_message or "")
                ):
                    video.status = "completed"
                    video.video_url = final_video_url
                    if final_thumbnail_url:
                        video.thumbnail_url = final_thumbnail_url
                    video.progress = 100
                    video.error_message = ""
                    video.cdn_uploaded = final_cdn_uploaded
                    updated_count += 1
                billing_service.finalize_charge_entry(
                    db,
                    billing_key=f"video:storyboard2:{video.sub_shot_id}:task:{task_id}",
                )
                continue

        if normalized_status == "failed":
            final_error = error_message or "任务失败"
            if (
                (video.status or "").strip().lower() != "failed"
                or (video.error_message or "") != final_error
                or int(video.progress or 0) != 0
            ):
                video.status = "failed"
                video.error_message = final_error
                video.progress = 0
                updated_count += 1
            billing_service.reverse_charge_entry(
                db,
                billing_key=f"video:storyboard2:{video.sub_shot_id}:task:{task_id}",
                reason="provider_failed",
            )
            continue

        # pending / processing
        target_status = normalized_status if normalized_status in {"pending", "processing"} else "processing"
        target_progress = max(0, min(progress, 99))
        if (
            (video.status or "").strip().lower() != target_status
            or int(video.progress or 0) != target_progress
            or (video.error_message or "")
        ):
            video.status = target_status
            video.progress = target_progress
            video.error_message = ""
            updated_count += 1

    if updated_count > 0:
        db.commit()

    return updated_count

@router.get("/api/episodes/{episode_id}/storyboard2")

async def get_storyboard2_data(
    episode_id: int,
    initialize_if_empty: bool = True,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取故事板2数据，首次为空时从详细分镜初始化"""
    _verify_episode_permission(episode_id, user, db)

    initialized_now = False
    if initialize_if_empty:
        initialized_now = _ensure_storyboard2_initialized(episode_id, db)

    # 回收服务重启后遗留的镜头图processing状态，避免前端一直显示“生成中”。
    try:
        recovered_images = _recover_orphan_storyboard2_image_tasks(episode_id, db)
        if recovered_images > 0:
            print(f"[故事板2镜头图状态回收] episode_id={episode_id} recovered={recovered_images}")
    except Exception as e:
        print(f"[故事板2镜头图状态回收] episode_id={episode_id} 回收失败: {str(e)}")

    # 兜底同步：即使后台轮询线程中断，也能在页面轮询时推进状态
    try:
        _sync_storyboard2_processing_videos(episode_id, db)
    except Exception as e:
        print(f"[故事板2视频状态同步] episode_id={episode_id} 同步失败: {str(e)}")

    payload = _serialize_storyboard2_board(episode_id, db)
    payload["initialized_now"] = initialized_now
    return payload

@router.post("/api/episodes/{episode_id}/storyboard2/batch-generate-sora-prompts")

async def batch_generate_storyboard2_sora_prompts(
    episode_id: int,
    request: Storyboard2BatchGenerateSoraPromptsRequest,
    background_tasks: BackgroundTasks,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """批量生成故事板2分镜的Sora提示词（后台任务）"""
    episode = _verify_episode_permission(episode_id, user, db)

    # 首次为空时自动初始化，确保可生成对象存在
    _ensure_storyboard2_initialized(episode_id, db)

    query = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.episode_id == episode_id
    )
    if request.shot_ids:
        query = query.filter(models.Storyboard2Shot.id.in_(request.shot_ids))

    shot_count = query.count()
    if shot_count == 0:
        raise HTTPException(status_code=400, detail="没有选择有效的镜头")

    print(
        f"[SoraSubjectDebug][storyboard2_batch_request] episode_id={episode_id} "
        f"requested_shot_ids={request.shot_ids if request.shot_ids else 'ALL'} "
        f"matched_shot_count={shot_count}"
    )

    episode.batch_generating_storyboard2_prompts = True
    submitted_count = 0
    storyboard2_shots = query.order_by(
        models.Storyboard2Shot.display_order.asc(),
        models.Storyboard2Shot.shot_number.asc(),
        models.Storyboard2Shot.id.asc()
    ).all()
    for storyboard2_shot in storyboard2_shots:
        try:
            _submit_storyboard2_prompt_task(db, storyboard2_shot=storyboard2_shot)
            submitted_count += 1
        except Exception as exc:
            print(f"[故事板2批量Sora提交失败] shot_id={storyboard2_shot.id} error={str(exc)}")

    _refresh_storyboard2_prompt_batch_state(episode_id, db)
    db.commit()

    return {
        "message": f"故事板2批量生成任务已提交，共 {submitted_count} 个镜头。",
        "total_count": shot_count,
        "submitted_count": submitted_count,
    }


def _process_storyboard2_sub_shot_image_generation(
    sub_shot_id: int,
    prompt_text: str,
    model_name: str,
    provider: Optional[str],
    size: str,
    resolution: str,
    timeout_seconds: int,
    image_cw: int = 50,
    reference_images: Optional[List[str]] = None,
    debug_dir: Optional[str] = None
):
    """后台线程：生成故事板2分镜候选图并落库"""
    _mark_storyboard2_image_task_active(sub_shot_id)
    db_local = SessionLocal()
    normalized_image_cw = _normalize_storyboard2_image_cw(image_cw, default_value=50)
    task_id = None
    polling_history = []
    saved_images = []
    last_task_result = None
    try:
        sub_shot = db_local.query(models.Storyboard2SubShot).filter(
            models.Storyboard2SubShot.id == sub_shot_id
        ).first()
        if not sub_shot:
            return
        storyboard2_shot = db_local.query(models.Storyboard2Shot).filter(
            models.Storyboard2Shot.id == sub_shot.storyboard2_shot_id
        ).first()
        if not storyboard2_shot:
            return

        sub_shot.image_generate_status = "processing"
        sub_shot.image_generate_progress = "1/4"
        sub_shot.image_generate_error = ""
        db_local.commit()

        _save_storyboard2_image_debug(debug_dir, "worker_start.json", {
            "sub_shot_id": sub_shot_id,
            "storyboard2_shot_id": sub_shot.storyboard2_shot_id,
            "prompt_text": prompt_text,
            "provider": provider,
            "model": model_name,
            "size": size,
            "resolution": resolution,
            "timeout_seconds": timeout_seconds,
            "reference_images": reference_images or [],
            "reference_image_count": len(reference_images or []),
            "started_at": datetime.now().isoformat()
        })

        _save_storyboard2_image_debug(debug_dir, "submit_result.json", {
            "provider": provider,
            "model": model_name,
            "submitted_at": datetime.now().isoformat(),
            "requested_image_count": 4
        })

        api_result = jimeng_generate_image_with_polling(
            prompt_text=prompt_text,
            ratio=size,
            cref=reference_images if reference_images else None,
            name=f"storyboard2_subshot_{sub_shot.id}",
            timeout=timeout_seconds,
            cw=normalized_image_cw,
            model=model_name,
            provider=provider,
        )
        last_task_result = api_result
        task_id = str(api_result.get("task_id") or "").strip()

        if task_id:
            _record_storyboard2_image_charge(
                db_local,
                sub_shot=sub_shot,
                storyboard2_shot=storyboard2_shot,
                task_id=task_id,
                model_name=model_name,
                resolution=resolution,
                quantity=4,
                detail_payload={
                    "size": size,
                    "resolution": resolution,
                    "requested_image_count": 4,
                },
            )

        remote_images = api_result.get("images") or []
        polling_history.append({
            "timestamp": datetime.now().isoformat(),
            "status": "completed" if api_result.get("success") else "failed",
            "image_count": len(remote_images),
            "error": api_result.get("error")
        })

        if not api_result.get("success"):
            _save_storyboard2_image_debug(debug_dir, "task_result_failed.json", api_result)
            raise Exception(api_result.get("error") or "镜头图生成失败")

        if not remote_images:
            raise Exception("生成任务已完成，但未返回图片")

        _save_storyboard2_image_debug(debug_dir, "task_result_completed.json", api_result)

        total_count = min(4, len(remote_images))
        new_images = []
        for idx, remote_url in enumerate(remote_images[:4], start=1):
            cdn_url = download_and_upload_image(remote_url, sub_shot.id)
            new_img = models.Storyboard2SubShotImage(
                sub_shot_id=sub_shot.id,
                image_url=cdn_url,
                size=size
            )
            db_local.add(new_img)
            db_local.flush()
            new_images.append(new_img)
            saved_images.append({
                "index": idx,
                "remote_url": remote_url,
                "cdn_url": cdn_url,
                "image_id": new_img.id
            })

            sub_shot.image_generate_progress = f"{idx}/{total_count}"
            db_local.commit()

        if not new_images:
            raise Exception("未成功保存生成图片")

        # 仅在当前图为空时，第一次生成自动将首图设为当前图
        if sub_shot.current_image_id is None:
            sub_shot.current_image_id = new_images[0].id

        sub_shot.image_generate_status = "idle"
        sub_shot.image_generate_progress = ""
        sub_shot.image_generate_error = ""
        if task_id:
            billing_service.record_image_task_cost_for_storyboard2_sub_shot(
                db_local,
                sub_shot_id=int(sub_shot.id),
                stage="storyboard2_image_generate",
                provider=str(api_result.get("provider") or provider or ""),
                model_name=str(api_result.get("model") or model_name or ""),
                resolution=str(api_result.get("resolution") or resolution or ""),
                cost_rmb=api_result.get("cost"),
                external_task_id=str(task_id or ""),
                billing_key=f"image:storyboard2:{sub_shot.id}:task:{task_id}:cost",
                operation_key=f"image:storyboard2:{storyboard2_shot.id}:sub{sub_shot.id}",
                detail_payload={
                    "size": size,
                    "resolution": resolution,
                    "requested_image_count": 4,
                    "remote_image_count": len(remote_images),
                    "saved_image_count": len(saved_images),
                },
            )
        db_local.commit()

        _save_storyboard2_image_debug(debug_dir, "output.json", {
            "provider": provider,
            "task_id": task_id,
            "status": "completed",
            "remote_image_count": len(remote_images),
            "saved_image_count": len(saved_images),
            "saved_images": saved_images,
            "current_image_id": sub_shot.current_image_id,
            "finished_at": datetime.now().isoformat()
        })
        _save_storyboard2_image_debug(debug_dir, "polling_history.json", polling_history)
        return
    except Exception as e:
        _save_storyboard2_image_debug(debug_dir, "error.json", {
            "task_id": task_id,
            "error": str(e),
            "last_task_result": last_task_result,
            "saved_images": saved_images,
            "failed_at": datetime.now().isoformat()
        })
        _save_storyboard2_image_debug(debug_dir, "polling_history.json", polling_history)
        try:
            db_local.rollback()
            failed_sub_shot = db_local.query(models.Storyboard2SubShot).filter(
                models.Storyboard2SubShot.id == sub_shot_id
            ).first()
            if failed_sub_shot:
                failed_sub_shot.image_generate_status = "failed"
                failed_sub_shot.image_generate_progress = ""
                failed_sub_shot.image_generate_error = str(e)
                if task_id:
                    billing_service.reverse_charge_entry(
                        db_local,
                        billing_key=f"image:storyboard2:{failed_sub_shot.id}:task:{task_id}",
                        reason="provider_failed",
                    )
                db_local.commit()
        except Exception:
            pass
    finally:
        db_local.close()
        _mark_storyboard2_image_task_inactive(sub_shot_id)


@router.post("/api/storyboard2/subshots/{sub_shot_id}/generate-images")
async def generate_storyboard2_sub_shot_images(
    sub_shot_id: int,
    request: Storyboard2GenerateImagesRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """为故事板2某个分镜追加4张可选图片（异步后台生成）"""
    sub_shot, storyboard2_shot = _get_storyboard2_sub_shot_with_permission(sub_shot_id, user, db)
    debug_dir = None

    if (sub_shot.image_generate_status or "").strip() == "processing":
        if _is_storyboard2_image_task_active(sub_shot.id):
            return {
                "message": "当前分镜正在生成中",
                "sub_shot_id": sub_shot.id,
                "status": "processing",
                "progress": sub_shot.image_generate_progress or "1/4"
            }
        # 历史遗留的processing（例如重启后线程丢失），先回收为failed，再允许重新提交。
        sub_shot.image_generate_status = "failed"
        sub_shot.image_generate_progress = ""
        if not (sub_shot.image_generate_error or "").strip():
            sub_shot.image_generate_error = "检测到历史任务中断，请重新生成"
        db.commit()

    episode = db.query(models.Episode).filter(models.Episode.id == storyboard2_shot.episode_id).first()
    episode_default_image_model = getattr(episode, "detail_images_model", None) if episode else "seedream-4.0"
    requested_image_model = _normalize_detail_images_model(
        request.model,
        default_model=episode_default_image_model,
    )
    requested_image_provider = _normalize_detail_images_provider(
        request.provider,
        default_provider=_resolve_episode_detail_images_provider(episode)
    ) or None
    image_debug_meta = _build_image_generation_debug_meta(
        requested_image_model,
        provider=requested_image_provider,
    )
    actual_model = image_debug_meta["actual_model"]

    image_prompt_prefix = _get_optional_prompt_config_content(
        STORYBOARD2_IMAGE_PROMPT_KEY,
        STORYBOARD2_IMAGE_PROMPT_DEFAULT
    )

    prompt_parts = []
    if image_prompt_prefix:
        prompt_parts.append(image_prompt_prefix)
    if request.requirement and request.requirement.strip():
        prompt_parts.append(request.requirement.strip())
    if request.style and request.style.strip():
        prompt_parts.append(request.style.strip())
    scene_override_text = _resolve_storyboard2_scene_override_text(
        sub_shot=sub_shot,
        storyboard2_shot=storyboard2_shot,
        db=db
    )
    if scene_override_text:
        prompt_parts.append(scene_override_text)
    visual_prompt = (sub_shot.sora_prompt or "").strip() or (sub_shot.visual_text or "").strip()
    if visual_prompt:
        prompt_parts.append(visual_prompt)

    final_prompt = " ".join(
        str(part or "").replace("\r", " ").replace("\n", " ").strip()
        for part in prompt_parts
        if str(part or "").strip()
    ).strip()
    if not final_prompt:
        raise HTTPException(status_code=400, detail="缺少可用于生成图片的提示词")

    include_scene_references = bool(getattr(episode, "storyboard2_include_scene_references", False)) if episode else False
    image_cw = _normalize_storyboard2_image_cw(
        getattr(episode, "storyboard2_image_cw", None),
        default_value=50
    ) if episode else 50
    reference_images = _collect_storyboard2_reference_images(
        storyboard2_shot,
        db,
        sub_shot=sub_shot,
        include_scene_references=include_scene_references
    )
    timeout_seconds = max(60, min(int(request.timeout_seconds or 420), 1800))
    default_image_ratio = _normalize_jimeng_ratio(getattr(episode, "shot_image_size", None), default_ratio="9:16")
    selected_size = _normalize_jimeng_ratio(request.size, default_ratio=default_image_ratio)

    try:
        debug_folder = (
            f"storyboard2_subshot_{sub_shot.id}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        debug_dir = os.path.abspath(os.path.join("ai_debug", debug_folder))
        existing_candidate_count = db.query(models.Storyboard2SubShotImage).filter(
            models.Storyboard2SubShotImage.sub_shot_id == sub_shot.id
        ).count()

        _save_storyboard2_image_debug(debug_dir, "input.json", {
            "sub_shot_id": sub_shot.id,
            "storyboard2_shot_id": storyboard2_shot.id,
            "episode_id": storyboard2_shot.episode_id,
            "source_shot_id": storyboard2_shot.source_shot_id,
            "sub_shot_index": sub_shot.sub_shot_index,
            "time_range": sub_shot.time_range,
            "visual_text": sub_shot.visual_text,
            "sora_prompt": sub_shot.sora_prompt,
            "scene_override": scene_override_text,
            "sub_shot_selected_card_ids": _parse_storyboard2_card_ids(getattr(sub_shot, "selected_card_ids", "[]")),
            "shot_excerpt": storyboard2_shot.excerpt,
            "image_prompt_prefix": image_prompt_prefix,
            "provider": image_debug_meta["provider"],
            "model": requested_image_model,
            "actual_model": actual_model,
            "size": selected_size,
            "final_prompt": final_prompt,
            "reference_images": reference_images,
            "reference_image_count": len(reference_images),
            "image_cw": image_cw,
            "include_scene_references": include_scene_references,
            "existing_candidate_count": existing_candidate_count,
            "requested_at": datetime.now().isoformat()
        })
        print(f"[故事板2镜头图调试] 已创建调试目录: {debug_dir}")
    except Exception as debug_error:
        debug_dir = None
        print(f"[故事板2镜头图调试] 创建调试目录失败: {str(debug_error)}")

    sub_shot.image_generate_status = "processing"
    sub_shot.image_generate_progress = "1/4"
    sub_shot.image_generate_error = ""
    db.commit()
    _mark_storyboard2_image_task_active(sub_shot.id)
    thread = Thread(
        target=_process_storyboard2_sub_shot_image_generation,
        args=(
            sub_shot.id,
            final_prompt,
            actual_model,
            image_debug_meta["provider"],
            selected_size,
            request.resolution,
            timeout_seconds,
            image_cw,
            reference_images,
            debug_dir
        )
    )
    thread.daemon = True
    try:
        thread.start()
    except Exception as e:
        _mark_storyboard2_image_task_inactive(sub_shot.id)
        sub_shot.image_generate_status = "failed"
        sub_shot.image_generate_progress = ""
        sub_shot.image_generate_error = f"任务启动失败: {str(e)}"
        db.commit()
        raise HTTPException(status_code=500, detail=f"镜头图任务启动失败: {str(e)}")

    return {
        "message": "镜头图生成任务已启动",
        "sub_shot_id": sub_shot.id,
        "status": "processing",
        "progress": "1/4",
        "debug_dir": debug_dir
    }


@router.post("/api/storyboard2/subshots/{sub_shot_id}/generate-video")
async def generate_storyboard2_sub_shot_video(
    sub_shot_id: int,
    request: Storyboard2GenerateVideoRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """为故事板2某个分镜提交视频生成任务（返回task_id并后台轮询落库）。"""
    sub_shot, storyboard2_shot = _get_storyboard2_sub_shot_with_permission(sub_shot_id, user, db)

    processing_video = db.query(models.Storyboard2SubShotVideo).filter(
        models.Storyboard2SubShotVideo.sub_shot_id == sub_shot.id,
        models.Storyboard2SubShotVideo.is_deleted == False,
        models.Storyboard2SubShotVideo.status.in_(["submitted", "pending", "processing"])
    ).order_by(
        models.Storyboard2SubShotVideo.id.desc()
    ).first()
    if processing_video:
        return {
            "message": "当前分镜已有视频任务进行中",
            "sub_shot_id": sub_shot.id,
            "video_id": processing_video.id,
            "task_id": processing_video.task_id,
            "status": "processing",
            "progress": int(processing_video.progress or 0)
        }

    episode = db.query(models.Episode).filter(models.Episode.id == storyboard2_shot.episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    current_image = None
    if sub_shot.current_image_id:
        current_image = db.query(models.Storyboard2SubShotImage).filter(
            models.Storyboard2SubShotImage.id == sub_shot.current_image_id
        ).first()
    if not current_image:
        current_image = db.query(models.Storyboard2SubShotImage).filter(
            models.Storyboard2SubShotImage.sub_shot_id == sub_shot.id
        ).order_by(
            models.Storyboard2SubShotImage.id.asc()
        ).first()
    if not current_image or not (current_image.image_url or "").strip():
        raise HTTPException(status_code=400, detail="请先生成并设置当前图片，再生成视频")

    default_ratio = _normalize_jimeng_ratio(getattr(episode, "shot_image_size", None), default_ratio="9:16")
    selected_ratio = _normalize_jimeng_ratio(request.aspect_ratio, default_ratio=default_ratio)
    default_duration = _normalize_storyboard2_video_duration(
        getattr(episode, "storyboard2_video_duration", None),
        default_value=6
    )
    selected_duration = _normalize_storyboard2_video_duration(request.duration, default_value=default_duration)
    selected_resolution_name = _normalize_storyboard_video_resolution_name(
        request.resolution_name,
        model="grok",
        default_resolution=getattr(episode, "storyboard_video_resolution_name", None) or "720p"
    )

    requested_model = (request.model or "").strip() or "grok"
    actual_model = "grok"

    # 从 GlobalSettings 读取 Grok 准则
    grok_rule = ""
    try:
        grok_setting = db.query(models.GlobalSettings).filter(
            models.GlobalSettings.key == "grok_rule"
        ).first()
        grok_rule = grok_setting.value if grok_setting and grok_setting.value else GROK_RULE_DEFAULT
    except Exception:
        grok_rule = GROK_RULE_DEFAULT

    prompt_parts = []
    if grok_rule:
        prompt_parts.append(grok_rule)
    if storyboard2_shot.excerpt and storyboard2_shot.excerpt.strip():
        prompt_parts.append(storyboard2_shot.excerpt.strip())

    visual_prompt = (sub_shot.sora_prompt or "").strip() or (sub_shot.visual_text or "").strip()
    if visual_prompt:
        prompt_parts.append(visual_prompt)

    final_prompt = "\n".join(prompt_parts).strip()
    if not final_prompt:
        raise HTTPException(status_code=400, detail="缺少可用于生成视频的提示词")

    debug_dir = None
    try:
        request_payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
        debug_folder = (
            f"storyboard2_subshot_video_{sub_shot.id}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        debug_dir = os.path.abspath(os.path.join("ai_debug", debug_folder))
        _save_storyboard2_video_debug(debug_dir, "input.json", {
            "sub_shot_id": sub_shot.id,
            "storyboard2_shot_id": storyboard2_shot.id,
            "episode_id": storyboard2_shot.episode_id,
            "source_shot_id": storyboard2_shot.source_shot_id,
            "sub_shot_index": sub_shot.sub_shot_index,
            "time_range": sub_shot.time_range,
            "visual_text": sub_shot.visual_text,
            "sora_prompt": sub_shot.sora_prompt,
            "shot_excerpt": storyboard2_shot.excerpt,
            "grok_rule": grok_rule,
            "request": request_payload,
            "requested_model": requested_model,
            "actual_model": actual_model,
            "duration": selected_duration,
            "aspect_ratio": selected_ratio,
            "resolution_name": selected_resolution_name,
            "image_url": current_image.image_url,
            "final_prompt": final_prompt,
            "requested_at": datetime.now().isoformat()
        })
    except Exception as debug_error:
        debug_dir = None
        print(f"[故事板2视频调试] 创建调试目录失败: {str(debug_error)}")

    request_data = {
        "username": user.username,
        "provider": "yijia",
        "model": actual_model,
        "content": _build_storyboard_video_text_and_images_content(final_prompt, [current_image.image_url]),
        "ratio": selected_ratio,
        "duration": selected_duration,
        "resolution_name": selected_resolution_name,
    }

    def call_storyboard2_video_api():
        return requests.post(
            get_video_task_create_url(),
            headers=get_video_api_headers(),
            json=request_data,
            timeout=60
        )

    try:
        loop = asyncio.get_event_loop()
        submit_response = await loop.run_in_executor(executor, call_storyboard2_video_api)
    except Exception as e:
        _save_storyboard2_video_debug(debug_dir, "submit_exception.json", {
            "error": str(e),
            "request_data": request_data
        })
        raise HTTPException(status_code=500, detail=f"视频任务提交失败: {str(e)}")

    response_json = {}
    try:
        response_json = submit_response.json()
    except Exception:
        response_json = {"raw_text": submit_response.text}

    if submit_response.status_code != 200:
        _save_storyboard2_video_debug(debug_dir, "submit_error.json", {
            "status_code": submit_response.status_code,
            "request_data": request_data,
            "response": response_json
        })
        raise HTTPException(
            status_code=500,
            detail=f"视频任务提交失败: HTTP {submit_response.status_code}"
        )

    task_id = str(response_json.get("task_id") or "").strip()
    if not task_id:
        _save_storyboard2_video_debug(debug_dir, "submit_error.json", {
            "status_code": submit_response.status_code,
            "request_data": request_data,
            "response": response_json
        })
        raise HTTPException(status_code=500, detail="视频任务提交失败: 未返回task_id")

    raw_status = str(response_json.get("status") or "pending").strip().lower()
    initial_status = _normalize_storyboard2_video_status(raw_status, default_value="pending")

    progress_value = response_json.get("progress", 0)
    try:
        progress_int = max(0, min(int(progress_value), 100))
    except Exception:
        progress_int = 0

    sub_shot_video = models.Storyboard2SubShotVideo(
        sub_shot_id=sub_shot.id,
        task_id=task_id,
        model_name=actual_model,
        duration=selected_duration,
        aspect_ratio=selected_ratio,
        status=initial_status,
        progress=progress_int,
        error_message=""
    )
    db.add(sub_shot_video)
    _record_storyboard2_video_charge(
        db,
        sub_shot=sub_shot,
        storyboard2_shot=storyboard2_shot,
        task_id=task_id,
        model_name=actual_model,
        duration=selected_duration,
        detail_payload={
            "aspect_ratio": selected_ratio,
            "resolution_name": selected_resolution_name,
            "initial_status": initial_status,
            "video_id_pending": True,
        },
    )
    db.commit()
    db.refresh(sub_shot_video)

    if initial_status == "completed":
        billing_service.finalize_charge_entry(
            db,
            billing_key=f"video:storyboard2:{sub_shot.id}:task:{task_id}",
        )
        db.commit()
    elif initial_status == "failed":
        billing_service.reverse_charge_entry(
            db,
            billing_key=f"video:storyboard2:{sub_shot.id}:task:{task_id}",
            reason="submit_failed",
        )
        db.commit()

    _save_storyboard2_video_debug(debug_dir, "submit_result.json", {
        "sub_shot_video_id": sub_shot_video.id,
        "task_id": task_id,
        "request_data": request_data,
        "response": response_json,
        "submitted_at": datetime.now().isoformat()
    })

    if _is_storyboard2_video_processing(initial_status):
        polling_thread = Thread(
            target=_poll_storyboard2_sub_shot_video_status,
            args=(sub_shot_video.id, task_id, debug_dir, 3600)
        )
        polling_thread.daemon = True
        polling_thread.start()
    elif initial_status == "completed":
        upstream_video_url = str(response_json.get("video_url") or "").strip()
        upstream_cdn_uploaded = bool(response_json.get("cdn_uploaded", False))
        final_video_url = upstream_video_url
        final_thumbnail_url = upstream_video_url
        final_cdn_uploaded = upstream_cdn_uploaded

        if upstream_video_url and not upstream_cdn_uploaded:
            processed_video_url, processed_thumbnail_url, processed_cdn_uploaded, _process_meta = _process_storyboard2_video_cover_and_cdn(
                video_record=sub_shot_video,
                db=db,
                upstream_video_url=upstream_video_url,
                task_id=task_id,
                debug_dir=debug_dir
            )
            final_video_url = processed_video_url or final_video_url
            final_thumbnail_url = processed_thumbnail_url or final_thumbnail_url
            final_cdn_uploaded = bool(processed_cdn_uploaded)

        sub_shot_video.video_url = final_video_url
        if final_thumbnail_url:
            sub_shot_video.thumbnail_url = final_thumbnail_url
        sub_shot_video.progress = 100
        sub_shot_video.cdn_uploaded = final_cdn_uploaded
        db.commit()

    return {
        "message": "视频生成任务已启动",
        "sub_shot_id": sub_shot.id,
        "video_id": sub_shot_video.id,
        "task_id": task_id,
        "status": "processing" if _is_storyboard2_video_processing(sub_shot_video.status) else sub_shot_video.status,
        "progress": int(sub_shot_video.progress or 0),
        "debug_dir": debug_dir
    }



@router.patch("/api/storyboard2/shots/{storyboard2_shot_id}")
async def update_storyboard2_shot(
    storyboard2_shot_id: int,
    request: Storyboard2UpdateShotRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    storyboard2_shot = _get_storyboard2_shot_with_permission(storyboard2_shot_id, user, db)
    storyboard2_shot.excerpt = (request.excerpt or "").strip()

    if request.selected_card_ids is not None:
        normalized_card_ids = []
        seen_card_ids = set()
        for card_id in request.selected_card_ids:
            if not isinstance(card_id, int) or card_id <= 0 or card_id in seen_card_ids:
                continue
            seen_card_ids.add(card_id)
            normalized_card_ids.append(card_id)

        if normalized_card_ids:
            library = db.query(models.StoryLibrary).filter(
                models.StoryLibrary.episode_id == storyboard2_shot.episode_id
            ).first()
            if not library:
                raise HTTPException(status_code=400, detail="当前片段未创建主体库，无法保存主体选择")

            valid_cards = db.query(models.SubjectCard.id).filter(
                models.SubjectCard.id.in_(normalized_card_ids),
                models.SubjectCard.library_id == library.id,
                models.SubjectCard.card_type.in_(ALLOWED_CARD_TYPES)
            ).all()
            valid_card_ids = {item[0] for item in valid_cards}
            invalid_ids = [card_id for card_id in normalized_card_ids if card_id not in valid_card_ids]
            if invalid_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"存在无效主体ID: {invalid_ids}"
                )

        storyboard2_shot.selected_card_ids = json.dumps(normalized_card_ids, ensure_ascii=False)

    db.commit()

    return {
        "message": "镜头描述已更新",
        "shot_id": storyboard2_shot.id,
        "excerpt": storyboard2_shot.excerpt,
        "selected_card_ids": _parse_storyboard2_card_ids(storyboard2_shot.selected_card_ids)
    }


@router.patch("/api/storyboard2/subshots/{sub_shot_id}")
async def update_storyboard2_sub_shot(
    sub_shot_id: int,
    request: Storyboard2UpdateSubShotRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    sub_shot, storyboard2_shot = _get_storyboard2_sub_shot_with_permission(sub_shot_id, user, db)

    if request.sora_prompt is not None:
        sub_shot.sora_prompt = (request.sora_prompt or "").strip()

    if request.scene_override is not None:
        sub_shot.scene_override = (request.scene_override or "").strip()
        sub_shot.scene_override_locked = True

    if request.selected_card_ids is not None:
        normalized_card_ids = []
        seen_card_ids = set()
        for card_id in request.selected_card_ids:
            if not isinstance(card_id, int) or card_id <= 0 or card_id in seen_card_ids:
                continue
            seen_card_ids.add(card_id)
            normalized_card_ids.append(card_id)

        if normalized_card_ids:
            library = db.query(models.StoryLibrary).filter(
                models.StoryLibrary.episode_id == storyboard2_shot.episode_id
            ).first()
            if not library:
                raise HTTPException(status_code=400, detail="当前片段未创建主体库，无法保存主体选择")

            valid_cards = db.query(models.SubjectCard.id).filter(
                models.SubjectCard.id.in_(normalized_card_ids),
                models.SubjectCard.library_id == library.id,
                models.SubjectCard.card_type.in_(ALLOWED_CARD_TYPES)
            ).all()
            valid_card_ids = {item[0] for item in valid_cards}
            invalid_ids = [card_id for card_id in normalized_card_ids if card_id not in valid_card_ids]
            if invalid_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"存在无效主体ID: {invalid_ids}"
                )

        sub_shot.selected_card_ids = json.dumps(normalized_card_ids, ensure_ascii=False)
        if not bool(getattr(sub_shot, "scene_override_locked", False)) and not (sub_shot.scene_override or "").strip():
            auto_scene_override = _extract_scene_description_from_card_ids(normalized_card_ids, db)
            if auto_scene_override:
                sub_shot.scene_override = auto_scene_override

    db.commit()

    return {
        "message": "分镜描述已更新",
        "sub_shot_id": sub_shot.id,
        "sora_prompt": sub_shot.sora_prompt or "",
        "scene_override": sub_shot.scene_override or "",
        "scene_override_locked": bool(getattr(sub_shot, "scene_override_locked", False)),
        "selected_card_ids": _parse_storyboard2_card_ids(getattr(sub_shot, "selected_card_ids", "[]"))
    }


@router.delete("/api/storyboard2/videos/{video_id}")
async def delete_storyboard2_video(
    video_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    video_record = db.query(models.Storyboard2SubShotVideo).filter(
        models.Storyboard2SubShotVideo.id == video_id
    ).first()
    if not video_record:
        raise HTTPException(status_code=404, detail="视频不存在")

    owner_sub_shot = db.query(models.Storyboard2SubShot).filter(
        models.Storyboard2SubShot.id == video_record.sub_shot_id
    ).first()
    if not owner_sub_shot:
        raise HTTPException(status_code=404, detail="视频所属分镜不存在")

    owner_storyboard2_shot = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.id == owner_sub_shot.storyboard2_shot_id
    ).first()
    if not owner_storyboard2_shot:
        raise HTTPException(status_code=404, detail="视频所属镜头不存在")

    _verify_episode_permission(owner_storyboard2_shot.episode_id, user, db)

    if bool(getattr(video_record, "is_deleted", False)):
        return {
            "message": "视频已删除",
            "video_id": video_id
        }

    video_record.is_deleted = True
    video_record.deleted_at = datetime.utcnow()
    db.commit()

    return {
        "message": "视频删除成功",
        "video_id": video_id
    }


@router.patch("/api/storyboard2/subshots/{sub_shot_id}/current-image")
async def set_storyboard2_current_image(
    sub_shot_id: int,
    request: Storyboard2SetCurrentImageRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    sub_shot, storyboard2_shot = _get_storyboard2_sub_shot_with_permission(sub_shot_id, user, db)

    if request.current_image_id is not None:
        target_image = db.query(models.Storyboard2SubShotImage).filter(
            models.Storyboard2SubShotImage.id == request.current_image_id
        ).first()
        if not target_image:
            raise HTTPException(status_code=404, detail="图片不存在")

        image_owner_sub_shot = db.query(models.Storyboard2SubShot).filter(
            models.Storyboard2SubShot.id == target_image.sub_shot_id
        ).first()
        if not image_owner_sub_shot:
            raise HTTPException(status_code=404, detail="图片所属分镜不存在")

        image_owner_storyboard2_shot = db.query(models.Storyboard2Shot).filter(
            models.Storyboard2Shot.id == image_owner_sub_shot.storyboard2_shot_id
        ).first()
        if not image_owner_storyboard2_shot or image_owner_storyboard2_shot.episode_id != storyboard2_shot.episode_id:
            raise HTTPException(status_code=400, detail="仅支持设置为同一片段内的图片")

    sub_shot.current_image_id = request.current_image_id
    db.commit()

    return {
        "message": "当前图设置成功",
        "sub_shot_id": sub_shot.id,
        "current_image_id": sub_shot.current_image_id
    }


@router.delete("/api/storyboard2/images/{image_id}")
async def delete_storyboard2_image(
    image_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    image_record = db.query(models.Storyboard2SubShotImage).filter(
        models.Storyboard2SubShotImage.id == image_id
    ).first()
    if not image_record:
        raise HTTPException(status_code=404, detail="图片不存在")

    owner_sub_shot = db.query(models.Storyboard2SubShot).filter(
        models.Storyboard2SubShot.id == image_record.sub_shot_id
    ).first()
    if not owner_sub_shot:
        raise HTTPException(status_code=404, detail="图片所属分镜不存在")

    owner_storyboard2_shot = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.id == owner_sub_shot.storyboard2_shot_id
    ).first()
    if not owner_storyboard2_shot:
        raise HTTPException(status_code=404, detail="图片所属镜头不存在")

    _verify_episode_permission(owner_storyboard2_shot.episode_id, user, db)

    owner_candidate_count = db.query(models.Storyboard2SubShotImage).filter(
        models.Storyboard2SubShotImage.sub_shot_id == owner_sub_shot.id
    ).count()

    if owner_candidate_count <= 1:
        raise HTTPException(status_code=400, detail="当前分镜仅剩1张可选图，无法删除")

    if owner_sub_shot.current_image_id == image_id:
        raise HTTPException(status_code=400, detail="当前图不允许删除")

    referenced_sub_shots = db.query(models.Storyboard2SubShot).join(
        models.Storyboard2Shot,
        models.Storyboard2SubShot.storyboard2_shot_id == models.Storyboard2Shot.id
    ).filter(
        models.Storyboard2Shot.episode_id == owner_storyboard2_shot.episode_id,
        models.Storyboard2SubShot.current_image_id == image_id
    ).all()

    for sub_shot in referenced_sub_shots:
        sub_shot.current_image_id = None

    db.delete(image_record)
    db.commit()

    return {
        "message": "删除成功",
        "image_id": image_id,
        "cleared_current_count": len(referenced_sub_shots)
    }
