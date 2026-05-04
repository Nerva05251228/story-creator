import json
from typing import List, Optional

from sqlalchemy.orm import Session

import models


def parse_storyboard2_card_ids(raw_value) -> List[int]:
    if raw_value is None:
        return []

    try:
        parsed = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except Exception:
        return []

    if not isinstance(parsed, list):
        return []

    resolved_ids = []
    seen = set()
    for item in parsed:
        card_id = None
        if isinstance(item, int):
            card_id = item
        elif isinstance(item, str) and item.strip().isdigit():
            card_id = int(item.strip())

        if not card_id or card_id in seen:
            continue
        seen.add(card_id)
        resolved_ids.append(card_id)

    return resolved_ids


def resolve_storyboard2_selected_card_ids(
    storyboard2_shot: models.Storyboard2Shot,
    db: Session,
) -> List[int]:
    selected_card_ids = parse_storyboard2_card_ids(storyboard2_shot.selected_card_ids)
    if selected_card_ids:
        return selected_card_ids

    if storyboard2_shot.source_shot_id:
        source_shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id == storyboard2_shot.source_shot_id
        ).first()
        if source_shot:
            return parse_storyboard2_card_ids(source_shot.selected_card_ids)

    return []


def is_scene_subject_card_type(card_type: str) -> bool:
    card_type_text = str(card_type or "").strip().lower()
    if not card_type_text:
        return False
    if card_type_text == "scene":
        return True
    if "场景" in card_type_text:
        return True
    if "鍦烘櫙" in card_type_text:
        return True
    return False


def collect_storyboard2_reference_images(
    storyboard2_shot: models.Storyboard2Shot,
    db: Session,
    sub_shot: Optional[models.Storyboard2SubShot] = None,
    include_scene_references: bool = False,
):
    selected_card_ids = parse_storyboard2_card_ids(getattr(sub_shot, "selected_card_ids", "[]"))
    if not selected_card_ids:
        selected_card_ids = resolve_storyboard2_selected_card_ids(storyboard2_shot, db)
    if not selected_card_ids:
        return []

    filtered_card_ids = list(selected_card_ids)
    if not include_scene_references and filtered_card_ids:
        selected_cards = db.query(models.SubjectCard.id, models.SubjectCard.card_type).filter(
            models.SubjectCard.id.in_(filtered_card_ids)
        ).all()
        scene_card_ids = {
            int(card_id) for card_id, card_type in selected_cards
            if is_scene_subject_card_type(card_type)
        }
        safe_filtered_ids = []
        for card_id in filtered_card_ids:
            try:
                card_id_int = int(card_id)
            except Exception:
                continue
            if card_id_int not in scene_card_ids:
                safe_filtered_ids.append(card_id_int)
        filtered_card_ids = safe_filtered_ids
        if not filtered_card_ids:
            return []

    reference_images = []
    seen_urls = set()
    for card_id in filtered_card_ids:
        ref_image = db.query(models.GeneratedImage).filter(
            models.GeneratedImage.card_id == card_id,
            models.GeneratedImage.is_reference == True,
            models.GeneratedImage.status == "completed",
        ).order_by(
            models.GeneratedImage.created_at.desc(),
            models.GeneratedImage.id.desc(),
        ).first()
        if ref_image and ref_image.image_path:
            if ref_image.image_path not in seen_urls:
                seen_urls.add(ref_image.image_path)
                reference_images.append(ref_image.image_path)
            continue

        uploaded_image = db.query(models.CardImage).filter(
            models.CardImage.card_id == card_id
        ).order_by(
            models.CardImage.order.desc(),
            models.CardImage.created_at.desc(),
            models.CardImage.id.desc(),
        ).first()
        if uploaded_image and uploaded_image.image_path:
            if uploaded_image.image_path not in seen_urls:
                seen_urls.add(uploaded_image.image_path)
                reference_images.append(uploaded_image.image_path)

    return reference_images
