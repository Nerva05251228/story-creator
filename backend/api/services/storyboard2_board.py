import json
import re
from typing import List, Optional

from sqlalchemy.orm import Session

import models
from api.services import storyboard2_reference_images
from api.services import storyboard_sync


ALLOWED_CARD_TYPES = storyboard_sync.ALLOWED_CARD_TYPES
SOUND_CARD_TYPE = "\u58f0\u97f3"


def normalize_jimeng_ratio(value: Optional[str], default_ratio: str = "9:16") -> str:
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


def clean_scene_ai_prompt_text(ai_prompt: str) -> str:
    text_value = str(ai_prompt or "")
    if not text_value:
        return ""
    text_value = re.sub(r'生成图片的风格是：[^\n]*\n?', '', text_value)
    text_value = re.sub(r'生成图片中场景的是：', '', text_value)
    return text_value.strip()


def extract_scene_description_from_card_ids(card_ids: List[int], db: Session) -> str:
    if not card_ids:
        return ""

    try:
        all_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.id.in_(card_ids)
        ).all()
        scene_cards = [card for card in all_cards if storyboard2_reference_images.is_scene_subject_card_type(getattr(card, "card_type", ""))]
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
            clean_prompt = clean_scene_ai_prompt_text(card.ai_prompt or "")
            if not clean_prompt:
                continue
            scene_parts.append(f"{(card.name or '').strip()}{clean_prompt}")

        return "；".join([part for part in scene_parts if str(part or "").strip()])
    except Exception:
        return ""


def resolve_storyboard2_scene_override_text(
    sub_shot: models.Storyboard2SubShot,
    storyboard2_shot: models.Storyboard2Shot,
    db: Session,
    fallback_selected_card_ids: Optional[List[int]] = None
) -> str:
    scene_override = str(getattr(sub_shot, "scene_override", "") or "").strip()
    scene_override_locked = bool(getattr(sub_shot, "scene_override_locked", False))
    if scene_override or scene_override_locked:
        return scene_override

    selected_card_ids = storyboard2_reference_images.parse_storyboard2_card_ids(getattr(sub_shot, "selected_card_ids", "[]"))
    if not selected_card_ids:
        if fallback_selected_card_ids is not None:
            selected_card_ids = list(fallback_selected_card_ids)
        else:
            selected_card_ids = storyboard2_reference_images.resolve_storyboard2_selected_card_ids(storyboard2_shot, db)

    scene_from_cards = extract_scene_description_from_card_ids(selected_card_ids, db)
    if scene_from_cards:
        return scene_from_cards

    if storyboard2_shot and storyboard2_shot.source_shot_id:
        source_shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id == storyboard2_shot.source_shot_id
        ).first()
        if source_shot and (source_shot.scene_override or "").strip():
            return (source_shot.scene_override or "").strip()

    return ""


def pick_storyboard2_source_shots(episode_id: int, db: Session):
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


def ensure_storyboard2_initialized(episode_id: int, db: Session) -> bool:
    existing_count = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.episode_id == episode_id
    ).count()

    if existing_count > 0:
        return False

    source_shots = pick_storyboard2_source_shots(episode_id, db)
    if not source_shots:
        return False

    for order_index, source_shot in enumerate(source_shots, start=1):
        excerpt = (
            (source_shot.script_excerpt or "").strip()
            or (source_shot.scene_override or "").strip()
            or (source_shot.storyboard_dialogue or "").strip()
            or f"镜头{source_shot.shot_number}原文描述"
        )
        initial_selected_card_ids = storyboard2_reference_images.parse_storyboard2_card_ids(source_shot.selected_card_ids)

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
            or extract_scene_description_from_card_ids(initial_selected_card_ids, db)
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


def serialize_storyboard2_board(episode_id: int, db: Session):
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
                subject_type_sort_key(card.card_type),
                (card.name or ""),
                card.id
            )
        )
        card_map = {card.id: card for card in all_library_cards}

    selected_card_ids_by_storyboard2_shot = {}
    for shot in storyboard2_shots:
        selected_ids = storyboard2_reference_images.parse_storyboard2_card_ids(shot.selected_card_ids)
        if not selected_ids:
            source_shot = source_shot_map.get(shot.source_shot_id)
            if source_shot:
                selected_ids = storyboard2_reference_images.parse_storyboard2_card_ids(source_shot.selected_card_ids)

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
                candidate_size = normalize_jimeng_ratio(getattr(candidate, "size", None), default_ratio="9:16")
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
                current_size = normalize_jimeng_ratio(getattr(current_image, "size", None), default_ratio="9:16")
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
                normalized_video_status = normalize_storyboard2_video_status(
                    str(video.status or "pending"),
                    default_value="processing"
                )
                video_payload.append({
                    "id": video.id,
                    "task_id": video.task_id or "",
                    "model_name": video.model_name or "grok",
                    "duration": int(video.duration or 6),
                    "aspect_ratio": normalize_jimeng_ratio(getattr(video, "aspect_ratio", None), default_ratio="9:16"),
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
                    if is_storyboard2_video_processing(str(item.status or ""))
                ),
                None
            )
            if processing_video:
                video_generate_status = "processing"
                video_generate_progress = max(0, min(int(processing_video.progress or 0), 99))
                video_generate_error = processing_video.error_message or ""
            elif latest_video and normalize_storyboard2_video_status(str(latest_video.status or ""), default_value="processing") == "failed":
                video_generate_status = "failed"
                video_generate_progress = 0
                video_generate_error = latest_video.error_message or ""
            else:
                video_generate_status = "idle"
                video_generate_progress = 0
                video_generate_error = ""

            sub_selected_card_ids = storyboard2_reference_images.parse_storyboard2_card_ids(getattr(sub, "selected_card_ids", "[]"))
            if not sub_selected_card_ids:
                sub_selected_card_ids = list(selected_card_ids_by_storyboard2_shot.get(shot.id, []))
            if card_map:
                sub_selected_card_ids = [card_id for card_id in sub_selected_card_ids if card_id in card_map]
            sub_scene_override_locked = bool(getattr(sub, "scene_override_locked", False))
            sub_scene_override = resolve_storyboard2_scene_override_text(
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


def subject_type_sort_key(card_type: str) -> int:
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


def normalize_storyboard2_video_status(status: str, default_value: str = "processing") -> str:
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


def is_storyboard2_video_processing(status: str) -> bool:
    return normalize_storyboard2_video_status(status, default_value="processing") in {"pending", "processing"}
