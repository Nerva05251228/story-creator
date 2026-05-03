import json
from typing import Any, Optional


def voiceover_shot_match_key(shot: dict, fallback_index: Optional[int] = None) -> str:
    if not isinstance(shot, dict):
        return f"index:{fallback_index}" if fallback_index is not None else ""

    shot_number = shot.get("shot_number")
    if shot_number is not None:
        normalized = str(shot_number).strip()
        if normalized:
            return f"shot_number:{normalized}"

    return f"index:{fallback_index}" if fallback_index is not None else ""


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
