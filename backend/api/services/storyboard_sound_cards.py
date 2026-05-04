import json
from typing import Any, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

import models
from api.services import storyboard_reference_assets


SOUND_CARD_TYPE = "声音"
ROLE_CARD_TYPE = "角色"


def parse_storyboard_sound_card_ids(raw_value: Any) -> Optional[List[int]]:
    """Parse storyboard selected_sound_card_ids; None means auto/default mode."""
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        if not raw_value.strip():
            return None
        try:
            parsed = json.loads(raw_value)
        except Exception:
            return None
    else:
        parsed = raw_value

    if parsed is None or not isinstance(parsed, list):
        return None

    normalized = []
    seen = set()
    for item in parsed:
        try:
            card_id = int(item)
        except Exception:
            continue
        if card_id <= 0 or card_id in seen:
            continue
        seen.add(card_id)
        normalized.append(card_id)
    return normalized


def get_episode_story_library(episode_id: int, db: Session) -> Optional[models.StoryLibrary]:
    return db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == episode_id
    ).first()


def normalize_storyboard_selected_sound_card_ids(
    raw_ids: Optional[List[int]],
    episode_id: int,
    db: Session,
) -> Optional[List[int]]:
    if raw_ids is None:
        return None

    normalized = []
    seen = set()
    for item in raw_ids:
        try:
            card_id = int(item)
        except Exception:
            continue
        if card_id <= 0 or card_id in seen:
            continue
        seen.add(card_id)
        normalized.append(card_id)

    if not normalized:
        return []

    library = get_episode_story_library(episode_id, db)
    if not library:
        raise HTTPException(status_code=400, detail="当前片段未创建主体库，无法保存声音选择")

    valid_rows = db.query(models.SubjectCard.id).filter(
        models.SubjectCard.id.in_(normalized),
        models.SubjectCard.library_id == library.id,
        models.SubjectCard.card_type == SOUND_CARD_TYPE,
    ).all()
    valid_ids = {row[0] for row in valid_rows}
    invalid_ids = [card_id for card_id in normalized if card_id not in valid_ids]
    if invalid_ids:
        raise HTTPException(status_code=400, detail=f"存在无效声音卡片ID: {invalid_ids}")
    return [card_id for card_id in normalized if card_id in valid_ids]


def resolve_storyboard_selected_sound_cards(
    shot: models.StoryboardShot,
    db: Session,
) -> List[models.SubjectCard]:
    library = get_episode_story_library(shot.episode_id, db)
    if not library:
        return []

    sound_cards = db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library.id,
        models.SubjectCard.card_type == SOUND_CARD_TYPE,
    ).all()
    if not sound_cards:
        return []

    sound_card_map = {card.id: card for card in sound_cards}
    explicit_sound_ids = parse_storyboard_sound_card_ids(getattr(shot, "selected_sound_card_ids", None))
    if explicit_sound_ids is not None:
        return [sound_card_map[card_id] for card_id in explicit_sound_ids if card_id in sound_card_map]

    selected_role_cards = []
    try:
        selected_ids = json.loads(getattr(shot, "selected_card_ids", "[]") or "[]")
    except Exception:
        selected_ids = []
    if selected_ids:
        selected_cards = storyboard_reference_assets.resolve_selected_cards(db, selected_ids, library.id)
        selected_role_cards = [card for card in selected_cards if getattr(card, "card_type", "") == ROLE_CARD_TYPE]

    linked_sound_map = {}
    fallback_name_map = {}
    narrator_card = None
    for sound_card in sound_cards:
        if (sound_card.name or "").strip() == "旁白" and narrator_card is None:
            narrator_card = sound_card
        linked_id = getattr(sound_card, "linked_card_id", None)
        if linked_id and linked_id not in linked_sound_map:
            linked_sound_map[int(linked_id)] = sound_card
        name_key = (sound_card.name or "").strip()
        if name_key and name_key not in fallback_name_map:
            fallback_name_map[name_key] = sound_card

    resolved = []
    seen = set()
    for role_card in selected_role_cards:
        sound_card = linked_sound_map.get(role_card.id)
        if not sound_card:
            sound_card = fallback_name_map.get((role_card.name or "").strip())
        if sound_card and sound_card.id not in seen:
            resolved.append(sound_card)
            seen.add(sound_card.id)

    if narrator_card and narrator_card.id not in seen:
        resolved.append(narrator_card)

    return resolved
