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


def _ensure_voiceover_permission(
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


def _replace_voice_reference_for_script_episodes(
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


def _clear_tts_field_for_script_episodes(
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


def _resolve_voiceover_audio_source(reference_item: dict) -> str:
    if not isinstance(reference_item, dict):
        return ""
    url = str(reference_item.get("url") or "").strip()
    if url:
        return url
    local_path = str(reference_item.get("local_path") or "").strip()
    if local_path:
        if os.path.isabs(local_path):
            return local_path
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", local_path))
    return ""


@router.put("/api/episodes/{episode_id}/voiceover")
async def update_voiceover_data(
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


@router.get("/api/episodes/{episode_id}/voiceover/shared")
async def get_voiceover_shared_data(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)
    shared = _load_script_voiceover_shared_data(script)
    return {"success": True, "shared": shared}


@router.post("/api/episodes/{episode_id}/voiceover/shared/voice-references")
async def create_voiceover_voice_reference(
    episode_id: int,
    name: str = Form(...),
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)

    ref_name = str(name or "").strip()
    if not ref_name:
        raise HTTPException(status_code=400, detail="音色参考音频名称不能为空")

    cdn_url = save_and_upload_to_cdn(file)
    shared = _load_script_voiceover_shared_data(script)
    item = {
        "id": f"voice_ref_{uuid.uuid4().hex}",
        "name": ref_name,
        "file_name": str(file.filename or "").strip(),
        "url": cdn_url,
        "local_path": "",
        "created_at": datetime.utcnow().isoformat(),
    }
    shared["voice_references"].append(item)
    _save_script_voiceover_shared_data(script, shared)
    db.commit()

    return {"success": True, "item": item, "shared": _load_script_voiceover_shared_data(script)}


@router.put("/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}")
async def rename_voiceover_voice_reference(
    episode_id: int,
    reference_id: str,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)

    target_id = str(reference_id or "").strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="reference_id不能为空")

    new_name = str(request.get("name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="音色名称不能为空")

    shared = _load_script_voiceover_shared_data(script)
    refs = shared.get("voice_references", [])
    if not isinstance(refs, list):
        refs = []
        shared["voice_references"] = refs

    target_item = None
    for item in refs:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == target_id:
            target_item = item
            break
    if not isinstance(target_item, dict):
        raise HTTPException(status_code=404, detail="音色参考音频不存在")

    target_item["name"] = new_name
    target_item["updated_at"] = datetime.utcnow().isoformat()

    _save_script_voiceover_shared_data(script, shared)
    db.commit()
    return {
        "success": True,
        "item": target_item,
        "shared": _load_script_voiceover_shared_data(script),
    }


@router.get("/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}/preview")
async def preview_voiceover_voice_reference(
    episode_id: int,
    reference_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)
    target_id = str(reference_id or "").strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="reference_id不能为空")

    shared = _load_script_voiceover_shared_data(script)
    refs = shared.get("voice_references", [])
    target = None
    if isinstance(refs, list):
        target = next((item for item in refs if str(item.get("id") or "").strip() == target_id), None)
    if not isinstance(target, dict):
        raise HTTPException(status_code=404, detail="音色参考音频不存在")

    source = _resolve_voiceover_audio_source(target)
    if not source:
        raise HTTPException(status_code=404, detail="音色参考音频不可访问")

    if source.startswith("http://") or source.startswith("https://"):
        return RedirectResponse(url=source, status_code=307)

    if not os.path.exists(source):
        raise HTTPException(status_code=404, detail="音色参考音频文件不存在")

    media_type = mimetypes.guess_type(source)[0] or "application/octet-stream"
    inline_name = os.path.basename(source)
    return FileResponse(
        source,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{inline_name}"'},
    )


@router.delete("/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}")
async def delete_voiceover_voice_reference(
    episode_id: int,
    reference_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)
    target_id = str(reference_id or "").strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="reference_id不能为空")

    shared = _load_script_voiceover_shared_data(script)
    before = len(shared.get("voice_references", []))
    shared["voice_references"] = [
        item for item in shared.get("voice_references", [])
        if str(item.get("id") or "").strip() != target_id
    ]
    if len(shared["voice_references"]) == before:
        raise HTTPException(status_code=404, detail="音色参考音频不存在")

    fallback_ref_id = _voiceover_first_reference_id(shared)
    _save_script_voiceover_shared_data(script, shared)
    updated_line_count = _replace_voice_reference_for_script_episodes(
        db, script.id, target_id, fallback_ref_id
    )
    db.commit()

    return {
        "success": True,
        "shared": _load_script_voiceover_shared_data(script),
        "fallback_voice_reference_id": fallback_ref_id,
        "updated_line_count": updated_line_count,
    }


@router.post("/api/episodes/{episode_id}/voiceover/shared/vector-presets")
async def upsert_voiceover_vector_preset(
    episode_id: int,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)
    name = str(request.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="预设名称不能为空")

    preset_id = str(request.get("id") or "").strip() or f"vector_preset_{uuid.uuid4().hex}"
    vector_config = _normalize_voiceover_vector_config(request.get("vector_config"))
    description = str(request.get("description") or "").strip()

    shared = _load_script_voiceover_shared_data(script)
    presets = shared.get("vector_presets", [])
    updated = False
    now_iso = datetime.utcnow().isoformat()
    for item in presets:
        if str(item.get("id") or "").strip() == preset_id:
            item["name"] = name
            item["description"] = description
            item["vector_config"] = vector_config
            updated = True
            break
    if not updated:
        presets.append({
            "id": preset_id,
            "name": name,
            "description": description,
            "vector_config": vector_config,
            "created_at": now_iso,
        })
    shared["vector_presets"] = presets
    _save_script_voiceover_shared_data(script, shared)
    db.commit()

    return {"success": True, "preset_id": preset_id, "shared": _load_script_voiceover_shared_data(script)}


@router.delete("/api/episodes/{episode_id}/voiceover/shared/vector-presets/{preset_id}")
async def delete_voiceover_vector_preset(
    episode_id: int,
    preset_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)
    target_id = str(preset_id or "").strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="preset_id不能为空")

    shared = _load_script_voiceover_shared_data(script)
    before = len(shared.get("vector_presets", []))
    shared["vector_presets"] = [
        item for item in shared.get("vector_presets", [])
        if str(item.get("id") or "").strip() != target_id
    ]
    if len(shared["vector_presets"]) == before:
        raise HTTPException(status_code=404, detail="向量预设不存在")

    _save_script_voiceover_shared_data(script, shared)
    updated_line_count = _clear_tts_field_for_script_episodes(
        db, script.id, "vector_preset_id", target_id
    )
    db.commit()

    return {
        "success": True,
        "shared": _load_script_voiceover_shared_data(script),
        "updated_line_count": updated_line_count,
    }


@router.post("/api/episodes/{episode_id}/voiceover/shared/emotion-audio-presets")
async def create_voiceover_emotion_audio_preset(
    episode_id: int,
    name: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)
    preset_name = str(name or "").strip()
    if not preset_name:
        raise HTTPException(status_code=400, detail="情感参考音频名称不能为空")

    cdn_url = save_and_upload_to_cdn(file)
    shared = _load_script_voiceover_shared_data(script)
    item = {
        "id": f"emotion_audio_preset_{uuid.uuid4().hex}",
        "name": preset_name,
        "description": str(description or "").strip(),
        "file_name": str(file.filename or "").strip(),
        "url": cdn_url,
        "local_path": "",
        "created_at": datetime.utcnow().isoformat(),
    }
    shared["emotion_audio_presets"].append(item)
    _save_script_voiceover_shared_data(script, shared)
    db.commit()

    return {"success": True, "item": item, "shared": _load_script_voiceover_shared_data(script)}


@router.delete("/api/episodes/{episode_id}/voiceover/shared/emotion-audio-presets/{preset_id}")
async def delete_voiceover_emotion_audio_preset(
    episode_id: int,
    preset_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)
    target_id = str(preset_id or "").strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="preset_id不能为空")

    shared = _load_script_voiceover_shared_data(script)
    before = len(shared.get("emotion_audio_presets", []))
    shared["emotion_audio_presets"] = [
        item for item in shared.get("emotion_audio_presets", [])
        if str(item.get("id") or "").strip() != target_id
    ]
    if len(shared["emotion_audio_presets"]) == before:
        raise HTTPException(status_code=404, detail="情感音频预设不存在")

    _save_script_voiceover_shared_data(script, shared)
    updated_line_count = _clear_tts_field_for_script_episodes(
        db, script.id, "emotion_audio_preset_id", target_id
    )
    db.commit()

    return {
        "success": True,
        "shared": _load_script_voiceover_shared_data(script),
        "updated_line_count": updated_line_count,
    }


@router.post("/api/episodes/{episode_id}/voiceover/shared/setting-templates")
async def upsert_voiceover_setting_template(
    episode_id: int,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)

    name = str(request.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="模板名称不能为空")

    shared = _load_script_voiceover_shared_data(script)
    default_voice_ref_id = _voiceover_first_reference_id(shared)
    settings = _normalize_voiceover_setting_template_payload(
        request.get("settings"),
        default_voice_ref_id,
    )

    templates = shared.get("setting_templates", [])
    if not isinstance(templates, list):
        templates = []

    target_id = str(request.get("id") or "").strip()
    target_item = None
    if target_id:
        target_item = next(
            (item for item in templates if str(item.get("id") or "").strip() == target_id),
            None,
        )
    if not target_item:
        target_item = next(
            (item for item in templates if str(item.get("name") or "").strip() == name),
            None,
        )

    now_iso = datetime.utcnow().isoformat()
    if target_item:
        target_item["name"] = name
        target_item["settings"] = settings
        target_item["updated_at"] = now_iso
        if not str(target_item.get("created_at") or "").strip():
            target_item["created_at"] = now_iso
        target_id = str(target_item.get("id") or "").strip() or f"setting_template_{uuid.uuid4().hex}"
        target_item["id"] = target_id
    else:
        target_id = target_id or f"setting_template_{uuid.uuid4().hex}"
        templates.append({
            "id": target_id,
            "name": name,
            "settings": settings,
            "created_at": now_iso,
            "updated_at": now_iso,
        })

    shared["setting_templates"] = templates
    _save_script_voiceover_shared_data(script, shared)
    db.commit()

    return {
        "success": True,
        "template_id": target_id,
        "shared": _load_script_voiceover_shared_data(script),
    }


@router.delete("/api/episodes/{episode_id}/voiceover/shared/setting-templates/{template_id}")
async def delete_voiceover_setting_template(
    episode_id: int,
    template_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)
    target_id = str(template_id or "").strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="template_id不能为空")

    shared = _load_script_voiceover_shared_data(script)
    before = len(shared.get("setting_templates", []))
    shared["setting_templates"] = [
        item for item in shared.get("setting_templates", [])
        if str(item.get("id") or "").strip() != target_id
    ]
    if len(shared["setting_templates"]) == before:
        raise HTTPException(status_code=404, detail="参数模板不存在")

    _save_script_voiceover_shared_data(script, shared)
    db.commit()
    return {
        "success": True,
        "shared": _load_script_voiceover_shared_data(script),
    }


