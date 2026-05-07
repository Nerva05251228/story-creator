import json
from functools import partial

from sqlalchemy.orm import Session

import models
from api.services import storyboard_sync
from api.services import voiceover_data
from api.services import voiceover_resources


_ALLOWED_CARD_TYPES = storyboard_sync.ALLOWED_CARD_TYPES
_build_subject_detail_map = storyboard_sync.build_subject_detail_map

_merge_voiceover_shots_preserving_extensions = voiceover_data.merge_voiceover_shots_preserving_extensions
_voiceover_default_test_mp3_path = partial(voiceover_data.voiceover_default_test_mp3_path, __file__)
_voiceover_default_reference_item = partial(
    voiceover_data.voiceover_default_reference_item,
    _voiceover_default_test_mp3_path,
)
_normalize_voiceover_shared_data = partial(
    voiceover_data.normalize_voiceover_shared_data,
    default_reference_item_factory=_voiceover_default_reference_item,
)
_load_script_voiceover_shared_data = partial(
    voiceover_data.load_script_voiceover_shared_data,
    normalize_shared_data=_normalize_voiceover_shared_data,
)
_normalize_voiceover_shots_for_tts = voiceover_data.normalize_voiceover_shots_for_tts
_parse_episode_voiceover_payload = voiceover_data.parse_episode_voiceover_payload
_voiceover_first_reference_id = voiceover_data.voiceover_first_reference_id

_ensure_voiceover_permission = voiceover_resources.ensure_voiceover_permission


def _build_storyboard_subjects(episode: models.Episode, db: Session) -> list:
    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == episode.id
    ).first()

    stored_subject_map = {}
    if episode.storyboard_data:
        try:
            stored_subject_map = _build_subject_detail_map(
                json.loads(episode.storyboard_data).get("subjects", [])
            )
        except Exception:
            stored_subject_map = {}

    subjects = []
    if library:
        cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library.id
        ).all()
        cards = [card for card in cards if card.card_type in _ALLOWED_CARD_TYPES]
        for card in cards:
            stored_subject = stored_subject_map.get((card.name, card.card_type), {})
            subjects.append(
                {
                    "id": card.id,
                    "name": card.name,
                    "card_type": card.card_type,
                    "type": card.card_type,
                    "ai_prompt": (card.ai_prompt or "").strip() or stored_subject.get("ai_prompt", ""),
                    "role_personality": (
                        getattr(card, "role_personality", "") or ""
                    ).strip() or stored_subject.get("role_personality", ""),
                    "alias": (card.alias or "").strip() or stored_subject.get("alias", ""),
                }
            )
    return subjects


def update_voiceover_data(
    episode_id: int,
    request: dict,
    user: models.User,
    db: Session,
):
    episode, script = _ensure_voiceover_permission(episode_id, user, db)

    request_payload = request if isinstance(request, dict) else {}
    incoming_shots = request_payload.get("shots", [])
    merged_voiceover_data = _merge_voiceover_shots_preserving_extensions(
        episode.voiceover_data,
        incoming_shots if isinstance(incoming_shots, list) else [],
    )

    shared_data = _load_script_voiceover_shared_data(script)
    default_voice_ref_id = _voiceover_first_reference_id(shared_data)
    normalized_shots, _ = _normalize_voiceover_shots_for_tts(
        merged_voiceover_data.get("shots", []),
        default_voice_ref_id,
    )
    merged_voiceover_data["shots"] = normalized_shots

    episode.voiceover_data = json.dumps(merged_voiceover_data, ensure_ascii=False)
    db.commit()

    return {
        "message": "\u914d\u97f3\u8868\u5df2\u4fdd\u5b58",
        "success": True,
        "shots": normalized_shots,
    }


def get_voiceover_shared_data(
    episode_id: int,
    user: models.User,
    db: Session,
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)
    shared = _load_script_voiceover_shared_data(script)
    return {"success": True, "shared": shared}


def get_detailed_storyboard(
    episode_id: int,
    user: models.User,
    db: Session,
):
    episode, script = _ensure_voiceover_permission(episode_id, user, db)

    subjects = _build_storyboard_subjects(episode, db)
    shared_data = _load_script_voiceover_shared_data(script)
    default_voice_ref_id = _voiceover_first_reference_id(shared_data)

    voiceover_payload = _parse_episode_voiceover_payload(episode)
    shots = voiceover_payload.get("shots", [])
    loaded_from_storyboard = False

    if not isinstance(shots, list):
        shots = []

    if not shots and episode.storyboard_data:
        try:
            data = json.loads(episode.storyboard_data)
            if isinstance(data, dict) and "shots" in data:
                loaded_from_storyboard = True
                for shot in data.get("shots", []):
                    shots.append(
                        {
                            "shot_number": shot.get("shot_number"),
                            "voice_type": shot.get("voice_type"),
                            "narration": shot.get("narration"),
                            "dialogue": shot.get("dialogue"),
                        }
                    )
        except json.JSONDecodeError:
            shots = []

    shots, changed = _normalize_voiceover_shots_for_tts(shots, default_voice_ref_id)
    if loaded_from_storyboard or changed:
        voiceover_payload["shots"] = shots
        episode.voiceover_data = json.dumps(voiceover_payload, ensure_ascii=False)
        db.commit()

    return {
        "generating": episode.storyboard_generating or False,
        "error": episode.storyboard_error or "",
        "shots": shots,
        "subjects": subjects,
        "tts_shared": shared_data,
    }


__all__ = [
    "update_voiceover_data",
    "get_voiceover_shared_data",
    "get_detailed_storyboard",
]
