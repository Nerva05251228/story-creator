import json
import mimetypes
import os
import uuid
from datetime import datetime
from functools import partial
from typing import Tuple

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

import models
from api.services import voiceover_data
from api.services.card_media import save_and_upload_to_cdn


__all__ = [
    "ensure_voiceover_permission",
    "replace_voice_reference_for_script_episodes",
    "clear_tts_field_for_script_episodes",
    "resolve_voiceover_audio_source",
    "create_voiceover_voice_reference",
    "rename_voiceover_voice_reference",
    "preview_voiceover_voice_reference",
    "delete_voiceover_voice_reference",
    "upsert_voiceover_vector_preset",
    "delete_voiceover_vector_preset",
    "create_voiceover_emotion_audio_preset",
    "delete_voiceover_emotion_audio_preset",
    "upsert_voiceover_setting_template",
    "delete_voiceover_setting_template",
]


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
_parse_episode_voiceover_payload = voiceover_data.parse_episode_voiceover_payload
_voiceover_first_reference_id = voiceover_data.voiceover_first_reference_id
_iter_voiceover_lines = voiceover_data.iter_voiceover_lines


def ensure_voiceover_permission(
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


def replace_voice_reference_for_script_episodes(
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


def clear_tts_field_for_script_episodes(
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


def resolve_voiceover_audio_source(reference_item: dict) -> str:
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


async def create_voiceover_voice_reference(
    episode_id: int,
    name: str,
    file: UploadFile,
    user: models.User,
    db: Session,
):
    _, script = ensure_voiceover_permission(episode_id, user, db)

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


async def rename_voiceover_voice_reference(
    episode_id: int,
    reference_id: str,
    request: dict,
    user: models.User,
    db: Session,
):
    _, script = ensure_voiceover_permission(episode_id, user, db)

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


async def preview_voiceover_voice_reference(
    episode_id: int,
    reference_id: str,
    user: models.User,
    db: Session,
):
    _, script = ensure_voiceover_permission(episode_id, user, db)
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

    source = resolve_voiceover_audio_source(target)
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


async def delete_voiceover_voice_reference(
    episode_id: int,
    reference_id: str,
    user: models.User,
    db: Session,
):
    _, script = ensure_voiceover_permission(episode_id, user, db)
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
    updated_line_count = replace_voice_reference_for_script_episodes(
        db, script.id, target_id, fallback_ref_id
    )
    db.commit()

    return {
        "success": True,
        "shared": _load_script_voiceover_shared_data(script),
        "fallback_voice_reference_id": fallback_ref_id,
        "updated_line_count": updated_line_count,
    }


async def upsert_voiceover_vector_preset(
    episode_id: int,
    request: dict,
    user: models.User,
    db: Session,
):
    _, script = ensure_voiceover_permission(episode_id, user, db)
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
        presets.append(
            {
                "id": preset_id,
                "name": name,
                "description": description,
                "vector_config": vector_config,
                "created_at": now_iso,
            }
        )
    shared["vector_presets"] = presets
    _save_script_voiceover_shared_data(script, shared)
    db.commit()

    return {"success": True, "preset_id": preset_id, "shared": _load_script_voiceover_shared_data(script)}


async def delete_voiceover_vector_preset(
    episode_id: int,
    preset_id: str,
    user: models.User,
    db: Session,
):
    _, script = ensure_voiceover_permission(episode_id, user, db)
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
    updated_line_count = clear_tts_field_for_script_episodes(
        db, script.id, "vector_preset_id", target_id
    )
    db.commit()

    return {
        "success": True,
        "shared": _load_script_voiceover_shared_data(script),
        "updated_line_count": updated_line_count,
    }


async def create_voiceover_emotion_audio_preset(
    episode_id: int,
    name: str,
    description: str,
    file: UploadFile,
    user: models.User,
    db: Session,
):
    _, script = ensure_voiceover_permission(episode_id, user, db)
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


async def delete_voiceover_emotion_audio_preset(
    episode_id: int,
    preset_id: str,
    user: models.User,
    db: Session,
):
    _, script = ensure_voiceover_permission(episode_id, user, db)
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
    updated_line_count = clear_tts_field_for_script_episodes(
        db, script.id, "emotion_audio_preset_id", target_id
    )
    db.commit()

    return {
        "success": True,
        "shared": _load_script_voiceover_shared_data(script),
        "updated_line_count": updated_line_count,
    }


async def upsert_voiceover_setting_template(
    episode_id: int,
    request: dict,
    user: models.User,
    db: Session,
):
    _, script = ensure_voiceover_permission(episode_id, user, db)

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
        templates.append(
            {
                "id": target_id,
                "name": name,
                "settings": settings,
                "created_at": now_iso,
                "updated_at": now_iso,
            }
        )

    shared["setting_templates"] = templates
    _save_script_voiceover_shared_data(script, shared)
    db.commit()

    return {
        "success": True,
        "template_id": target_id,
        "shared": _load_script_voiceover_shared_data(script),
    }


async def delete_voiceover_setting_template(
    episode_id: int,
    template_id: str,
    user: models.User,
    db: Session,
):
    _, script = ensure_voiceover_permission(episode_id, user, db)
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
