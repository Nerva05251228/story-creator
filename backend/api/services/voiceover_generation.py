import json
from functools import partial

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

import models
from api.services import voiceover_data, voiceover_resources
from dashboard_service import sync_voiceover_tts_task_to_dashboard


VOICEOVER_TTS_METHOD_SAME = voiceover_data.VOICEOVER_TTS_METHOD_SAME
VOICEOVER_TTS_METHOD_AUDIO = voiceover_data.VOICEOVER_TTS_METHOD_AUDIO
VOICEOVER_TTS_ALLOWED_METHODS = voiceover_data.VOICEOVER_TTS_ALLOWED_METHODS

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
_normalize_voiceover_vector_config = voiceover_data.normalize_voiceover_vector_config
_normalize_voiceover_line_tts = voiceover_data.normalize_voiceover_line_tts
_normalize_voiceover_shots_for_tts = voiceover_data.normalize_voiceover_shots_for_tts
_extract_voiceover_tts_line_states = voiceover_data.extract_voiceover_tts_line_states
_find_voiceover_line_entry = voiceover_data.find_voiceover_line_entry
_parse_episode_voiceover_payload = voiceover_data.parse_episode_voiceover_payload
_voiceover_first_reference_id = voiceover_data.voiceover_first_reference_id

_ensure_voiceover_permission = voiceover_resources.ensure_voiceover_permission


async def enqueue_voiceover_line_generate(
    episode_id: int,
    line_id: str,
    request: dict,
    user: models.User,
    db: Session,
):
    episode, script = _ensure_voiceover_permission(episode_id, user, db)
    target_line_id = str(line_id or "").strip()
    if not target_line_id:
        raise HTTPException(status_code=400, detail="line_id涓嶈兘涓虹┖")

    shared = _load_script_voiceover_shared_data(script)
    refs = shared.get("voice_references", [])
    default_voice_ref_id = _voiceover_first_reference_id(shared)

    voiceover_payload = _parse_episode_voiceover_payload(episode)
    shots, _ = _normalize_voiceover_shots_for_tts(
        voiceover_payload.get("shots", []),
        default_voice_ref_id,
    )
    line_entry = _find_voiceover_line_entry(shots, target_line_id)
    if not isinstance(line_entry, dict):
        raise HTTPException(status_code=404, detail=f"鏈壘鍒?line_id={target_line_id}")

    line_tts = _normalize_voiceover_line_tts(line_entry.get("tts"), default_voice_ref_id)

    line_text = str(request.get("text") or line_entry.get("text") or "").strip()
    if not line_text:
        raise HTTPException(status_code=400, detail="閰嶉煶鏂囨湰涓虹┖")

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
        raise HTTPException(status_code=400, detail="璇峰厛閫夋嫨闊宠壊鍙傝€冮煶棰?")

    selected_voice_ref = None
    if isinstance(refs, list):
        selected_voice_ref = next(
            (item for item in refs if str(item.get("id") or "").strip() == voice_reference_id),
            None,
        )
    if not selected_voice_ref:
        raise HTTPException(status_code=400, detail="闊宠壊鍙傝€冮煶棰戜笉瀛樺湪")

    emotion_audio_preset_id = ""
    if method == VOICEOVER_TTS_METHOD_AUDIO:
        emotion_audio_preset_id = str(
            request.get("emotion_audio_preset_id")
            or line_tts.get("emotion_audio_preset_id")
            or ""
        ).strip()
        if not emotion_audio_preset_id:
            raise HTTPException(status_code=400, detail="璇峰厛閫夋嫨鎯呮劅鍙傝€冮煶棰戦璁?")
        emotion_presets = shared.get("emotion_audio_presets", [])
        selected_emotion = None
        if isinstance(emotion_presets, list):
            selected_emotion = next(
                (
                    item
                    for item in emotion_presets
                    if str(item.get("id") or "").strip() == emotion_audio_preset_id
                ),
                None,
            )
        if not selected_emotion:
            raise HTTPException(status_code=400, detail="鎯呮劅鍙傝€冮煶棰戦璁句笉瀛樺湪")

    vector_preset_id = str(request.get("vector_preset_id") or line_tts.get("vector_preset_id") or "").strip()
    vector_config = _normalize_voiceover_vector_config(
        request.get("vector_config") or line_tts.get("vector_config")
    )
    emo_text = str(
        request.get("emo_text")
        if request.get("emo_text") is not None
        else line_entry.get("emotion") or ""
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


async def enqueue_voiceover_generate_all(
    episode_id: int,
    user: models.User,
    db: Session,
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
                skipped.append({"line_id": "", "reason": "line_id缂哄け"})
                continue
            if line_id in seen_line_ids:
                skipped.append({"line_id": line_id, "reason": "line_id閲嶅"})
                continue
            seen_line_ids.add(line_id)

            line_text = str(line_entry.get("text") or "").strip()
            if not line_text:
                skipped.append({"line_id": line_id, "reason": "閰嶉煶鏂囨湰涓虹┖"})
                continue

            line_tts = _normalize_voiceover_line_tts(line_entry.get("tts"), default_voice_ref_id)
            status = str(line_tts.get("generate_status") or "").strip().lower()
            if status in {"pending", "processing"}:
                skipped.append({"line_id": line_id, "reason": "宸插湪闃熷垪涓垨鐢熸垚涓?"})
                continue

            method = str(line_tts.get("emotion_control_method") or VOICEOVER_TTS_METHOD_SAME).strip()
            if method not in VOICEOVER_TTS_ALLOWED_METHODS:
                method = VOICEOVER_TTS_METHOD_SAME

            voice_reference_id = str(line_tts.get("voice_reference_id") or default_voice_ref_id).strip()
            if not voice_reference_id:
                skipped.append({"line_id": line_id, "reason": "鏈€夋嫨闊宠壊鍙傝€冮煶棰?"})
                continue
            if ref_id_set and voice_reference_id not in ref_id_set:
                skipped.append({"line_id": line_id, "reason": "闊宠壊鍙傝€冮煶棰戜笉瀛樺湪"})
                continue

            emotion_audio_preset_id = ""
            if method == VOICEOVER_TTS_METHOD_AUDIO:
                emotion_audio_preset_id = str(line_tts.get("emotion_audio_preset_id") or "").strip()
                if not emotion_audio_preset_id:
                    skipped.append({"line_id": line_id, "reason": "鏈€夋嫨鎯呮劅鍙傝€冮煶棰戦璁?"})
                    continue
                if emotion_preset_id_set and emotion_audio_preset_id not in emotion_preset_id_set:
                    skipped.append({"line_id": line_id, "reason": "鎯呮劅鍙傝€冮煶棰戰璁句笉瀛樺湪"})
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


def get_voiceover_tts_status(
    episode_id: int,
    user: models.User,
    db: Session,
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
