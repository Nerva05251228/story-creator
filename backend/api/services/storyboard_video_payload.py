import json
from typing import Any, Dict, List, Optional

import models
from api.services.card_media import _ensure_audio_duration_seconds_cached
from api.services import storyboard_reference_assets
from api.services import storyboard_video_settings
from storyboard_video_reference import (
    build_seedance_content_text,
    build_seedance_prompt,
    build_seedance_reference_images,
)


SOUND_CARD_TYPE = "声音"
ROLE_CARD_TYPE = "角色"
PROP_CARD_TYPE = "道具"
MOTI_REFERENCE_IMAGE_ROLE = "reference_image"


DEFAULT_STORYBOARD_VIDEO_MODEL = storyboard_video_settings.DEFAULT_STORYBOARD_VIDEO_MODEL
STORYBOARD_VIDEO_MODEL_CONFIG = storyboard_video_settings.STORYBOARD_VIDEO_MODEL_CONFIG
MOTI_STORYBOARD_VIDEO_MODELS = storyboard_video_settings.MOTI_STORYBOARD_VIDEO_MODELS
normalize_storyboard_video_appoint_account = storyboard_video_settings.normalize_storyboard_video_appoint_account
normalize_storyboard_video_model = storyboard_video_settings.normalize_storyboard_video_model
normalize_storyboard_video_aspect_ratio = storyboard_video_settings.normalize_storyboard_video_aspect_ratio
normalize_storyboard_video_duration = storyboard_video_settings.normalize_storyboard_video_duration
normalize_storyboard_video_resolution_name = storyboard_video_settings.normalize_storyboard_video_resolution_name
resolve_storyboard_video_provider = storyboard_video_settings.resolve_storyboard_video_provider
is_moti_storyboard_video_model = storyboard_video_settings.is_moti_storyboard_video_model
resolve_storyboard_video_model_by_provider = storyboard_video_settings.resolve_storyboard_video_model_by_provider
_resolve_selected_cards = storyboard_reference_assets.resolve_selected_cards
_debug_parse_card_ids = storyboard_reference_assets.parse_card_ids
_get_subject_card_reference_image_url = storyboard_reference_assets.get_subject_card_reference_image_url
_get_selected_scene_card_image_url = storyboard_reference_assets.get_selected_scene_card_image_url
_resolve_selected_scene_reference_image_url = storyboard_reference_assets.resolve_selected_scene_reference_image_url


def get_seedance_audio_validation_error(audio_items: List[dict], total_audio_duration_seconds: float) -> str:
    audio_count = len(audio_items or [])
    if audio_count > 3:
        return "请检查音频总数是否不超过3个"
    if total_audio_duration_seconds >= 15.0:
        return "请检查音频时长总和是否小于15s"
    return ""


def _get_episode_story_library(episode_id: int, db):
    return db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == episode_id
    ).first()


def _parse_storyboard_sound_card_ids(raw_value: Any) -> Optional[List[int]]:
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


def _is_prop_subject_card_type(card_type: str) -> bool:
    card_type_text = str(card_type or "").strip().lower()
    if not card_type_text:
        return False
    if card_type_text == "prop":
        return True
    if "道具" in card_type_text:
        return True
    if "閬撳叿" in card_type_text:
        return True
    return False


def _is_role_subject_card_type(card_type: str) -> bool:
    card_type_text = str(card_type or "").strip().lower()
    if not card_type_text:
        return False
    if card_type_text == "role":
        return True
    if "角色" in card_type_text:
        return True
    if "瑙掕壊" in card_type_text:
        return True
    return False


def _collect_reference_items(db, cards: List[models.SubjectCard]) -> List[tuple[str, str]]:
    items = []
    for card in cards:
        ref_image = db.query(models.GeneratedImage).filter(
            models.GeneratedImage.card_id == card.id,
            models.GeneratedImage.is_reference == True,
            models.GeneratedImage.status == "completed",
        ).first()
        if ref_image:
            image_url = ref_image.image_path
        else:
            card_img = db.query(models.CardImage).filter(
                models.CardImage.card_id == card.id
            ).order_by(models.CardImage.order.asc(), models.CardImage.id.asc()).first()
            image_url = card_img.image_path if card_img else None

        if image_url:
            items.append((card.name, image_url))
    return items


