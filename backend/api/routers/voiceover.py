import json
import mimetypes
import os
import uuid
from datetime import datetime
from functools import partial
from typing import Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

import models
from api.services import voiceover_data
from api.services import voiceover_generation
from api.services import voiceover_resources
from api.services import voiceover_shared_state
from api.services.card_media import save_and_upload_to_cdn
from auth import get_current_user
from dashboard_service import sync_voiceover_tts_task_to_dashboard
from database import get_db


router = APIRouter()


VOICEOVER_TTS_METHOD_SAME = voiceover_data.VOICEOVER_TTS_METHOD_SAME
VOICEOVER_TTS_METHOD_AUDIO = voiceover_data.VOICEOVER_TTS_METHOD_AUDIO
VOICEOVER_TTS_ALLOWED_METHODS = voiceover_data.VOICEOVER_TTS_ALLOWED_METHODS

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
_save_script_voiceover_shared_data = partial(
    voiceover_data.save_script_voiceover_shared_data,
    normalize_shared_data=_normalize_voiceover_shared_data,
)
_normalize_voiceover_vector_config = voiceover_data.normalize_voiceover_vector_config
_normalize_voiceover_setting_template_payload = voiceover_data.normalize_voiceover_setting_template_payload
_normalize_voiceover_line_tts = voiceover_data.normalize_voiceover_line_tts
_normalize_voiceover_shots_for_tts = voiceover_data.normalize_voiceover_shots_for_tts
_extract_voiceover_tts_line_states = voiceover_data.extract_voiceover_tts_line_states
_find_voiceover_line_entry = voiceover_data.find_voiceover_line_entry
_parse_episode_voiceover_payload = voiceover_data.parse_episode_voiceover_payload
_voiceover_first_reference_id = voiceover_data.voiceover_first_reference_id
_iter_voiceover_lines = voiceover_data.iter_voiceover_lines


def _legacy_ensure_voiceover_permission(
    episode_id: int,
    user: models.User,
    db: Session,
) -> Tuple[models.Episode, models.Script]:
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if not script or script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    return episode, script


def _legacy_replace_voice_reference_for_script_episodes(
    db: Session,
    script_id: int,
    removed_ref_id: str,
    fallback_ref_id: str,
) -> int:
    removed = str(removed_ref_id or "").strip()
    if not removed:
        return 0

    updated_lines = 0
    episodes = db.query(models.Episode).filter(models.Episode.script_id == script_id).all()
    for episode in episodes:
        payload = _parse_episode_voiceover_payload(episode)
        shots, changed = _normalize_voiceover_shots_for_tts(payload.get("shots", []), fallback_ref_id)
        episode_changed = bool(changed)
        for line in _iter_voiceover_lines(shots):
            tts = _normalize_voiceover_line_tts(line.get("tts"), fallback_ref_id)
            if tts.get("voice_reference_id") == removed:
                tts["voice_reference_id"] = fallback_ref_id or ""
                line["tts"] = tts
                updated_lines += 1
                episode_changed = True
        if episode_changed:
            payload["shots"] = shots
            episode.voiceover_data = json.dumps(payload, ensure_ascii=False)
    return updated_lines


def _legacy_clear_tts_field_for_script_episodes(
    db: Session,
    script_id: int,
    field_name: str,
    removed_value: str,
) -> int:
    target = str(removed_value or "").strip()
    if not target:
        return 0

    updated_lines = 0
    episodes = db.query(models.Episode).filter(models.Episode.script_id == script_id).all()
    for episode in episodes:
        payload = _parse_episode_voiceover_payload(episode)
        shots, changed = _normalize_voiceover_shots_for_tts(payload.get("shots", []), "")
        episode_changed = bool(changed)
        for line in _iter_voiceover_lines(shots):
            tts = _normalize_voiceover_line_tts(line.get("tts"), "")
            if str(tts.get(field_name) or "").strip() == target:
                tts[field_name] = ""
                line["tts"] = tts
                updated_lines += 1
                episode_changed = True
        if episode_changed:
            payload["shots"] = shots
            episode.voiceover_data = json.dumps(payload, ensure_ascii=False)
    return updated_lines


_ensure_voiceover_permission = voiceover_resources.ensure_voiceover_permission
_replace_voice_reference_for_script_episodes = voiceover_resources.replace_voice_reference_for_script_episodes
_clear_tts_field_for_script_episodes = voiceover_resources.clear_tts_field_for_script_episodes
_resolve_voiceover_audio_source = voiceover_resources.resolve_voiceover_audio_source