@router.post("/api/episodes/{episode_id}/voiceover/lines/{line_id}/generate")
async def enqueue_voiceover_line_generate(
    episode_id: int,
    line_id: str,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    episode, script = _ensure_voiceover_permission(episode_id, user, db)
    target_line_id = str(line_id or "").strip()
    if not target_line_id:
        raise HTTPException(status_code=400, detail="line_id不能为空")

    shared = _load_script_voiceover_shared_data(script)
    refs = shared.get("voice_references", [])
    default_voice_ref_id = _voiceover_first_reference_id(shared)

    voiceover_payload = _parse_episode_voiceover_payload(episode)
    shots, changed = _normalize_voiceover_shots_for_tts(
        voiceover_payload.get("shots", []),
        default_voice_ref_id,
    )
    line_entry = _find_voiceover_line_entry(shots, target_line_id)
    if not isinstance(line_entry, dict):
        raise HTTPException(status_code=404, detail=f"未找到 line_id={target_line_id}")

    line_tts = _normalize_voiceover_line_tts(line_entry.get("tts"), default_voice_ref_id)

    line_text = str(request.get("text") or line_entry.get("text") or "").strip()
    if not line_text:
        raise HTTPException(status_code=400, detail="配音文本为空")

    method = str(
        request.get("emotion_control_method")
        or line_tts.get("emotion_control_method")
        or VOICEOVER_TTS_METHOD_SAME
    ).strip()
    if method not in VOICEOVER_TTS_ALLOWED_METHODS:
        method = VOICEOVER_TTS_METHOD_SAME

    voice_reference_id = str(
        request.get("voice_reference_id")
        or line_tts.get("voice_reference_id")
        or default_voice_ref_id
    ).strip()
    if not voice_reference_id:
        raise HTTPException(status_code=400, detail="请先选择音色参考音频")

    selected_voice_ref = None
    if isinstance(refs, list):
        selected_voice_ref = next((x for x in refs if str(x.get("id") or "").strip() == voice_reference_id), None)
    if not selected_voice_ref:
        raise HTTPException(status_code=400, detail="音色参考音频不存在")

    emotion_audio_preset_id = ""
    if method == VOICEOVER_TTS_METHOD_AUDIO:
        emotion_audio_preset_id = str(
            request.get("emotion_audio_preset_id")
            or line_tts.get("emotion_audio_preset_id")
            or ""
        ).strip()
        if not emotion_audio_preset_id:
            raise HTTPException(status_code=400, detail="请先选择情感参考音频预设")
        emotion_presets = shared.get("emotion_audio_presets", [])
        selected_emotion = None
        if isinstance(emotion_presets, list):
            selected_emotion = next(
                (x for x in emotion_presets if str(x.get("id") or "").strip() == emotion_audio_preset_id),
                None,
            )
        if not selected_emotion:
            raise HTTPException(status_code=400, detail="情感参考音频预设不存在")

    vector_preset_id = str(request.get("vector_preset_id") or line_tts.get("vector_preset_id") or "").strip()
    vector_config = _normalize_voiceover_vector_config(
        request.get("vector_config") or line_tts.get("vector_config")
    )
    emo_text = str(
        request.get("emo_text")
        if request.get("emo_text") is not None
        else line_entry.get("emotion")
        or ""
    ).strip()

    task_payload = {
        "text": line_text,
        "emo_text": emo_text,
        "emotion_control_method": method,
        "voice_reference_id": voice_reference_id,
        "vector_preset_id": vector_preset_id,
        "emotion_audio_preset_id": emotion_audio_preset_id,
        "vector_config": vector_config,
    }

    task = models.VoiceoverTtsTask(
        episode_id=episode.id,
        line_id=target_line_id,
        status="pending",
        request_json=json.dumps(task_payload, ensure_ascii=False),
        result_json="",
        error_message="",
    )
    db.add(task)
    db.flush()

    line_tts["emotion_control_method"] = method
    line_tts["voice_reference_id"] = voice_reference_id
    line_tts["vector_preset_id"] = vector_preset_id
    line_tts["emotion_audio_preset_id"] = emotion_audio_preset_id
    line_tts["vector_config"] = vector_config
    line_tts["generate_status"] = "pending"
    line_tts["generate_error"] = ""
    line_tts["latest_task_id"] = str(task.id)
    line_entry["tts"] = line_tts

    voiceover_payload["shots"] = shots
    episode.voiceover_data = json.dumps(voiceover_payload, ensure_ascii=False)
    db.commit()
    sync_voiceover_tts_task_to_dashboard(task.id)

    queue_position = db.query(func.count(models.VoiceoverTtsTask.id)).filter(
        models.VoiceoverTtsTask.status.in_(["pending", "processing"]),
        models.VoiceoverTtsTask.id <= task.id,
    ).scalar() or 1

    return {
        "success": True,
        "task_id": task.id,
        "line_id": target_line_id,
        "status": "pending",
        "queue_position": int(queue_position),
    }


@router.post("/api/episodes/{episode_id}/voiceover/generate-all")
async def enqueue_voiceover_generate_all(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    episode, script = _ensure_voiceover_permission(episode_id, user, db)

    shared = _load_script_voiceover_shared_data(script)
    refs = shared.get("voice_references", [])
    default_voice_ref_id = _voiceover_first_reference_id(shared)
    ref_id_set = {
        str(item.get("id") or "").strip()
        for item in refs
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    emotion_presets = shared.get("emotion_audio_presets", [])
    emotion_preset_id_set = {
        str(item.get("id") or "").strip()
        for item in emotion_presets
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }

    voiceover_payload = _parse_episode_voiceover_payload(episode)
    shots, _ = _normalize_voiceover_shots_for_tts(
        voiceover_payload.get("shots", []),
        default_voice_ref_id,
    )

    enqueued_line_ids = []
    skipped = []
    seen_line_ids = set()

    created_task_ids = []
    for shot in shots:
        if not isinstance(shot, dict):
            continue

        line_entries = []
        narration = shot.get("narration")
        if isinstance(narration, dict):
            line_entries.append(narration)

        dialogue = shot.get("dialogue")
        if isinstance(dialogue, list):
            for item in dialogue:
                if isinstance(item, dict):
                    line_entries.append(item)

        for line_entry in line_entries:
            line_id = str(line_entry.get("line_id") or "").strip()
            if not line_id:
                skipped.append({"line_id": "", "reason": "line_id缺失"})
                continue
            if line_id in seen_line_ids:
                skipped.append({"line_id": line_id, "reason": "line_id重复"})
                continue
            seen_line_ids.add(line_id)

            line_text = str(line_entry.get("text") or "").strip()
            if not line_text:
                skipped.append({"line_id": line_id, "reason": "配音文本为空"})
                continue

            line_tts = _normalize_voiceover_line_tts(line_entry.get("tts"), default_voice_ref_id)
            status = str(line_tts.get("generate_status") or "").strip().lower()
            if status in {"pending", "processing"}:
                skipped.append({"line_id": line_id, "reason": "已在队列中或生成中"})
                continue

            method = str(line_tts.get("emotion_control_method") or VOICEOVER_TTS_METHOD_SAME).strip()
            if method not in VOICEOVER_TTS_ALLOWED_METHODS:
                method = VOICEOVER_TTS_METHOD_SAME

            voice_reference_id = str(
                line_tts.get("voice_reference_id") or default_voice_ref_id
            ).strip()
            if not voice_reference_id:
                skipped.append({"line_id": line_id, "reason": "未选择音色参考音频"})
                continue
            if ref_id_set and voice_reference_id not in ref_id_set:
                skipped.append({"line_id": line_id, "reason": "音色参考音频不存在"})
                continue

            emotion_audio_preset_id = ""
            if method == VOICEOVER_TTS_METHOD_AUDIO:
                emotion_audio_preset_id = str(line_tts.get("emotion_audio_preset_id") or "").strip()
                if not emotion_audio_preset_id:
                    skipped.append({"line_id": line_id, "reason": "未选择情感参考音频预设"})
                    continue
                if emotion_preset_id_set and emotion_audio_preset_id not in emotion_preset_id_set:
                    skipped.append({"line_id": line_id, "reason": "情感参考音频预设不存在"})
                    continue

            vector_preset_id = str(line_tts.get("vector_preset_id") or "").strip()
            vector_config = _normalize_voiceover_vector_config(line_tts.get("vector_config"))
            emo_text = str(line_entry.get("emotion") or "").strip()

            task_payload = {
                "text": line_text,
                "emo_text": emo_text,
                "emotion_control_method": method,
                "voice_reference_id": voice_reference_id,
                "vector_preset_id": vector_preset_id,
                "emotion_audio_preset_id": emotion_audio_preset_id,
                "vector_config": vector_config,
            }
            task = models.VoiceoverTtsTask(
                episode_id=episode.id,
                line_id=line_id,
                status="pending",
                request_json=json.dumps(task_payload, ensure_ascii=False),
                result_json="",
                error_message="",
            )
            db.add(task)
            db.flush()
            created_task_ids.append(int(task.id))

            line_tts["emotion_control_method"] = method
            line_tts["voice_reference_id"] = voice_reference_id
            line_tts["vector_preset_id"] = vector_preset_id
            line_tts["emotion_audio_preset_id"] = emotion_audio_preset_id
            line_tts["vector_config"] = vector_config
            line_tts["generate_status"] = "pending"
            line_tts["generate_error"] = ""
            line_tts["latest_task_id"] = str(task.id)
            line_entry["tts"] = line_tts

            enqueued_line_ids.append(line_id)

    voiceover_payload["shots"] = shots
    episode.voiceover_data = json.dumps(voiceover_payload, ensure_ascii=False)
    db.commit()
    for created_task_id in created_task_ids:
        sync_voiceover_tts_task_to_dashboard(created_task_id)

    pending_count = db.query(func.count(models.VoiceoverTtsTask.id)).filter(
        models.VoiceoverTtsTask.status == "pending"
    ).scalar() or 0
    processing_count = db.query(func.count(models.VoiceoverTtsTask.id)).filter(
        models.VoiceoverTtsTask.status == "processing"
    ).scalar() or 0

    return {
        "success": True,
        "enqueued_count": len(enqueued_line_ids),
        "skipped_count": len(skipped),
        "enqueued_line_ids": enqueued_line_ids,
        "skipped": skipped,
        "queue": {
            "pending": int(pending_count),
            "processing": int(processing_count),
        },
    }


@router.get("/api/episodes/{episode_id}/voiceover/tts-status")
def get_voiceover_tts_status(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    episode, script = _ensure_voiceover_permission(episode_id, user, db)
    shared = _load_script_voiceover_shared_data(script)
    default_voice_ref_id = _voiceover_first_reference_id(shared)

    payload = _parse_episode_voiceover_payload(episode)
    shots, changed = _normalize_voiceover_shots_for_tts(payload.get("shots", []), default_voice_ref_id)
    if changed:
        payload["shots"] = shots
        episode.voiceover_data = json.dumps(payload, ensure_ascii=False)
        db.commit()

    line_states = _extract_voiceover_tts_line_states(shots)
    pending_count = db.query(func.count(models.VoiceoverTtsTask.id)).filter(
        models.VoiceoverTtsTask.status == "pending"
    ).scalar() or 0
    processing_count = db.query(func.count(models.VoiceoverTtsTask.id)).filter(
        models.VoiceoverTtsTask.status == "processing"
    ).scalar() or 0

    return {
        "success": True,
        "line_states": line_states,
        "queue": {
            "pending": int(pending_count),
            "processing": int(processing_count),
        },
    }