def _resolve_storyboard_selected_sound_cards(
    shot: models.StoryboardShot,
    db,
) -> List[models.SubjectCard]:
    library = _get_episode_story_library(shot.episode_id, db)
    if not library:
        return []

    sound_cards = db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library.id,
        models.SubjectCard.card_type == SOUND_CARD_TYPE,
    ).all()
    if not sound_cards:
        return []

    sound_card_map = {card.id: card for card in sound_cards}
    explicit_sound_ids = _parse_storyboard_sound_card_ids(getattr(shot, "selected_sound_card_ids", None))
    if explicit_sound_ids is not None:
        return [sound_card_map[card_id] for card_id in explicit_sound_ids if card_id in sound_card_map]

    selected_role_cards = []
    try:
        selected_ids = json.loads(getattr(shot, "selected_card_ids", "[]") or "[]")
    except Exception:
        selected_ids = []
    if selected_ids:
        selected_cards = _resolve_selected_cards(db, selected_ids, library.id)
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


def _collect_moti_v2_reference_assets(shot, db, first_frame_image_url: str = "") -> dict:
    if shot is None or db is None:
        normalized_first_frame = str(first_frame_image_url or "").strip()
        return {
            "image_prefix_parts": ["首帧参考图"] if normalized_first_frame else [],
            "image_urls": [normalized_first_frame] if normalized_first_frame else [],
            "selected_scene_image_url": "",
            "audio_prefix_parts": [],
            "audio_items": [],
            "total_audio_duration_seconds": 0.0,
        }

    selected_ids = []
    try:
        selected_ids = json.loads(shot.selected_card_ids or "[]")
    except Exception:
        pass

    role_cards = []
    prop_cards = []
    if selected_ids:
        cards_by_id = {
            c.id: c for c in db.query(models.SubjectCard).filter(
                models.SubjectCard.id.in_(selected_ids)
            ).all()
        }
        for cid in selected_ids:
            card = cards_by_id.get(cid)
            if not card:
                continue
            if _is_prop_subject_card_type(card.card_type):
                prop_cards.append(card)
            elif _is_role_subject_card_type(card.card_type):
                role_cards.append(card)

    selected_scene_image_url = _resolve_selected_scene_reference_image_url(shot, db)
    image_meta = build_seedance_reference_images(
        first_frame_image_url=first_frame_image_url,
        scene_image_url=selected_scene_image_url,
        prop_reference_items=_collect_reference_items(db, prop_cards),
        role_reference_items=_collect_reference_items(db, role_cards),
    )

    audio_prefix_parts = []
    audio_items = []
    audio_index = 1
    total_audio_duration_seconds = 0.0

    role_card_map = {card.id: card for card in role_cards if card}
    selected_sound_cards = _resolve_storyboard_selected_sound_cards(shot, db)
    for sound_card in selected_sound_cards:
        ref_audio = db.query(models.SubjectCardAudio).filter(
            models.SubjectCardAudio.card_id == sound_card.id,
            models.SubjectCardAudio.is_reference == True,
        ).first()
        if not ref_audio or not ref_audio.audio_path:
            continue

        duration_seconds = _ensure_audio_duration_seconds_cached(ref_audio, db)
        sound_name = (sound_card.name or "").strip()
        linked_role = role_card_map.get(getattr(sound_card, "linked_card_id", None))
        if not linked_role and getattr(sound_card, "linked_card_id", None):
            linked_role = db.query(models.SubjectCard).filter(
                models.SubjectCard.id == sound_card.linked_card_id
            ).first()
        label = "旁白" if sound_name == "旁白" else ((linked_role.name or "").strip() if linked_role else sound_name)
        if not label:
            label = sound_name or f"声音{audio_index}"

        audio_prefix_parts.append(f"{label}[音频{audio_index}]")
        audio_items.append({
            "url": ref_audio.audio_path,
            "label": label,
            "duration_seconds": duration_seconds,
        })
        total_audio_duration_seconds += duration_seconds
        audio_index += 1

    return {
        "image_prefix_parts": image_meta["image_prefix_parts"],
        "image_urls": image_meta["image_urls"],
        "selected_scene_image_url": selected_scene_image_url,
        "audio_prefix_parts": audio_prefix_parts,
        "audio_items": audio_items,
        "total_audio_duration_seconds": total_audio_duration_seconds,
    }


