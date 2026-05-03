import json
import os
import uuid
from datetime import datetime
from typing import Any, Callable, Optional, Tuple


VOICEOVER_TTS_METHOD_SAME = "与音色参考音频相同"
VOICEOVER_TTS_METHOD_VECTOR = "使用情感向量控制"
VOICEOVER_TTS_METHOD_EMO_TEXT = "使用情感描述文本控制"
VOICEOVER_TTS_METHOD_AUDIO = "使用情感参考音频"
VOICEOVER_TTS_ALLOWED_METHODS = {
    VOICEOVER_TTS_METHOD_SAME,
    VOICEOVER_TTS_METHOD_VECTOR,
    VOICEOVER_TTS_METHOD_EMO_TEXT,
    VOICEOVER_TTS_METHOD_AUDIO,
}
VOICEOVER_TTS_VECTOR_KEYS = [
    "joy", "anger", "sadness", "fear",
    "disgust", "depression", "surprise", "neutral",
]


def voiceover_shot_match_key(shot: dict, fallback_index: Optional[int] = None) -> str:
    if not isinstance(shot, dict):
        return f"index:{fallback_index}" if fallback_index is not None else ""

    shot_number = shot.get("shot_number")
    if shot_number is not None:
        normalized = str(shot_number).strip()
        if normalized:
            return f"shot_number:{normalized}"

    return f"index:{fallback_index}" if fallback_index is not None else ""


def voiceover_default_vector_config() -> dict:
    return {
        "weight": 0.65,
        "joy": 0.0,
        "anger": 0.0,
        "sadness": 0.0,
        "fear": 0.0,
        "disgust": 0.0,
        "depression": 0.0,
        "surprise": 0.0,
        "neutral": 1.0,
    }


def safe_float(value: Any, default_value: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default_value)
    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return parsed


def normalize_voiceover_vector_config(raw_config: Any) -> dict:
    source = raw_config if isinstance(raw_config, dict) else {}
    normalized = {"weight": safe_float(source.get("weight"), 0.65)}
    for key in VOICEOVER_TTS_VECTOR_KEYS:
        normalized[key] = safe_float(source.get(key), 0.0)

    if all(normalized.get(k, 0.0) == 0.0 for k in VOICEOVER_TTS_VECTOR_KEYS):
        normalized["neutral"] = 1.0

    return normalized


def normalize_voiceover_setting_template_payload(
    raw_settings: Any,
    default_voice_reference_id: str = "",
) -> dict:
    source = raw_settings if isinstance(raw_settings, dict) else {}
    method = str(source.get("emotion_control_method") or VOICEOVER_TTS_METHOD_SAME).strip()
    if method not in VOICEOVER_TTS_ALLOWED_METHODS:
        method = VOICEOVER_TTS_METHOD_SAME
    return {
        "emotion_control_method": method,
        "voice_reference_id": str(source.get("voice_reference_id") or default_voice_reference_id or "").strip(),
        "vector_preset_id": str(source.get("vector_preset_id") or "").strip(),
        "emotion_audio_preset_id": str(source.get("emotion_audio_preset_id") or "").strip(),
        "vector_config": normalize_voiceover_vector_config(source.get("vector_config")),
    }


def voiceover_default_line_tts(default_voice_reference_id: str = "") -> dict:
    return {
        "emotion_control_method": VOICEOVER_TTS_METHOD_SAME,
        "voice_reference_id": default_voice_reference_id or "",
        "vector_preset_id": "",
        "emotion_audio_preset_id": "",
        "vector_config": voiceover_default_vector_config(),
        "generated_audios": [],
        "generate_status": "idle",
        "generate_error": "",
        "latest_task_id": "",
    }


def normalize_voiceover_line_tts(raw_tts: Any, default_voice_reference_id: str = "") -> dict:
    source = raw_tts if isinstance(raw_tts, dict) else {}
    normalized = voiceover_default_line_tts(default_voice_reference_id)

    method = str(source.get("emotion_control_method") or "").strip()
    if method in VOICEOVER_TTS_ALLOWED_METHODS:
        normalized["emotion_control_method"] = method

    normalized["voice_reference_id"] = str(
        source.get("voice_reference_id") or normalized["voice_reference_id"]
    ).strip()
    normalized["vector_preset_id"] = str(source.get("vector_preset_id") or "").strip()
    normalized["emotion_audio_preset_id"] = str(source.get("emotion_audio_preset_id") or "").strip()
    normalized["vector_config"] = normalize_voiceover_vector_config(source.get("vector_config"))
    normalized["generate_status"] = str(source.get("generate_status") or "idle").strip().lower()
    if normalized["generate_status"] not in {"idle", "pending", "processing", "completed", "failed"}:
        normalized["generate_status"] = "idle"
    normalized["generate_error"] = str(source.get("generate_error") or "").strip()
    normalized["latest_task_id"] = str(source.get("latest_task_id") or "").strip()

    generated_audios = source.get("generated_audios", [])
    if isinstance(generated_audios, list):
        cleaned = []
        for item in generated_audios:
            if not isinstance(item, dict):
                continue
            audio_url = str(item.get("url") or "").strip()
            if not audio_url:
                continue
            cleaned.append({
                "id": str(item.get("id") or uuid.uuid4().hex).strip(),
                "name": str(item.get("name") or "生成结果").strip(),
                "url": audio_url,
                "task_id": str(item.get("task_id") or "").strip(),
                "created_at": str(item.get("created_at") or datetime.utcnow().isoformat()),
                "status": str(item.get("status") or "completed").strip().lower(),
            })
        normalized["generated_audios"] = cleaned

    return normalized