async def _legacy_update_voiceover_data(
    episode_id: int,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    episode, script = _ensure_voiceover_permission(episode_id, user, db)

    incoming_shots = request.get("shots", [])
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

    print(f"✅ 配音表已保存，共 {len(normalized_shots)} 个镜头")

    return {"message": "配音表已保存", "success": True, "shots": normalized_shots}


async def _legacy_get_voiceover_shared_data(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)
    shared = _load_script_voiceover_shared_data(script)
    return {"success": True, "shared": shared}


@router.put("/api/episodes/{episode_id}/voiceover")
async def update_voiceover_data(
    episode_id: int,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return voiceover_shared_state.update_voiceover_data(
        episode_id,
        request,
        user,
        db,
    )


@router.get("/api/episodes/{episode_id}/voiceover/shared")
async def get_voiceover_shared_data(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return voiceover_shared_state.get_voiceover_shared_data(
        episode_id,
        user,
        db,
    )


@router.post("/api/episodes/{episode_id}/voiceover/shared/voice-references")
async def create_voiceover_voice_reference(
    episode_id: int,
    name: str = Form(...),
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await voiceover_resources.create_voiceover_voice_reference(
        episode_id,
        name,
        file,
        user,
        db,
    )


@router.put("/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}")
async def rename_voiceover_voice_reference(
    episode_id: int,
    reference_id: str,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await voiceover_resources.rename_voiceover_voice_reference(
        episode_id,
        reference_id,
        request,
        user,
        db,
    )


@router.get("/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}/preview")
async def preview_voiceover_voice_reference(
    episode_id: int,
    reference_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await voiceover_resources.preview_voiceover_voice_reference(
        episode_id,
        reference_id,
        user,
        db,
    )


@router.delete("/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}")
async def delete_voiceover_voice_reference(
    episode_id: int,
    reference_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await voiceover_resources.delete_voiceover_voice_reference(
        episode_id,
        reference_id,
        user,
        db,
    )


@router.post("/api/episodes/{episode_id}/voiceover/shared/vector-presets")
async def upsert_voiceover_vector_preset(
    episode_id: int,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await voiceover_resources.upsert_voiceover_vector_preset(
        episode_id,
        request,
        user,
        db,
    )


@router.delete("/api/episodes/{episode_id}/voiceover/shared/vector-presets/{preset_id}")
async def delete_voiceover_vector_preset(
    episode_id: int,
    preset_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await voiceover_resources.delete_voiceover_vector_preset(
        episode_id,
        preset_id,
        user,
        db,
    )


@router.post("/api/episodes/{episode_id}/voiceover/shared/emotion-audio-presets")
async def create_voiceover_emotion_audio_preset(
    episode_id: int,
    name: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await voiceover_resources.create_voiceover_emotion_audio_preset(
        episode_id,
        name,
        description,
        file,
        user,
        db,
    )


@router.delete("/api/episodes/{episode_id}/voiceover/shared/emotion-audio-presets/{preset_id}")
async def delete_voiceover_emotion_audio_preset(
    episode_id: int,
    preset_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await voiceover_resources.delete_voiceover_emotion_audio_preset(
        episode_id,
        preset_id,
        user,
        db,
    )


@router.post("/api/episodes/{episode_id}/voiceover/shared/setting-templates")
async def upsert_voiceover_setting_template(
    episode_id: int,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await voiceover_resources.upsert_voiceover_setting_template(
        episode_id,
        request,
        user,
        db,
    )


@router.delete("/api/episodes/{episode_id}/voiceover/shared/setting-templates/{template_id}")
async def delete_voiceover_setting_template(
    episode_id: int,
    template_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await voiceover_resources.delete_voiceover_setting_template(
        episode_id,
        template_id,
        user,
        db,
    )


@router.post("/api/episodes/{episode_id}/voiceover/lines/{line_id}/generate")
async def enqueue_voiceover_line_generate(
    episode_id: int,
    line_id: str,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await voiceover_generation.enqueue_voiceover_line_generate(
        episode_id,
        line_id,
        request,
        user,
        db,
    )


@router.post("/api/episodes/{episode_id}/voiceover/generate-all")
async def enqueue_voiceover_generate_all(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await voiceover_generation.enqueue_voiceover_generate_all(
        episode_id,
        user,
        db,
    )


@router.get("/api/episodes/{episode_id}/voiceover/tts-status")
def get_voiceover_tts_status(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return voiceover_generation.get_voiceover_tts_status(
        episode_id,
        user,
        db,
    )
