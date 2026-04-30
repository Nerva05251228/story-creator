import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError

from ai_config import RELAY_CHAT_COMPLETIONS_URL
from database import SessionLocal
import models


DASHBOARD_TASK_TYPE_LABELS = {
    "detailed_storyboard_stage1": "\u8be6\u7ec6\u5206\u955c\uff08Stage 1 \u521d\u59cb\u5206\u6790\uff09",
    "detailed_storyboard_stage2": "\u8be6\u7ec6\u5206\u955c\uff08Stage 2 \u4e3b\u4f53\u63d0\u793a\u8bcd\uff09",
    "simple_storyboard": "简单分镜",
    "detailed_storyboard": "详细分镜",
    "sora_prompt": "Sora提示词",
    "large_shot_prompt": "大镜头提示词",
    "video_generate": "故事板Sora视频",
    "subject_prompt": "主体提示词",
    "card_image_generate": "主体图片生成",
    "detail_images": "细化图片",
    "storyboard2_image": "故事板2镜头图",
    "storyboard2_video": "故事板2视频",
    "voiceover_tts": "配音TTS",
    "managed_video": "托管视频",
    "image_generation": "图片生成",
    "opening": "精彩开头",
    "narration": "解说剧转换",
    "managed_prompt_optimize": "托管提示词优化",
    "other": "其他任务",
}

DASHBOARD_STATUS_LABELS = {
    "submitting": "提交中",
    "processing": "处理中",
    "completed": "完成",
    "failed": "失败",
    "cancelled": "已取消",
}

_STATUS_ALIASES = {
    "idle": "submitting",
    "pending": "submitting",
    "queued": "submitting",
    "request_received": "submitting",
    "submitted": "submitting",
    "submitting": "submitting",
    "preparing": "processing",
    "starting": "processing",
    "processing": "processing",
    "running": "processing",
    "in_progress": "processing",
    "completed": "completed",
    "complete": "completed",
    "success": "completed",
    "succeeded": "completed",
    "done": "completed",
    "failed": "failed",
    "failure": "failed",
    "error": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}

_API_URL_KEYS = (
    "api_url",
    "submit_api_url",
    "detail_images_api_url",
)
_STATUS_API_URL_KEYS = (
    "status_api_url",
    "status_api_url_template",
    "detail_images_status_api_url_template",
)
_MODEL_KEYS = (
    "actual_model",
    "model_name",
    "model",
    "requested_model",
    "detail_images_actual_model",
)
_PROVIDER_KEYS = (
    "provider",
    "detail_images_provider",
)
_EXTERNAL_TASK_ID_KEYS = (
    "task_id",
    "record_id",
    "line_id",
)
_STATUS_KEYS = (
    "status",
    "task_status",
)
_SUBMITTED_TASK_ID_KEYS = (
    "task_id",
)
_ERROR_KEYS = (
    "error_message",
    "error",
    "exception",
    "detail",
    "message",
)
_SUBJECT_CARD_TASK_TYPES = {"subject_prompt", "card_image_generate", "character_three_view_image"}
_DETAIL_STORYBOARD_TASK_TYPES = {"detailed_storyboard_stage1", "detailed_storyboard_stage2"}


