import json
from typing import List, Optional

import models
from storyboard_video_reference import resolve_scene_reference_image_url


SCENE_CARD_TYPE = "场景"


def parse_card_ids(raw_value) -> List[int]:
    if raw_value is None:
        return []
    try:
        parsed = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []

    resolved = []
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
        resolved.append(card_id)
    return resolved


def resolve_selected_cards(
    db,
    selected_ids: List[int],
    library_id: Optional[int] = None,
) -> List[models.SubjectCard]:
    if not selected_ids:
        return []

    query = db.query(models.SubjectCard).filter(
        models.SubjectCard.id.in_(selected_ids)
    )
    if library_id is not None:
        query = query.filter(models.SubjectCard.library_id == library_id)

    cards = query.all()
    card_map = {card.id: card for card in cards if card}
    return [card_map[card_id] for card_id in selected_ids if card_id in card_map]


def get_subject_card_reference_image_url(
    card_id: int,
    db,
    *,
    allow_uploaded_fallback: bool = True,
) -> str:
    reference_image = db.query(models.GeneratedImage).filter(
        models.GeneratedImage.card_id == card_id,
        models.GeneratedImage.is_reference == True,
        models.GeneratedImage.status == "completed",
    ).order_by(
        models.GeneratedImage.created_at.desc(),
        models.GeneratedImage.id.desc(),
    ).first()
    if reference_image and str(reference_image.image_path or "").strip():
        return str(reference_image.image_path).strip()

    if not allow_uploaded_fallback:
        return ""

    uploaded_image = db.query(models.CardImage).filter(
        models.CardImage.card_id == card_id
    ).order_by(
        models.CardImage.order.desc(),
        models.CardImage.id.desc(),
    ).first()
    if uploaded_image and str(uploaded_image.image_path or "").strip():
        return str(uploaded_image.image_path).strip()
    return ""


def collect_storyboard_subject_reference_urls(
    shot: models.StoryboardShot,
    db,
    *,
    allow_uploaded_fallback: bool = True,
) -> List[str]:
    selected_ids = parse_card_ids(getattr(shot, "selected_card_ids", "[]"))
    if not selected_ids:
        return []

    selected_cards = resolve_selected_cards(db, selected_ids)
    reference_urls: List[str] = []
    seen_urls = set()
    for card in selected_cards:
        if not card:
            continue
        image_url = get_subject_card_reference_image_url(
            card.id,
            db,
            allow_uploaded_fallback=allow_uploaded_fallback,
        )
        normalized_url = str(image_url or "").strip()
        if not normalized_url or normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        reference_urls.append(normalized_url)
    return reference_urls


def get_selected_scene_card_image_url(shot: models.StoryboardShot, db) -> str:
    selected_ids = parse_card_ids(getattr(shot, "selected_card_ids", "[]"))
    selected_cards = resolve_selected_cards(db, selected_ids)
    for card in selected_cards:
        if not card or card.card_type != SCENE_CARD_TYPE:
            continue
        image_url = get_subject_card_reference_image_url(
            card.id,
            db,
            allow_uploaded_fallback=False,
        )
        if image_url:
            return image_url
    return ""


def resolve_selected_scene_reference_image_url(shot: models.StoryboardShot, db) -> str:
    return resolve_scene_reference_image_url(
        selected_scene_card_image_url=get_selected_scene_card_image_url(shot, db),
        uploaded_scene_image_url=getattr(shot, "uploaded_scene_image_url", ""),
        use_uploaded_scene_image=bool(getattr(shot, "use_uploaded_scene_image", False)),
    )