def build_storyboard_video_reference_content(full_prompt: str, image_urls: List[str]) -> list:
    text = str(full_prompt or "").strip()
    content = [{"type": "text", "text": text}]
    for url in image_urls or []:
        normalized_url = str(url or "").strip()
        if not normalized_url:
            continue
        content.append({
            "type": "image_url",
            "image_url": {"url": normalized_url},
            "role": MOTI_REFERENCE_IMAGE_ROLE,
        })
    return content


def _build_moti_v2_content(shot, db, full_prompt: str, first_frame_image_url: str = "") -> list:
    assets = _collect_moti_v2_reference_assets(
        shot,
        db,
        first_frame_image_url=first_frame_image_url,
    )
    audio_items = assets["audio_items"]
    validation_error = get_seedance_audio_validation_error(
        audio_items,
        float(assets["total_audio_duration_seconds"] or 0.0),
    )
    if validation_error:
        raise ValueError(validation_error)

    clean_prompt = build_seedance_prompt(prompt=full_prompt)
    text = build_seedance_content_text(
        prompt=clean_prompt,
        image_prefix_parts=assets["image_prefix_parts"],
        audio_prefix_parts=assets["audio_prefix_parts"],
    )

    content = [{"type": "text", "text": text}]
    for url in assets["image_urls"]:
        content.append({
            "type": "image_url",
            "image_url": {"url": url},
            "role": MOTI_REFERENCE_IMAGE_ROLE,
        })
    for audio_item in audio_items:
        content.append({
            "type": "audio_url",
            "audio_url": {"url": audio_item["url"]},
        })

    return content


def _build_grok_video_content(shot, db, full_prompt: str, first_frame_image_url: str = "") -> list:
    assets = _collect_moti_v2_reference_assets(
        shot,
        db,
        first_frame_image_url=first_frame_image_url,
    )
    return build_storyboard_video_reference_content(full_prompt, assets["image_urls"])


def _build_unified_storyboard_video_task_payload(
    *,
    shot,
    db,
    username: str,
    model_name: str,
    provider: str,
    full_prompt: str,
    aspect_ratio: str,
    duration: int,
    first_frame_image_url: str = "",
    resolution_name: Optional[str] = None,
    appoint_account: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_provider = str(provider or "").strip().lower()
    normalized_model = normalize_storyboard_video_model(model_name, default_model=DEFAULT_STORYBOARD_VIDEO_MODEL)
    if normalized_provider == "yijia-grok":
        normalized_provider = "yijia"
        normalized_model = "grok"

    payload: Dict[str, Any] = {
        "username": str(username or "").strip(),
        "provider": normalized_provider,
        "model": normalized_model,
        "ratio": normalize_storyboard_video_aspect_ratio(
            aspect_ratio,
            model=normalized_model,
            default_ratio=STORYBOARD_VIDEO_MODEL_CONFIG[normalized_model]["default_ratio"],
        ),
        "duration": normalize_storyboard_video_duration(
            duration,
            model=normalized_model,
            default_duration=STORYBOARD_VIDEO_MODEL_CONFIG[normalized_model]["default_duration"],
        ),
    }

    if normalized_provider == "moti":
        payload.update({
            "content": _build_moti_v2_content(
                shot,
                db,
                full_prompt,
                first_frame_image_url=first_frame_image_url,
            ),
            "typography": "全能参考",
            "watermark": False,
        })
        normalized_appoint_account = normalize_storyboard_video_appoint_account(appoint_account)
        if normalized_appoint_account:
            payload["extra"] = {
                "appoint_accounts": [normalized_appoint_account]
            }
        return payload

    if normalized_model == "grok":
        payload.update({
            "content": _build_grok_video_content(
                shot,
                db,
                full_prompt,
                first_frame_image_url=first_frame_image_url,
            ),
            "resolution_name": normalize_storyboard_video_resolution_name(
                resolution_name,
                model=normalized_model,
                default_resolution=STORYBOARD_VIDEO_MODEL_CONFIG[normalized_model].get("default_resolution", ""),
            ),
        })
        return payload

    payload.update({
        "prompt": str(full_prompt or "").strip(),
        "aspect_ratio": payload["ratio"],
    })
    if first_frame_image_url:
        payload["image_url"] = str(first_frame_image_url).strip()
    return payload