def _safe_json_dumps(value: Any, fallback: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        try:
            return json.dumps(str(value), ensure_ascii=False, indent=2)
        except Exception:
            return fallback


def _safe_json_loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _flatten_text(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = _safe_json_dumps(value, fallback="")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _recursive_find(payload: Any, keys: Tuple[str, ...]) -> str:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (int, float)) and key in {"task_id", "record_id"}:
                return str(value)
        for value in payload.values():
            found = _recursive_find(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _recursive_find(item, keys)
            if found:
                return found
    return ""


def _extract_error_message(*payloads: Any) -> str:
    for payload in payloads:
        if isinstance(payload, str) and payload.strip():
            return payload.strip()
        if not isinstance(payload, (dict, list)):
            continue
        found = _recursive_find(payload, _ERROR_KEYS)
        if found:
            if isinstance(payload, dict) and found == str(payload.get("error", "")).strip():
                nested = payload.get("error")
                if isinstance(nested, dict):
                    nested_message = _recursive_find(nested, ("message", "detail", "error"))
                    if nested_message:
                        return nested_message
            return found
    return ""


def _extract_status_hint(*payloads: Any) -> str:
    for payload in payloads:
        if isinstance(payload, str):
            normalized = _normalize_status(payload)
            if normalized != payload.strip().lower() or normalized in DASHBOARD_STATUS_LABELS:
                return normalized
            continue
        if not isinstance(payload, (dict, list)):
            continue
        found = _recursive_find(payload, _STATUS_KEYS)
        if not found:
            continue
        normalized = _normalize_status(found)
        if normalized:
            return normalized
    return ""


def _extract_result_summary(task_type: str, status: str, output_payload: Any, raw_response: Any, latest_payload: Any) -> str:
    if status == "failed":
        return _extract_error_message(output_payload, raw_response, latest_payload)

    payload = output_payload if output_payload not in (None, "") else latest_payload
    if isinstance(payload, dict):
        timeline = payload.get("timeline")
        if isinstance(timeline, list):
            return f"timeline {len(timeline)} 段"
        images = payload.get("images")
        if isinstance(images, list):
            return f"图片 {len(images)} 张"
        video_url = str(payload.get("video_url") or payload.get("video_path") or "").strip()
        if video_url:
            return _flatten_text(video_url, limit=160)
        content = payload.get("content") or payload.get("result") or payload.get("output")
        if content:
            return _flatten_text(content)
    if isinstance(payload, list):
        return f"结果 {len(payload)} 项"
    if task_type == "voiceover_tts" and status == "completed":
        return "TTS生成完成"
    return _flatten_text(payload)


def _normalize_status(status: Optional[str]) -> str:
    normalized = str(status or "").strip().lower()
    if not normalized:
        return "submitting"
    return _STATUS_ALIASES.get(normalized, normalized)


def _infer_debug_status(output_data: Any, raw_response: Any) -> str:
    explicit_status = _extract_status_hint(output_data, raw_response)
    if explicit_status:
        return explicit_status
    if _extract_error_message(output_data, raw_response):
        return "failed"
    if _recursive_find(output_data, _SUBMITTED_TASK_ID_KEYS) or _recursive_find(raw_response, _SUBMITTED_TASK_ID_KEYS):
        return "processing"
    if output_data is not None:
        return "completed"
    if raw_response is not None:
        return "failed"
    return "submitting"


def _infer_status_from_filename(file_name: str) -> str:
    normalized = str(file_name or "").strip().lower()
    if not normalized:
        return "processing"
    if any(token in normalized for token in ("error", "failed", "exception")):
        return "failed"
    if any(token in normalized for token in ("output", "completed", "result", "post_process")):
        return "completed"
    if any(token in normalized for token in ("worker_start", "polling", "submit")):
        return "processing"
    if "input" in normalized:
        return "submitting"
    return "processing"


def _canonicalize_task_key(task_folder: str, source_record_type: str, source_record_id: Optional[int]) -> tuple[str, str, Optional[int]]:
    if source_record_type and source_record_id:
        return f"{source_record_type}_{source_record_id}", source_record_type, source_record_id

    folder = str(task_folder or "").strip()
    voiceover_match = re.match(r"voiceover_tts_task_(\d+)", folder)
    if voiceover_match:
        task_id = int(voiceover_match.group(1))
        return f"voiceover_tts_task_{task_id}", "voiceover_tts_task", task_id

    return folder or f"dashboard_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}", source_record_type, source_record_id


def _derive_task_type(stage: str, task_type: str, task_folder: str, payloads: List[Any]) -> str:
    explicit = str(task_type or "").strip()
    if explicit:
        return explicit

    normalized_stage = str(stage or "").strip()
    folder = str(task_folder or "").strip()

    if normalized_stage == "simple_storyboard":
        return "simple_storyboard"
    if normalized_stage in {"stage1", "detailed_storyboard"}:
        return "detailed_storyboard_stage1"
    if normalized_stage == "stage2":
        return "detailed_storyboard_stage2"
    if normalized_stage == "video_generate":
        return "video_generate"
    if normalized_stage in {"generate_subject_prompt", "subject_prompt"}:
        return "subject_prompt"
    if normalized_stage == "opening":
        return "opening"
    if normalized_stage == "narration":
        return "narration"
    if normalized_stage == "managed_prompt_optimize":
        return "managed_prompt_optimize"
    if normalized_stage == "card_image_generate":
        return "card_image_generate"
    if normalized_stage == "sora_prompt":
        for payload in payloads:
            if isinstance(payload, dict):
                prompt_key = str(payload.get("prompt_key") or "").strip()
                duration_field = str(payload.get("duration_template_field") or "").strip()
                if prompt_key == "generate_large_shot_prompts" or duration_field == "large_shot_prompt_rule":
                    return "large_shot_prompt"
        return "sora_prompt"

    if folder.startswith("detail_images_shot_"):
        return "detail_images"
    if folder.startswith("storyboard2_subshot_video_"):
        return "storyboard2_video"
    if folder.startswith("storyboard2_subshot_"):
        return "storyboard2_image"
    if folder.startswith("voiceover_tts_task_"):
        return "voiceover_tts"

    return "other"


def _apply_episode_context(record: models.DashboardTaskLog, episode: Optional[models.Episode], script: Optional[models.Script], user: Optional[models.User]) -> None:
    if episode:
        record.episode_id = episode.id
        record.episode_name = str(getattr(episode, "name", "") or "")
    if script:
        record.script_id = script.id
        record.script_name = str(getattr(script, "name", "") or "")
    if user:
        record.creator_user_id = user.id
        record.creator_username = str(getattr(user, "username", "") or "")


def _resolve_context(
    db,
    record: models.DashboardTaskLog,
    stage: str,
    task_type: str,
    episode_id: Optional[int],
    shot_id: Optional[int],
    source_record_type: str,
    source_record_id: Optional[int],
    payloads: List[Any],
) -> None:
    if source_record_type == "managed_task" and source_record_id:
        task = db.query(models.ManagedTask).filter(models.ManagedTask.id == source_record_id).first()
        if task:
            record.source_record_type = source_record_type
            record.source_record_id = source_record_id
            record.shot_id = int(getattr(task, "shot_id", 0) or 0) or None
            record.external_task_id = str(getattr(task, "task_id", "") or "")
            session = db.query(models.ManagedSession).filter(models.ManagedSession.id == task.session_id).first()
            if session:
                episode = db.query(models.Episode).filter(models.Episode.id == session.episode_id).first()
                script = db.query(models.Script).filter(models.Script.id == getattr(episode, "script_id", None)).first() if episode else None
                user = db.query(models.User).filter(models.User.id == getattr(script, "user_id", None)).first() if script else None
                _apply_episode_context(record, episode, script, user)
            shot = None
            if record.shot_id:
                shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == record.shot_id).first()
            if shot:
                record.shot_number = getattr(shot, "shot_number", None)
        return

    if source_record_type == "voiceover_tts_task" and source_record_id:
        task = db.query(models.VoiceoverTtsTask).filter(models.VoiceoverTtsTask.id == source_record_id).first()
        if task:
            record.source_record_type = source_record_type
            record.source_record_id = source_record_id
            record.external_task_id = str(task.id)
            episode = db.query(models.Episode).filter(models.Episode.id == task.episode_id).first()
            script = db.query(models.Script).filter(models.Script.id == getattr(episode, "script_id", None)).first() if episode else None
            user = db.query(models.User).filter(models.User.id == getattr(script, "user_id", None)).first() if script else None
            _apply_episode_context(record, episode, script, user)
        return

    if episode_id:
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        script = db.query(models.Script).filter(models.Script.id == getattr(episode, "script_id", None)).first() if episode else None
        user = db.query(models.User).filter(models.User.id == getattr(script, "user_id", None)).first() if script else None
        _apply_episode_context(record, episode, script, user)
        return

    if shot_id and task_type in _SUBJECT_CARD_TASK_TYPES:
        card = db.query(models.SubjectCard).filter(models.SubjectCard.id == shot_id).first()
        if card:
            record.shot_id = card.id
            library = db.query(models.StoryLibrary).filter(models.StoryLibrary.id == card.library_id).first()
            episode = db.query(models.Episode).filter(models.Episode.id == getattr(library, "episode_id", None)).first() if library else None
            script = db.query(models.Script).filter(models.Script.id == getattr(episode, "script_id", None)).first() if episode else None
            user = db.query(models.User).filter(models.User.id == getattr(library, "user_id", None)).first() if library else None
            _apply_episode_context(record, episode, script, user)
        return

    if shot_id:
        shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
        if shot:
            record.shot_id = shot.id
            record.shot_number = getattr(shot, "shot_number", None)
            episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
            script = db.query(models.Script).filter(models.Script.id == getattr(episode, "script_id", None)).first() if episode else None
            user = db.query(models.User).filter(models.User.id == getattr(script, "user_id", None)).first() if script else None
            _apply_episode_context(record, episode, script, user)
            return

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        sub_shot_id = payload.get("sub_shot_id")
        if sub_shot_id:
            sub_shot = db.query(models.Storyboard2SubShot).filter(models.Storyboard2SubShot.id == int(sub_shot_id)).first()
            if sub_shot:
                storyboard2_shot = db.query(models.Storyboard2Shot).filter(
                    models.Storyboard2Shot.id == sub_shot.storyboard2_shot_id
                ).first()
                episode = db.query(models.Episode).filter(
                    models.Episode.id == getattr(storyboard2_shot, "episode_id", None)
                ).first() if storyboard2_shot else None
                script = db.query(models.Script).filter(models.Script.id == getattr(episode, "script_id", None)).first() if episode else None
                user = db.query(models.User).filter(models.User.id == getattr(script, "user_id", None)).first() if script else None
                _apply_episode_context(record, episode, script, user)
                record.shot_number = getattr(storyboard2_shot, "shot_number", None)
                return

        storyboard2_shot_id = payload.get("storyboard2_shot_id")
        if storyboard2_shot_id:
            storyboard2_shot = db.query(models.Storyboard2Shot).filter(
                models.Storyboard2Shot.id == int(storyboard2_shot_id)
            ).first()
            if storyboard2_shot:
                episode = db.query(models.Episode).filter(models.Episode.id == storyboard2_shot.episode_id).first()
                script = db.query(models.Script).filter(models.Script.id == getattr(episode, "script_id", None)).first() if episode else None
                user = db.query(models.User).filter(models.User.id == getattr(script, "user_id", None)).first() if script else None
                _apply_episode_context(record, episode, script, user)
                record.shot_number = getattr(storyboard2_shot, "shot_number", None)
                return

        card_id = payload.get("card_id")
        if card_id:
            card = db.query(models.SubjectCard).filter(models.SubjectCard.id == int(card_id)).first()
            if card:
                record.shot_id = card.id
                library = db.query(models.StoryLibrary).filter(models.StoryLibrary.id == card.library_id).first()
                episode = db.query(models.Episode).filter(models.Episode.id == getattr(library, "episode_id", None)).first() if library else None
                script = db.query(models.Script).filter(models.Script.id == getattr(episode, "script_id", None)).first() if episode else None
                user = db.query(models.User).filter(models.User.id == getattr(library, "user_id", None)).first() if library else None
                _apply_episode_context(record, episode, script, user)
                return


def _derive_title(task_type: str, record: models.DashboardTaskLog) -> str:
    base = DASHBOARD_TASK_TYPE_LABELS.get(task_type, task_type or "任务")
    parts = [base]
    if record.script_name:
        parts.append(record.script_name)
    if record.episode_name:
        parts.append(f"第{record.episode_name}集")
    if record.shot_number is not None:
        parts.append(f"镜头{record.shot_number}")
    elif record.shot_id:
        parts.append(f"ID {record.shot_id}")
    return " / ".join(part for part in parts if part)


def _append_event(record: models.DashboardTaskLog, event: Dict[str, Any]) -> None:
    events = _safe_json_loads(record.events_json, [])
    if not isinstance(events, list):
        events = []
    events.append(event)
    record.events_json = _safe_json_dumps(events[-80:], "[]")


def _normalize_batch_identifier(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _extract_batch_identifier(*payloads: Any) -> str:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("batch_idx", "batch_id"):
            value = payload.get(key)
            normalized = _normalize_batch_identifier(value)
            if normalized:
                return normalized
    return ""


def _extract_attempt_number(*payloads: Any) -> Optional[int]:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        value = payload.get("attempt")
        if value in (None, ""):
            continue
        try:
            return int(value)
        except Exception:
            continue
    return None


def _extract_shots_count(payload: Any) -> Optional[int]:
    if not isinstance(payload, dict):
        return None
    value = payload.get("shots_count")
    if value in (None, ""):
        shots = payload.get("shots")
        if isinstance(shots, list):
            return len(shots)
        return None
    try:
        return int(value)
    except Exception:
        return None


def _batch_sort_key(batch_id: str) -> Tuple[int, Any]:
    if str(batch_id).isdigit():
        return (0, int(batch_id))
    return (1, str(batch_id))


def _summarize_batch_overall_status(statuses: List[str], fallback_status: str = "submitting") -> str:
    normalized = [_normalize_status(status) for status in statuses if str(status or "").strip()]
    if not normalized:
        return _normalize_status(fallback_status)
    if all(status == "completed" for status in normalized):
        return "completed"
    if any(status == "submitting" for status in normalized):
        return "submitting"
    if any(status == "processing" for status in normalized):
        return "processing"
    if any(status == "failed" for status in normalized):
        return "failed"
    if any(status == "cancelled" for status in normalized):
        return "cancelled"
    return _normalize_status(fallback_status)


def summarize_dashboard_batch_events(events: Any, fallback_status: str = "submitting") -> Dict[str, Any]:
    parsed_events = _safe_json_loads(events, [])
    if not isinstance(parsed_events, list):
        parsed_events = []

    batches: Dict[str, Dict[str, Any]] = {}
    for event in parsed_events:
        if not isinstance(event, dict):
            continue
        input_payload = event.get("input")
        output_payload = event.get("output")
        raw_payload = event.get("raw_response")
        batch_id = _extract_batch_identifier(
            input_payload,
            output_payload,
            raw_payload,
            event,
        )
        if not batch_id:
            continue

        batch = batches.setdefault(
            batch_id,
            {
                "batch_id": batch_id,
                "latest_status": _normalize_status(fallback_status),
                "latest_attempt": None,
                "latest_timestamp": "",
                "attempt_count": 0,
                "failed_attempts": [],
                "completed_attempts": [],
                "last_error": "",
                "shots_count": None,
                "_attempts_seen": set(),
            },
        )
        status = _normalize_status(event.get("status"))
        attempt = _extract_attempt_number(input_payload, output_payload, raw_payload, event)
        if attempt is not None:
            batch["_attempts_seen"].add(int(attempt))
            batch["attempt_count"] = len(batch["_attempts_seen"])

        error_message = _extract_error_message(output_payload, raw_payload, event)
        if status == "failed":
            if attempt is not None and attempt not in batch["failed_attempts"]:
                batch["failed_attempts"].append(int(attempt))
            if error_message:
                batch["last_error"] = error_message
        elif status == "completed":
            if attempt is not None and attempt not in batch["completed_attempts"]:
                batch["completed_attempts"].append(int(attempt))
            shots_count = _extract_shots_count(output_payload)
            if shots_count is not None:
                batch["shots_count"] = shots_count
        elif error_message and not batch["last_error"]:
            batch["last_error"] = error_message

        batch["latest_status"] = status
        batch["latest_attempt"] = attempt
        batch["latest_timestamp"] = str(event.get("timestamp") or "")

    if not batches:
        return {
            "has_batches": False,
            "overall_status": _normalize_status(fallback_status),
            "counts": {
                "total": 0,
                "submitting": 0,
                "processing": 0,
                "completed": 0,
                "failed": 0,
                "cancelled": 0,
            },
            "items": [],
        }

    items: List[Dict[str, Any]] = []
    counts = {
        "total": 0,
        "submitting": 0,
        "processing": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for batch_id in sorted(batches.keys(), key=_batch_sort_key):
        batch = dict(batches[batch_id])
        batch.pop("_attempts_seen", None)
        batch["failed_attempts"] = sorted(batch["failed_attempts"])
        batch["completed_attempts"] = sorted(batch["completed_attempts"])
        counts["total"] += 1
        counts[batch["latest_status"]] = counts.get(batch["latest_status"], 0) + 1
        items.append(batch)

    return {
        "has_batches": True,
        "overall_status": _summarize_batch_overall_status(
            [item["latest_status"] for item in items],
            fallback_status=fallback_status,
        ),
        "counts": counts,
        "items": items,
    }


def log_debug_task_event(
    *,
    stage: str,
    task_folder: str,
    input_data: Any = None,
    output_data: Any = None,
    raw_response: Any = None,
    episode_id: Optional[int] = None,
    shot_id: Optional[int] = None,
    batch_id: Optional[str] = None,
    task_type: str = "",
    status: Optional[str] = None,
    file_name: str = "",
    source_type: str = "debug",
    source_record_type: str = "",
    source_record_id: Optional[int] = None,
) -> None:
    canonical_key, normalized_source_record_type, normalized_source_record_id = _canonicalize_task_key(
        task_folder, source_record_type, source_record_id
    )
    payloads = [input_data, output_data, raw_response]
    resolved_task_type = _derive_task_type(stage, task_type, task_folder, payloads)
    record_task_key = canonical_key
    if canonical_key and resolved_task_type in _DETAIL_STORYBOARD_TASK_TYPES:
        record_task_key = f"{canonical_key}::{resolved_task_type}"
    resolved_status = _normalize_status(status or _infer_debug_status(output_data, raw_response))
    db = SessionLocal()
    try:
        record = db.query(models.DashboardTaskLog).filter(
            models.DashboardTaskLog.task_key == record_task_key
        ).first()
        if not record:
            record = models.DashboardTaskLog(
                task_key=record_task_key,
                task_folder=str(task_folder or ""),
                source_type=source_type or "debug",
                source_record_type=normalized_source_record_type or "",
                source_record_id=normalized_source_record_id,
                created_at=datetime.utcnow(),
            )
            db.add(record)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                record = db.query(models.DashboardTaskLog).filter(
                    models.DashboardTaskLog.task_key == record_task_key
                ).first()
                if not record:
                    return

        record.task_folder = str(task_folder or record.task_folder or "")
        record.source_type = str(source_type or record.source_type or "debug")
        if normalized_source_record_type:
            record.source_record_type = normalized_source_record_type
        if normalized_source_record_id:
            record.source_record_id = normalized_source_record_id
        record.task_type = resolved_task_type
        record.stage = str(stage or record.stage or "")
        record.status = resolved_status
        if batch_id:
            record.batch_id = str(batch_id)
        if file_name:
            record.latest_filename = str(file_name)

        _resolve_context(
            db,
            record,
            stage=stage,
            task_type=resolved_task_type,
            episode_id=episode_id,
            shot_id=shot_id,
            source_record_type=record.source_record_type,
            source_record_id=record.source_record_id,
            payloads=payloads,
        )

        api_url = _recursive_find(input_data, _API_URL_KEYS) or _recursive_find(output_data, _API_URL_KEYS) or _recursive_find(raw_response, _API_URL_KEYS)
        if api_url:
            record.api_url = api_url
        status_api_url = _recursive_find(input_data, _STATUS_API_URL_KEYS) or _recursive_find(output_data, _STATUS_API_URL_KEYS) or _recursive_find(raw_response, _STATUS_API_URL_KEYS)
        if status_api_url:
            record.status_api_url = status_api_url
        provider = _recursive_find(input_data, _PROVIDER_KEYS) or _recursive_find(output_data, _PROVIDER_KEYS) or _recursive_find(raw_response, _PROVIDER_KEYS)
        if provider:
            record.provider = provider
        model_name = _recursive_find(input_data, _MODEL_KEYS) or _recursive_find(output_data, _MODEL_KEYS) or _recursive_find(raw_response, _MODEL_KEYS)
        if model_name:
            record.model_name = model_name
        external_task_id = _recursive_find(output_data, _EXTERNAL_TASK_ID_KEYS) or _recursive_find(raw_response, _EXTERNAL_TASK_ID_KEYS) or _recursive_find(input_data, _EXTERNAL_TASK_ID_KEYS)
        if external_task_id:
            record.external_task_id = external_task_id

        if input_data is not None:
            record.input_payload = _safe_json_dumps(input_data, "{}")
        if output_data is not None:
            record.output_payload = _safe_json_dumps(output_data, "{}")
            record.result_payload = _safe_json_dumps(output_data, "{}")
        if raw_response is not None:
            record.raw_response_payload = _safe_json_dumps(raw_response, "{}")

        record.error_message = _extract_error_message(raw_response) if resolved_status == "completed" else _extract_error_message(raw_response, output_data)
        record.result_summary = _extract_result_summary(
            resolved_task_type,
            resolved_status,
            output_data,
            raw_response,
            output_data if output_data is not None else raw_response,
        )
        record.title = _derive_title(resolved_task_type, record)
        latest_payload = {
            "stage": stage,
            "status": resolved_status,
            "file_name": file_name,
            "input": input_data,
            "output": output_data,
            "raw_response": raw_response,
        }
        record.latest_event_payload = _safe_json_dumps(latest_payload, "{}")
        record.updated_at = datetime.utcnow()
        _append_event(record, {
            "timestamp": datetime.utcnow().isoformat(),
            "stage": stage,
            "status": resolved_status,
            "file_name": file_name,
            "input": input_data,
            "output": output_data,
            "raw_response": raw_response,
        })
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[dashboard] log_debug_task_event failed: {str(exc)}")
    finally:
        db.close()


def log_file_task_event(
    *,
    task_folder: str,
    file_name: str,
    payload: Any,
    task_type: str,
    stage: str = "",
    status: Optional[str] = None,
    episode_id: Optional[int] = None,
    shot_id: Optional[int] = None,
    source_record_type: str = "",
    source_record_id: Optional[int] = None,
) -> None:
    resolved_status = _normalize_status(status or _infer_status_from_filename(file_name))
    output_payload = payload if resolved_status == "completed" else None
    raw_payload = payload if resolved_status == "failed" else None
    input_payload = payload if resolved_status == "submitting" else None
    processing_payload = payload if resolved_status == "processing" else None

    log_debug_task_event(
        stage=stage or task_type,
        task_folder=task_folder,
        input_data=input_payload if input_payload is not None else processing_payload,
        output_data=output_payload,
        raw_response=raw_payload,
        episode_id=episode_id,
        shot_id=shot_id,
        task_type=task_type,
        status=resolved_status,
        file_name=file_name,
        source_type="debug",
        source_record_type=source_record_type,
        source_record_id=source_record_id,
    )


def sync_external_task_status_to_dashboard(
    *,
    external_task_id: str,
    status: str,
    output_data: Any = None,
    raw_response: Any = None,
    stage: str = "",
    api_url: str = "",
    status_api_url: str = "",
) -> int:
    normalized_external_task_id = str(external_task_id or "").strip()
    if not normalized_external_task_id:
        return 0

    resolved_status = _normalize_status(status)
    db = SessionLocal()
    try:
        records = db.query(models.DashboardTaskLog).filter(
            models.DashboardTaskLog.external_task_id == normalized_external_task_id
        ).all()
        if not records:
            return 0

        for record in records:
            record.status = resolved_status
            if stage:
                record.stage = str(stage).strip()
            if api_url:
                record.api_url = str(api_url).strip()
            if status_api_url:
                record.status_api_url = str(status_api_url).strip()

            if output_data is not None:
                dumped_output = _safe_json_dumps(output_data, "{}")
                record.output_payload = dumped_output
                if resolved_status == "completed":
                    record.result_payload = dumped_output

            if raw_response is not None:
                record.raw_response_payload = _safe_json_dumps(raw_response, "{}")

            if resolved_status == "completed":
                record.error_message = ""
            else:
                record.error_message = _extract_error_message(raw_response, output_data)
                if resolved_status == "failed":
                    record.result_payload = ""

            latest_payload = output_data if output_data is not None else raw_response
            if resolved_status == "completed" and latest_payload not in (None, ""):
                record.result_summary = _safe_json_dumps(latest_payload, "")
            else:
                record.result_summary = _extract_result_summary(
                    record.task_type,
                    resolved_status,
                    output_data,
                    raw_response,
                    latest_payload,
                )
            record.title = _derive_title(record.task_type, record)
            record.updated_at = datetime.utcnow()
            record.latest_event_payload = _safe_json_dumps(
                {
                    "stage": record.stage,
                    "status": resolved_status,
                    "output": output_data,
                    "raw_response": raw_response,
                },
                "{}",
            )
            _append_event(record, {
                "timestamp": datetime.utcnow().isoformat(),
                "stage": record.stage,
                "status": resolved_status,
                "output": output_data,
                "raw_response": raw_response,
            })

        db.commit()
        return len(records)
    except Exception as exc:
        db.rollback()
        print(f"[dashboard] sync_external_task_status_to_dashboard failed: {str(exc)}")
        return 0
    finally:
        db.close()


def sync_managed_task_to_dashboard(task_id: int) -> None:
    db = SessionLocal()
    try:
        task = db.query(models.ManagedTask).filter(models.ManagedTask.id == int(task_id)).first()
        if not task:
            return

        canonical_key, source_record_type, source_record_id = _canonicalize_task_key(
            "", "managed_task", task.id
        )
        record = db.query(models.DashboardTaskLog).filter(
            models.DashboardTaskLog.task_key == canonical_key
        ).first()
        if not record:
            record = models.DashboardTaskLog(
                task_key=canonical_key,
                task_folder=canonical_key,
                source_type="database_task",
                source_record_type=source_record_type,
                source_record_id=source_record_id,
                created_at=task.created_at or datetime.utcnow(),
            )
            db.add(record)
            db.flush()

        record.source_type = "database_task"
        record.source_record_type = "managed_task"
        record.source_record_id = task.id
        record.task_type = "managed_video"
        record.stage = "managed_video"
        record.status = _normalize_status(task.status)
        record.external_task_id = str(getattr(task, "task_id", "") or "")
        record.error_message = str(getattr(task, "error_message", "") or "")
        record.input_payload = _safe_json_dumps(
            {"prompt_text": getattr(task, "prompt_text", "") or ""},
            "{}",
        )
        record.result_payload = _safe_json_dumps(
            {"video_path": getattr(task, "video_path", "") or ""},
            "{}",
        )
        record.result_summary = _flatten_text(getattr(task, "video_path", "") or record.error_message, 200)
        record.updated_at = datetime.utcnow()
        _resolve_context(
            db,
            record,
            stage="managed_video",
            task_type="managed_video",
            episode_id=None,
            shot_id=getattr(task, "shot_id", None),
            source_record_type="managed_task",
            source_record_id=task.id,
            payloads=[],
        )
        record.title = _derive_title("managed_video", record)
        _append_event(record, {
            "timestamp": datetime.utcnow().isoformat(),
            "stage": "managed_video",
            "status": record.status,
            "managed_task_id": task.id,
            "task_id": getattr(task, "task_id", "") or "",
            "error_message": record.error_message,
            "video_path": getattr(task, "video_path", "") or "",
        })
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[dashboard] sync_managed_task_to_dashboard failed: {str(exc)}")
    finally:
        db.close()


def sync_voiceover_tts_task_to_dashboard(task_id: int) -> None:
    db = SessionLocal()
    try:
        task = db.query(models.VoiceoverTtsTask).filter(models.VoiceoverTtsTask.id == int(task_id)).first()
        if not task:
            return

        canonical_key, source_record_type, source_record_id = _canonicalize_task_key(
            "", "voiceover_tts_task", task.id
        )
        record = db.query(models.DashboardTaskLog).filter(
            models.DashboardTaskLog.task_key == canonical_key
        ).first()
        if not record:
            record = models.DashboardTaskLog(
                task_key=canonical_key,
                task_folder=canonical_key,
                source_type="database_task",
                source_record_type=source_record_type,
                source_record_id=source_record_id,
                created_at=task.created_at or datetime.utcnow(),
            )
            db.add(record)
            db.flush()

        record.source_type = "database_task"
        record.source_record_type = "voiceover_tts_task"
        record.source_record_id = task.id
        record.task_type = "voiceover_tts"
        record.stage = "voiceover_tts"
        record.status = _normalize_status(task.status)
        record.external_task_id = str(task.id)
        record.input_payload = str(getattr(task, "request_json", "") or "")
        record.output_payload = str(getattr(task, "result_json", "") or "")
        record.result_payload = str(getattr(task, "result_json", "") or "")
        record.error_message = str(getattr(task, "error_message", "") or "")
        record.result_summary = _flatten_text(record.error_message or record.result_payload, 200)
        record.updated_at = datetime.utcnow()
        _resolve_context(
            db,
            record,
            stage="voiceover_tts",
            task_type="voiceover_tts",
            episode_id=getattr(task, "episode_id", None),
            shot_id=None,
            source_record_type="voiceover_tts_task",
            source_record_id=task.id,
            payloads=[_safe_json_loads(task.request_json, {}), _safe_json_loads(task.result_json, {})],
        )
        record.title = _derive_title("voiceover_tts", record)
        _append_event(record, {
            "timestamp": datetime.utcnow().isoformat(),
            "stage": "voiceover_tts",
            "status": record.status,
            "voiceover_tts_task_id": task.id,
            "line_id": getattr(task, "line_id", "") or "",
            "error_message": record.error_message,
        })
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[dashboard] sync_voiceover_tts_task_to_dashboard failed: {str(exc)}")
    finally:
        db.close()


def _extract_text_relay_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    result = payload.get("result")
    if isinstance(result, dict):
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            first_choice = choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
    return ""


def _extract_text_relay_model_name(task: Any, request_payload: Any, result_payload: Any) -> str:
    if isinstance(result_payload, dict):
        nested_result = result_payload.get("result")
        if isinstance(nested_result, dict):
            nested_model = str(nested_result.get("model") or "").strip()
            if nested_model:
                return nested_model
        upstream_model = str(result_payload.get("model") or "").strip()
        if upstream_model:
            return upstream_model
    if isinstance(request_payload, dict):
        requested_model = str(request_payload.get("model") or "").strip()
        if requested_model:
            return requested_model
    return str(getattr(task, "model_id", "") or "").strip()


def sync_text_relay_task_to_dashboard(task_id: int, db=None) -> bool:
    owns_session = db is None
    db = db or SessionLocal()
    try:
        task = db.query(models.TextRelayTask).filter(models.TextRelayTask.id == int(task_id)).first()
        if not task:
            return False

        canonical_key, source_record_type, source_record_id = _canonicalize_task_key(
            "",
            "text_relay_task",
            int(task.id),
        )
        record = db.query(models.DashboardTaskLog).filter(
            models.DashboardTaskLog.task_key == canonical_key
        ).first()
        if not record:
            record = models.DashboardTaskLog(
                task_key=canonical_key,
                task_folder=canonical_key,
                source_type="database_task",
                source_record_type=source_record_type,
                source_record_id=source_record_id,
                created_at=getattr(task, "created_at", None) or datetime.utcnow(),
            )
            db.add(record)
            db.flush()

        task_payload = _safe_json_loads(getattr(task, "task_payload", ""), {})
        request_payload = _safe_json_loads(getattr(task, "request_payload", ""), {})
        result_payload = _safe_json_loads(getattr(task, "result_payload", ""), {})
        owner_type = str(getattr(task, "owner_type", "") or "").strip()
        dashboard_task_type = str(getattr(task, "task_type", "") or getattr(task, "stage_key", "") or "").strip()
        if dashboard_task_type == "simple_storyboard_batch":
            dashboard_task_type = "simple_storyboard"

        record.source_type = "database_task"
        record.source_record_type = "text_relay_task"
        record.source_record_id = int(task.id)
        record.task_folder = canonical_key
        record.task_type = dashboard_task_type
        record.stage = str(getattr(task, "stage_key", "") or dashboard_task_type or "")
        record.status = _normalize_status(getattr(task, "status", ""))
        record.batch_id = str(task_payload.get("batch_index") or "")
        record.provider = "relay"
        record.model_name = _extract_text_relay_model_name(task, request_payload, result_payload)
        record.api_url = RELAY_CHAT_COMPLETIONS_URL
        record.status_api_url = str(getattr(task, "poll_url", "") or "")
        record.external_task_id = str(getattr(task, "external_task_id", "") or "")
        record.input_payload = _safe_json_dumps(request_payload, "{}")
        record.output_payload = _safe_json_dumps(result_payload, "{}")
        record.result_payload = _safe_json_dumps(result_payload, "{}")
        record.error_message = str(getattr(task, "error_message", "") or _extract_error_message(result_payload)).strip()

        episode_id = None
        shot_id = None
        if owner_type == "episode":
            episode_id = int(task_payload.get("episode_id") or getattr(task, "owner_id", 0) or 0) or None
        elif owner_type == "simple_storyboard_batch":
            episode_id = int(task_payload.get("episode_id") or 0) or None
            if episode_id is None:
                batch_row = db.query(models.SimpleStoryboardBatch).filter(
                    models.SimpleStoryboardBatch.id == int(getattr(task, "owner_id", 0) or 0)
                ).first()
                if batch_row:
                    episode_id = int(getattr(batch_row, "episode_id", 0) or 0) or None
        elif owner_type == "card":
            shot_id = int(getattr(task, "owner_id", 0) or 0) or None
        elif owner_type == "shot":
            shot_id = int(getattr(task, "owner_id", 0) or 0) or None

        if owner_type == "storyboard2_shot":
            storyboard2_shot_id = int(task_payload.get("storyboard2_shot_id") or getattr(task, "owner_id", 0) or 0)
            storyboard2_shot = db.query(models.Storyboard2Shot).filter(
                models.Storyboard2Shot.id == storyboard2_shot_id
            ).first()
            if storyboard2_shot:
                episode = db.query(models.Episode).filter(models.Episode.id == storyboard2_shot.episode_id).first()
                script = db.query(models.Script).filter(models.Script.id == getattr(episode, "script_id", None)).first() if episode else None
                user = db.query(models.User).filter(models.User.id == getattr(script, "user_id", None)).first() if script else None
                _apply_episode_context(record, episode, script, user)
                record.shot_number = getattr(storyboard2_shot, "shot_number", None)
        else:
            _resolve_context(
                db,
                record,
                stage=record.stage,
                task_type=dashboard_task_type,
                episode_id=episode_id,
                shot_id=shot_id,
                source_record_type="text_relay_task",
                source_record_id=int(task.id),
                payloads=[task_payload, request_payload, result_payload],
            )

        summary_text = _extract_text_relay_content(result_payload)
        if summary_text:
            record.result_summary = _flatten_text(summary_text, 200)
        else:
            record.result_summary = _extract_result_summary(
                dashboard_task_type,
                record.status,
                result_payload,
                result_payload,
                result_payload,
            )

        record.title = _derive_title(dashboard_task_type, record)
        latest_event = {
            "timestamp": datetime.utcnow().isoformat(),
            "stage": record.stage,
            "status": record.status,
            "text_relay_task_id": int(task.id),
            "task_id": record.external_task_id,
            "batch_id": record.batch_id,
            "error_message": record.error_message,
        }
        last_payload = _safe_json_loads(getattr(record, "latest_event_payload", ""), {})
        record.latest_event_payload = _safe_json_dumps(latest_event, "{}")
        if not isinstance(last_payload, dict) or any(last_payload.get(key) != latest_event.get(key) for key in latest_event.keys()):
            _append_event(record, latest_event)
        record.updated_at = datetime.utcnow()

        if owns_session:
            db.commit()
        return True
    except Exception as exc:
        if owns_session:
            db.rollback()
        print(f"[dashboard] sync_text_relay_task_to_dashboard failed: {str(exc)}")
        return False
    finally:
        if owns_session:
            db.close()