def ensure_voiceover_shot_line_fields(
    shot: dict,
    default_voice_reference_id: str = "",
) -> bool:
    if not isinstance(shot, dict):
        return False

    changed = False
    shot_number = str(shot.get("shot_number") or "").strip() or "0"

    narration = shot.get("narration")
    if isinstance(narration, dict):
        current_line_id = str(narration.get("line_id") or "").strip()
        target_line_id = current_line_id or f"shot_{shot_number}_narration"
        if current_line_id != target_line_id:
            narration["line_id"] = target_line_id
            changed = True
        normalized_tts = normalize_voiceover_line_tts(
            narration.get("tts"),
            default_voice_reference_id,
        )
        if narration.get("tts") != normalized_tts:
            narration["tts"] = normalized_tts
            changed = True

    dialogue = shot.get("dialogue")
    if isinstance(dialogue, list):
        for idx, item in enumerate(dialogue, start=1):
            if not isinstance(item, dict):
                continue
            current_line_id = str(item.get("line_id") or "").strip()
            target_line_id = current_line_id or f"shot_{shot_number}_dialogue_{idx}"
            if current_line_id != target_line_id:
                item["line_id"] = target_line_id
                changed = True
            normalized_tts = normalize_voiceover_line_tts(
                item.get("tts"),
                default_voice_reference_id,
            )
            if item.get("tts") != normalized_tts:
                item["tts"] = normalized_tts
                changed = True

    return changed


def normalize_voiceover_shots_for_tts(
    shots: Any,
    default_voice_reference_id: str = "",
) -> Tuple[list, bool]:
    changed = False
    normalized_shots = shots if isinstance(shots, list) else []
    for shot in normalized_shots:
        changed = ensure_voiceover_shot_line_fields(shot, default_voice_reference_id) or changed
    return normalized_shots, changed


def extract_voiceover_tts_line_states(shots: list) -> list:
    states = []
    for shot in shots:
        if not isinstance(shot, dict):
            continue

        narration = shot.get("narration")
        if isinstance(narration, dict):
            line_id = str(narration.get("line_id") or "").strip()
            tts = narration.get("tts")
            if line_id and isinstance(tts, dict):
                states.append({"line_id": line_id, "tts": tts})

        dialogue = shot.get("dialogue")
        if isinstance(dialogue, list):
            for item in dialogue:
                if not isinstance(item, dict):
                    continue
                line_id = str(item.get("line_id") or "").strip()
                tts = item.get("tts")
                if line_id and isinstance(tts, dict):
                    states.append({"line_id": line_id, "tts": tts})
    return states


def find_voiceover_line_entry(shots: list, line_id: str) -> Optional[dict]:
    target = str(line_id or "").strip()
    if not target:
        return None

    for shot in shots:
        if not isinstance(shot, dict):
            continue
        narration = shot.get("narration")
        if isinstance(narration, dict) and str(narration.get("line_id") or "").strip() == target:
            return narration
        dialogue = shot.get("dialogue")
        if isinstance(dialogue, list):
            for item in dialogue:
                if isinstance(item, dict) and str(item.get("line_id") or "").strip() == target:
                    return item
    return None


def parse_episode_voiceover_payload(episode) -> dict:
    payload = {}
    raw_text = str(getattr(episode, "voiceover_data", "") or "").strip()
    if raw_text:
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}
    shots = payload.get("shots")
    if not isinstance(shots, list):
        payload["shots"] = []
    return payload


def voiceover_default_test_mp3_path(module_file: str) -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(module_file), "..", "TTS_example", "test.mp3")
    )


def voiceover_default_shared_data() -> dict:
    return {
        "initialized": False,
        "voice_references": [],
        "vector_presets": [],
        "emotion_audio_presets": [],
        "setting_templates": [],
    }


