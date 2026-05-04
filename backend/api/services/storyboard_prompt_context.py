from typing import List, Optional

from sqlalchemy.orm import Session

import models


SORA_REFERENCE_PROMPT_INSTRUCTION = "请你参考这段提示词中的人物站位进行编写新的提示词："


def debug_resolve_subject_names(
    db: Session,
    selected_ids: List[int],
    library_id: Optional[int] = None,
) -> List[str]:
    if not selected_ids:
        return []

    query = db.query(models.SubjectCard).filter(
        models.SubjectCard.id.in_(selected_ids)
    )
    if library_id is not None:
        query = query.filter(models.SubjectCard.library_id == library_id)

    cards = query.all()
    card_name_map = {card.id: card.name for card in cards if card and card.name}
    return [card_name_map[card_id] for card_id in selected_ids if card_id in card_name_map]


def build_subject_text_for_ai(selected_cards: List[models.SubjectCard]) -> str:
    if not selected_cards:
        return "无"

    def format_subject_label(card: models.SubjectCard) -> str:
        name = ((getattr(card, "name", "") or "")).strip()
        if not name:
            return ""
        if getattr(card, "card_type", "") == "角色":
            personality = (getattr(card, "role_personality", "") or "").strip()
            if personality:
                return f"{name}-{personality}"
        return name

    male_protagonists = []
    female_protagonists = []
    other_subjects = []

    for card in selected_cards:
        if not card:
            continue
        name = (card.name or "").strip()
        if not name:
            continue
        subject_label = format_subject_label(card)
        if not subject_label:
            continue
        card_gender = getattr(card, "protagonist_gender", "") or ""
        is_protagonist = bool(getattr(card, "is_protagonist", False))
        if card.card_type == "角色" and is_protagonist and card_gender in ("male", "female"):
            if card_gender == "male":
                male_protagonists.append(subject_label)
            else:
                female_protagonists.append(subject_label)
        else:
            other_subjects.append(subject_label)

    segments = []
    for idx, name in enumerate(male_protagonists, start=1):
        segments.append(f"男主{idx}：{name}")
    for idx, name in enumerate(female_protagonists, start=1):
        segments.append(f"女主{idx}：{name}")
    if other_subjects:
        segments.append(f"其他角色、场景或道具：{'、'.join(other_subjects)}")

    if segments:
        return "，".join(segments)

    names = [format_subject_label(card) for card in selected_cards if card]
    names = [name for name in names if name]
    return "、".join(names) if names else "无"


def build_storyboard2_subject_text(selected_cards: List[models.SubjectCard]) -> str:
    if not selected_cards:
        return "无"

    lines = []
    for card in selected_cards:
        if not card:
            continue
        name = ((getattr(card, "name", "") or "")).strip()
        if not name:
            continue
        if getattr(card, "card_type", "") == "角色":
            personality = (getattr(card, "role_personality", "") or "").strip()
            lines.append(f"{name}-{personality}" if personality else name)
        else:
            lines.append(name)

    return "\n".join(lines) if lines else "无"


def resolve_large_shot_template(
    db: Session,
    template_id: Optional[int] = None,
) -> Optional[models.LargeShotTemplate]:
    query = db.query(models.LargeShotTemplate)
    if template_id:
        return query.filter(models.LargeShotTemplate.id == template_id).first()

    default_template = query.filter(models.LargeShotTemplate.is_default == True).order_by(
        models.LargeShotTemplate.id.asc()
    ).first()
    if default_template:
        return default_template

    return query.order_by(
        models.LargeShotTemplate.created_at.asc(),
        models.LargeShotTemplate.id.asc(),
    ).first()


def append_sora_reference_prompt(base_prompt: str, reference_prompt: str) -> str:
    clean_base = str(base_prompt or "").strip()
    clean_reference = str(reference_prompt or "").strip()
    if not clean_reference:
        return clean_base
    reference_block = f"{SORA_REFERENCE_PROMPT_INSTRUCTION}{clean_reference}"
    if not clean_base:
        return reference_block
    return f"{clean_base}\n\n{reference_block}"


def resolve_sora_reference_prompt(
    db: Session,
    episode_id: int,
    reference_shot_id: Optional[int] = None,
) -> str:
    try:
        clean_reference_id = int(reference_shot_id or 0)
    except Exception:
        clean_reference_id = 0
    if clean_reference_id <= 0:
        return ""

    reference_shot = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.id == clean_reference_id,
        models.StoryboardShot.episode_id == episode_id,
    ).first()
    if not reference_shot:
        return ""
    return str(reference_shot.sora_prompt or "").strip()