def voiceover_default_reference_item(
    default_test_mp3_path_factory: Callable[[], str],
    *,
    now_factory: Optional[Callable[[], str]] = None,
) -> dict:
    created_at = now_factory() if callable(now_factory) else datetime.utcnow().isoformat()
    return {
        "id": "voice_ref_default_female_1",
        "name": "女声1",
        "file_name": "test.mp3",
        "url": "",
        "local_path": str(default_test_mp3_path_factory() or "").strip(),
        "created_at": str(created_at),
    }


def normalize_voiceover_shared_data(
    raw_data: Any,
    *,
    default_reference_item_factory: Optional[Callable[[], dict]] = None,
    reference_exists: Callable[[str], bool] = os.path.exists,
) -> dict:
    source = raw_data if isinstance(raw_data, dict) else {}
    normalized = voiceover_default_shared_data()
    normalized["initialized"] = bool(source.get("initialized", False))

    voice_references = source.get("voice_references", [])
    if isinstance(voice_references, list):
        for item in voice_references:
            if not isinstance(item, dict):
                continue
            ref_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not ref_id or not name:
                continue
            normalized["voice_references"].append({
                "id": ref_id,
                "name": name,
                "file_name": str(item.get("file_name") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "local_path": str(item.get("local_path") or "").strip(),
                "created_at": str(item.get("created_at") or datetime.utcnow().isoformat()),
            })

    vector_presets = source.get("vector_presets", [])
    if isinstance(vector_presets, list):
        for item in vector_presets:
            if not isinstance(item, dict):
                continue
            preset_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not preset_id or not name:
                continue
            normalized["vector_presets"].append({
                "id": preset_id,
                "name": name,
                "description": str(item.get("description") or "").strip(),
                "vector_config": normalize_voiceover_vector_config(item.get("vector_config")),
                "created_at": str(item.get("created_at") or datetime.utcnow().isoformat()),
            })

    emotion_audio_presets = source.get("emotion_audio_presets", [])
    if isinstance(emotion_audio_presets, list):
        for item in emotion_audio_presets:
            if not isinstance(item, dict):
                continue
            preset_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not preset_id or not name:
                continue
            normalized["emotion_audio_presets"].append({
                "id": preset_id,
                "name": name,
                "description": str(item.get("description") or "").strip(),
                "file_name": str(item.get("file_name") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "local_path": str(item.get("local_path") or "").strip(),
                "created_at": str(item.get("created_at") or datetime.utcnow().isoformat()),
            })

    default_voice_ref_id = voiceover_first_reference_id(normalized)
    setting_templates = source.get("setting_templates", [])
    if isinstance(setting_templates, list):
        for item in setting_templates:
            if not isinstance(item, dict):
                continue
            template_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not template_id or not name:
                continue
            normalized["setting_templates"].append({
                "id": template_id,
                "name": name,
                "settings": normalize_voiceover_setting_template_payload(
                    item.get("settings"),
                    default_voice_ref_id,
                ),
                "created_at": str(item.get("created_at") or datetime.utcnow().isoformat()),
                "updated_at": str(
                    item.get("updated_at") or item.get("created_at") or datetime.utcnow().isoformat()
                ),
            })

    if not normalized["initialized"]:
        if not normalized["voice_references"] and callable(default_reference_item_factory):
            default_item = default_reference_item_factory()
            if isinstance(default_item, dict) and reference_exists(str(default_item.get("local_path") or "")):
                normalized["voice_references"].append(default_item)
        normalized["initialized"] = True

    return normalized


def load_script_voiceover_shared_data(
    script,
    *,
    normalize_shared_data=normalize_voiceover_shared_data,
) -> dict:
    raw_payload = {}
    raw_text = str(getattr(script, "voiceover_shared_data", "") or "").strip()
    if raw_text:
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                raw_payload = parsed
        except Exception:
            raw_payload = {}
    return normalize_shared_data(raw_payload)


def save_script_voiceover_shared_data(
    script,
    payload: dict,
    *,
    normalize_shared_data=normalize_voiceover_shared_data,
):
    script.voiceover_shared_data = json.dumps(
        normalize_shared_data(payload),
        ensure_ascii=False,
    )


def voiceover_first_reference_id(shared_data: dict) -> str:
    refs = shared_data.get("voice_references", []) if isinstance(shared_data, dict) else []
    if isinstance(refs, list) and refs:
        return str(refs[0].get("id") or "").strip()
    return ""


def iter_voiceover_lines(shots: list):
    if not isinstance(shots, list):
        return
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        narration = shot.get("narration")
        if isinstance(narration, dict):
            yield narration
        dialogue = shot.get("dialogue")
        if isinstance(dialogue, list):
            for item in dialogue:
                if isinstance(item, dict):
                    yield item


def merge_voiceover_line_preserving_tts(
    existing_line: Any,
    incoming_line: Any,
    fallback_line_id: str = "",
) -> Any:
    if not isinstance(incoming_line, dict):
        return incoming_line

    existing = existing_line if isinstance(existing_line, dict) else {}
    merged = dict(existing)
    merged.update(incoming_line)

    incoming_has_tts = "tts" in incoming_line
    existing_tts = existing.get("tts")
    incoming_tts = incoming_line.get("tts")

    if incoming_has_tts:
        if isinstance(incoming_tts, dict) and isinstance(existing_tts, dict):
            merged_tts = dict(existing_tts)
            merged_tts.update(incoming_tts)
            merged["tts"] = merged_tts
        else:
            merged["tts"] = incoming_tts
    elif isinstance(existing_tts, dict):
        merged["tts"] = existing_tts

    line_id = str(merged.get("line_id") or "").strip()
    if not line_id:
        old_line_id = str(existing.get("line_id") or "").strip()
        if old_line_id:
            merged["line_id"] = old_line_id
        elif fallback_line_id:
            merged["line_id"] = fallback_line_id

    return merged


def merge_voiceover_dialogue_preserving_tts(
    existing_dialogue: Any,
    incoming_dialogue: Any,
    shot_number: Any,
) -> Any:
    if not isinstance(incoming_dialogue, list):
        return incoming_dialogue

    existing_list = existing_dialogue if isinstance(existing_dialogue, list) else []
    by_line_id = {}
    by_index = {}
    for idx, item in enumerate(existing_list, start=1):
        if not isinstance(item, dict):
            continue
        by_index[idx] = item
        line_id = str(item.get("line_id") or "").strip()
        if line_id and line_id not in by_line_id:
            by_line_id[line_id] = item

    normalized_shot_number = str(shot_number or "").strip() or "0"
    merged_list = []
    for idx, incoming_item in enumerate(incoming_dialogue, start=1):
        incoming_dict = incoming_item if isinstance(incoming_item, dict) else {}
        incoming_line_id = str(incoming_dict.get("line_id") or "").strip()
        existing_item = by_line_id.get(incoming_line_id) if incoming_line_id else None
        if not isinstance(existing_item, dict):
            existing_item = by_index.get(idx)
        fallback_line_id = incoming_line_id or f"shot_{normalized_shot_number}_dialogue_{idx}"
        merged_item = merge_voiceover_line_preserving_tts(
            existing_item,
            incoming_dict,
            fallback_line_id,
        )
        merged_list.append(merged_item)

    return merged_list


def merge_voiceover_shots_preserving_extensions(
    existing_voiceover_data: str,
    incoming_voiceover_shots: list,
) -> dict:
    existing_payload = {}
    if isinstance(existing_voiceover_data, str) and existing_voiceover_data.strip():
        try:
            parsed = json.loads(existing_voiceover_data)
            if isinstance(parsed, dict):
                existing_payload = parsed
        except Exception:
            existing_payload = {}

    existing_shots = existing_payload.get("shots", [])
    if not isinstance(existing_shots, list):
        existing_shots = []

    existing_shot_map = {}
    for idx, item in enumerate(existing_shots):
        if not isinstance(item, dict):
            continue
        key = voiceover_shot_match_key(item, idx)
        if key and key not in existing_shot_map:
            existing_shot_map[key] = item

    if not isinstance(incoming_voiceover_shots, list):
        incoming_voiceover_shots = []

    merged_shots = []
    for idx, incoming in enumerate(incoming_voiceover_shots):
        incoming_shot = incoming if isinstance(incoming, dict) else {}
        key = voiceover_shot_match_key(incoming_shot, idx)
        existing_shot = existing_shot_map.get(key, {})

        merged_shot = dict(existing_shot) if isinstance(existing_shot, dict) else {}
        merged_shot["shot_number"] = incoming_shot.get("shot_number")
        merged_shot["voice_type"] = incoming_shot.get("voice_type")

        shot_number_for_line = str(
            incoming_shot.get("shot_number")
            or merged_shot.get("shot_number")
            or idx + 1
        ).strip()

        incoming_narration = incoming_shot.get("narration")
        existing_narration = existing_shot.get("narration") if isinstance(existing_shot, dict) else None
        if isinstance(incoming_narration, dict):
            merged_shot["narration"] = merge_voiceover_line_preserving_tts(
                existing_narration,
                incoming_narration,
                f"shot_{shot_number_for_line}_narration",
            )
        else:
            merged_shot["narration"] = incoming_narration

        merged_shot["dialogue"] = merge_voiceover_dialogue_preserving_tts(
            existing_shot.get("dialogue") if isinstance(existing_shot, dict) else None,
            incoming_shot.get("dialogue"),
            shot_number_for_line,
        )
        merged_shots.append(merged_shot)

    merged_payload = dict(existing_payload) if isinstance(existing_payload, dict) else {}
    merged_payload["shots"] = merged_shots
    return merged_payload
