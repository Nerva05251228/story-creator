import asyncio
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
import traceback
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from threading import Lock, Thread
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
import requests
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import billing_service
import image_platform_client
import models
from ai_config import get_ai_config
from ai_service import get_prompt_by_key
from auth import get_current_user
from database import SessionLocal, get_db
from managed_generation_service import ACTIVE_MANAGED_SESSION_STATUSES
from simple_storyboard_rules import (
    generate_simple_storyboard_shots,
    get_default_rule_config,
    normalize_rule_config,
)
from storyboard_prompt_templates import inject_large_shot_template_content
from storyboard_variant import build_storyboard_sync_variant_payload
from storyboard_video_reference import (
    build_seedance_content_text,
    build_seedance_prompt,
    build_seedance_reference_images,
    collect_first_frame_candidate_urls,
    is_allowed_first_frame_candidate_url,
    normalize_first_frame_candidate_url,
    resolve_scene_reference_image_url,
    should_autofill_scene_override,
)
from text_relay_service import submit_and_persist_text_task
from utils import upload_to_cdn
from video_api_config import get_video_api_headers, get_video_task_status_url
from video_service import (
    check_video_status,
    is_transient_video_status_error,
    process_and_upload_video_with_cover,
)
from image_generation_service import normalize_image_model_key
from api.schemas.episodes import (
    DEFAULT_STORYBOARD_VIDEO_MODEL,
    AnalyzeStoryboardRequest,
    BatchGenerateSoraPromptsRequest,
    BatchGenerateSoraVideosRequest,
    CreateStoryboardRequest,
    EpisodeCreate,
    EpisodeResponse,
    ManagedSessionStatusResponse,
    SimpleStoryboardRequest,
    StartManagedGenerationRequest,
    Storyboard2BatchGenerateSoraPromptsRequest,
    StoryboardAnalyzeResponse,
)


router = APIRouter()


storyboard2_active_image_tasks = set()

storyboard2_active_image_tasks_lock = Lock()

STORYBOARD2_VIDEO_PROMPT_KEY = "generate_storyboard2_video_prompts"

ALLOWED_CARD_TYPES = ("角色", "场景", "道具")

SOUND_CARD_TYPE = "声音"

SQLITE_LOCK_RETRY_DELAYS = (0.3, 0.8, 1.5, 3.0)

_SUBJECT_MATCH_STOP_FRAGMENTS = {
    "侯府",
    "王府",
    "府中",
    "府内",
    "宫中",
    "宫内",
    "古代",
    "现代",
    "室内",
    "室外",
}

VOICEOVER_TTS_METHOD_SAME = "与音色参考音频相同"

VOICEOVER_TTS_METHOD_VECTOR = "使用情感向量控制"

VOICEOVER_TTS_METHOD_EMO_TEXT = "使用情感描述文本控制"

VOICEOVER_TTS_METHOD_AUDIO = "使用情感参考音频"

VOICEOVER_TTS_ALLOWED_METHODS = {
    VOICEOVER_TTS_METHOD_SAME,
    VOICEOVER_TTS_METHOD_VECTOR,
    VOICEOVER_TTS_METHOD_EMO_TEXT,
    VOICEOVER_TTS_METHOD_AUDIO
}

VOICEOVER_TTS_VECTOR_KEYS = [
    "joy", "anger", "sadness", "fear",
    "disgust", "depression", "surprise", "neutral"
]

SIMPLE_STORYBOARD_TIMEOUT_SECONDS = 3600

SIMPLE_STORYBOARD_TIMEOUT_ERROR = "简单分镜生成超时（超过 1 小时），已自动标记为失败，请重新生成。"

SORA_REFERENCE_PROMPT_INSTRUCTION = "请你参考这段提示词中的人物站位进行编写新的提示词："

ACTIVE_VIDEO_GENERATION_STATUSES = ("submitting", "preparing", "processing")

ACTIVE_MANAGED_TASK_STATUSES = ("pending", "processing")

MAX_ACTIVE_VIDEO_GENERATIONS_PER_SHOT = 1

_STORYBOARD_VIDEO_MODEL_CONFIG = {
    "sora-2": {
        "aspect_ratios": ("16:9", "9:16"),
        "durations": (10, 15, 25),
        "default_ratio": "16:9",
        "default_duration": 15,
        "resolution_names": (),
        "default_resolution": "",
        "provider": "yijia"
    },
    "grok": {
        "aspect_ratios": ("21:9", "16:9", "3:2", "4:3", "1:1", "3:4", "2:3", "9:16"),
        "durations": (10, 20, 30),
        "default_ratio": "9:16",
        "default_duration": 10,
        "resolution_names": ("480p", "720p"),
        "default_resolution": "720p",
        "provider": "yijia"
    },
    "Seedance 2.0 Fast VIP": {
        "aspect_ratios": ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"),
        "durations": tuple(range(4, 16)),
        "default_ratio": "16:9",
        "default_duration": 10,
        "resolution_names": (),
        "default_resolution": "",
        "provider": "moti"
    },
    "Seedance 2.0 Fast": {
        "aspect_ratios": ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"),
        "durations": tuple(range(4, 16)),
        "default_ratio": "16:9",
        "default_duration": 10,
        "resolution_names": (),
        "default_resolution": "",
        "provider": "moti"
    },
    "Seedance 2.0 VIP": {
        "aspect_ratios": ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"),
        "durations": tuple(range(4, 16)),
        "default_ratio": "16:9",
        "default_duration": 10,
        "resolution_names": (),
        "default_resolution": "",
        "provider": "moti"
    },
    "Seedance 2.0": {
        "aspect_ratios": ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"),
        "durations": tuple(range(4, 16)),
        "default_ratio": "16:9",
        "default_duration": 10,
        "resolution_names": (),
        "default_resolution": "",
        "provider": "moti"
    }
}

def _rollback_quietly(db: Session):
    try:
        db.rollback()
    except Exception:
        pass

def _is_sqlite_lock_error(db: Session, exc: Exception) -> bool:
    dialect = getattr(getattr(db, "bind", None), "dialect", None)
    dialect_name = getattr(dialect, "name", "")
    return dialect_name == "sqlite" and "database is locked" in str(exc).lower()

def commit_with_retry(
    db: Session,
    prepare_fn=None,
    context: str = "db commit"
):
    max_retries = len(SQLITE_LOCK_RETRY_DELAYS)

    for attempt in range(max_retries + 1):
        if prepare_fn:
            prepare_fn()
        try:
            db.commit()
            return
        except OperationalError as e:
            _rollback_quietly(db)
            if not _is_sqlite_lock_error(db, e) or attempt >= max_retries:
                raise
            delay = SQLITE_LOCK_RETRY_DELAYS[attempt]
            print(f"[db] {context} 遇到 SQLite 写锁，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries})")
            time.sleep(delay)
        except Exception:
            _rollback_quietly(db)
            raise

def _parse_simple_storyboard_batch_shots(raw_value: Optional[str]) -> List[Dict[str, Any]]:
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return []
    if isinstance(parsed, dict):
        parsed = parsed.get("shots")
    return parsed if isinstance(parsed, list) else []

def _build_simple_storyboard_from_batches(batch_rows: List[models.SimpleStoryboardBatch]) -> Dict[str, Any]:
    ordered_rows = sorted(batch_rows, key=lambda row: int(getattr(row, "batch_index", 0) or 0))
    all_shots: List[Dict[str, Any]] = []
    shot_number = 1
    for row in ordered_rows:
        if str(getattr(row, "status", "") or "").strip() != "completed":
            continue
        for shot in _parse_simple_storyboard_batch_shots(getattr(row, "shots_data", "")):
            if not isinstance(shot, dict):
                continue
            normalized_shot = dict(shot)
            normalized_shot["shot_number"] = shot_number
            shot_number += 1
            all_shots.append(normalized_shot)
    return {"shots": all_shots}

def _serialize_simple_storyboard_batch(row: models.SimpleStoryboardBatch) -> Dict[str, Any]:
    shots = _parse_simple_storyboard_batch_shots(getattr(row, "shots_data", ""))
    retry_count = int(getattr(row, "retry_count", 0) or 0)
    status = str(getattr(row, "status", "") or "").strip() or "pending"
    return {
        "id": int(getattr(row, "id", 0) or 0),
        "batch_index": int(getattr(row, "batch_index", 0) or 0),
        "total_batches": int(getattr(row, "total_batches", 0) or 0),
        "status": status,
        "source_text": str(getattr(row, "source_text", "") or ""),
        "error_message": str(getattr(row, "error_message", "") or ""),
        "last_attempt": int(getattr(row, "last_attempt", 0) or 0),
        "retry_count": retry_count,
        "can_retry": status == "failed" and retry_count < 1,
        "shots_count": len(shots),
        "created_at": getattr(row, "created_at", None).isoformat() if getattr(row, "created_at", None) else None,
        "updated_at": getattr(row, "updated_at", None).isoformat() if getattr(row, "updated_at", None) else None,
    }

def _get_simple_storyboard_batch_rows(episode_id: int, db: Session) -> List[models.SimpleStoryboardBatch]:
    return db.query(models.SimpleStoryboardBatch).filter(
        models.SimpleStoryboardBatch.episode_id == episode_id
    ).order_by(models.SimpleStoryboardBatch.batch_index.asc(), models.SimpleStoryboardBatch.id.asc()).all()

def _get_simple_storyboard_batch_summary(episode_id: int, db: Session) -> Dict[str, Any]:
    db.flush()
    rows = _get_simple_storyboard_batch_rows(episode_id, db)
    completed_count = 0
    failed_count = 0
    submitting_count = 0
    total_batches = 0
    errors: List[Dict[str, Any]] = []
    for row in rows:
        total_batches = max(total_batches, int(getattr(row, "total_batches", 0) or 0), int(getattr(row, "batch_index", 0) or 0))
        status = str(getattr(row, "status", "") or "").strip()
        if status == "completed":
            completed_count += 1
        elif status == "failed":
            failed_count += 1
            error_message = str(getattr(row, "error_message", "") or "").strip()
            if error_message:
                errors.append({
                    "batch_index": int(getattr(row, "batch_index", 0) or 0),
                    "message": error_message,
                    "last_attempt": int(getattr(row, "last_attempt", 0) or 0),
                    "retry_count": int(getattr(row, "retry_count", 0) or 0),
                })
        elif status in {"submitting", "pending"}:
            submitting_count += 1
    aggregate = _build_simple_storyboard_from_batches(rows)
    return {
        "total_batches": total_batches or len(rows),
        "completed_batches": completed_count,
        "failed_batches": failed_count,
        "submitting_batches": submitting_count,
        "has_failures": failed_count > 0,
        "batches": [_serialize_simple_storyboard_batch(row) for row in rows],
        "failed_batch_errors": errors,
        "shots": aggregate.get("shots", []),
    }

def _refresh_episode_simple_storyboard_from_batches(episode: models.Episode, db: Session) -> Dict[str, Any]:
    summary = _get_simple_storyboard_batch_summary(int(episode.id), db)
    aggregate_data = {"shots": summary["shots"]}
    episode.simple_storyboard_data = json.dumps(aggregate_data, ensure_ascii=False)
    still_running = summary["submitting_batches"] > 0 or (
        summary["total_batches"] > 0 and summary["completed_batches"] + summary["failed_batches"] < summary["total_batches"]
    )
    if summary["has_failures"]:
        combined_error = "；".join(
            [f"Batch {item['batch_index']}: {item['message']}" for item in summary["failed_batch_errors"]]
        )
        episode.simple_storyboard_error = combined_error
        episode.simple_storyboard_generating = still_running
    else:
        episode.simple_storyboard_error = ""
        episode.simple_storyboard_generating = still_running
    return summary

def _group_simple_storyboard_shots_into_batches(
    shots: List[Dict[str, Any]],
    batch_size: int,
) -> List[Dict[str, Any]]:
    if not shots:
        return []

    normalized_batch_size = max(1, int(batch_size or 1))
    grouped: List[Dict[str, Any]] = []
    current_shots: List[Dict[str, Any]] = []
    current_length = 0

    for shot in shots:
        shot_text = str((shot or {}).get("original_text") or "")
        shot_length = len(shot_text)
        if current_shots and current_length + shot_length > normalized_batch_size:
            grouped.append({
                "source_text": "".join(str(item.get("original_text") or "") for item in current_shots),
                "shots": current_shots,
            })
            current_shots = [dict(shot)]
            current_length = shot_length
            continue
        current_shots.append(dict(shot))
        current_length += shot_length

    if current_shots:
        grouped.append({
            "source_text": "".join(str(item.get("original_text") or "") for item in current_shots),
            "shots": current_shots,
        })
    return grouped

def _persist_programmatic_simple_storyboard_batches(
    episode_id: int,
    shots: List[Dict[str, Any]],
    batch_size: int,
    db: Session,
) -> List[models.SimpleStoryboardBatch]:
    grouped_batches = _group_simple_storyboard_shots_into_batches(shots, batch_size)
    db.query(models.SimpleStoryboardBatch).filter(models.SimpleStoryboardBatch.episode_id == episode_id).delete()
    now = datetime.utcnow()
    total_batches = len(grouped_batches)
    rows: List[models.SimpleStoryboardBatch] = []
    for index, batch_payload in enumerate(grouped_batches, start=1):
        row = models.SimpleStoryboardBatch(
            episode_id=episode_id,
            batch_index=index,
            total_batches=total_batches,
            status="completed",
            source_text=str(batch_payload.get("source_text") or ""),
            shots_data=json.dumps(batch_payload.get("shots") or [], ensure_ascii=False),
            error_message="",
            last_attempt=1,
            retry_count=0,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows

def _normalize_subject_detail_entry(subject: dict, fallback: Optional[dict] = None) -> Optional[dict]:
    if not isinstance(subject, dict):
        return None

    fallback = fallback or {}
    name = (subject.get("name") or fallback.get("name") or "").strip()
    subject_type = (subject.get("type") or fallback.get("type") or "角色").strip() or "角色"
    if not name or subject_type not in ALLOWED_CARD_TYPES:
        return None

    alias = subject.get("alias")
    if alias is None:
        alias = fallback.get("alias")

    ai_prompt = subject.get("ai_prompt")
    if ai_prompt is None:
        ai_prompt = fallback.get("ai_prompt")

    role_personality = subject.get("role_personality")
    if role_personality is None:
        role_personality = subject.get("role_personality_en")
    if role_personality is None:
        role_personality = subject.get("personality_en")
    if role_personality is None:
        role_personality = fallback.get("role_personality")
    if role_personality is None:
        role_personality = fallback.get("role_personality_en")
    if role_personality is None:
        role_personality = fallback.get("personality_en")

    return {
        "name": name,
        "type": subject_type,
        "alias": (alias or "").strip(),
        "ai_prompt": (ai_prompt or "").strip(),
        "role_personality": (role_personality or "").strip() if subject_type == "角色" else ""
    }

def _build_subject_detail_map(subjects: Optional[list]) -> dict:
    subject_map = {}
    if not isinstance(subjects, list):
        return subject_map

    for subject in subjects:
        normalized = _normalize_subject_detail_entry(subject)
        if not normalized:
            continue
        subject_map[(normalized["name"], normalized["type"])] = normalized
    return subject_map

def _normalize_storyboard_generation_subjects(subjects: Optional[list]) -> list:
    normalized_subjects = []
    if not isinstance(subjects, list):
        return normalized_subjects

    for subject in subjects:
        if not isinstance(subject, dict):
            continue

        name = (subject.get("name") or "").strip()
        if not name:
            continue

        subject_type = (subject.get("type") or "角色").strip() or "角色"
        if subject_type not in ALLOWED_CARD_TYPES:
            continue

        normalized_subjects.append({
            "name": name,
            "type": subject_type,
        })

    deduped_subjects = []
    seen_subjects = set()
    for subject in normalized_subjects:
        subject_key = (subject["name"], subject["type"])
        if subject_key in seen_subjects:
            continue
        seen_subjects.add(subject_key)
        deduped_subjects.append(subject)

    return deduped_subjects

def _find_meaningful_common_fragment(
    left_text: str,
    right_text: str,
    stop_fragments: Optional[set] = None,
) -> str:
    left_value = (left_text or "").strip()
    right_value = (right_text or "").strip()
    if not left_value or not right_value:
        return ""

    ignored_fragments = stop_fragments or set()
    max_length = min(len(left_value), len(right_value))
    for fragment_length in range(max_length, 1, -1):
        seen_fragments = set()
        for start_index in range(len(left_value) - fragment_length + 1):
            fragment = left_value[start_index:start_index + fragment_length].strip()
            if not fragment or fragment in seen_fragments or fragment in ignored_fragments:
                continue
            seen_fragments.add(fragment)
            if fragment in right_value:
                return fragment
    return ""

def _infer_storyboard_role_name_from_shot(
    subject_name: str,
    shot_data: dict,
    canonical_subject_map: dict,
) -> Optional[str]:
    normalized_name = (subject_name or "").strip()
    if normalized_name not in {"我", "自己", "本人", "我自己"}:
        return None

    narration = shot_data.get("narration")
    if isinstance(narration, dict):
        speaker = (narration.get("speaker") or "").strip()
        if speaker and (speaker, "角色") in canonical_subject_map:
            return speaker

    dialogue = shot_data.get("dialogue")
    if isinstance(dialogue, list):
        speakers = []
        for item in dialogue:
            if not isinstance(item, dict):
                continue
            speaker = (item.get("speaker") or "").strip()
            if speaker and speaker not in speakers:
                speakers.append(speaker)
        if len(speakers) == 1 and (speakers[0], "角色") in canonical_subject_map:
            return speakers[0]

    return None

def _resolve_storyboard_subject_name(
    subject: dict,
    shot_data: dict,
    canonical_subject_map: dict,
    name_mappings: Optional[dict] = None,
) -> str:
    normalized_subject = _normalize_subject_detail_entry(subject)
    if not normalized_subject:
        return ""

    subject_name = normalized_subject["name"]
    subject_type = normalized_subject["type"]

    mapped_name = (name_mappings or {}).get(subject_name)
    if mapped_name and (mapped_name, subject_type) in canonical_subject_map:
        return mapped_name

    if (subject_name, subject_type) in canonical_subject_map:
        return subject_name

    if subject_type == "角色":
        inferred_role_name = _infer_storyboard_role_name_from_shot(
            subject_name,
            shot_data,
            canonical_subject_map,
        )
        if inferred_role_name:
            return inferred_role_name
        return subject_name

    if subject_type not in {"场景", "道具"}:
        return subject_name

    candidate_details = [
        detail
        for detail in canonical_subject_map.values()
        if detail.get("type") == subject_type
    ]
    if not candidate_details:
        return subject_name

    candidate_texts = [subject_name]
    original_text = (shot_data.get("original_text") or "").strip()
    if original_text:
        candidate_texts.append(original_text)

    best_match_name = subject_name
    best_match_score = 0
    second_best_score = 0

    for candidate in candidate_details:
        current_score = 0
        candidate_name = candidate.get("name", "")
        candidate_alias = candidate.get("alias", "")
        for source_text in candidate_texts:
            current_score = max(
                current_score,
                len(_find_meaningful_common_fragment(source_text, candidate_name, _SUBJECT_MATCH_STOP_FRAGMENTS)),
                len(_find_meaningful_common_fragment(source_text, candidate_alias, _SUBJECT_MATCH_STOP_FRAGMENTS)),
            )

        if current_score > best_match_score:
            second_best_score = best_match_score
            best_match_score = current_score
            best_match_name = candidate_name
        elif current_score > second_best_score:
            second_best_score = current_score

    if best_match_score >= 2 and best_match_score > second_best_score:
        return best_match_name

    return subject_name

def _reconcile_storyboard_shot_subjects(
    shot_data: dict,
    canonical_subjects: Optional[Any],
    name_mappings: Optional[dict] = None,
) -> list:
    if isinstance(canonical_subjects, dict):
        canonical_subject_map = canonical_subjects
    else:
        canonical_subject_map = _build_subject_detail_map(canonical_subjects)

    reconciled_subjects = []
    seen_subjects = set()
    for subject in _normalize_storyboard_generation_subjects(shot_data.get("subjects", [])):
        resolved_name = _resolve_storyboard_subject_name(
            subject,
            shot_data,
            canonical_subject_map,
            name_mappings=name_mappings,
        )
        if not resolved_name:
            continue
        subject_key = (resolved_name, subject["type"])
        if subject_key in seen_subjects:
            continue
        seen_subjects.add(subject_key)
        reconciled_subjects.append({
            "name": resolved_name,
            "type": subject["type"],
        })

    return reconciled_subjects

def save_and_upload_to_cdn(upload_file: UploadFile) -> str:
    """保存上传的文件，上传到CDN，并返回CDN URL"""
    local_path = None
    try:
        # 生成唯一文件名
        ext = os.path.splitext(upload_file.filename)[1]
        filename = f"{uuid.uuid4()}{ext}"
        local_path = os.path.join("uploads", filename)

        # 保存文件到本地
        with open(local_path, "wb") as buffer:
            shutil.copyfileobj(upload_file.file, buffer)


        # 上传到CDN
        cdn_url = upload_to_cdn(local_path)

        # 删除本地临时文件
        try:
            os.remove(local_path)
        except Exception as e:
            print(f"删除临时文件失败: {str(e)}")

        return cdn_url

    except Exception as e:
        print(f"图片上传CDN失败: {str(e)}")
        # 清理临时文件
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except:
                pass
        # 失败时抛出异常
        raise Exception(f"图片上传CDN失败: {str(e)}")

def _safe_audio_duration_seconds(value: Any) -> float:
    try:
        duration_seconds = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return duration_seconds if duration_seconds > 0 else 0.0

def _voiceover_shot_match_key(shot: dict, fallback_index: Optional[int] = None) -> str:
    """为voiceover单镜头生成稳定匹配键。"""
    if not isinstance(shot, dict):
        return f"index:{fallback_index}" if fallback_index is not None else ""

    shot_number = shot.get("shot_number")
    if shot_number is not None:
        normalized = str(shot_number).strip()
        if normalized:
            return f"shot_number:{normalized}"

    return f"index:{fallback_index}" if fallback_index is not None else ""

def _merge_voiceover_line_preserving_tts(
    existing_line: Any,
    incoming_line: Any,
    fallback_line_id: str = ""
) -> Any:
    """合并单条配音行，优先使用新字段，同时尽量保留旧的tts配置。"""
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

def _merge_voiceover_dialogue_preserving_tts(
    existing_dialogue: Any,
    incoming_dialogue: Any,
    shot_number: Any
) -> Any:
    """按 line_id（其次按位置）合并对白数组并保留旧tts。"""
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
        merged_item = _merge_voiceover_line_preserving_tts(existing_item, incoming_dict, fallback_line_id)
        merged_list.append(merged_item)

    return merged_list

def _merge_voiceover_shots_preserving_extensions(
    existing_voiceover_data: str,
    incoming_voiceover_shots: list
) -> dict:
    """
    合并voiceover镜头数据：
    - 基础字段（shot_number/voice_type/narration/dialogue）以新数据为准；
    - 其他扩展字段（如tts等）按镜头匹配后保留。
    """
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
        key = _voiceover_shot_match_key(item, idx)
        if key and key not in existing_shot_map:
            existing_shot_map[key] = item

    if not isinstance(incoming_voiceover_shots, list):
        incoming_voiceover_shots = []

    merged_shots = []
    for idx, incoming in enumerate(incoming_voiceover_shots):
        incoming_shot = incoming if isinstance(incoming, dict) else {}
        key = _voiceover_shot_match_key(incoming_shot, idx)
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
            merged_shot["narration"] = _merge_voiceover_line_preserving_tts(
                existing_narration,
                incoming_narration,
                f"shot_{shot_number_for_line}_narration"
            )
        else:
            merged_shot["narration"] = incoming_narration

        merged_shot["dialogue"] = _merge_voiceover_dialogue_preserving_tts(
            existing_shot.get("dialogue") if isinstance(existing_shot, dict) else None,
            incoming_shot.get("dialogue"),
            shot_number_for_line
        )
        merged_shots.append(merged_shot)

    merged_payload = dict(existing_payload) if isinstance(existing_payload, dict) else {}
    merged_payload["shots"] = merged_shots
    return merged_payload

def _voiceover_default_test_mp3_path() -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "TTS_example", "test.mp3")
    )

def _voiceover_default_vector_config() -> dict:
    return {
        "weight": 0.65,
        "joy": 0.0,
        "anger": 0.0,
        "sadness": 0.0,
        "fear": 0.0,
        "disgust": 0.0,
        "depression": 0.0,
        "surprise": 0.0,
        "neutral": 1.0
    }

def _voiceover_default_shared_data() -> dict:
    return {
        "initialized": False,
        "voice_references": [],
        "vector_presets": [],
        "emotion_audio_presets": [],
        "setting_templates": []
    }

def _voiceover_default_reference_item() -> dict:
    return {
        "id": "voice_ref_default_female_1",
        "name": "女声1",
        "file_name": "test.mp3",
        "url": "",
        "local_path": _voiceover_default_test_mp3_path(),
        "created_at": datetime.utcnow().isoformat()
    }

def _safe_float(value: Any, default_value: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default_value)
    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return parsed

def _normalize_voiceover_vector_config(raw_config: Any) -> dict:
    source = raw_config if isinstance(raw_config, dict) else {}
    normalized = {"weight": _safe_float(source.get("weight"), 0.65)}
    for key in VOICEOVER_TTS_VECTOR_KEYS:
        normalized[key] = _safe_float(source.get(key), 0.0)

    # 保底给一个中性值，避免全0
    if all(normalized.get(k, 0.0) == 0.0 for k in VOICEOVER_TTS_VECTOR_KEYS):
        normalized["neutral"] = 1.0

    return normalized

def _normalize_voiceover_setting_template_payload(
    raw_settings: Any,
    default_voice_reference_id: str = ""
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
        "vector_config": _normalize_voiceover_vector_config(source.get("vector_config"))
    }

def _normalize_voiceover_shared_data(raw_data: Any) -> dict:
    source = raw_data if isinstance(raw_data, dict) else {}
    normalized = _voiceover_default_shared_data()
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
                "created_at": str(item.get("created_at") or datetime.utcnow().isoformat())
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
                "vector_config": _normalize_voiceover_vector_config(item.get("vector_config")),
                "created_at": str(item.get("created_at") or datetime.utcnow().isoformat())
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
                "created_at": str(item.get("created_at") or datetime.utcnow().isoformat())
            })

    default_voice_ref_id = ""
    if normalized["voice_references"]:
        default_voice_ref_id = str(normalized["voice_references"][0].get("id") or "").strip()

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
                "settings": _normalize_voiceover_setting_template_payload(
                    item.get("settings"),
                    default_voice_ref_id
                ),
                "created_at": str(item.get("created_at") or datetime.utcnow().isoformat()),
                "updated_at": str(item.get("updated_at") or item.get("created_at") or datetime.utcnow().isoformat())
            })

    # 首次初始化：自动加入默认音色
    if not normalized["initialized"]:
        if not normalized["voice_references"]:
            default_item = _voiceover_default_reference_item()
            if os.path.exists(default_item["local_path"]):
                normalized["voice_references"].append(default_item)
        normalized["initialized"] = True

    return normalized

def _load_script_voiceover_shared_data(script: models.Script) -> dict:
    raw_payload = {}
    raw_text = (script.voiceover_shared_data or "").strip()
    if raw_text:
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                raw_payload = parsed
        except Exception:
            raw_payload = {}
    return _normalize_voiceover_shared_data(raw_payload)

def _save_script_voiceover_shared_data(script: models.Script, payload: dict):
    script.voiceover_shared_data = json.dumps(
        _normalize_voiceover_shared_data(payload),
        ensure_ascii=False
    )

def _voiceover_default_line_tts(default_voice_reference_id: str = "") -> dict:
    return {
        "emotion_control_method": VOICEOVER_TTS_METHOD_SAME,
        "voice_reference_id": default_voice_reference_id or "",
        "vector_preset_id": "",
        "emotion_audio_preset_id": "",
        "vector_config": _voiceover_default_vector_config(),
        "generated_audios": [],
        "generate_status": "idle",
        "generate_error": "",
        "latest_task_id": ""
    }

def _normalize_voiceover_line_tts(raw_tts: Any, default_voice_reference_id: str = "") -> dict:
    source = raw_tts if isinstance(raw_tts, dict) else {}
    normalized = _voiceover_default_line_tts(default_voice_reference_id)

    method = str(source.get("emotion_control_method") or "").strip()
    if method in VOICEOVER_TTS_ALLOWED_METHODS:
        normalized["emotion_control_method"] = method

    normalized["voice_reference_id"] = str(
        source.get("voice_reference_id") or normalized["voice_reference_id"]
    ).strip()
    normalized["vector_preset_id"] = str(source.get("vector_preset_id") or "").strip()
    normalized["emotion_audio_preset_id"] = str(source.get("emotion_audio_preset_id") or "").strip()
    normalized["vector_config"] = _normalize_voiceover_vector_config(source.get("vector_config"))
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
                "status": str(item.get("status") or "completed").strip().lower()
            })
        normalized["generated_audios"] = cleaned

    return normalized

def _ensure_voiceover_shot_line_fields(
    shot: dict,
    default_voice_reference_id: str = ""
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
        normalized_tts = _normalize_voiceover_line_tts(
            narration.get("tts"),
            default_voice_reference_id
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
            normalized_tts = _normalize_voiceover_line_tts(
                item.get("tts"),
                default_voice_reference_id
            )
            if item.get("tts") != normalized_tts:
                item["tts"] = normalized_tts
                changed = True

    return changed

def _normalize_voiceover_shots_for_tts(
    shots: Any,
    default_voice_reference_id: str = ""
) -> Tuple[list, bool]:
    changed = False
    normalized_shots = shots if isinstance(shots, list) else []
    for shot in normalized_shots:
        changed = _ensure_voiceover_shot_line_fields(shot, default_voice_reference_id) or changed
    return normalized_shots, changed

def _extract_voiceover_tts_line_states(shots: list) -> list:
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

def _find_voiceover_line_entry(shots: list, line_id: str) -> Optional[dict]:
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

def _parse_episode_voiceover_payload(episode: models.Episode) -> dict:
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

def _voiceover_first_reference_id(shared_data: dict) -> str:
    refs = shared_data.get("voice_references", []) if isinstance(shared_data, dict) else []
    if isinstance(refs, list) and refs:
        return str(refs[0].get("id") or "").strip()
    return ""

def _iter_voiceover_lines(shots: list):
    """遍历shots中的 narration/dialogue 行（原位可改）。"""
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

def _ensure_voiceover_permission(
    episode_id: int,
    user: models.User,
    db: Session
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
    fallback_ref_id: str
) -> int:
    """删除音色引用后，回填所有剧集里对应行的音色ID。"""
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
    removed_value: str
) -> int:
    """清理所有剧集中被删除的预设ID引用。"""
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

def _mark_simple_storyboard_timeout_if_needed(episode: Optional[models.Episode], db: Session) -> bool:
    if not episode or not bool(getattr(episode, "simple_storyboard_generating", False)):
        return False
    reference_time = getattr(episode, "updated_at", None) or getattr(episode, "created_at", None)
    if not reference_time:
        return False
    if (datetime.utcnow() - reference_time).total_seconds() < SIMPLE_STORYBOARD_TIMEOUT_SECONDS:
        return False
    batch_rows = _get_simple_storyboard_batch_rows(int(getattr(episode, "id", 0) or 0), db)
    if batch_rows:
        for row in batch_rows:
            if str(getattr(row, "status", "") or "").strip() in {"completed", "failed"}:
                continue
            row.status = "failed"
            if not str(getattr(row, "error_message", "") or "").strip():
                row.error_message = SIMPLE_STORYBOARD_TIMEOUT_ERROR
            row.updated_at = datetime.utcnow()
        _refresh_episode_simple_storyboard_from_batches(episode, db)
        if not str(getattr(episode, "simple_storyboard_error", "") or "").strip():
            episode.simple_storyboard_error = SIMPLE_STORYBOARD_TIMEOUT_ERROR
    else:
        episode.simple_storyboard_generating = False
        if not str(getattr(episode, "simple_storyboard_error", "") or "").strip():
            episode.simple_storyboard_error = SIMPLE_STORYBOARD_TIMEOUT_ERROR
    db.commit()
    db.refresh(episode)
    return True

def _load_simple_storyboard_rule_config_for_duration(duration: int, db: Session):
    template = db.query(models.ShotDurationTemplate).filter(
        models.ShotDurationTemplate.duration == int(duration or 15)
    ).first()
    if template:
        raw_text = str(getattr(template, "simple_storyboard_config_json", "") or "").strip()
        if raw_text:
            try:
                return normalize_rule_config(json.loads(raw_text), int(duration or 15))
            except Exception:
                pass
    return get_default_rule_config(duration)

def _normalize_storyboard_video_appoint_account(value: Any, default_value: str = "") -> str:
    raw = str(value if value is not None else default_value or "").strip()
    return raw


_DETAIL_IMAGES_MODEL_CONFIG = {
    "seedream-4.0": {},
    "seedream-4.1": {},
    "seedream-4.5": {},
    "seedream-4.6": {},
    "nano-banana-2": {},
    "nano-banana-pro": {},
    "gpt-image-2": {},
}


def _get_pydantic_fields_set(payload: Any) -> set:
    fields_set = getattr(payload, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(payload, "__fields_set__", set())
    return set(fields_set or set())


def _normalize_detail_images_provider(
    value: Optional[str],
    default_provider: str = "",
) -> str:
    aliases = {
        "jimeng": "jimeng",
        "momo": "momo",
        "banana": "momo",
        "moti": "momo",
        "moapp": "momo",
        "gettoken": "momo",
    }
    raw = str(value or "").strip().lower()
    if raw:
        return aliases.get(raw, raw)
    fallback = str(default_provider or "").strip().lower()
    return aliases.get(fallback, fallback)


def _resolve_episode_detail_images_provider(
    episode: Optional[models.Episode],
    default_provider: str = "",
) -> str:
    return _normalize_detail_images_provider(
        getattr(episode, "detail_images_provider", None) if episode is not None else None,
        default_provider=default_provider,
    )


def _normalize_detail_images_model(
    value: Optional[str],
    default_model: str = "seedream-4.0",
) -> str:
    raw = str(value or "").strip()
    fallback_raw = str(default_model or "").strip() or "seedream-4.0"
    normalized = normalize_image_model_key(raw or fallback_raw)
    try:
        route = image_platform_client.resolve_image_route(normalized)
        return str(route.get("key") or normalized)
    except Exception:
        if raw and normalized in _DETAIL_IMAGES_MODEL_CONFIG:
            return normalized
        fallback = normalize_image_model_key(fallback_raw)
        try:
            route = image_platform_client.resolve_image_route(fallback)
            return str(route.get("key") or fallback)
        except Exception:
            return fallback or "seedream-4.0"


def _normalize_storyboard2_video_duration(value: Optional[int], default_value: int = 6) -> int:
    allowed = {6, 10}
    try:
        parsed = int(value) if value is not None else int(default_value)
    except Exception:
        parsed = int(default_value) if default_value in allowed else 6
    if parsed in allowed:
        return parsed
    return int(default_value) if default_value in allowed else 6


def _normalize_storyboard2_image_cw(value: Optional[int], default_value: int = 50) -> int:
    try:
        parsed = int(value) if value is not None else int(default_value)
    except Exception:
        parsed = int(default_value) if default_value is not None else 50
    return max(1, min(100, parsed))


def _get_first_episode_for_storyboard_defaults(script_id: int, db: Session):
    return db.query(models.Episode).filter(
        models.Episode.script_id == script_id
    ).order_by(
        models.Episode.created_at.asc(),
        models.Episode.id.asc()
    ).first()


def _build_episode_storyboard_sora_create_values(
    script_id: int,
    episode_payload: Any,
    db: Session,
) -> Dict[str, Any]:
    fields_set = _get_pydantic_fields_set(episode_payload)
    source_episode = _get_first_episode_for_storyboard_defaults(script_id, db)

    def resolve_value(field_name: str, fallback: Any = None):
        if field_name in fields_set:
            return getattr(episode_payload, field_name, fallback)
        if source_episode is not None:
            return getattr(source_episode, field_name, fallback)
        return getattr(episode_payload, field_name, fallback)

    raw_model = _normalize_storyboard_video_model(
        resolve_value("storyboard_video_model", DEFAULT_STORYBOARD_VIDEO_MODEL),
        default_model=DEFAULT_STORYBOARD_VIDEO_MODEL
    )
    raw_aspect_ratio = _normalize_storyboard_video_aspect_ratio(
        resolve_value("storyboard_video_aspect_ratio", None),
        model=raw_model,
        default_ratio=_STORYBOARD_VIDEO_MODEL_CONFIG[raw_model]["default_ratio"]
    )
    raw_duration = _normalize_storyboard_video_duration(
        resolve_value("storyboard_video_duration", None),
        model=raw_model,
        default_duration=_STORYBOARD_VIDEO_MODEL_CONFIG[raw_model]["default_duration"]
    )
    raw_shot_image_size = _normalize_jimeng_ratio(
        resolve_value("shot_image_size", raw_aspect_ratio),
        default_ratio=raw_aspect_ratio
    )

    raw_video_style_template_id = resolve_value("video_style_template_id", None)
    try:
        normalized_video_style_template_id = int(raw_video_style_template_id) if raw_video_style_template_id else None
    except Exception:
        normalized_video_style_template_id = None

    return {
        "shot_image_size": raw_shot_image_size,
        "detail_images_model": _normalize_detail_images_model(
            resolve_value("detail_images_model", "seedream-4.0"),
            default_model="seedream-4.0"
        ),
        "detail_images_provider": _normalize_detail_images_provider(
            resolve_value("detail_images_provider", ""),
        ),
        "storyboard2_image_cw": _normalize_storyboard2_image_cw(
            resolve_value("storyboard2_image_cw", 50),
            default_value=50
        ),
        "storyboard2_include_scene_references": bool(
            resolve_value("storyboard2_include_scene_references", False)
        ),
        "storyboard_video_model": raw_model,
        "storyboard_video_aspect_ratio": raw_aspect_ratio,
        "storyboard_video_duration": raw_duration,
        "storyboard_video_resolution_name": _normalize_storyboard_video_resolution_name(
            resolve_value("storyboard_video_resolution_name", None),
            model=raw_model,
            default_resolution=_STORYBOARD_VIDEO_MODEL_CONFIG[raw_model].get("default_resolution", "")
        ),
        "storyboard_video_appoint_account": _normalize_storyboard_video_appoint_account(
            resolve_value("storyboard_video_appoint_account", "")
        ),
        "video_style_template_id": normalized_video_style_template_id,
    }


def _serialize_script_episode(episode: models.Episode, db: Session) -> Dict[str, Any]:
    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == episode.id
    ).first()
    storyboard_video_model = _normalize_storyboard_video_model(
        getattr(episode, "storyboard_video_model", None),
        default_model=DEFAULT_STORYBOARD_VIDEO_MODEL
    )
    return {
        "id": episode.id,
        "script_id": episode.script_id,
        "name": episode.name,
        "content": episode.content,
        "video_prompt_template": getattr(episode, "video_prompt_template", "") or "",
        "shot_image_size": _normalize_jimeng_ratio(getattr(episode, "shot_image_size", None), default_ratio="9:16"),
        "detail_images_model": _normalize_detail_images_model(
            getattr(episode, "detail_images_model", None),
            default_model="seedream-4.0"
        ),
        "detail_images_provider": _resolve_episode_detail_images_provider(episode),
        "storyboard2_video_duration": _normalize_storyboard2_video_duration(
            getattr(episode, "storyboard2_video_duration", None),
            default_value=6
        ),
        "storyboard2_image_cw": _normalize_storyboard2_image_cw(
            getattr(episode, "storyboard2_image_cw", None),
            default_value=50
        ),
        "storyboard2_include_scene_references": bool(
            getattr(episode, "storyboard2_include_scene_references", False)
        ),
        "storyboard_video_model": storyboard_video_model,
        "storyboard_video_aspect_ratio": _normalize_storyboard_video_aspect_ratio(
            getattr(episode, "storyboard_video_aspect_ratio", None),
            model=storyboard_video_model,
            default_ratio="16:9"
        ),
        "storyboard_video_duration": _normalize_storyboard_video_duration(
            getattr(episode, "storyboard_video_duration", None),
            model=storyboard_video_model,
            default_duration=15
        ),
        "storyboard_video_resolution_name": _normalize_storyboard_video_resolution_name(
            getattr(episode, "storyboard_video_resolution_name", None),
            model=storyboard_video_model,
            default_resolution="720p"
        ),
        "storyboard_video_appoint_account": _normalize_storyboard_video_appoint_account(
            getattr(episode, "storyboard_video_appoint_account", "")
        ),
        "video_style_template_id": getattr(episode, "video_style_template_id", None),
        "batch_generating_prompts": episode.batch_generating_prompts,
        "batch_generating_storyboard2_prompts": bool(getattr(episode, "batch_generating_storyboard2_prompts", False)),
        "narration_converting": episode.narration_converting,
        "narration_error": episode.narration_error,
        "opening_content": episode.opening_content or "",
        "opening_generating": episode.opening_generating or False,
        "opening_error": episode.opening_error or "",
        "library_id": library.id if library else None,
        "created_at": episode.created_at,
    }


@router.post("/api/scripts/{script_id}/episodes", response_model=EpisodeResponse)
async def create_episode(
    script_id: int,
    episode: EpisodeCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    script = db.query(models.Script).filter(models.Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="剧本不存在")
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    storyboard_sora_defaults = _build_episode_storyboard_sora_create_values(
        script_id,
        episode,
        db,
    )

    new_episode = models.Episode(
        script_id=script_id,
        name=episode.name,
        content=episode.content,
        billing_version=1,
        video_prompt_template=episode.video_prompt_template or "",
        batch_size=episode.batch_size or 500,
        shot_image_size=storyboard_sora_defaults["shot_image_size"],
        detail_images_model=storyboard_sora_defaults["detail_images_model"],
        detail_images_provider=storyboard_sora_defaults["detail_images_provider"],
        storyboard2_duration=int(getattr(episode, "storyboard2_duration", 15)),
        storyboard2_video_duration=_normalize_storyboard2_video_duration(
            getattr(episode, "storyboard2_video_duration", None),
            default_value=6
        ),
        storyboard2_image_cw=storyboard_sora_defaults["storyboard2_image_cw"],
        storyboard2_include_scene_references=storyboard_sora_defaults["storyboard2_include_scene_references"],
        storyboard_video_model=storyboard_sora_defaults["storyboard_video_model"],
        storyboard_video_aspect_ratio=storyboard_sora_defaults["storyboard_video_aspect_ratio"],
        storyboard_video_duration=storyboard_sora_defaults["storyboard_video_duration"],
        storyboard_video_resolution_name=storyboard_sora_defaults["storyboard_video_resolution_name"],
        storyboard_video_appoint_account=storyboard_sora_defaults["storyboard_video_appoint_account"],
        video_style_template_id=storyboard_sora_defaults["video_style_template_id"],
    )
    db.add(new_episode)
    db.commit()
    db.refresh(new_episode)

    library = models.StoryLibrary(
        user_id=user.id,
        episode_id=new_episode.id,
        name=f"{episode.name} - 主体库",
        description=f"{script.name} 的剧集主体库",
    )
    db.add(library)
    db.commit()

    return new_episode


@router.get("/api/scripts/{script_id}/episodes", response_model=List[EpisodeResponse])
async def get_script_episodes(
    script_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    script = db.query(models.Script).filter(models.Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="剧本不存在")
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    episode_rows = db.query(models.Episode).filter(
        models.Episode.script_id == script_id
    ).order_by(models.Episode.created_at.asc()).all()

    any_runtime_flag_changed = False
    result = []
    for episode in episode_rows:
        any_runtime_flag_changed = _reconcile_episode_runtime_flags(episode, db) or any_runtime_flag_changed
        result.append(_serialize_script_episode(episode, db))

    if any_runtime_flag_changed:
        db.commit()

    return result


def _resolve_narration_template(episode: models.Episode, db: Session, custom_template: Optional[str] = None) -> str:
    template = str(custom_template or "").strip()
    if template:
        return template
    if episode.script and episode.script.narration_template:
        template = str(episode.script.narration_template or "").strip()
        if template:
            return template
    template_setting = db.query(models.GlobalSettings).filter(
        models.GlobalSettings.key == "narration_conversion_template"
    ).first()
    return str(getattr(template_setting, "value", "") or "").strip()


def _resolve_opening_template(db: Session, custom_template: Optional[str] = None) -> str:
    template = str(custom_template or "").strip()
    if template:
        return template
    template_setting = db.query(models.GlobalSettings).filter(
        models.GlobalSettings.key == "opening_generation_template"
    ).first()
    template = str(getattr(template_setting, "value", "") or "").strip()
    if template:
        return template
    return "我想把这个片段做成一个短视频，需要一个精彩吸引人的开头，请你帮我写一个开头"


def _submit_episode_text_relay_task(
    db: Session,
    *,
    episode: models.Episode,
    task_type: str,
    function_key: str,
    prompt: str,
    response_format_json: bool = False,
):
    config = get_ai_config(function_key)
    request_payload = {
        "model": config["model"],
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "stream": False,
    }
    if response_format_json:
        request_payload["response_format"] = {"type": "json_object"}

    task_payload = {
        "episode_id": int(episode.id),
        "task_type": task_type,
        "function_key": function_key,
    }

    return submit_and_persist_text_task(
        db,
        task_type=task_type,
        owner_type="episode",
        owner_id=int(episode.id),
        stage_key=task_type,
        function_key=function_key,
        request_payload=request_payload,
        task_payload=task_payload,
    )


def _get_owned_script_episode(
    db: Session,
    *,
    script_id: int,
    episode_id: int,
    user: models.User,
):
    script = db.query(models.Script).filter(models.Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="剧本不存在")
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    episode = db.query(models.Episode).filter(
        models.Episode.id == episode_id,
        models.Episode.script_id == script_id,
    ).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")
    return episode


@router.post("/api/scripts/{script_id}/episodes/{episode_id}/convert-to-narration")
async def convert_to_narration(
    script_id: int,
    episode_id: int,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    episode = _get_owned_script_episode(
        db,
        script_id=script_id,
        episode_id=episode_id,
        user=user,
    )

    if episode.narration_converting:
        raise HTTPException(status_code=400, detail="正在转换中，请稍后")

    content = request.get("content", "")
    if content and content.strip():
        episode.content = content.strip()

    if not episode.content or not episode.content.strip():
        raise HTTPException(status_code=400, detail="文本内容不能为空")

    resolved_template = _resolve_narration_template(
        episode,
        db,
        request.get("template", None),
    )
    if not resolved_template:
        raise HTTPException(status_code=400, detail="提示词模板未配置")

    full_prompt = f"{resolved_template}\n\n原文本：\n{episode.content.strip()}"

    episode.narration_converting = True
    episode.narration_error = ""
    try:
        relay_task = _submit_episode_text_relay_task(
            db,
            episode=episode,
            task_type="narration",
            function_key="narration",
            prompt=full_prompt,
            response_format_json=False,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            episode.narration_converting = False
            episode.narration_error = str(exc)
            db.commit()
        raise HTTPException(status_code=502, detail=f"提交文本任务失败: {str(exc)}")

    return {
        "success": True,
        "message": "文本转解说剧任务已启动",
        "episode_id": episode_id,
        "task_id": relay_task.external_task_id,
    }


@router.post("/api/scripts/{script_id}/episodes/{episode_id}/generate-opening")
async def generate_opening(
    script_id: int,
    episode_id: int,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    episode = _get_owned_script_episode(
        db,
        script_id=script_id,
        episode_id=episode_id,
        user=user,
    )

    if episode.opening_generating:
        raise HTTPException(status_code=400, detail="正在生成中，请稍后")

    content = request.get("content", "")
    if content and content.strip():
        episode.content = content.strip()

    if not episode.content or not episode.content.strip():
        raise HTTPException(status_code=400, detail="文本内容不能为空")

    resolved_template = _resolve_opening_template(db, request.get("template", None))
    full_prompt = f"{resolved_template}\n\n原文本：\n{episode.content.strip()}"

    episode.opening_generating = True
    episode.opening_error = ""
    try:
        relay_task = _submit_episode_text_relay_task(
            db,
            episode=episode,
            task_type="opening",
            function_key="opening",
            prompt=full_prompt,
            response_format_json=False,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            episode.opening_generating = False
            episode.opening_error = str(exc)
            db.commit()
        raise HTTPException(status_code=502, detail=f"提交文本任务失败: {str(exc)}")

    return {
        "success": True,
        "message": "精彩开头生成任务已启动",
        "episode_id": episode_id,
        "task_id": relay_task.external_task_id,
    }


@router.get("/api/episodes/{episode_id}", response_model=EpisodeResponse)

def get_episode(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取单个片段信息"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    if _reconcile_episode_runtime_flags(episode, db):
        db.commit()

    return episode

def _build_episode_poll_status_payload(episode: models.Episode) -> dict:
    return {
        "narration_converting": bool(getattr(episode, "narration_converting", False)),
        "narration_error": getattr(episode, "narration_error", "") or "",
        "opening_generating": bool(getattr(episode, "opening_generating", False)),
        "opening_error": getattr(episode, "opening_error", "") or "",
        "opening_content": getattr(episode, "opening_content", "") or "",
        "batch_generating_prompts": bool(getattr(episode, "batch_generating_prompts", False)),
        "batch_generating_storyboard2_prompts": bool(getattr(episode, "batch_generating_storyboard2_prompts", False)),
    }

def _count_storyboard_items(raw_data: Optional[str]) -> int:
    if not raw_data:
        return 0
    try:
        parsed = json.loads(raw_data)
    except Exception:
        return 0
    shots = parsed.get("shots") if isinstance(parsed, dict) else None
    return len(shots) if isinstance(shots, list) else 0

@router.get("/api/episodes/{episode_id}/poll-status")

def get_episode_poll_status(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    episode = _verify_episode_permission(episode_id, user, db)
    if _reconcile_episode_runtime_flags(episode, db):
        db.commit()
    return _build_episode_poll_status_payload(episode)

@router.get("/api/episodes/{episode_id}/total-cost")

async def get_episode_total_cost(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取剧集的总花费"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    if int(getattr(episode, "billing_version", 0) or 0) >= 1:
        detail = billing_service.get_episode_billing_detail(
            db,
            episode_id=int(episode_id),
            user_id=int(user.id),
        ) or {"summary": {"net_amount_rmb": "0.00000"}}
        total_cost_yuan = float(detail["summary"]["net_amount_rmb"])
        return {
            "episode_id": episode_id,
            "total_cost_cents": int(round(total_cost_yuan * 100)),
            "total_cost_yuan": total_cost_yuan,
            "billing_version": int(getattr(episode, "billing_version", 0) or 0),
        }

    # 统计该剧集下所有镜头的总花费（单位：分）
    total_cost_cents = db.query(func.sum(models.StoryboardShot.price)).filter(
        models.StoryboardShot.episode_id == episode_id
    ).scalar() or 0

    # 转换为元
    total_cost_yuan = total_cost_cents / 100.0

    return {
        "episode_id": episode_id,
        "total_cost_cents": total_cost_cents,
        "total_cost_yuan": round(total_cost_yuan, 2),
        "billing_version": int(getattr(episode, "billing_version", 0) or 0),
    }

@router.put("/api/episodes/{episode_id}", response_model=EpisodeResponse)

async def update_episode(
    episode_id: int,
    episode_data: EpisodeCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新片段"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    episode.name = episode_data.name
    episode.content = episode_data.content
    db.commit()
    db.refresh(episode)
    return episode

@router.put("/api/episodes/{episode_id}/storyboard2-duration")

async def update_episode_storyboard2_duration(
    episode_id: int,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新片段的故事板2时长规格"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    duration = request.get("duration")
    if duration not in [6, 10, 15, 25]:
        raise HTTPException(status_code=400, detail="不支持的时长规格，只能是6/10/15/25")

    episode.storyboard2_duration = duration
    db.commit()
    db.refresh(episode)
    return {"message": "时长规格已更新", "duration": duration}

def _normalize_storyboard_shot_ids(shot_ids: List[int], allow_zero: bool = False) -> List[int]:
    normalized_ids = []
    seen_ids = set()
    for raw_shot_id in shot_ids or []:
        try:
            shot_id = int(raw_shot_id or 0)
        except (TypeError, ValueError):
            continue
        if shot_id < 0 or (shot_id == 0 and not allow_zero) or shot_id in seen_ids:
            continue
        seen_ids.add(shot_id)
        normalized_ids.append(shot_id)
    return normalized_ids

def _clear_storyboard_shot_dependencies(shot_ids: List[int], db: Session, allow_zero: bool = False) -> Dict[str, int]:
    """
    删除镜头前先清理直接依赖 storyboard_shots.id 的记录。

    PostgreSQL 外键不会替 ORM bulk delete 自动兜底，所以这里要显式处理
    storyboard2_shots.source_shot_id / managed_tasks / collages / videos / detail_images。
    """
    normalized_shot_ids = _normalize_storyboard_shot_ids(shot_ids, allow_zero=allow_zero)
    if not normalized_shot_ids:
        return {
            "storyboard2_unlinked": 0,
            "deleted_collages": 0,
            "deleted_videos": 0,
            "deleted_detail_images": 0,
            "deleted_managed_tasks": 0,
        }

    storyboard2_unlinked = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.source_shot_id.in_(normalized_shot_ids)
    ).update(
        {models.Storyboard2Shot.source_shot_id: None},
        synchronize_session=False
    )

    deleted_collages = db.query(models.ShotCollage).filter(
        models.ShotCollage.shot_id.in_(normalized_shot_ids)
    ).delete(synchronize_session=False)
    deleted_videos = db.query(models.ShotVideo).filter(
        models.ShotVideo.shot_id.in_(normalized_shot_ids)
    ).delete(synchronize_session=False)
    deleted_detail_images = db.query(models.ShotDetailImage).filter(
        models.ShotDetailImage.shot_id.in_(normalized_shot_ids)
    ).delete(synchronize_session=False)
    deleted_managed_tasks = db.query(models.ManagedTask).filter(
        models.ManagedTask.shot_id.in_(normalized_shot_ids)
    ).delete(synchronize_session=False)

    return {
        "storyboard2_unlinked": int(storyboard2_unlinked or 0),
        "deleted_collages": int(deleted_collages or 0),
        "deleted_videos": int(deleted_videos or 0),
        "deleted_detail_images": int(deleted_detail_images or 0),
        "deleted_managed_tasks": int(deleted_managed_tasks or 0),
    }

def _delete_storyboard_shots_by_ids(
    shot_ids: List[int],
    db: Session,
    log_context: str = "",
    allow_zero: bool = False
) -> int:
    normalized_shot_ids = _normalize_storyboard_shot_ids(shot_ids, allow_zero=allow_zero)
    if not normalized_shot_ids:
        return 0

    cleanup_stats = _clear_storyboard_shot_dependencies(
        normalized_shot_ids,
        db,
        allow_zero=allow_zero
    )
    deleted_shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.id.in_(normalized_shot_ids)
    ).delete(synchronize_session=False)

    print(
        "[分镜删除清理] "
        f"{log_context} shots={deleted_shots} "
        f"collages={cleanup_stats['deleted_collages']} "
        f"videos={cleanup_stats['deleted_videos']} "
        f"detail_images={cleanup_stats['deleted_detail_images']} "
        f"managed_tasks={cleanup_stats['deleted_managed_tasks']} "
        f"storyboard2_unlinked={cleanup_stats['storyboard2_unlinked']}"
    )
    return deleted_shots

def _delete_episode_storyboard_shots(episode_id: int, db: Session) -> int:
    shot_ids = [
        shot_id
        for shot_id, in db.query(models.StoryboardShot.id).filter(
            models.StoryboardShot.episode_id == episode_id
        ).all()
    ]
    return _delete_storyboard_shots_by_ids(
        shot_ids,
        db,
        log_context=f"episode_id={episode_id}",
        allow_zero=True
    )

def _create_shots_from_storyboard_data(episode_id: int, db: Session):
    """
    从episode.storyboard_data JSON创建storyboard_shots表记录

    此函数被以下场景调用：
    1. AI生成分镜完成后自动调用
    2. 用户手动点击"创建镜头"按钮
    """
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode or not episode.storyboard_data:
        return

    # 解析JSON数据
    try:
        storyboard = json.loads(episode.storyboard_data)
        shots_data = storyboard.get("shots", [])
        subjects_data = storyboard.get("subjects", [])
    except Exception as e:
        print(f"解析storyboard_data失败: {e}")
        return

    if not shots_data:
        return

    canonical_subject_map = _build_subject_detail_map(subjects_data)
    reconciled_shots_data = []
    combined_subject_map = dict(canonical_subject_map)
    for shot_data in shots_data:
        shot_copy = dict(shot_data)
        shot_copy["subjects"] = _reconcile_storyboard_shot_subjects(
            shot_copy,
            canonical_subject_map,
        )
        for subject in shot_copy.get("subjects", []):
            subject_key = (subject["name"], subject["type"])
            if subject_key not in combined_subject_map:
                combined_subject_map[subject_key] = _normalize_subject_detail_entry(subject)
        reconciled_shots_data.append(shot_copy)

    shots_data = reconciled_shots_data
    subjects_data = list(combined_subject_map.values())

    # 获取剧本和主体库
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if not script:
        return

    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == episode.id
    ).first()
    if not library:
        return

    # ========== 清理旧数据（避免孤儿记录） ==========
    # 获取当前主体库的所有旧主体
    old_cards = db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library.id
    ).all()

    # 先删除这些主体的所有图片（避免孤儿记录）
    for old_card in old_cards:
        # 删除手动上传的图片
        db.query(models.CardImage).filter(
            models.CardImage.card_id == old_card.id
        ).delete()
        # 删除AI生成的图片
        db.query(models.GeneratedImage).filter(
            models.GeneratedImage.card_id == old_card.id
        ).delete()
        # 删除声音素材
        db.query(models.SubjectCardAudio).filter(
            models.SubjectCardAudio.card_id == old_card.id
        ).delete()

    # 再删除主体卡片
    db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library.id
    ).delete()
    db.commit()
    print(f"[清理] 已清空当前主体库的所有旧主体、图片和声音素材")

    allowed_subject_types = set(ALLOWED_CARD_TYPES)
    existing_names_to_ids = {}

    # ========== 渐进式回退：从最新到最旧的剧集查找可复用主体 ==========
    # 获取当前剧集需要的所有主体
    needed_subjects = set()
    for subj in subjects_data:
        name = subj.get('name', '').strip()
        subject_type = (subj.get('type') or "角色").strip() or "角色"
        if name and subject_type in allowed_subject_types:
            needed_subjects.add((name, subject_type))

    # 获取同一剧本下其他剧集（按创建时间倒序：从新到旧）
    other_episodes = db.query(models.Episode).filter(
        models.Episode.script_id == script.id,
        models.Episode.id != episode.id
    ).order_by(models.Episode.created_at.desc()).all()

    # 已找到的主体字典：(name, card_type) -> SubjectCard
    found_subjects = {}

    # 遍历每个剧集（从新到旧）
    for ep in other_episodes:
        # 获取这个剧集的主体库
        ep_library = db.query(models.StoryLibrary).filter(
            models.StoryLibrary.episode_id == ep.id
        ).first()

        if not ep_library:
            continue

        # 获取这个主体库的所有符合类型的主体
        ep_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == ep_library.id,
            models.SubjectCard.card_type.in_(allowed_subject_types)
        ).all()

        # 遍历这个剧集的主体
        for card in ep_cards:
            key = (card.name, card.card_type)
            # 如果需要这个主体 且 还没找到过，则记录
            if key in needed_subjects and key not in found_subjects:
                found_subjects[key] = card
                print(f"[素材查找] 从剧集 '{ep.name}' 找到可复用主体：{card.name}（{card.card_type}）")

        # 如果所有需要的主体都找到了，提前退出
        if len(found_subjects) >= len(needed_subjects):
            print(f"[素材查找] 所有需要的主体都已找到，停止查找")
            break

    print(f"[素材查找] 共找到 {len(found_subjects)}/{len(needed_subjects)} 个可复用主体")

    # 创建新主体卡片
    for subj in subjects_data:
        name = subj.get('name', '').strip()
        subject_type = (subj.get('type') or "角色").strip() or "角色"
        # 跳过空名字或已创建的名字（防止同批次重复）
        if not name or name in existing_names_to_ids:
            continue
        if subject_type not in allowed_subject_types:
            continue

        # ========== 检查是否有可复用的主体 ==========
        key = (name, subject_type)
        source_card = found_subjects.get(key)

        if source_card:
            # 找到可复用的主体，复制 SubjectCard
            new_card = models.SubjectCard(
                library_id=library.id,
                name=source_card.name,
                alias=source_card.alias,
                card_type=source_card.card_type,
                ai_prompt=source_card.ai_prompt,
                role_personality=(getattr(source_card, "role_personality", "") or ""),
                style_template_id=source_card.style_template_id
            )
            db.add(new_card)
            db.flush()

            # 复制所有图片记录
            source_images = db.query(models.CardImage).filter(
                models.CardImage.card_id == source_card.id
            ).order_by(models.CardImage.order).all()

            copied_count = 0
            for img in source_images:
                # 判断图片路径类型
                is_cdn_url = img.image_path.startswith(('http://', 'https://'))

                if is_cdn_url:
                    # CDN图片：直接复制记录，共享同一个URL
                    new_image = models.CardImage(
                        card_id=new_card.id,
                        image_path=img.image_path,  # 直接使用同一个CDN URL
                        order=img.order
                    )
                    db.add(new_image)
                    copied_count += 1
                else:
                    # 本地图片：检查文件是否存在，物理复制
                    if os.path.exists(img.image_path):
                        file_ext = os.path.splitext(img.image_path)[1]
                        new_filename = f"card_{new_card.id}_{uuid.uuid4().hex[:8]}{file_ext}"
                        new_path = os.path.join("uploads", new_filename)

                        try:
                            shutil.copy2(img.image_path, new_path)
                            new_image = models.CardImage(
                                card_id=new_card.id,
                                image_path=new_path,
                                order=img.order
                            )
                            db.add(new_image)
                            copied_count += 1
                        except Exception as e:
                            print(f"复制本地图片失败 {img.image_path}: {e}")

            # ========== 复制 GeneratedImage 记录 ==========
            source_generated_images = db.query(models.GeneratedImage).filter(
                models.GeneratedImage.card_id == source_card.id
            ).order_by(models.GeneratedImage.created_at).all()

            for gen_img in source_generated_images:
                new_generated_image = models.GeneratedImage(
                    card_id=new_card.id,
                    image_path=gen_img.image_path,  # CDN URL 直接复用
                    model_name=gen_img.model_name,
                    is_reference=gen_img.is_reference,
                    task_id=gen_img.task_id,
                    status=gen_img.status
                )
                db.add(new_generated_image)

            source_audios = db.query(models.SubjectCardAudio).filter(
                models.SubjectCardAudio.card_id == source_card.id
            ).order_by(models.SubjectCardAudio.created_at).all()
            for audio in source_audios:
                new_audio = models.SubjectCardAudio(
                    card_id=new_card.id,
                    audio_path=audio.audio_path,
                    file_name=audio.file_name,
                    duration_seconds=_safe_audio_duration_seconds(audio.duration_seconds),
                    is_reference=audio.is_reference
                )
                db.add(new_audio)

            existing_names_to_ids[name] = new_card.id
            print(f"[主体复用] 复用主体：{name}（{subject_type}），复制了 {copied_count} 张卡片图，{len(source_generated_images)} 张AI图，{len(source_audios)} 条声音素材")
        else:
            # 没有可复用的主体，创建空主体（原逻辑）
            new_card = models.SubjectCard(
                library_id=library.id,
                name=name,
                alias=subj.get('alias', '').strip(),
                card_type=subject_type,
                ai_prompt=subj.get('ai_prompt', '').strip(),
                role_personality=(subj.get('role_personality') or subj.get('role_personality_en') or subj.get('personality_en') or '').strip()
            )
            db.add(new_card)
            db.flush()
            existing_names_to_ids[name] = new_card.id

    db.commit()

    # 重新获取所有卡片
    all_cards = db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library.id
    ).all()
    all_cards = [card for card in all_cards if card.card_type in allowed_subject_types]
    card_name_to_id = {card.name: card.id for card in all_cards}

    # 删除旧镜头（替换模式）
    _delete_episode_storyboard_shots(episode_id, db)
    db.commit()

    # 创建新镜头
    for shot_data in shots_data:
        shot_number = int(shot_data.get('shot_number', 0))
        if shot_number <= 0:
            continue

        # 解析主体ID
        selected_card_ids = []
        subjects = shot_data.get('subjects', [])
        if isinstance(subjects, list):
            for subj in subjects:
                if isinstance(subj, dict):
                    name = subj.get('name', '').strip()
                    if name and name in card_name_to_id:
                        selected_card_ids.append(card_name_to_id[name])

        # 处理新格式的 dialogue 和 narration - 格式化为可读文本
        def format_voice_content(shot_data: dict) -> str:
            """将narration或dialogue格式化为可读文本"""
            voice_type = shot_data.get('voice_type', 'none')

            if voice_type == 'narration':
                narration = shot_data.get('narration')
                if narration and isinstance(narration, dict):
                    speaker = narration.get('speaker', '')
                    gender = narration.get('gender', '')
                    emotion = narration.get('emotion', '')
                    text = narration.get('text', '')
                    return f"旁白（{speaker}/{gender}/{emotion}）：{text}"

            elif voice_type == 'dialogue':
                dialogue = shot_data.get('dialogue')
                if dialogue and isinstance(dialogue, list):
                    dialogue_lines = []
                    for d in dialogue:
                        speaker = d.get('speaker', '')
                        gender = d.get('gender', '')
                        target = d.get('target')
                        emotion = d.get('emotion', '')
                        text = d.get('text', '')

                        if target:
                            dialogue_lines.append(f"{speaker}（{gender}）对{target}说（{emotion}）：{text}")
                        else:
                            dialogue_lines.append(f"{speaker}（{gender}）说（{emotion}）：{text}")

                    return '\n'.join(dialogue_lines)

            return ""

        # 格式化语音内容
        formatted_voice = format_voice_content(shot_data)

        # 使用原剧本段落作为基础文本
        excerpt = shot_data.get('original_text', '')

        # 构建sora_prompt: 原剧本段落 + 旁白/对白
        if excerpt and formatted_voice:
            sora_prompt_value = f"{excerpt}\n{formatted_voice}"
        elif excerpt:
            sora_prompt_value = excerpt
        elif formatted_voice:
            sora_prompt_value = formatted_voice
        else:
            sora_prompt_value = ""

        # storyboard_dialogue保存格式化的语音内容
        storyboard_dialogue_value = formatted_voice

        for _ in [None]:
            new_shot = models.StoryboardShot(
                episode_id=episode_id,
                shot_number=shot_number,
                variant_index=0,
                prompt_template='',
                script_excerpt=shot_data.get('original_text', ''),
                storyboard_dialogue=storyboard_dialogue_value,  # ✅ 格式化的旁白/对白
                sora_prompt=sora_prompt_value,  # ✅ 原剧本段落 + 旁白/对白
                selected_card_ids=json.dumps(selected_card_ids),
                selected_sound_card_ids=None,
                aspect_ratio='16:9',
                duration=15,
                storyboard_video_model="",
                storyboard_video_model_override_enabled=False,
                duration_override_enabled=False
            )
            db.add(new_shot)

    db.commit()

def _submit_detailed_storyboard_stage1_task(db: Session, *, episode_id: int, simple_shots: List[Dict[str, Any]]):
    shots_content = ""
    for shot in simple_shots:
        shot_num = shot.get("shot_number", "?")
        original_text = shot.get("original_text", "")
        shots_content += f"镜头{shot_num}:\n{original_text}\n\n"

    prompt_template = get_prompt_by_key("detailed_storyboard_content_analysis")
    prompt = prompt_template.format(shots_content=shots_content)
    config = get_ai_config("detailed_storyboard_s1")
    request_data = {
        "model": config["model"],
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    task_payload = {
        "episode_id": int(episode_id),
        "simple_shots": simple_shots,
    }
    return submit_and_persist_text_task(
        db,
        task_type="detailed_storyboard_stage1",
        owner_type="episode",
        owner_id=int(episode_id),
        stage_key="detailed_storyboard_stage1",
        function_key="detailed_storyboard_s1",
        request_payload=request_data,
        task_payload=task_payload,
    )

@router.post("/api/episodes/{episode_id}/generate-simple-storyboard")

async def generate_simple_storyboard_api(
    episode_id: int,
    request: SimpleStoryboardRequest = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """同步生成简单分镜（程序化规则）

    可选参数：
    - content: 自定义文案内容。如果不提供，则使用片段的content
    - batch_size: 批次展示阈值，默认500
    """
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 使用自定义内容或片段内容
    if request and request.content:
        episode_content = request.content
    else:
        episode_content = episode.content

    # 获取分批字数（优先使用请求参数，否则使用episode设置，最后使用默认值）
    if request and request.batch_size:
        batch_size = request.batch_size
    else:
        batch_size = episode.batch_size or 500

    duration = 25 if int(episode.storyboard2_duration or 15) == 25 else 15

    def mark_simple_storyboard_request_started():
        episode.batch_size = batch_size
        episode.simple_storyboard_data = None
        episode.simple_storyboard_generating = True
        episode.simple_storyboard_error = ""

    commit_with_retry(
        db,
        prepare_fn=mark_simple_storyboard_request_started,
        context=f"simple_storyboard_request episode={episode_id}"
    )

    try:
        rule_config = _load_simple_storyboard_rule_config_for_duration(duration, db)
        shots = generate_simple_storyboard_shots(
            episode_content,
            duration,
            rule_override=rule_config,
        )
        _persist_programmatic_simple_storyboard_batches(
            episode_id,
            shots,
            batch_size,
            db,
        )
        episode.simple_storyboard_data = json.dumps({"shots": shots}, ensure_ascii=False)
        episode.simple_storyboard_generating = False
        episode.simple_storyboard_error = ""
        summary = _refresh_episode_simple_storyboard_from_batches(episode, db)
        db.commit()
        print(
            f"[SimpleStoryboard][generate] episode_id={episode_id} duration={duration} "
            f"content_len={len(str(episode_content or ''))} shots={len(shots)} "
            f"total_batches={int(summary.get('total_batches') or 0)} "
            f"completed_batches={int(summary.get('completed_batches') or 0)} "
            f"failed_batches={int(summary.get('failed_batches') or 0)}"
        )
    except Exception as exc:
        db.rollback()
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            episode.simple_storyboard_generating = False
            episode.simple_storyboard_error = str(exc)
            db.commit()
        raise HTTPException(status_code=500, detail=f"简单分镜生成失败: {str(exc)}")

    return {
        "message": "简单分镜生成完成",
        "generating": False,
        "submitted_batches": int(summary.get("total_batches") or 0),
        "error": episode.simple_storyboard_error or "",
        "shots": summary.get("shots") or [],
        "batch_size": int(episode.batch_size or batch_size or 500),
        "total_batches": int(summary.get("total_batches") or 0),
        "completed_batches": int(summary.get("completed_batches") or 0),
        "failed_batches": int(summary.get("failed_batches") or 0),
        "submitting_batches": int(summary.get("submitting_batches") or 0),
        "has_failures": bool(summary.get("has_failures")),
        "failed_batch_errors": summary.get("failed_batch_errors") or [],
        "batches": summary.get("batches") or [],
    }

@router.get("/api/episodes/{episode_id}/simple-storyboard")

def get_simple_storyboard(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取片段的简单分镜数据"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    _mark_simple_storyboard_timeout_if_needed(episode, db)
    if _reconcile_episode_runtime_flags(episode, db):
        db.commit()

    # 解析简单分镜数据
    shots = []
    if episode.simple_storyboard_data:
        try:
            data = json.loads(episode.simple_storyboard_data)
            shots = data.get("shots", [])
        except:
            shots = []

    summary = _get_simple_storyboard_batch_summary(episode_id, db)
    print(
        f"[SimpleStoryboard][fetch] episode_id={episode_id} generating={bool(episode.simple_storyboard_generating)} "
        f"error={bool(episode.simple_storyboard_error)} shots={len(shots)} "
        f"total_batches={int(summary.get('total_batches') or 0)} "
        f"completed_batches={int(summary.get('completed_batches') or 0)} "
        f"failed_batches={int(summary.get('failed_batches') or 0)} "
        f"submitting_batches={int(summary.get('submitting_batches') or 0)}"
    )
    return {
        "generating": episode.simple_storyboard_generating,
        "error": episode.simple_storyboard_error or "",
        "shots": shots,
        "batch_size": episode.batch_size or 500,
        "total_batches": int(summary.get("total_batches") or 0),
        "completed_batches": int(summary.get("completed_batches") or 0),
        "failed_batches": int(summary.get("failed_batches") or 0),
        "submitting_batches": int(summary.get("submitting_batches") or 0),
        "has_failures": bool(summary.get("has_failures")),
        "failed_batch_errors": summary.get("failed_batch_errors") or [],
        "batches": summary.get("batches") or [],
    }

@router.get("/api/episodes/{episode_id}/simple-storyboard/status")

def get_simple_storyboard_status(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    episode = _verify_episode_permission(episode_id, user, db)
    _mark_simple_storyboard_timeout_if_needed(episode, db)
    if _reconcile_episode_runtime_flags(episode, db):
        db.commit()
    summary = _get_simple_storyboard_batch_summary(episode_id, db)
    return {
        "generating": bool(episode.simple_storyboard_generating),
        "error": episode.simple_storyboard_error or "",
        "shots_count": _count_storyboard_items(episode.simple_storyboard_data),
        "total_batches": int(summary.get("total_batches") or 0),
        "completed_batches": int(summary.get("completed_batches") or 0),
        "failed_batches": int(summary.get("failed_batches") or 0),
        "submitting_batches": int(summary.get("submitting_batches") or 0),
        "failed_batch_errors": summary.get("failed_batch_errors") or [],
        "batches": summary.get("batches") or [],
    }

@router.post("/api/episodes/{episode_id}/simple-storyboard/retry-failed-batches")

async def retry_failed_simple_storyboard_batches_api(
    episode_id: int,
    background_tasks: BackgroundTasks = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _verify_episode_permission(episode_id, user, db)
    raise HTTPException(status_code=400, detail="失败批次重试已移除，请重新发起整次简单分镜生成")

@router.put("/api/episodes/{episode_id}/simple-storyboard")

async def update_simple_storyboard(
    episode_id: int,
    data: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新简单分镜数据（用户手动编辑后保存）"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    batch_rows = _get_simple_storyboard_batch_rows(episode_id, db)
    for row in batch_rows:
        row.status = "completed"
        row.error_message = ""
        row.shots_data = ""
    # 保存更新后的简单分镜数据
    episode.simple_storyboard_data = json.dumps(data, ensure_ascii=False)
    episode.simple_storyboard_generating = False
    episode.simple_storyboard_error = ""
    db.commit()

    return {"message": "简单分镜数据已更新"}

@router.post("/api/episodes/{episode_id}/generate-detailed-storyboard")

async def generate_detailed_storyboard_api(
    episode_id: int,
    background_tasks: BackgroundTasks = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """启动后台详细分镜生成任务（新阶段2+3：内容分析 + 主体提示词）"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    summary = _get_simple_storyboard_batch_summary(episode_id, db)
    if summary.get("has_failures"):
        raise HTTPException(status_code=400, detail="简单分镜存在失败批次，请先重试失败批次")

    # 清空旧的详细分镜数据
    def mark_detailed_storyboard_request_started():
        episode.storyboard_data = None
        episode.storyboard_generating = True
        episode.storyboard_error = ""

    commit_with_retry(
        db,
        prepare_fn=mark_detailed_storyboard_request_started,
        context=f"detailed_storyboard_request episode={episode_id}"
    )

    simple_storyboard = json.loads(episode.simple_storyboard_data or "{}")
    simple_shots = simple_storyboard.get("shots", [])
    if not simple_shots:
        raise HTTPException(status_code=400, detail="简单分镜中没有镜头数据")

    try:
        relay_task = _submit_detailed_storyboard_stage1_task(
            db,
            episode_id=episode_id,
            simple_shots=simple_shots,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            episode.storyboard_generating = False
            episode.storyboard_error = str(exc)
            db.commit()
        raise HTTPException(status_code=502, detail=f"提交文本任务失败: {str(exc)}")

    return {
        "message": "详细分镜生成任务已提交",
        "generating": True,
        "task_id": relay_task.external_task_id,
    }

@router.post("/api/episodes/{episode_id}/analyze-storyboard", response_model=StoryboardAnalyzeResponse)

async def analyze_episode_for_storyboard(
    episode_id: int,
    request: AnalyzeStoryboardRequest = None,
    background_tasks: BackgroundTasks = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """兼容旧入口：无追加参数时复用新的详细分镜提交流程。"""
    if request and (bool(request.append) or str(request.content or "").strip()):
        raise HTTPException(status_code=400, detail="旧版追加分析流程已下线，请使用新的简单分镜/详细分镜流程")

    payload = await generate_detailed_storyboard_api(
        episode_id=episode_id,
        background_tasks=background_tasks,
        user=user,
        db=db,
    )
    return {
        "message": str(payload.get("message") or "分镜表生成任务已提交"),
        "generating": bool(payload.get("generating", True)),
    }

@router.get("/api/episodes/{episode_id}/detailed-storyboard")

def get_detailed_storyboard(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取详细分镜的原始JSON数据（用于配音表等功能）"""
    episode, script = _ensure_voiceover_permission(episode_id, user, db)

    # 获取主体库
    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == episode.id
    ).first()

    stored_subject_map = {}
    if episode.storyboard_data:
        try:
            stored_subject_map = _build_subject_detail_map(json.loads(episode.storyboard_data).get("subjects", []))
        except Exception:
            stored_subject_map = {}

    subjects = []
    if library:
        cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library.id
        ).all()
        cards = [card for card in cards if card.card_type in ALLOWED_CARD_TYPES]
        for card in cards:
            stored_subject = stored_subject_map.get((card.name, card.card_type), {})
            subjects.append({
                "id": card.id,
                "name": card.name,
                "card_type": card.card_type,
                "type": card.card_type,
                "ai_prompt": (card.ai_prompt or "").strip() or stored_subject.get("ai_prompt", ""),
                "role_personality": (getattr(card, "role_personality", "") or "").strip() or stored_subject.get("role_personality", ""),
                "alias": (card.alias or "").strip() or stored_subject.get("alias", "")
            })

    shared_data = _load_script_voiceover_shared_data(script)
    default_voice_ref_id = _voiceover_first_reference_id(shared_data)

    # 优先从voiceover_data读取，缺失时回退storyboard_data并自动补齐line_id/tts
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
                # 提取配音相关字段
                for shot in data.get("shots", []):
                    shots.append({
                        "shot_number": shot.get("shot_number"),
                        "voice_type": shot.get("voice_type"),
                        "narration": shot.get("narration"),
                        "dialogue": shot.get("dialogue")
                    })
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
        "tts_shared": shared_data
    }

@router.put("/api/episodes/{episode_id}/voiceover")

async def update_voiceover_data(
    episode_id: int,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """保存配音表数据（只更新voiceover_data，不影响其他数据）"""
    episode, script = _ensure_voiceover_permission(episode_id, user, db)

    incoming_shots = request.get("shots", [])
    merged_voiceover_data = _merge_voiceover_shots_preserving_extensions(
        episode.voiceover_data,
        incoming_shots if isinstance(incoming_shots, list) else []
    )

    shared_data = _load_script_voiceover_shared_data(script)
    default_voice_ref_id = _voiceover_first_reference_id(shared_data)
    normalized_shots, _ = _normalize_voiceover_shots_for_tts(
        merged_voiceover_data.get("shots", []),
        default_voice_ref_id
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
    db: Session = Depends(get_db)
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
    db: Session = Depends(get_db)
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
        "created_at": datetime.utcnow().isoformat()
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
    db: Session = Depends(get_db)
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
        "shared": _load_script_voiceover_shared_data(script)
    }

@router.get("/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}/preview")

async def preview_voiceover_voice_reference(
    episode_id: int,
    reference_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
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
        headers={"Content-Disposition": f'inline; filename="{inline_name}"'}
    )

@router.delete("/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}")

async def delete_voiceover_voice_reference(
    episode_id: int,
    reference_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
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
        "updated_line_count": updated_line_count
    }

@router.post("/api/episodes/{episode_id}/voiceover/shared/vector-presets")

async def upsert_voiceover_vector_preset(
    episode_id: int,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
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
            "created_at": now_iso
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
    db: Session = Depends(get_db)
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
        "updated_line_count": updated_line_count
    }

@router.post("/api/episodes/{episode_id}/voiceover/shared/emotion-audio-presets")

async def create_voiceover_emotion_audio_preset(
    episode_id: int,
    name: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
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
        "created_at": datetime.utcnow().isoformat()
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
    db: Session = Depends(get_db)
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
        "updated_line_count": updated_line_count
    }

@router.post("/api/episodes/{episode_id}/voiceover/shared/setting-templates")

async def upsert_voiceover_setting_template(
    episode_id: int,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)

    name = str(request.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="模板名称不能为空")

    shared = _load_script_voiceover_shared_data(script)
    default_voice_ref_id = _voiceover_first_reference_id(shared)
    settings = _normalize_voiceover_setting_template_payload(
        request.get("settings"),
        default_voice_ref_id
    )

    templates = shared.get("setting_templates", [])
    if not isinstance(templates, list):
        templates = []

    target_id = str(request.get("id") or "").strip()
    target_item = None
    if target_id:
        target_item = next(
            (item for item in templates if str(item.get("id") or "").strip() == target_id),
            None
        )
    if not target_item:
        target_item = next(
            (item for item in templates if str(item.get("name") or "").strip() == name),
            None
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
            "updated_at": now_iso
        })

    shared["setting_templates"] = templates
    _save_script_voiceover_shared_data(script, shared)
    db.commit()

    return {
        "success": True,
        "template_id": target_id,
        "shared": _load_script_voiceover_shared_data(script)
    }

@router.delete("/api/episodes/{episode_id}/voiceover/shared/setting-templates/{template_id}")

async def delete_voiceover_setting_template(
    episode_id: int,
    template_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
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
        "shared": _load_script_voiceover_shared_data(script)
    }

@router.post("/api/episodes/{episode_id}/voiceover/lines/{line_id}/generate")

async def enqueue_voiceover_line_generate(
    episode_id: int,
    line_id: str,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
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
        default_voice_ref_id
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
                None
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
        "vector_config": vector_config
    }

    task = models.VoiceoverTtsTask(
        episode_id=episode.id,
        line_id=target_line_id,
        status="pending",
        request_json=json.dumps(task_payload, ensure_ascii=False),
        result_json="",
        error_message=""
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
        models.VoiceoverTtsTask.id <= task.id
    ).scalar() or 1

    return {
        "success": True,
        "task_id": task.id,
        "line_id": target_line_id,
        "status": "pending",
        "queue_position": int(queue_position)
    }

@router.post("/api/episodes/{episode_id}/voiceover/generate-all")

async def enqueue_voiceover_generate_all(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
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
        default_voice_ref_id
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
                "vector_config": vector_config
            }
            task = models.VoiceoverTtsTask(
                episode_id=episode.id,
                line_id=line_id,
                status="pending",
                request_json=json.dumps(task_payload, ensure_ascii=False),
                result_json="",
                error_message=""
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
            "processing": int(processing_count)
        }
    }

@router.get("/api/episodes/{episode_id}/voiceover/tts-status")

def get_voiceover_tts_status(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
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
            "processing": int(processing_count)
        }
    }

@router.get("/api/episodes/{episode_id}/storyboard")

def get_episode_storyboard(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取片段的分镜表数据（优先从episode.storyboard_data读取完整AI生成数据）"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # ✅ 获取主体库（总是从数据库加载最新的subjects，包括用户后来添加的）
    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == episode.id
    ).first()

    stored_subject_map = {}
    if episode.storyboard_data:
        try:
            stored_subject_map = _build_subject_detail_map(json.loads(episode.storyboard_data).get("subjects", []))
        except Exception:
            stored_subject_map = {}

    # 构建完整的subjects列表
    subjects = []
    if library:
        cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library.id
        ).all()
        cards = [card for card in cards if card.card_type in ALLOWED_CARD_TYPES]
        for card in cards:
            stored_subject = stored_subject_map.get((card.name, card.card_type), {})
            subjects.append({
                "id": card.id,  # ✅ 添加id字段，前端需要用它匹配selected_card_ids
                "name": card.name,
                "card_type": card.card_type,
                "type": card.card_type,
                "ai_prompt": (card.ai_prompt or "").strip() or stored_subject.get("ai_prompt", ""),
                "role_personality": (getattr(card, "role_personality", "") or "").strip() or stored_subject.get("role_personality", ""),
                "alias": (card.alias or "").strip() or stored_subject.get("alias", "")
            })

    # ✅ 优先从 episode.storyboard_data 读取完整的 AI 生成数据
    if episode.storyboard_data:
        try:
            data = json.loads(episode.storyboard_data)
            # 验证数据格式
            if isinstance(data, dict) and "shots" in data:
                # ✅ 查询数据库中最新的 selected_card_ids（用户在故事板界面选择的主体）
                shots_records = db.query(models.StoryboardShot).filter(
                    models.StoryboardShot.episode_id == episode_id,
                    models.StoryboardShot.variant_index == 0  # 只查询主镜头
                ).all()

                # 建立镜头号到最新 selected_card_ids 的映射
                shot_card_ids_map = {}
                shot_id_map = {}  # ✅ 添加：镜头号到数据库ID的映射
                for shot_record in shots_records:
                    # 同时存储整数和字符串形式的镜头号，确保能匹配
                    shot_card_ids_map[shot_record.shot_number] = shot_record.selected_card_ids
                    shot_card_ids_map[str(shot_record.shot_number)] = shot_record.selected_card_ids
                    shot_id_map[shot_record.shot_number] = shot_record.id  # ✅ 保存数据库ID
                    shot_id_map[str(shot_record.shot_number)] = shot_record.id

                # 创建卡片ID到卡片对象的映射
                card_map = {}
                if library:
                    cards = db.query(models.SubjectCard).filter(
                        models.SubjectCard.library_id == library.id
                    ).all()
                    card_map = {card.id: card for card in cards if card.card_type in ALLOWED_CARD_TYPES}
                card_name_to_id = {
                    (card.name, card.card_type): card.id
                    for card in card_map.values()
                }
                storyboard_subject_map = _build_subject_detail_map(data.get("subjects", []))

                # ✅ 格式化shots数据，将dialogue和narration转换为前端期望的字符串格式
                formatted_shots = []
                for shot in data.get("shots", []):
                    formatted_shot = shot.copy()

                    # ✅ 添加数据库ID
                    shot_number = shot.get('shot_number')
                    if shot_number and shot_number in shot_id_map:
                        formatted_shot['id'] = shot_id_map[shot_number]

                    # ✅ 用数据库中最新的 selected_card_ids 替换旧数据，并转换为 subjects 数组
                    if shot_number and shot_number in shot_card_ids_map:
                        selected_card_ids_json = shot_card_ids_map[shot_number]
                        formatted_shot['selected_card_ids'] = selected_card_ids_json

                        # ✅ 将 selected_card_ids 转换为 subjects 数组（前端渲染需要）
                        try:
                            selected_ids = json.loads(selected_card_ids_json or "[]")
                            shot_subjects = []
                            for card_id in selected_ids:
                                if card_id in card_map:
                                    card = card_map[card_id]
                                    shot_subjects.append({
                                        "name": card.name,
                                        "type": card.card_type
                                    })
                            fallback_subjects = _reconcile_storyboard_shot_subjects(
                                formatted_shot,
                                storyboard_subject_map,
                            )
                            existing_subject_keys = {
                                ((subject.get("name") or "").strip(), (subject.get("type") or "角色").strip() or "角色")
                                for subject in shot_subjects
                                if isinstance(subject, dict)
                            }
                            merged_selected_ids = list(selected_ids)
                            for fallback_subject in fallback_subjects:
                                fallback_key = (
                                    (fallback_subject.get("name") or "").strip(),
                                    (fallback_subject.get("type") or "角色").strip() or "角色",
                                )
                                if fallback_key in existing_subject_keys:
                                    continue
                                existing_subject_keys.add(fallback_key)
                                shot_subjects.append({
                                    "name": fallback_key[0],
                                    "type": fallback_key[1],
                                })
                                fallback_card_id = card_name_to_id.get(fallback_key)
                                if fallback_card_id and fallback_card_id not in merged_selected_ids:
                                    merged_selected_ids.append(fallback_card_id)
                            if merged_selected_ids != selected_ids:
                                formatted_shot['selected_card_ids'] = json.dumps(merged_selected_ids, ensure_ascii=False)
                            formatted_shot['subjects'] = shot_subjects
                        except Exception as e:
                            print(f"[获取分镜表] 转换 selected_card_ids 失败: {str(e)}")
                            # 保留原有的 subjects（如果有的话）
                            if 'subjects' not in formatted_shot:
                                formatted_shot['subjects'] = []
                    elif 'subjects' not in formatted_shot:
                        formatted_shot['subjects'] = _reconcile_storyboard_shot_subjects(
                            formatted_shot,
                            storyboard_subject_map,
                        )

                    # 格式化dialogue字段为可读字符串（同时保留原始配音字段）
                    voice_type = shot.get('voice_type', 'none')

                    # ✅ 保留原始配音字段
                    formatted_shot['voice_type'] = shot.get('voice_type')
                    formatted_shot['narration'] = shot.get('narration')
                    formatted_shot['dialogue_array'] = shot.get('dialogue')  # 原始对白数组

                    # 格式化为可读字符串（用于表格显示）
                    if voice_type == 'narration':
                        narration = shot.get('narration')
                        if narration and isinstance(narration, dict):
                            speaker = narration.get('speaker', '')
                            gender = narration.get('gender', '')
                            emotion = narration.get('emotion', '')
                            text = narration.get('text', '')
                            formatted_shot['dialogue'] = f"旁白（{speaker}/{gender}/{emotion}）：{text}"
                        else:
                            formatted_shot['dialogue'] = ""

                    elif voice_type == 'dialogue':
                        dialogue = shot.get('dialogue')
                        if dialogue and isinstance(dialogue, list):
                            dialogue_lines = []
                            for d in dialogue:
                                speaker = d.get('speaker', '')
                                gender = d.get('gender', '')
                                target = d.get('target')
                                emotion = d.get('emotion', '')
                                text = d.get('text', '')
                                if target:
                                    dialogue_lines.append(f"{speaker}（{gender}）对{target}说（{emotion}）：{text}")
                                else:
                                    dialogue_lines.append(f"{speaker}（{gender}）说（{emotion}）：{text}")
                            formatted_shot['dialogue'] = '\n'.join(dialogue_lines)
                        else:
                            formatted_shot['dialogue'] = ""

                    else:
                        # voice_type为none或其他值时，dialogue应该为空
                        if not isinstance(shot.get('dialogue'), str):
                            formatted_shot['dialogue'] = ""

                    formatted_shots.append(formatted_shot)

                # ✅ 返回最新的subjects列表（包含用户后来添加的主体）
                return {
                    "shots": formatted_shots,
                    "subjects": subjects,  # 使用从数据库查询的最新subjects
                    "generating": episode.storyboard_generating or False,
                    "error": episode.storyboard_error or ""
                }
        except json.JSONDecodeError:
            # JSON解析失败，继续使用后备方案
            pass

    # ❌ 后备方案：从storyboard_shots表重建（会丢失voice_type、narration等数据）
    shots_records = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id,
    ).order_by(
        models.StoryboardShot.shot_number.asc(),
        models.StoryboardShot.variant_index.asc()
    ).all()

    # ✅ 使用前面已经查询的subjects和library（避免重复查询）
    card_map = {}
    if library:
        cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library.id
        ).all()
        cards = [card for card in cards if card.card_type in ALLOWED_CARD_TYPES]
        card_map = {card.id: card for card in cards}

    # 构建shots列表（转换为前端期望的格式）
    shots = []
    seen_shot_numbers = set()  # ✅ 用于去重，确保每个镜头号只显示一次

    for shot_record in shots_records:
        # 只显示主镜头（variant_index=0）
        if shot_record.variant_index != 0:
            continue

        # ✅ 跳过已经处理过的镜头号（去重）
        if shot_record.shot_number in seen_shot_numbers:
            continue
        seen_shot_numbers.add(shot_record.shot_number)

        # 解析selected_card_ids
        try:
            selected_ids = json.loads(shot_record.selected_card_ids or "[]")
        except:
            selected_ids = []

        # 构建subjects数组
        shot_subjects = []
        for card_id in selected_ids:
            if card_id in card_map:
                card = card_map[card_id]
                shot_subjects.append({
                    "name": card.name,
                    "type": card.card_type
                })

        shots.append({
            "shot_number": str(shot_record.shot_number),
            "subjects": shot_subjects,
            "original_text": shot_record.script_excerpt or "",
            "dialogue": shot_record.storyboard_dialogue or "",
            "storyboard_prompt": shot_record.sora_prompt or ""
        })

    return {
        "shots": shots,
        "subjects": subjects,
        "generating": episode.storyboard_generating or False,
        "error": episode.storyboard_error or ""
    }

@router.get("/api/episodes/{episode_id}/storyboard/status")

def get_episode_storyboard_status(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    episode = _verify_episode_permission(episode_id, user, db)
    return {
        "generating": bool(episode.storyboard_generating),
        "error": episode.storyboard_error or "",
        "shots_count": _count_storyboard_items(episode.storyboard_data),
    }

def _sync_subjects_to_database(episode_id: int, storyboard_data: dict, db: Session):
    """
    从分镜表JSON中提取所有主体，同步到SubjectCard表，并更新镜头的selected_card_ids

    此函数会：
    1. 从所有镜头中收集所有主体
    2. 去重（按名称和类型）
    3. 创建数据库中不存在的主体卡片
    4. ✅ 更新每个镜头的selected_card_ids，关联主体ID

    Args:
        episode_id: 片段ID
        storyboard_data: 分镜表JSON数据（dict格式）
        db: 数据库会话
    """
    try:
        # 获取episode和script
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if not episode:
            return

        script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
        if not script:
            return

        # 获取主体库
        library = db.query(models.StoryLibrary).filter(
            models.StoryLibrary.episode_id == episode.id
        ).first()
        if not library:
            print(f"[同步主体] 警告：找不到剧集 {episode.id} 的主体库")
            return

        # 从主体列表和镜头中收集主体（去重）
        all_subjects = _build_subject_detail_map(storyboard_data.get("subjects", []))
        shots = storyboard_data.get("shots", [])
        reconciled_shots = []

        for shot in shots:
            if not isinstance(shot, dict):
                continue
            shot_copy = dict(shot)
            shot_copy["subjects"] = _reconcile_storyboard_shot_subjects(
                shot_copy,
                all_subjects,
            )
            reconciled_shots.append(shot_copy)

        shots = reconciled_shots

        for shot in shots:
            subjects = shot.get("subjects", [])
            if not isinstance(subjects, list):
                continue

            for subj in subjects:
                if not isinstance(subj, dict):
                    continue

                name = (subj.get("name") or "").strip()
                subject_type = (subj.get("type") or "角色").strip() or "角色"

                if not name:
                    continue

                if subject_type not in ALLOWED_CARD_TYPES:
                    continue

                key = (name, subject_type)
                if key not in all_subjects:
                    all_subjects[key] = {
                        "name": name,
                        "type": subject_type,
                        "alias": "",
                        "ai_prompt": "",
                        "role_personality": ""
                    }

        if not all_subjects:
            print(f"[同步主体] 没有发现新主体")
            return

        print(f"[同步主体] 从分镜表中提取到 {len(all_subjects)} 个唯一主体")

        # 获取数据库中已有的主体
        existing_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library.id
        ).all()
        existing_card_map = {(card.name, card.card_type): card for card in existing_cards}
        existing_names = {(card.name, card.card_type): card.id for card in existing_cards}

        updated_count = 0
        for key, subject_info in all_subjects.items():
            existing_card = existing_card_map.get(key)
            if not existing_card:
                continue

            changed = False
            alias = (subject_info.get("alias") or "").strip()
            ai_prompt = (subject_info.get("ai_prompt") or "").strip()
            role_personality = (subject_info.get("role_personality") or "").strip()

            if alias and alias != (existing_card.alias or ""):
                existing_card.alias = alias
                changed = True
            if ai_prompt and ai_prompt != (existing_card.ai_prompt or ""):
                existing_card.ai_prompt = ai_prompt
                changed = True
            if existing_card.card_type == "角色" and role_personality and role_personality != (getattr(existing_card, "role_personality", "") or ""):
                existing_card.role_personality = role_personality
                changed = True

            if changed:
                updated_count += 1

        # 创建不存在的主体
        created_count = 0
        for key, subject_info in all_subjects.items():
            if key in existing_names:
                continue

            new_card = models.SubjectCard(
                library_id=library.id,
                name=subject_info["name"],
                card_type=subject_info["type"],
                alias=subject_info.get("alias", ""),
                ai_prompt=subject_info.get("ai_prompt", ""),
                role_personality=subject_info.get("role_personality", "") if subject_info["type"] == "角色" else ""
            )
            db.add(new_card)
            db.flush()  # ✅ 刷新以获取新ID
            existing_names[key] = new_card.id
            existing_card_map[key] = new_card
            created_count += 1
            print(f"[同步主体] 创建新主体: {subject_info['name']} ({subject_info['type']}) - ID: {new_card.id}")

        if created_count > 0 or updated_count > 0:
            db.commit()
            print(f"[同步主体] 成功创建 {created_count} 个新主体卡片，更新 {updated_count} 个主体卡片")
        else:
            print(f"[同步主体] 所有主体已存在，无需创建")

        # ✅ 更新每个镜头的 selected_card_ids
        updated_shots = 0
        for shot in shots:
            shot_number = shot.get("shot_number")
            if not shot_number:
                continue

            subjects = shot.get("subjects", [])
            if not isinstance(subjects, list):
                continue

            # 将主体名称转换为ID列表
            card_ids = []
            for subj in subjects:
                if not isinstance(subj, dict):
                    continue

                name = (subj.get("name") or "").strip()
                subject_type = (subj.get("type") or "角色").strip() or "角色"

                if not name:
                    continue

                key = (name, subject_type)
                if key in existing_names:
                    card_ids.append(existing_names[key])

            # 更新数据库中的 storyboard_shots 表
            shot_record = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.episode_id == episode_id,
                models.StoryboardShot.shot_number == shot_number,
                models.StoryboardShot.variant_index == 0
            ).first()

            if shot_record:
                shot_record.selected_card_ids = json.dumps(card_ids)
                updated_shots += 1

        if updated_shots > 0:
            db.commit()
            print(f"[同步主体] 成功更新 {updated_shots} 个镜头的 selected_card_ids")

    except Exception as e:
        print(f"[同步主体] 错误: {str(e)}")
        import traceback
        traceback.print_exc()
        db.rollback()

def _sync_storyboard_to_shots(episode_id: int, new_storyboard_data: dict, old_storyboard_data: dict, db: Session):
    """
    将分镜表JSON同步到StoryboardShot表（和旧 JSON 比对）

    参数：
        episode_id: 片段ID
        new_storyboard_data: 新的分镜表数据
        old_storyboard_data: 旧的分镜表数据（用于比对）
        db: 数据库会话

    规则：
    1. 修改的镜头：
       - video_status in ["processing", "completed"] → 创建新变体
       - 否则 → 直接更新
    2. 删除的镜头：
       - video_status in ["processing", "completed"] → 保留
       - 否则 → 删除
    3. 新增的镜头：创建新镜头（variant_index=0）
    """
    try:
        # 获取episode和主体库信息
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if not episode:
            return

        script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
        if not script:
            return

        library = db.query(models.StoryLibrary).filter(
            models.StoryLibrary.episode_id == episode.id
        ).first()
        if not library:
            return

        # ✅ 从旧的 JSON 数据读取镜头信息（用于比对）
        old_shots_dict_by_id = {}  # ✅ 按 ID 索引（优先）
        if old_storyboard_data:
            old_shots = old_storyboard_data.get("shots", [])
            for old_shot in old_shots:
                shot_id = old_shot.get("id")
                if shot_id:
                    old_shots_dict_by_id[shot_id] = old_shot

        # 获取主体名称到ID的映射
        existing_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library.id
        ).all()
        card_name_to_id = {(card.name, card.card_type): card.id for card in existing_cards}

        # 获取现有的镜头（所有变体）
        existing_shots = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.episode_id == episode_id
        ).all()

        # ✅ 按数据库ID索引
        existing_shots_by_id = {shot.id: shot for shot in existing_shots}
        # 按stable_id索引（用于变体分组）
        existing_shots_by_stable_id = {}
        for shot in existing_shots:
            if shot.stable_id:
                if shot.stable_id not in existing_shots_by_stable_id:
                    existing_shots_by_stable_id[shot.stable_id] = []
                existing_shots_by_stable_id[shot.stable_id].append(shot)

        # 处理分镜表中的每个镜头
        new_shots = new_storyboard_data.get("shots", [])
        processed_ids = set()  # ✅ 跟踪已处理的数据库ID

        for new_shot in new_shots:
            shot_number_str = new_shot.get("shot_number", "")
            try:
                shot_number = int(shot_number_str)
            except:
                continue

            # ✅ 获取数据库ID和stable_id
            shot_id = new_shot.get("id")
            stable_id = new_shot.get("stable_id")

            if shot_id:
                processed_ids.add(shot_id)

            # 解析主体ID列表
            new_subjects = new_shot.get("subjects", [])
            selected_card_ids = []
            for subj in new_subjects:
                if not isinstance(subj, dict):
                    continue
                name = (subj.get("name") or "").strip()
                subject_type = (subj.get("type") or "角色").strip() or "角色"
                if name:
                    key = (name, subject_type)
                    if key in card_name_to_id:
                        selected_card_ids.append(card_name_to_id[key])

            # 构建新数据
            new_script_excerpt = (new_shot.get("original_text") or "").strip()
            new_dialogue = (new_shot.get("dialogue_text") or "").strip()  # ✅ 使用dialogue_text（表格中的台词字符串）
            new_sora_prompt = new_script_excerpt  # 初始值 = 原剧本段落

            # ✅ 通过ID匹配数据库记录
            if shot_id and shot_id in existing_shots_by_id:
                # 找到了现有记录，更新它
                db_record = existing_shots_by_id[shot_id]

                # ✅ 通过ID在旧JSON中找到旧数据，用于比对
                old_shot = old_shots_dict_by_id.get(shot_id)

                is_modified = False
                if old_shot:
                    # 比较内容
                    old_original_text = (old_shot.get("original_text") or "").strip()
                    old_dialogue = (old_shot.get("dialogue_text") or "").strip()  # ✅ 使用dialogue_text

                    if new_script_excerpt != old_original_text or new_dialogue != old_dialogue:
                        is_modified = True

                # 检查是否有视频
                has_video = db_record.video_status in ["processing", "completed"]

                if is_modified and has_video:
                    # ✅ 检查是否已经有相同内容的变体存在
                    variants = existing_shots_by_stable_id.get(db_record.stable_id, [])

                    # 查找是否有变体的内容和新内容相同
                    existing_variant_with_same_content = None
                    for v in variants:
                        if v.variant_index > 0:  # 只检查变体
                            v_excerpt = (v.script_excerpt or "").strip()
                            v_dialogue = (v.storyboard_dialogue or "").strip()
                            if v_excerpt == new_script_excerpt and v_dialogue == new_dialogue:
                                existing_variant_with_same_content = v
                                break

                    if existing_variant_with_same_content:
                        # 已经有相同内容的变体，不创建新变体，只更新shot_number
                        print(f"[同步镜头] 镜头{shot_number}已有相同内容的变体 (id={existing_variant_with_same_content.id})，不重复创建")
                    else:
                        # 创建新变体
                        max_variant = max((v.variant_index for v in variants), default=0)

                        new_variant = models.StoryboardShot(
                            **build_storyboard_sync_variant_payload(
                                db_record,
                                next_variant=max_variant + 1,
                                script_excerpt=new_script_excerpt,
                                storyboard_dialogue=new_dialogue,
                                selected_card_ids=json.dumps(selected_card_ids),
                                sora_prompt=new_sora_prompt,
                            )
                        )
                        db.add(new_variant)
                        print(f"[同步镜头] 镜头{shot_number}已有视频，创建新变体 (id={shot_id})")
                else:
                    # 直接更新
                    db_record.shot_number = shot_number
                    db_record.script_excerpt = new_script_excerpt
                    db_record.storyboard_dialogue = new_dialogue
                    db_record.selected_card_ids = json.dumps(selected_card_ids)
                    # ✅ 只有在内容修改时，才重置 sora_prompt（保护已生成的提示词）
                    if is_modified:
                        db_record.sora_prompt = new_sora_prompt
                        db_record.sora_prompt_status = "idle"
                    print(f"[同步镜头] 更新镜头{shot_number} (id={shot_id})")

                    # ✅ 同时更新所有变体的shot_number
                    if db_record.stable_id and db_record.stable_id in existing_shots_by_stable_id:
                        for variant in existing_shots_by_stable_id[db_record.stable_id]:
                            if variant.id != db_record.id:  # 不重复更新主镜头
                                variant.shot_number = shot_number
                                print(f"[同步镜头] 更新变体镜头{shot_number}_{variant.variant_index} (id={variant.id})")
            else:
                # 新镜头，创建记录
                if not stable_id:
                    stable_id = str(uuid.uuid4())

                new_record = models.StoryboardShot(
                    episode_id=episode_id,
                    shot_number=shot_number,
                    stable_id=stable_id,
                    variant_index=0,
                    script_excerpt=new_script_excerpt,
                    storyboard_dialogue=new_dialogue,
                    selected_card_ids=json.dumps(selected_card_ids),
                    selected_sound_card_ids=None,
                    sora_prompt=new_sora_prompt,
                    aspect_ratio='16:9',
                    duration=15,
                    storyboard_video_model="",
                    storyboard_video_model_override_enabled=False,
                    duration_override_enabled=False,
                    prompt_template='',
                    video_status='idle',
                    sora_prompt_status='idle'
                )
                db.add(new_record)
                print(f"[同步镜头] 创建新镜头{shot_number} (stable_id={stable_id})")

        # ✅ 处理删除：只删除主镜头（variant_index=0）如果它不在JSON中
        # 变体镜头由stable_id关联，只要主镜头还在就保留
        for shot in existing_shots:
            should_delete = False

            # 只处理主镜头
            if shot.variant_index == 0:
                # 如果主镜头的ID不在processed_ids中，说明被删除了
                if shot.id not in processed_ids:
                    should_delete = True

                    if should_delete:
                        # 检查是否有视频
                        has_video = shot.video_status in ["processing", "completed"]

                        if not has_video:
                            # 没有视频，删除主镜头及其所有变体
                            db.delete(shot)
                            print(f"[同步镜头] 删除镜头{shot.shot_number} (id={shot.id}，未生成视频)")

                            # 同时删除所有变体
                            if shot.stable_id and shot.stable_id in existing_shots_by_stable_id:
                                for variant in existing_shots_by_stable_id[shot.stable_id]:
                                    if variant.id != shot.id:
                                        db.delete(variant)
                                        print(f"[同步镜头] 删除变体镜头{variant.shot_number}_{variant.variant_index} (id={variant.id})")
                        else:
                            print(f"[同步镜头] 保留镜头{shot.shot_number} (id={shot.id}，已生成视频)")
            # 变体镜头不处理，由主镜头决定是否删除

        db.commit()
        print(f"[同步镜头] 同步完成")

    except Exception as e:
        print(f"[同步镜头] 错误: {str(e)}")
        import traceback
        traceback.print_exc()
        db.rollback()

def _analyze_storyboard_changes(episode_id: int, new_storyboard_data: dict, db: Session):
    """
    分析分镜表变更（和原始 JSON 数据比对）

    返回格式：
    {
        "modified": [{"shot_number": 1, "reason": "修改了原剧本段落", "has_video": True}, ...],
        "deleted": [{"shot_number": 2, "has_video": False}, ...],
        "added": [3, 4, ...]
    }
    """
    new_shots = new_storyboard_data.get("shots", [])

    # 获取episode
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        return {"modified": [], "deleted": [], "added": []}

    # ✅ 从 Episode.storyboard_data（JSON）读取旧数据
    old_shots = []
    if episode.storyboard_data:
        try:
            old_data = json.loads(episode.storyboard_data)
            old_shots = old_data.get("shots", [])
        except:
            pass

    # 如果没有旧数据，说明是第一次保存，所有镜头都是新增
    if not old_shots:
        added = []
        for new_shot in new_shots:
            try:
                shot_number = int(new_shot.get("shot_number", 0))
                if shot_number > 0:
                    added.append(shot_number)
            except:
                pass
        return {"modified": [], "deleted": [], "added": added}

    # 构建旧数据的字典（按 shot_number 索引）
    old_shots_dict = {}
    for old_shot in old_shots:
        shot_number_str = old_shot.get("shot_number", "")
        try:
            shot_number = int(shot_number_str)
            old_shots_dict[shot_number] = old_shot
        except:
            continue

    # 查询数据库中的镜头（用于判断是否有视频）
    all_existing_shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id
    ).all()

    # 按shot_number分组
    shots_video_status = {}  # {shot_number: has_video}
    for shot in all_existing_shots:
        if shot.shot_number not in shots_video_status:
            shots_video_status[shot.shot_number] = False
        # 只要有任意一个变体有视频，就标记为 True
        if shot.video_status in ["processing", "completed"]:
            shots_video_status[shot.shot_number] = True

    new_shot_numbers = set()
    modified = []
    added = []

    # 检查修改和新增
    for new_shot in new_shots:
        shot_number_str = new_shot.get("shot_number", "")
        try:
            shot_number = int(shot_number_str)
        except:
            continue

        new_shot_numbers.add(shot_number)

        if shot_number in old_shots_dict:
            # 镜头已存在，比较内容
            old_shot = old_shots_dict[shot_number]
            changes = []

            # 1. 比较原剧本段落
            new_original_text = new_shot.get("original_text", "").strip()
            old_original_text = old_shot.get("original_text", "").strip()
            if new_original_text != old_original_text:
                changes.append("原剧本段落")

            # 2. 比较对白
            new_dialogue = new_shot.get("dialogue", "").strip()
            old_dialogue = old_shot.get("dialogue", "").strip()
            if new_dialogue != old_dialogue:
                changes.append("对白")

            # 3. 角色/场景的修改不算作修改（已移除比较逻辑）

            # 如果有变更
            if changes:
                has_video = shots_video_status.get(shot_number, False)
                modified.append({
                    "shot_number": shot_number,
                    "reason": "、".join(changes),
                    "has_video": has_video
                })
        else:
            # 新增镜头
            added.append(shot_number)

    # 检查删除
    deleted = []
    for shot_number, old_shot in old_shots_dict.items():
        if shot_number not in new_shot_numbers:
            has_video = shots_video_status.get(shot_number, False)
            deleted.append({
                "shot_number": shot_number,
                "has_video": has_video
            })

    return {
        "modified": modified,
        "deleted": deleted,
        "added": added
    }

@router.put("/api/episodes/{episode_id}/storyboard")

async def update_episode_storyboard(
    episode_id: int,
    request: dict,
    analyze_only: bool = False,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """保存编辑后的分镜表数据"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 如果只是分析变更，不保存
    if analyze_only:
        changes = _analyze_storyboard_changes(episode_id, request, db)
        return {"analyze_only": True, "changes": changes}

    # ✅ 先读取旧的 storyboard_data（用于比对）
    old_storyboard_data = None
    if episode.storyboard_data:
        try:
            old_storyboard_data = json.loads(episode.storyboard_data)
        except:
            pass

    subject_fallbacks = _build_subject_detail_map((old_storyboard_data or {}).get("subjects", []))
    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == episode.id
    ).first()
    if library:
        existing_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library.id,
            models.SubjectCard.card_type.in_(ALLOWED_CARD_TYPES)
        ).all()
        for card in existing_cards:
            key = (card.name, card.card_type)
            fallback = subject_fallbacks.get(key, {})
            subject_fallbacks[key] = _normalize_subject_detail_entry({
                "name": card.name,
                "type": card.card_type,
                "alias": card.alias or "",
                "ai_prompt": card.ai_prompt or "",
                "role_personality": getattr(card, "role_personality", "") or ""
            }, fallback)

    incoming_subject_map = _build_subject_detail_map(request.get("subjects", []))
    if incoming_subject_map:
        merged_subjects = []
        for key, incoming_subject in incoming_subject_map.items():
            merged_subject = _normalize_subject_detail_entry(incoming_subject, subject_fallbacks.get(key))
            if merged_subject:
                merged_subjects.append(merged_subject)
        request["subjects"] = merged_subjects
    elif subject_fallbacks:
        request["subjects"] = list(subject_fallbacks.values())

    canonical_subject_map = _build_subject_detail_map(request.get("subjects", []))
    if canonical_subject_map and isinstance(request.get("shots"), list):
        reconciled_shots = []
        for shot in request.get("shots", []):
            if not isinstance(shot, dict):
                continue
            shot_copy = dict(shot)
            shot_copy["subjects"] = _reconcile_storyboard_shot_subjects(
                shot_copy,
                canonical_subject_map,
            )
            reconciled_shots.append(shot_copy)
        request["shots"] = reconciled_shots

    # ✅ 保存新的分镜表数据
    episode.storyboard_data = json.dumps(request, ensure_ascii=False)

    # ✅ 同步配音数据到 voiceover_data（基础字段来自分镜，保留已有扩展字段）
    voiceover_shots = []
    for shot in request.get("shots", []):
        voiceover_shots.append({
            "shot_number": shot.get("shot_number"),
            "voice_type": shot.get("voice_type"),
            "narration": shot.get("narration"),
            "dialogue": shot.get("dialogue")
        })

    merged_voiceover_data = _merge_voiceover_shots_preserving_extensions(
        episode.voiceover_data,
        voiceover_shots
    )
    episode.voiceover_data = json.dumps(merged_voiceover_data, ensure_ascii=False)
    print(f"[保存分镜表] 同步更新了配音表数据，共 {len(voiceover_shots)} 个镜头")

    db.commit()

    # ✅ 同步新主体到数据库
    _sync_subjects_to_database(episode_id, request, db)

    # ✅ 同步到StoryboardShot表（传入旧数据）
    _sync_storyboard_to_shots(episode_id, request, old_storyboard_data, db)

    return {"message": "分镜表已保存", "success": True}

@router.post("/api/episodes/{episode_id}/create-from-storyboard")

async def create_from_storyboard(
    episode_id: int,
    request: Optional[CreateStoryboardRequest] = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """根据分镜表JSON创建主体和镜头（统一使用辅助函数）"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    if not episode.storyboard_data:
        raise HTTPException(status_code=400, detail="没有分镜表数据")

    # ✅ 调用统一的辅助函数
    try:
        storyboard = json.loads(episode.storyboard_data)
        shots_count = len(storyboard.get("shots", []))
        subjects_count = len(storyboard.get("subjects", []))

        _create_shots_from_storyboard_data(episode_id, db)

        return {
            "message": "创建成功",
            "created_subjects": subjects_count,
            "created_shots": shots_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建失败: {str(e)}")

def _resolve_selected_cards(
    db: Session,
    selected_ids: List[int],
    library_id: Optional[int] = None
) -> List[models.SubjectCard]:
    """Resolve selected subject cards in selected_ids order."""
    if not selected_ids:
        return []

    query = db.query(models.SubjectCard).filter(
        models.SubjectCard.id.in_(selected_ids)
    )
    if library_id is not None:
        query = query.filter(models.SubjectCard.library_id == library_id)

    cards = query.all()
    card_map = {card.id: card for card in cards if card}
    return [card_map[card_id] for card_id in selected_ids if card_id in card_map]

def _build_subject_text_for_ai(selected_cards: List[models.SubjectCard]) -> str:
    """Build subject_text for Sora prompt generation with protagonist support."""
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

def _build_storyboard2_subject_text(selected_cards: List[models.SubjectCard]) -> str:
    """Build candidate subject text for storyboard2 prompts with role personality context."""
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

def _resolve_large_shot_template(
    db: Session,
    template_id: Optional[int] = None
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
        models.LargeShotTemplate.id.asc()
    ).first()

def _append_sora_reference_prompt(base_prompt: str, reference_prompt: str) -> str:
    clean_base = str(base_prompt or "").strip()
    clean_reference = str(reference_prompt or "").strip()
    if not clean_reference:
        return clean_base
    reference_block = f"{SORA_REFERENCE_PROMPT_INSTRUCTION}{clean_reference}"
    if not clean_base:
        return reference_block
    return f"{clean_base}\n\n{reference_block}"

def _resolve_sora_reference_prompt(
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

def _build_storyboard_prompt_request_data(
    db: Session,
    *,
    shot: models.StoryboardShot,
    episode: models.Episode,
    script: models.Script,
    prompt_key: str = "generate_video_prompts",
    duration_template_field: str = "video_prompt_rule",
    large_shot_template_id: Optional[int] = None,
    reference_shot_id: Optional[int] = None,
):
    storyboard2_prompt_key = "generate_storyboard2_video_prompts"
    effective_video_settings = _apply_episode_storyboard_video_settings_to_shot(shot, episode)
    safe_duration = max(1, int(effective_video_settings["duration"] or 15))
    template_duration = 15 if safe_duration <= 15 else 25

    selected_ids = []
    try:
        selected_ids = json.loads(shot.selected_card_ids or "[]")
    except Exception:
        selected_ids = []

    selected_cards = _resolve_selected_cards(db, selected_ids)
    subject_names = [card.name for card in selected_cards if card and card.name]
    subject_text = _build_subject_text_for_ai(selected_cards)
    scene_text = (shot.scene_override or "").strip()
    custom_style = (script.sora_prompt_style or "").strip()
    template_field = (duration_template_field or "video_prompt_rule").strip() or "video_prompt_rule"
    excerpt = (shot.script_excerpt or "").strip()
    if not excerpt:
        raise ValueError("请先填写原剧本段落")

    large_shot_template_content = ""
    large_shot_template_name = ""
    if prompt_key == "generate_large_shot_prompts":
        large_shot_template = _resolve_large_shot_template(db, large_shot_template_id)
        if not large_shot_template:
            raise ValueError("大镜头模板不存在")
        large_shot_template_id = large_shot_template.id
        large_shot_template_name = (large_shot_template.name or "").strip()
        large_shot_template_content = (large_shot_template.content or "").strip()

    if custom_style:
        template_for_format = custom_style
        if prompt_key == "generate_large_shot_prompts":
            template_for_format = inject_large_shot_template_content(template_for_format, large_shot_template_content)
        try:
            prompt = template_for_format.format(
                script_excerpt=excerpt,
                scene_description=scene_text,
                subject_text=subject_text,
                safe_duration=safe_duration,
                extra_style="",
                large_shot_template_content=large_shot_template_content
            )
        except KeyError:
            prompt = template_for_format
    else:
        use_duration_template = prompt_key != storyboard2_prompt_key
        if use_duration_template:
            template = db.query(models.ShotDurationTemplate).filter(
                models.ShotDurationTemplate.duration == template_duration
            ).first()
            template_rule = str(getattr(template, template_field, "") or "").strip() if template else ""
            prompt_template = template_rule or get_prompt_by_key(prompt_key)
        else:
            prompt_template = get_prompt_by_key(prompt_key)
        template_for_format = prompt_template
        if prompt_key == "generate_large_shot_prompts":
            template_for_format = inject_large_shot_template_content(template_for_format, large_shot_template_content)
        prompt = template_for_format.format(
            script_excerpt=excerpt,
            scene_description=scene_text,
            subject_text=subject_text,
            safe_duration=safe_duration,
            extra_style="",
            large_shot_template_content=large_shot_template_content
        )

    reference_prompt = _resolve_sora_reference_prompt(db, episode.id, reference_shot_id)
    prompt = _append_sora_reference_prompt(prompt, reference_prompt)

    config = get_ai_config("video_prompt")
    request_data = {
        "model": config["model"],
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    task_payload = {
        "shot_id": int(shot.id),
        "episode_id": int(episode.id),
        "prompt_key": str(prompt_key or "generate_video_prompts"),
        "duration_template_field": template_field,
        "large_shot_template_id": int(large_shot_template_id or 0) if large_shot_template_id else None,
        "large_shot_template_name": large_shot_template_name,
        "large_shot_template_content": large_shot_template_content,
        "reference_shot_id": int(reference_shot_id or 0) if reference_shot_id else None,
    }
    return request_data, task_payload

def _refresh_episode_batch_sora_prompt_state(episode_id: int, db: Session):
    remaining = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id,
        models.StoryboardShot.sora_prompt_status == "generating",
    ).count()
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if episode:
        episode.batch_generating_prompts = remaining > 0

def _repair_stale_storyboard_prompt_generation(episode_id: int, db: Session) -> bool:
    shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id,
        models.StoryboardShot.sora_prompt_status == "generating",
    ).all()
    if not shots:
        return False

    shot_ids = [int(getattr(shot, "id", 0) or 0) for shot in shots]
    tasks = db.query(models.TextRelayTask).filter(
        models.TextRelayTask.task_type == "sora_prompt",
        models.TextRelayTask.owner_type == "shot",
        models.TextRelayTask.owner_id.in_(shot_ids),
    ).order_by(
        models.TextRelayTask.owner_id.asc(),
        models.TextRelayTask.id.desc(),
    ).all()

    latest_task_by_shot: Dict[int, models.TextRelayTask] = {}
    active_shot_ids = set()
    for task in tasks:
        shot_id = int(getattr(task, "owner_id", 0) or 0)
        if shot_id <= 0:
            continue
        if shot_id not in latest_task_by_shot:
            latest_task_by_shot[shot_id] = task
        if str(getattr(task, "status", "") or "").strip() in {"submitted", "queued", "running"}:
            active_shot_ids.add(shot_id)

    changed = False
    for shot in shots:
        shot_id = int(getattr(shot, "id", 0) or 0)
        if shot_id in active_shot_ids:
            continue

        next_status = ""
        latest_task = latest_task_by_shot.get(shot_id)
        latest_task_status = str(getattr(latest_task, "status", "") or "").strip() if latest_task else ""
        if latest_task_status == "succeeded":
            next_status = "completed"
        elif latest_task_status == "failed":
            next_status = "failed"
        else:
            has_prompt_content = bool(
                str(getattr(shot, "sora_prompt", "") or "").strip()
                or str(getattr(shot, "storyboard_video_prompt", "") or "").strip()
            )
            has_video_progress = str(getattr(shot, "video_status", "") or "").strip() in {
                "submitting",
                "preparing",
                "processing",
                "completed",
                "failed",
            }
            next_status = "completed" if (has_prompt_content or has_video_progress) else "failed"

        if str(getattr(shot, "sora_prompt_status", "") or "").strip() != next_status:
            shot.sora_prompt_status = next_status
            changed = True

    if changed:
        db.flush()

    return changed

def _reconcile_episode_runtime_flags(episode: Optional[models.Episode], db: Session) -> bool:
    if not episode:
        return False

    episode_id = int(getattr(episode, "id", 0) or 0)
    if episode_id <= 0:
        return False

    changed = False

    changed = _repair_stale_storyboard_prompt_generation(episode_id, db) or changed

    has_generating_sora_prompt = db.query(models.StoryboardShot.id).filter(
        models.StoryboardShot.episode_id == episode_id,
        models.StoryboardShot.sora_prompt_status == "generating",
    ).first() is not None
    if bool(getattr(episode, "batch_generating_prompts", False)) != has_generating_sora_prompt:
        episode.batch_generating_prompts = has_generating_sora_prompt
        changed = True

    simple_summary = _get_simple_storyboard_batch_summary(episode_id, db)
    simple_generating = bool(
        simple_summary.get("submitting_batches", 0) > 0
        or (
            simple_summary.get("total_batches", 0) > 0
            and simple_summary.get("completed_batches", 0) + simple_summary.get("failed_batches", 0)
            < simple_summary.get("total_batches", 0)
        )
    )
    if bool(getattr(episode, "simple_storyboard_generating", False)) != simple_generating:
        episode.simple_storyboard_generating = simple_generating
        changed = True

    if changed:
        db.flush()

    return changed

def _refresh_storyboard2_prompt_batch_state(episode_id: int, db: Session):
    pending_count = db.query(models.TextRelayTask).filter(
        models.TextRelayTask.task_type == "storyboard2_sora_prompt",
        models.TextRelayTask.status.in_(["submitted", "queued", "running"]),
    ).all()
    pending_task_ids = [int(row.owner_id or 0) for row in pending_count]
    active = False
    if pending_task_ids:
        active = db.query(models.Storyboard2Shot).filter(
            models.Storyboard2Shot.id.in_(pending_task_ids),
            models.Storyboard2Shot.episode_id == episode_id,
        ).count() > 0
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if episode:
        episode.batch_generating_storyboard2_prompts = active

def _submit_storyboard_prompt_task(
    db: Session,
    *,
    shot: models.StoryboardShot,
    episode: models.Episode,
    script: models.Script,
    prompt_key: str = "generate_video_prompts",
    duration_template_field: str = "video_prompt_rule",
    large_shot_template_id: Optional[int] = None,
    reference_shot_id: Optional[int] = None,
):
    request_data, task_payload = _build_storyboard_prompt_request_data(
        db,
        shot=shot,
        episode=episode,
        script=script,
        prompt_key=prompt_key,
        duration_template_field=duration_template_field,
        large_shot_template_id=large_shot_template_id,
        reference_shot_id=reference_shot_id,
    )
    return submit_and_persist_text_task(
        db,
        task_type="sora_prompt",
        owner_type="shot",
        owner_id=int(shot.id),
        stage_key=str(prompt_key or "video_prompt"),
        function_key="video_prompt",
        request_payload=request_data,
        task_payload=task_payload,
    )

def _submit_storyboard2_prompt_task(
    db: Session,
    *,
    storyboard2_shot: models.Storyboard2Shot,
):
    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == storyboard2_shot.episode_id
    ).first()
    all_subject_cards = []
    if library:
        all_subject_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library.id,
            models.SubjectCard.card_type.in_(ALLOWED_CARD_TYPES)
        ).all()
        all_subject_cards.sort(
            key=lambda card: (
                _subject_type_sort_key(card.card_type),
                (card.name or ""),
                card.id
            )
        )
    subject_names = [card.name for card in all_subject_cards if card and (card.name or "").strip()]
    subject_text = _build_storyboard2_subject_text(all_subject_cards)

    excerpt = (storyboard2_shot.excerpt or "").strip()
    if not excerpt:
        raise ValueError("镜头原文为空")

    source_shot = None
    if storyboard2_shot.source_shot_id:
        source_shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id == storyboard2_shot.source_shot_id
        ).first()
    duration = int(source_shot.duration or 10) if source_shot else 10
    if duration not in (10, 15):
        duration = 10 if duration < 13 else 15

    prompt_template = get_prompt_by_key(STORYBOARD2_VIDEO_PROMPT_KEY)
    prompt = prompt_template.format(
        script_excerpt=excerpt,
        scene_description="",
        subject_text=subject_text,
        safe_duration=duration,
        extra_style="",
    )
    config = get_ai_config("video_prompt")
    request_data = {
        "model": config["model"],
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    task_payload = {
        "episode_id": int(storyboard2_shot.episode_id),
        "storyboard2_shot_id": int(storyboard2_shot.id),
        "duration": int(duration),
        "subject_names": subject_names,
    }
    return submit_and_persist_text_task(
        db,
        task_type="storyboard2_sora_prompt",
        owner_type="storyboard2_shot",
        owner_id=int(storyboard2_shot.id),
        stage_key=STORYBOARD2_VIDEO_PROMPT_KEY,
        function_key="video_prompt",
        request_payload=request_data,
        task_payload=task_payload,
    )

@router.post("/api/episodes/{episode_id}/batch-generate-sora-prompts")

async def batch_generate_sora_prompts(
    episode_id: int,
    request: BatchGenerateSoraPromptsRequest,
    background_tasks: BackgroundTasks,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """批量生成Sora提示词（后台任务）"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 获取镜头数量（如果指定了shot_ids，则只计算这些镜头）
    if request.shot_ids:
        shot_count = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.episode_id == episode_id,
            models.StoryboardShot.id.in_(request.shot_ids)
        ).count()
    else:
        shot_count = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.episode_id == episode_id
        ).count()

    if shot_count == 0:
        raise HTTPException(status_code=400, detail="没有选择有效的镜头")

    print(
        f"[SoraSubjectDebug][batch_request] episode_id={episode_id} "
        f"requested_shot_ids={request.shot_ids if request.shot_ids else 'ALL'} "
        f"matched_shot_count={shot_count}"
    )

    # 清空选中镜头的旧sora_prompt，让前端显示"生成中"状态
    if request.shot_ids:
        shots_to_clear = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.episode_id == episode_id,
            models.StoryboardShot.id.in_(request.shot_ids)
        ).all()
    else:
        shots_to_clear = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.episode_id == episode_id
        ).all()

    for shot in shots_to_clear:
        old_prompt = shot.sora_prompt
        # 不清空 sora_prompt，保留原内容，只设置状态为生成中
        shot.sora_prompt_status = 'generating'  # 设置状态为生成中

    db.commit()
    print("批量生成：状态已设置为生成中，已提交到数据库")

    submitted_count = 0
    for shot in shots_to_clear:
        try:
            _submit_storyboard_prompt_task(
                db,
                shot=shot,
                episode=episode,
                script=script,
                prompt_key="generate_video_prompts",
                duration_template_field="video_prompt_rule",
                large_shot_template_id=None,
            )
            submitted_count += 1
        except Exception as exc:
            shot.sora_prompt_status = 'failed'
            print(f"[批量Sora提交失败] shot_id={shot.id} error={str(exc)}")

    _refresh_episode_batch_sora_prompt_state(episode_id, db)
    db.commit()

    return {
        "message": f"批量生成任务已提交，共 {submitted_count} 个镜头。",
        "total_count": shot_count,
        "submitted_count": submitted_count,
    }

def _do_batch_generate_sora_videos(
    episode_id: int,
    user_id: int,
    shot_ids: Optional[List[int]] = None,
    appoint_account: Optional[str] = None,
):
    """后台任务：批量生成Sora视频（并发提交任务）"""
    db = SessionLocal()
    try:
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if not episode:
            print(f"批量生成视频失败：片段 {episode_id} 不存在")
            return

        script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
        if script.user_id != user_id:
            print(f"批量生成视频失败：用户 {user_id} 无权限")
            return

        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            print(f"批量生成视频失败：用户 {user_id} 不存在")
            return

        query = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.episode_id == episode_id
        )
        if shot_ids:
            query = query.filter(models.StoryboardShot.id.in_(shot_ids))

        shots = query.order_by(models.StoryboardShot.shot_number, models.StoryboardShot.variant_index).all()
        if not shots:
            print(f"批量生成视频失败：片段 {episode_id} 没有镜头")
            return

        try:
            _ensure_storyboard_video_generation_slots_available(shots, db)
        except HTTPException as e:
            print(f"批量生成视频失败：{getattr(e, 'detail', str(e))}")
            return

        episode_settings = _get_episode_storyboard_video_settings(episode)
        effective_appoint_account = _normalize_storyboard_video_appoint_account(
            appoint_account,
            default_value=episode_settings.get("appoint_account", "")
        )
        for shot in shots:
            _apply_episode_storyboard_video_settings_to_shot(shot, episode)
            shot.video_status = 'submitting'
        db.commit()

        target_ids = [shot.id for shot in shots]

        def mark_failed(shot_id: int, reason: str):
            try:
                db.rollback()
                failed_shot = db.query(models.StoryboardShot).filter(
                    models.StoryboardShot.id == shot_id
                ).first()
                if failed_shot:
                    failed_shot.video_status = 'failed'
                    failed_shot.video_path = f"error:{reason}"
                    db.commit()
            except Exception as e:
                print(f"批量生成视频失败：镜头 {shot_id} 更新失败: {str(e)}")

        async def generate_single_video(shot_id: int):
            """处理单个镜头的视频生成（带错误处理）"""
            try:
                # 不使用 BackgroundTasks，直接在这里处理拼图生成和视频提交
                db_local = SessionLocal()
                try:
                    shot = db_local.query(models.StoryboardShot).filter(
                        models.StoryboardShot.id == shot_id
                    ).first()

                    if not shot:
                        print(f"[批量生成] 镜头 {shot_id} 不存在")
                        return

                    # 构建完整提示词
                    full_prompt = build_sora_prompt(shot, db_local)
                    if not full_prompt:
                        mark_failed(shot_id, "缺少Sora提示词")
                        return
                    selected_first_frame_image_url = _resolve_selected_first_frame_reference_image_url(
                        shot,
                        db_local,
                    )

                    print(f"[批量生成] 镜头 {shot_id} 提交视频生成任务...")
                    model_name = _resolve_storyboard_video_model_by_provider(
                        shot.provider,
                        default_model=getattr(shot, "storyboard_video_model", None) or episode_settings["model"]
                    )
                    request_data = _build_unified_storyboard_video_task_payload(
                        shot=shot,
                        db=db_local,
                        username=user.username,
                        model_name=model_name,
                        provider=shot.provider or episode_settings["provider"],
                        full_prompt=full_prompt,
                        aspect_ratio=shot.aspect_ratio,
                        duration=shot.duration,
                        first_frame_image_url=selected_first_frame_image_url,
                        resolution_name=episode_settings.get("resolution_name", ""),
                        appoint_account=effective_appoint_account,
                    )
                    submit_timeout = 60 if _is_moti_storyboard_video_model(model_name) else 30

                    submit_response = requests.post(
                        get_video_task_create_url(),
                        headers=get_video_api_headers(),
                        json=request_data,
                        timeout=submit_timeout
                    )

                    if submit_response.status_code != 200:
                        error_msg = f"视频请求失败: {submit_response.status_code}"
                        mark_failed(shot_id, error_msg)
                        return

                    submit_result = submit_response.json()
                    task_id = submit_result.get('task_id')

                    if not task_id:
                        error_msg = f"视频返回异常: {submit_result.get('message', '未知错误')}"
                        mark_failed(shot_id, error_msg)
                        return

                    shot.task_id = task_id
                    shot.video_status = 'processing'
                    shot.video_submitted_at = datetime.utcnow()
                    _record_storyboard_video_charge(
                        db_local,
                        shot=shot,
                        task_id=task_id,
                        stage="video_generate",
                        detail_payload={
                            "source": "batch_generate",
                            "provider": request_data.get("provider"),
                            "model": request_data.get("model"),
                        },
                    )
                    db_local.commit()
                    print(f"[批量生成] 镜头 {shot_id} 视频任务已提交: {task_id}")

                finally:
                    db_local.close()

            except HTTPException as e:
                mark_failed(shot_id, getattr(e, "detail", str(e)))
            except Exception as e:
                mark_failed(shot_id, str(e))

        async def run_batch():
            # 并发执行所有镜头的视频生成
            tasks = [generate_single_video(shot_id) for shot_id in target_ids]
            await asyncio.gather(*tasks, return_exceptions=True)
            print(f"批量生成完成：共处理 {len(target_ids)} 个镜头")

        asyncio.run(run_batch())

    except Exception as e:
        print(f"批量生成视频出错: {str(e)}")
    finally:
        db.close()

@router.post("/api/episodes/{episode_id}/batch-generate-sora-videos")

async def batch_generate_sora_videos(
    episode_id: int,
    request: BatchGenerateSoraVideosRequest,
    background_tasks: BackgroundTasks,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """批量生成Sora视频（后台任务）"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    if request.shot_ids:
        shot_count = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.episode_id == episode_id,
            models.StoryboardShot.id.in_(request.shot_ids)
        ).count()
    else:
        shot_count = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.episode_id == episode_id
        ).count()

    if shot_count == 0:
        raise HTTPException(status_code=400, detail="没有选择有效的镜头")

    target_query = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id
    )
    if request.shot_ids:
        target_query = target_query.filter(models.StoryboardShot.id.in_(request.shot_ids))
    target_shots = target_query.all()
    _ensure_storyboard_video_generation_slots_available(target_shots, db)

    background_tasks.add_task(
        _do_batch_generate_sora_videos,
        episode_id,
        user.id,
        request.shot_ids,
        request.appoint_account
    )

    return {
        "message": f"批量生成任务已开始，共 {shot_count} 个镜头。请稍后刷新页面查看结果。",
        "total_count": shot_count
    }

def _get_next_managed_reserved_variant_index(original_shot: models.StoryboardShot, db: Session) -> int:
    max_variant = db.query(func.max(models.StoryboardShot.variant_index)).filter(
        models.StoryboardShot.episode_id == original_shot.episode_id,
        models.StoryboardShot.shot_number == original_shot.shot_number
    ).scalar()
    family_count = db.query(func.count(models.StoryboardShot.id)).filter(
        models.StoryboardShot.episode_id == original_shot.episode_id,
        models.StoryboardShot.shot_number == original_shot.shot_number
    ).scalar()
    return max(int(max_variant or 0), int(family_count or 0)) + 1

def _create_managed_reserved_shot(
    original_shot: models.StoryboardShot,
    provider: str,
    reserved_variant_index: int
) -> models.StoryboardShot:
    return models.StoryboardShot(
        episode_id=original_shot.episode_id,
        shot_number=original_shot.shot_number,
        stable_id=original_shot.stable_id,
        variant_index=reserved_variant_index,
        prompt_template=original_shot.prompt_template,
        script_excerpt=original_shot.script_excerpt,
        storyboard_video_prompt=original_shot.storyboard_video_prompt,
        storyboard_audio_prompt=original_shot.storyboard_audio_prompt,
        storyboard_dialogue=original_shot.storyboard_dialogue,
        scene_override=original_shot.scene_override,
        scene_override_locked=bool(getattr(original_shot, "scene_override_locked", False)),
        sora_prompt=original_shot.sora_prompt,
        sora_prompt_is_full=bool(getattr(original_shot, "sora_prompt_is_full", False)),
        sora_prompt_status=original_shot.sora_prompt_status,
        selected_card_ids=original_shot.selected_card_ids,
        selected_sound_card_ids=getattr(original_shot, "selected_sound_card_ids", None),
        first_frame_reference_image_url=getattr(original_shot, "first_frame_reference_image_url", ""),
        uploaded_scene_image_url=getattr(original_shot, "uploaded_scene_image_url", ""),
        use_uploaded_scene_image=bool(getattr(original_shot, "use_uploaded_scene_image", False)),
        aspect_ratio=original_shot.aspect_ratio,
        duration=original_shot.duration,
        storyboard_video_model=getattr(original_shot, "storyboard_video_model", ""),
        storyboard_video_model_override_enabled=bool(getattr(original_shot, "storyboard_video_model_override_enabled", False)),
        duration_override_enabled=bool(getattr(original_shot, "duration_override_enabled", False)),
        provider=provider,
        video_status="processing",
        video_error_message="托管排队中",
        timeline_json=original_shot.timeline_json,
        detail_image_prompt_overrides=original_shot.detail_image_prompt_overrides,
        storyboard_image_path=original_shot.storyboard_image_path,
        storyboard_image_status=original_shot.storyboard_image_status,
        storyboard_image_task_id=original_shot.storyboard_image_task_id,
        storyboard_image_model=original_shot.storyboard_image_model,
    )

def _reserve_legacy_managed_session_slots(session: models.ManagedSession, db: Session) -> int:
    active_tasks = db.query(models.ManagedTask).filter(
        models.ManagedTask.session_id == session.id,
        models.ManagedTask.status.in_(["pending", "processing"]),
        models.ManagedTask.shot_id <= 0
    ).order_by(models.ManagedTask.id.asc()).all()

    if not active_tasks:
        return 0

    original_shot_cache = {}
    reserved_count = 0

    for task in active_tasks:
        stable_id = str(task.shot_stable_id or "").strip()
        if not stable_id:
            continue

        if stable_id not in original_shot_cache:
            original_shot_cache[stable_id] = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.stable_id == stable_id,
                models.StoryboardShot.variant_index == 0
            ).first()
        original_shot = original_shot_cache.get(stable_id)
        if not original_shot:
            continue

        has_original_video = bool((original_shot.video_path or "").strip()) and not str(original_shot.video_path or "").startswith("error:")
        has_existing_variants = db.query(func.count(models.StoryboardShot.id)).filter(
            models.StoryboardShot.episode_id == original_shot.episode_id,
            models.StoryboardShot.shot_number == original_shot.shot_number,
            models.StoryboardShot.variant_index > 0
        ).scalar()
        if not has_original_video and not int(has_existing_variants or 0):
            continue

        reserved_variant_index = _get_next_managed_reserved_variant_index(original_shot, db)
        reserved_shot = _create_managed_reserved_shot(
            original_shot,
            session.provider,
            reserved_variant_index
        )
        db.add(reserved_shot)
        db.flush()

        task.shot_id = reserved_shot.id
        reserved_count += 1

    return reserved_count

@router.post("/api/episodes/{episode_id}/start-managed-generation")

async def start_managed_generation(
    episode_id: int,
    request: StartManagedGenerationRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """开始托管视频生成"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 检查是否已有运行中的托管会话
    existing_session = db.query(models.ManagedSession).filter(
        models.ManagedSession.episode_id == episode_id,
        models.ManagedSession.status == "running"
    ).first()

    if existing_session:
        raise HTTPException(status_code=400, detail="已有托管任务在进行中")

    # 获取原始镜头（variant_index=0）
    query = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id,
        models.StoryboardShot.variant_index == 0
    )

    # 如果指定了镜头ID，只处理这些镜头
    if request.shot_ids:
        query = query.filter(models.StoryboardShot.id.in_(request.shot_ids))

    original_shots = query.all()

    if not original_shots:
        raise HTTPException(status_code=400, detail="没有可生成的镜头")

    # 当前托管模式仅允许每个镜头生成 1 个视频
    variant_count = int(request.variant_count or 1)
    if variant_count != 1:
        raise HTTPException(status_code=400, detail="当前每个镜头仅支持托管生成1个视频")

    _ensure_storyboard_video_generation_slots_available(
        original_shots,
        db,
        requested_count_per_shot=variant_count,
    )

    episode_settings = _get_episode_storyboard_video_settings(episode)
    for original_shot in original_shots:
        _apply_episode_storyboard_video_settings_to_shot(original_shot, episode)
    db.flush()

    # 创建托管会话，保存provider
    session = models.ManagedSession(
        episode_id=episode_id,
        status="running",
        total_shots=len(original_shots),
        completed_shots=0,
        variant_count=variant_count,
        provider=episode_settings["provider"]
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    # 为每个原始镜头预留结果槽位，并创建指定数量的任务
    for original_shot in original_shots:
        # 确保有stable_id
        if not original_shot.stable_id:
            original_shot.stable_id = str(uuid.uuid4())
            db.flush()

        next_reserved_variant_index = _get_next_managed_reserved_variant_index(original_shot, db)
        for offset in range(variant_count):
            reserved_variant_index = next_reserved_variant_index + offset
            reserved_shot = _create_managed_reserved_shot(
                original_shot,
                episode_settings["provider"],
                reserved_variant_index
            )
            db.add(reserved_shot)
            db.flush()

            task = models.ManagedTask(
                session_id=session.id,
                shot_id=reserved_shot.id,
                shot_stable_id=original_shot.stable_id,
                status="pending"
            )
            db.add(task)

    db.commit()
    session_tasks = db.query(models.ManagedTask).filter(
        models.ManagedTask.session_id == session.id
    ).all()
    for managed_task in session_tasks:
        sync_managed_task_to_dashboard(managed_task.id)

    return {
        "session_id": session.id,
        "message": f"托管已开始，共{len(original_shots)}个镜头，将生成{len(original_shots) * variant_count}个视频",
        "total_shots": len(original_shots),
        "variant_count": variant_count,
        "model": episode_settings["model"],
        "aspect_ratio": episode_settings["aspect_ratio"],
        "duration": episode_settings["duration"],
        "provider": episode_settings["provider"]
    }

@router.post("/api/episodes/{episode_id}/stop-managed-generation")

async def stop_managed_generation(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """停止托管视频生成"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 查找运行中的会话
    session = db.query(models.ManagedSession).filter(
        models.ManagedSession.episode_id == episode_id,
        models.ManagedSession.status == "running"
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="没有正在运行的托管任务")

    reserved_count = _reserve_legacy_managed_session_slots(session, db)

    # 转为后台继续收尾，不向上游发送取消请求
    session.status = "detached"
    session.completed_at = None
    db.commit()

    return {
        "message": (
            f"托管已转为后台继续收尾，已预留的结果槽位会继续完成"
            + (f"（本次补齐 {reserved_count} 个旧任务槽位）" if reserved_count > 0 else "")
        )
    }

@router.get("/api/episodes/{episode_id}/managed-session-status", response_model=ManagedSessionStatusResponse)

def get_managed_session_status(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取当前托管会话状态"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 查找最新的会话
    session = db.query(models.ManagedSession).filter(
        models.ManagedSession.episode_id == episode_id
    ).order_by(models.ManagedSession.created_at.desc()).first()

    if not session:
        return ManagedSessionStatusResponse(
            session_id=None,
            status="none",
            total_shots=0,
            completed_shots=0,
            created_at=None
        )

    return ManagedSessionStatusResponse(
        session_id=session.id,
        status=session.status,
        total_shots=session.total_shots,
        completed_shots=session.completed_shots,
        created_at=session.created_at
    )

@router.post("/api/episodes/{episode_id}/refresh-videos")

async def refresh_episode_videos(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """刷新片段所有视频的最新状态和URL（并发查询，最大并发数6）"""
    import asyncio
    import aiohttp

    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 获取所有有task_id的镜头
    shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id,
        models.StoryboardShot.task_id != ''
    ).all()

    if not shots:
        return {
            "success": True,
            "total_shots": 0,
            "updated_count": 0
        }

    # 准备并发查询
    async def check_single_shot(shot_data, session, semaphore):
        """检查单个镜头状态（带并发控制）"""
        async with semaphore:
            shot_id = shot_data['id']
            task_id = shot_data['task_id']

            try:
                url = get_video_task_status_url(task_id)
                headers = {
                    "Authorization": get_video_api_headers()["Authorization"]
                }

                timeout = aiohttp.ClientTimeout(total=10)
                async with session.get(url, headers=headers, timeout=timeout, ssl=False) as response:
                    if response.status == 200:
                        result = await response.json()

                        status = result.get('status', '')
                        video_url = result.get('video_url', '')
                        cdn_uploaded = result.get('cdn_uploaded', False)
                        price = result.get('price')

                        return {
                            'shot_id': shot_id,
                            'success': True,
                            'status': status,
                            'video_url': video_url,
                            'cdn_uploaded': cdn_uploaded,
                            'price': price
                        }
                    else:
                        return {'shot_id': shot_id, 'success': False}

            except Exception as e:
                print(f"[refresh_videos] 查询镜头{shot_id}失败: {str(e)}")
                return {'shot_id': shot_id, 'success': False}

    async def batch_check_shots():
        """批量并发查询所有镜头状态"""
        # 准备镜头数据（避免在异步中访问ORM对象）
        shots_data = [{'id': s.id, 'task_id': s.task_id} for s in shots]

        # 创建信号量限制并发数为6
        semaphore = asyncio.Semaphore(6)

        # 创建HTTP会话
        connector = aiohttp.TCPConnector(limit=6)
        async with aiohttp.ClientSession(connector=connector) as session:
            # 并发查询所有镜头
            tasks = [
                check_single_shot(shot_data, session, semaphore)
                for shot_data in shots_data
            ]
            results = await asyncio.gather(*tasks)

        return results

    # 执行并发查询
    results = await batch_check_shots()

    # 批量更新数据库
    updated_count = 0
    for result in results:
        if not result['success']:
            continue

        shot_id = result['shot_id']
        status = result.get('status', '')
        video_url = result.get('video_url', '')
        cdn_uploaded = result.get('cdn_uploaded', False)
        price = result.get('price')

        # 只更新已完成且有URL的镜头
        if status != 'completed' or not video_url:
            continue

        shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id == shot_id
        ).first()

        if not shot:
            continue

        # 检查是否需要更新
        needs_update = False

        if shot.video_path != video_url or shot.cdn_uploaded != cdn_uploaded:
            previous_video_path = shot.video_path
            previous_thumbnail = shot.thumbnail_video_path

            shot.video_path = video_url
            shot.cdn_uploaded = cdn_uploaded

            if not previous_thumbnail or previous_thumbnail == previous_video_path:
                shot.thumbnail_video_path = video_url

            if shot.video_status != 'completed':
                shot.video_status = 'completed'

            needs_update = True

            # 更新ShotVideo表
            latest_shot_video = db.query(models.ShotVideo).filter(
                models.ShotVideo.shot_id == shot.id
            ).order_by(models.ShotVideo.created_at.desc()).first()

            if latest_shot_video and latest_shot_video.video_path != video_url:
                latest_shot_video.video_path = video_url

        # 更新价格（如果有）
        if price is not None:
            price_cents = int(float(price) * 100)
            if shot.price != price_cents:
                shot.price = price_cents
                needs_update = True

        if needs_update:
            updated_count += 1

    db.commit()

    return {
        "success": True,
        "total_shots": len(shots),
        "updated_count": updated_count
    }

@router.post("/api/episodes/{episode_id}/import-storyboard")

async def import_storyboard(
    episode_id: int,
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """导入分镜表（xls）并生成镜头"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".xls", ".xlsx"]:
        raise HTTPException(status_code=400, detail="仅支持.xls或.xlsx格式的分镜表")

    try:
        from openpyxl import load_workbook
        from io import BytesIO
    except ImportError:
        raise HTTPException(status_code=500, detail="缺少依赖openpyxl，请先安装")

    content = await file.read()
    try:
        # 去掉data_only=True，确保读取最新保存的值
        wb = load_workbook(filename=BytesIO(content))
        ws = wb.active
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析失败: {str(e)}")

    if ws.max_row < 2:
        raise HTTPException(status_code=400, detail="表格为空")

    def normalize_header(value: str) -> str:
        text = str(value).strip() if value else ""
        text = text.replace("\n", "").replace("\r", "").replace(" ", "")
        text = text.replace("（", "(").replace("）", ")")
        return text

    def find_column(header_map, keywords):
        for key, idx in header_map.items():
            for kw in keywords:
                if kw in key:
                    return idx
        return None

    def cell_to_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            if value.is_integer():
                return str(int(value))
            return str(value)
        return str(value).strip()

    def parse_shot_number(value, fallback):
        if value is None or value == "":
            return fallback
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value) if value.is_integer() else fallback
        text = str(value).strip()
        if not text:
            return fallback
        try:
            return int(text)
        except Exception:
            match = re.search(r"\d+", text)
            return int(match.group(0)) if match else fallback

    def parse_subjects_with_type(value) -> List[dict]:
        """解析主体列表，支持格式：萧景珩(角色)、书房(场景)

        Returns:
            List[dict]: [{"name": "萧景珩", "type": "角色"}, ...]
        """
        text = cell_to_text(value)
        if not text:
            return []

        # 支持多种分隔符：顿号、逗号、分号等
        normalized = re.sub(r"[，、/;；|]+", "、", text)
        parts = [part.strip() for part in normalized.split("、") if part.strip()]

        subjects = []
        allowed_types = {"角色", "场景"}
        for part in parts:
            # 尝试匹配 "名称(类型)" 格式
            match = re.match(r"^(.+?)\(([^)]+)\)$", part)
            if match:
                name = match.group(1).strip()
                subject_type = match.group(2).strip()
            else:
                # 没有括号，默认为角色
                name = part
                subject_type = "角色"

            if name and subject_type in allowed_types:
                subjects.append({"name": name, "type": subject_type})

        return subjects

    # 读取表头（openpyxl的行列从1开始）
    headers = [normalize_header(ws.cell(row=1, column=c).value) for c in range(1, ws.max_column + 1)]
    header_map = {headers[i]: i + 1 for i in range(len(headers)) if headers[i]}  # 存储column号（1-based）

    shot_idx = find_column(header_map, ["镜号"])
    subjects_idx = find_column(header_map, ["角色/场景", "角色场景", "主体"])
    excerpt_idx = find_column(header_map, ["对应的原剧本段落", "原剧本段落"])
    dialogue_idx = find_column(header_map, ["对白", "台词"])
    storyboard_prompt_idx = find_column(header_map, ["分镜提示词"])
    duration_idx = find_column(header_map, ["时长"])

    # 至少需要有"角色/场景"或"原剧本段落"或"对白"或"分镜提示词"之一
    if subjects_idx is None and excerpt_idx is None and dialogue_idx is None and storyboard_prompt_idx is None:
        raise HTTPException(status_code=400, detail="未找到必要列：至少需要有【角色/场景】或【原剧本段落】或【对白】或【分镜提示词】之一")

    rows_data = []
    for r in range(2, ws.max_row + 1):  # 从第2行开始读取数据
        raw_shot = ws.cell(row=r, column=shot_idx).value if shot_idx is not None else None
        shot_number = parse_shot_number(raw_shot, r - 1)  # r-1 作为fallback（因为第2行对应镜号1）

        # 解析新的6列结构
        subjects = parse_subjects_with_type(ws.cell(row=r, column=subjects_idx).value) if subjects_idx is not None else []
        script_excerpt = cell_to_text(ws.cell(row=r, column=excerpt_idx).value) if excerpt_idx is not None else ""
        dialogue = cell_to_text(ws.cell(row=r, column=dialogue_idx).value) if dialogue_idx is not None else ""
        storyboard_prompt = cell_to_text(ws.cell(row=r, column=storyboard_prompt_idx).value) if storyboard_prompt_idx is not None else ""

        # 调试日志：打印第一行数据
        if r == 2:
            print(f"  镜号={shot_number}, 主体={subjects}")

        # 跳过空行
        if not subjects and not script_excerpt and not dialogue and not storyboard_prompt:
            continue

        duration = 15
        if duration_idx is not None:
            raw_duration = ws.cell(row=r, column=duration_idx).value
            parsed_duration = parse_shot_number(raw_duration, None)
            if parsed_duration in (10, 15):
                duration = parsed_duration

        rows_data.append({
            "shot_number": shot_number,
            "subjects": subjects,
            "script_excerpt": script_excerpt,
            "dialogue": dialogue,
            "storyboard_prompt": storyboard_prompt,
            "duration": duration
        })

    if not rows_data:
        raise HTTPException(status_code=400, detail="表格无有效数据")

    # 获取剧集的主体库
    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == episode.id
    ).first()

    if not library:
        raise HTTPException(status_code=500, detail="主体库不存在")

    # 现有主体卡片映射
    existing_cards = db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library.id
    ).all()
    name_to_id = {card.name: card.id for card in existing_cards}

    # 根据Excel中的主体自动补齐主体卡片
    created_subjects = []
    for row in rows_data:
        for subject in row.get("subjects", []):
            name = subject["name"]
            subject_type = subject["type"]
            if subject_type not in ("角色", "场景"):
                continue
            if name not in name_to_id:
                new_card = models.SubjectCard(
                    library_id=library.id,
                    name=name,
                    card_type=subject_type
                )
                db.add(new_card)
                db.flush()
                name_to_id[name] = new_card.id
                created_subjects.append(f"{name}({subject_type})")

    # ⚠️ 替换模式：删除该episode的所有旧镜头
    deleted_count = _delete_episode_storyboard_shots(episode_id, db)
    db.commit()

    # 创建新导入的镜头
    for idx, row in enumerate(rows_data):
        # 获取主体ID列表
        selected_ids = []
        for subject in row.get("subjects", []):
            name = subject["name"]
            if name in name_to_id:
                selected_ids.append(name_to_id[name])

        # 打印每个镜头的对白字段（用于调试）
        dialogue_preview = row['dialogue'][:50] + '...' if len(row['dialogue']) > 50 else row['dialogue']
        storyboard_prompt_preview = row['storyboard_prompt'][:50] + '...' if len(row['storyboard_prompt']) > 50 else row['storyboard_prompt']

        for _ in [None]:
            new_shot = models.StoryboardShot(
                episode_id=episode_id,
                shot_number=int(row["shot_number"]),
                variant_index=0,
                prompt_template="",
                script_excerpt=row["script_excerpt"],  # 原剧本段落
                storyboard_dialogue=row["dialogue"],  # 对白
                sora_prompt=row["storyboard_prompt"],  # ✅ 保存Excel中的分镜提示词
                selected_card_ids=json.dumps(selected_ids),
                selected_sound_card_ids=None,
                aspect_ratio="16:9",
                duration=row["duration"],
                storyboard_video_model="",
                storyboard_video_model_override_enabled=False,
                duration_override_enabled=True,
            )
            db.add(new_shot)

    db.commit()

    # 验证：查询刚保存的数据
    saved_shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id
    ).order_by(models.StoryboardShot.shot_number).limit(3).all()

    for shot in saved_shots:
        dialogue_preview = (shot.storyboard_dialogue or '')[:50] + '...' if len(shot.storyboard_dialogue or '') > 50 else (shot.storyboard_dialogue or '')
        sora_prompt_preview = (shot.sora_prompt or '')[:50] + '...' if len(shot.sora_prompt or '') > 50 else (shot.sora_prompt or '')


    return {
        "message": "导入成功（已替换所有镜头）",
        "imported_shots": len(rows_data),
        "deleted_shots": deleted_count,
        "created_subjects": len(created_subjects),
        "created_subject_names": created_subjects[:10]  # 显示前10个
    }

@router.get("/api/episodes/{episode_id}/export-storyboard")

async def export_storyboard(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """导出分镜表为Excel文件（.xlsx）"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 获取所有镜头
    shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id
    ).order_by(
        models.StoryboardShot.shot_number.asc(),
        models.StoryboardShot.variant_index.asc()
    ).all()

    if not shots:
        raise HTTPException(status_code=400, detail="没有镜头数据")

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
    except ImportError:
        raise HTTPException(status_code=500, detail="缺少依赖openpyxl，请先安装")

    # 创建工作簿
    wb = Workbook()
    ws = wb.active
    ws.title = "分镜表"

    # 表头
    headers = ["镜号", "角色/场景", "原剧本段落", "对白", "分镜提示词", "时长"]
    ws.append(headers)

    # 设置表头样式
    header_font = Font(bold=True, size=11)
    header_fill = PatternFill(start_color="D3D3D3", end_color="D3D3D3", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment

    # 设置列宽
    column_widths = [8, 30, 40, 30, 50, 8]
    for col_idx, width in enumerate(column_widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width

    # 获取主体库以便查询主体详情
    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == episode.id
    ).first()

    card_map = {}
    if library:
        cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library.id
        ).all()
        card_map = {card.id: card for card in cards}

    # 填充数据
    for shot in shots:
        # 解析主体列表
        try:
            selected_ids = json.loads(shot.selected_card_ids or "[]")
        except:
            selected_ids = []

        subjects_text_parts = []
        for card_id in selected_ids:
            if card_id in card_map:
                card = card_map[card_id]
                subjects_text_parts.append(f"{card.name}({card.card_type})")

        subjects_text = "、".join(subjects_text_parts)

        # 构建行数据
        row_data = [
            shot.shot_number,
            subjects_text,
            shot.script_excerpt or "",
            shot.storyboard_dialogue or "",
            shot.sora_prompt or "",  # 分镜提示词使用sora_prompt
            shot.duration
        ]
        ws.append(row_data)

        # 设置单元格自动换行
        row_idx = ws.max_row
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    # 生成文件名
    safe_episode_name = re.sub(r'[\\/*?:"<>|]', '_', episode.name or f"片段{episode_id}")
    filename = f"{safe_episode_name}_分镜表.xlsx"
    output_path = os.path.join("uploads", f"export_{uuid.uuid4().hex[:8]}_{filename}")

    # 保存文件
    wb.save(output_path)

    return FileResponse(
        path=output_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

def _get_storyboard_shot_family_identity(shot: models.StoryboardShot) -> str:
    stable_id = str(getattr(shot, "stable_id", "") or "").strip()
    if stable_id:
        return f"stable:{int(getattr(shot, 'episode_id', 0) or 0)}:{stable_id}"
    return f"shot_number:{int(getattr(shot, 'episode_id', 0) or 0)}:{int(getattr(shot, 'shot_number', 0) or 0)}"

def _get_storyboard_shot_family_filters(shot: models.StoryboardShot):
    stable_id = str(getattr(shot, "stable_id", "") or "").strip()
    if stable_id:
        return [
            models.StoryboardShot.episode_id == shot.episode_id,
            or_(
                models.StoryboardShot.stable_id == stable_id,
                and_(
                    models.StoryboardShot.shot_number == shot.shot_number,
                    or_(
                        models.StoryboardShot.stable_id.is_(None),
                        models.StoryboardShot.stable_id == "",
                    ),
                ),
            ),
        ]
    return [
        models.StoryboardShot.episode_id == shot.episode_id,
        models.StoryboardShot.shot_number == shot.shot_number,
    ]

def _count_active_video_generations_for_shot_family(
    shot: models.StoryboardShot,
    db: Session
) -> int:
    family_rows = db.query(
        models.StoryboardShot.id,
        models.StoryboardShot.video_status,
    ).filter(
        *_get_storyboard_shot_family_filters(shot)
    ).all()

    family_shot_ids = []
    active_shot_ids = set()
    for shot_id, video_status in family_rows:
        numeric_shot_id = int(shot_id or 0)
        if numeric_shot_id <= 0:
            continue
        family_shot_ids.append(numeric_shot_id)
        if str(video_status or "").strip().lower() in ACTIVE_VIDEO_GENERATION_STATUSES:
            active_shot_ids.add(numeric_shot_id)

    active_count = len(active_shot_ids)
    stable_id = str(getattr(shot, "stable_id", "") or "").strip()

    if stable_id:
        managed_tasks = db.query(
            models.ManagedTask.id,
            models.ManagedTask.shot_id,
        ).filter(
            models.ManagedTask.shot_stable_id == stable_id,
            models.ManagedTask.status.in_(ACTIVE_MANAGED_TASK_STATUSES),
        ).all()
    elif family_shot_ids:
        managed_tasks = db.query(
            models.ManagedTask.id,
            models.ManagedTask.shot_id,
        ).filter(
            models.ManagedTask.shot_id.in_(family_shot_ids),
            models.ManagedTask.status.in_(ACTIVE_MANAGED_TASK_STATUSES),
        ).all()
    else:
        managed_tasks = []

    for _, managed_shot_id in managed_tasks:
        numeric_shot_id = int(managed_shot_id or 0)
        if numeric_shot_id <= 0 or numeric_shot_id not in active_shot_ids:
            active_count += 1

    return active_count

def _build_active_video_generation_limit_message(
    blocked_entries: List[Dict[str, Any]]
) -> str:
    if not blocked_entries:
        return ""

    if len(blocked_entries) == 1:
        entry = blocked_entries[0]
        shot = entry["shot"]
        current_active = int(entry["current_active"] or 0)
        remaining = max(0, MAX_ACTIVE_VIDEO_GENERATIONS_PER_SHOT - current_active)
        if remaining <= 0:
            return f"镜头{shot.shot_number}已有{current_active}个正在生成中的视频，请等待完成"
        return (
            f"镜头{shot.shot_number}当前已有{current_active}个正在生成中的视频，"
            f"本次最多还能再提交{remaining}个，请等待完成"
        )

    labels = []
    for entry in blocked_entries[:6]:
        shot = entry["shot"]
        labels.append(f"镜头{shot.shot_number}")
    labels_text = "、".join(labels)
    if len(blocked_entries) > 6:
        labels_text += "等"
    return (
        f"{labels_text}已达到同时生成上限或本次提交后会超出上限，"
        f"当前每个镜头最多只能有{MAX_ACTIVE_VIDEO_GENERATIONS_PER_SHOT}个正在生成中的视频，请等待完成"
    )

def _ensure_storyboard_video_generation_slots_available(
    shots: List[models.StoryboardShot],
    db: Session,
    requested_count_per_shot: int = 1,
):
    blocked_entries = []
    family_entries: Dict[str, Dict[str, Any]] = {}
    requested_count = max(1, int(requested_count_per_shot or 1))

    for shot in shots or []:
        if not shot:
            continue

        family_key = _get_storyboard_shot_family_identity(shot)
        entry = family_entries.get(family_key)
        if not entry:
            entry = {
                "shot": shot,
                "requested_count": 0,
            }
            family_entries[family_key] = entry
        entry["requested_count"] += requested_count

    for entry in family_entries.values():
        shot = entry["shot"]
        current_active = _count_active_video_generations_for_shot_family(shot, db)
        if current_active + int(entry["requested_count"] or 0) > MAX_ACTIVE_VIDEO_GENERATIONS_PER_SHOT:
            blocked_entries.append({
                "shot": shot,
                "current_active": current_active,
            })

    if blocked_entries:
        raise HTTPException(
            status_code=400,
            detail=_build_active_video_generation_limit_message(blocked_entries),
        )

def _normalize_jimeng_ratio(value: Optional[str], default_ratio: str = "9:16") -> str:
    allowed_ratios = {"21:9", "16:9", "3:2", "4:3", "1:1", "3:4", "2:3", "9:16"}
    legacy_map = {
        "1:2": "9:16",
        "2:1": "16:9"
    }
    raw = (value or "").strip()
    normalized = legacy_map.get(raw, raw)
    if normalized in allowed_ratios:
        return normalized
    fallback = legacy_map.get((default_ratio or "").strip(), (default_ratio or "").strip())
    return fallback if fallback in allowed_ratios else "9:16"

def _normalize_storyboard_video_model(value: Optional[str], default_model: str = DEFAULT_STORYBOARD_VIDEO_MODEL) -> str:
    raw = (value or "").strip()
    if raw in _STORYBOARD_VIDEO_MODEL_CONFIG:
        return raw
    fallback = (default_model or "").strip()
    if fallback in _STORYBOARD_VIDEO_MODEL_CONFIG:
        return fallback
    return DEFAULT_STORYBOARD_VIDEO_MODEL

def _normalize_storyboard_video_aspect_ratio(
    value: Optional[str],
    model: str,
    default_ratio: str = "16:9"
) -> str:
    model_key = _normalize_storyboard_video_model(model, default_model=DEFAULT_STORYBOARD_VIDEO_MODEL)
    config = _STORYBOARD_VIDEO_MODEL_CONFIG[model_key]
    allowed = tuple(config["aspect_ratios"])
    legacy_map = {
        "1:2": "9:16",
        "2:1": "16:9"
    }
    raw = (value or "").strip()
    normalized = legacy_map.get(raw, raw)
    if normalized in allowed:
        return normalized
    fallback_raw = (default_ratio or "").strip()
    fallback = legacy_map.get(fallback_raw, fallback_raw)
    if fallback in allowed:
        return fallback
    default_value = config["default_ratio"]
    if default_value in allowed:
        return default_value
    return allowed[0]

def _normalize_storyboard_video_duration(
    value: Optional[int],
    model: str,
    default_duration: Optional[int] = None
) -> int:
    model_key = _normalize_storyboard_video_model(model, default_model=DEFAULT_STORYBOARD_VIDEO_MODEL)
    config = _STORYBOARD_VIDEO_MODEL_CONFIG[model_key]
    allowed = tuple(int(item) for item in config["durations"])
    if default_duration is None:
        fallback = int(config["default_duration"])
    else:
        try:
            fallback = int(default_duration)
        except Exception:
            fallback = int(config["default_duration"])
    if fallback not in allowed:
        fallback = int(config["default_duration"])
    try:
        parsed = int(value) if value is not None else fallback
    except Exception:
        parsed = fallback
    if parsed in allowed:
        return parsed
    return fallback

def _normalize_storyboard_video_resolution_name(
    value: Optional[str],
    model: str,
    default_resolution: str = ""
) -> str:
    model_key = _normalize_storyboard_video_model(model, default_model=DEFAULT_STORYBOARD_VIDEO_MODEL)
    config = _STORYBOARD_VIDEO_MODEL_CONFIG[model_key]
    allowed = tuple(str(item).strip() for item in config.get("resolution_names", ()) if str(item).strip())
    if not allowed:
        return ""
    fallback_raw = str(default_resolution or config.get("default_resolution") or "").strip().lower()
    fallback = fallback_raw if fallback_raw in allowed else str(config.get("default_resolution") or allowed[0]).strip().lower()
    raw = str(value or "").strip().lower()
    if raw in allowed:
        return raw
    return fallback

def _map_storyboard_prompt_template_duration(duration: Optional[int]) -> int:
    try:
        parsed = int(duration or 0)
    except Exception:
        parsed = 15
    if parsed <= 6:
        return 6
    if parsed <= 10:
        return 10
    if parsed <= 15:
        return 15
    return 25

def _is_storyboard_shot_duration_override_enabled(shot) -> bool:
    return bool(getattr(shot, "duration_override_enabled", False))

def _is_storyboard_shot_model_override_enabled(shot) -> bool:
    return bool(getattr(shot, "storyboard_video_model_override_enabled", False))

def _resolve_storyboard_video_provider(model: str) -> str:
    model_key = _normalize_storyboard_video_model(model, default_model=DEFAULT_STORYBOARD_VIDEO_MODEL)
    return str(_STORYBOARD_VIDEO_MODEL_CONFIG[model_key]["provider"])

def _get_episode_storyboard_video_settings(episode) -> Dict[str, Any]:
    model = _normalize_storyboard_video_model(
        getattr(episode, "storyboard_video_model", None),
        default_model=DEFAULT_STORYBOARD_VIDEO_MODEL
    )
    aspect_ratio = _normalize_storyboard_video_aspect_ratio(
        getattr(episode, "storyboard_video_aspect_ratio", None),
        model=model,
        default_ratio=_STORYBOARD_VIDEO_MODEL_CONFIG[model]["default_ratio"]
    )
    duration = _normalize_storyboard_video_duration(
        getattr(episode, "storyboard_video_duration", None),
        model=model,
        default_duration=_STORYBOARD_VIDEO_MODEL_CONFIG[model]["default_duration"]
    )
    provider = _resolve_storyboard_video_provider(model)
    resolution_name = _normalize_storyboard_video_resolution_name(
        getattr(episode, "storyboard_video_resolution_name", None),
        model=model,
        default_resolution=_STORYBOARD_VIDEO_MODEL_CONFIG[model].get("default_resolution", "")
    )
    appoint_account = _normalize_storyboard_video_appoint_account(
        getattr(episode, "storyboard_video_appoint_account", "") if episode is not None else ""
    )
    return {
        "model": model,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "resolution_name": resolution_name,
        "provider": provider,
        "appoint_account": appoint_account,
    }

def _get_effective_storyboard_video_settings_for_shot(shot, episode) -> Dict[str, Any]:
    episode_settings = _get_episode_storyboard_video_settings(episode)
    model_override_enabled = _is_storyboard_shot_model_override_enabled(shot)
    effective_model = episode_settings["model"]
    if model_override_enabled:
        effective_model = _normalize_storyboard_video_model(
            getattr(shot, "storyboard_video_model", None),
            default_model=episode_settings["model"]
        )
    aspect_ratio = _normalize_storyboard_video_aspect_ratio(
        episode_settings["aspect_ratio"],
        model=effective_model,
        default_ratio=episode_settings["aspect_ratio"]
    )
    resolution_name = _normalize_storyboard_video_resolution_name(
        episode_settings.get("resolution_name", ""),
        model=effective_model,
        default_resolution=episode_settings.get("resolution_name", "")
    )
    duration_override_enabled = _is_storyboard_shot_duration_override_enabled(shot)
    effective_duration = _normalize_storyboard_video_duration(
        episode_settings["duration"],
        model=effective_model,
        default_duration=episode_settings["duration"]
    )
    if duration_override_enabled:
        effective_duration = _normalize_storyboard_video_duration(
            getattr(shot, "duration", None),
            model=effective_model,
            default_duration=episode_settings["duration"]
        )
    return {
        "model": effective_model,
        "aspect_ratio": aspect_ratio,
        "duration": effective_duration,
        "resolution_name": resolution_name,
        "provider": _resolve_storyboard_video_provider(effective_model),
        "appoint_account": episode_settings.get("appoint_account", ""),
        "model_override_enabled": model_override_enabled,
        "duration_override_enabled": duration_override_enabled,
        "prompt_template_duration": _map_storyboard_prompt_template_duration(effective_duration),
    }

def _apply_episode_storyboard_video_settings_to_shot(shot, episode) -> Dict[str, Any]:
    settings = _get_effective_storyboard_video_settings_for_shot(shot, episode)
    shot.storyboard_video_model = settings["model"]
    shot.storyboard_video_model_override_enabled = bool(settings["model_override_enabled"])
    shot.aspect_ratio = settings["aspect_ratio"]
    shot.duration = settings["duration"]
    shot.provider = settings["provider"]
    return settings

@router.get("/api/episodes/{episode_id}/export-all")

async def export_all_videos(
    episode_id: int,
    db: Session = Depends(get_db)
):
    """导出片段的所有视频"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id,
        models.StoryboardShot.video_status == 'completed'
    ).order_by(
        models.StoryboardShot.shot_number.asc(),
        models.StoryboardShot.variant_index.asc()
    ).all()

    if not shots:
        raise HTTPException(status_code=404, detail="没有已完成的视频")

    videos = []
    for shot in shots:
        if shot.video_path:
            # video_path现在保存的是CDN URL，直接使用
            videos.append({
                "shot_id": shot.id,
                "shot_number": shot.shot_number,
                "video_url": shot.video_path
            })

    return {
        "episode_name": episode.name,
        "total_videos": len(videos),
        "videos": videos
    }

def _verify_episode_permission(episode_id: int, user: models.User, db: Session) -> models.Episode:
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if not script or script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    return episode

def _parse_storyboard2_card_ids(raw_value) -> List[int]:
    if raw_value is None:
        return []

    try:
        parsed = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except Exception:
        return []

    if not isinstance(parsed, list):
        return []

    resolved_ids = []
    seen = set()
    for item in parsed:
        card_id = None
        if isinstance(item, int):
            card_id = item
        elif isinstance(item, str) and item.strip().isdigit():
            card_id = int(item.strip())

        if not card_id or card_id in seen:
            continue
        seen.add(card_id)
        resolved_ids.append(card_id)

    return resolved_ids

def _clean_scene_ai_prompt_text(ai_prompt: str) -> str:
    text_value = str(ai_prompt or "")
    if not text_value:
        return ""
    text_value = re.sub(r'生成图片的风格是：[^\n]*\n?', '', text_value)
    text_value = re.sub(r'生成图片中场景的是：', '', text_value)
    return text_value.strip()

def _extract_scene_description_from_card_ids(card_ids: List[int], db: Session) -> str:
    if not card_ids:
        return ""

    try:
        all_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.id.in_(card_ids)
        ).all()
        scene_cards = [card for card in all_cards if _is_scene_subject_card_type(getattr(card, "card_type", ""))]
        if not scene_cards:
            return ""

        card_map = {int(card.id): card for card in scene_cards if card}
        scene_parts: List[str] = []
        for raw_card_id in card_ids:
            try:
                card_id = int(raw_card_id)
            except Exception:
                continue
            card = card_map.get(card_id)
            if not card:
                continue
            clean_prompt = _clean_scene_ai_prompt_text(card.ai_prompt or "")
            if not clean_prompt:
                continue
            scene_parts.append(f"{(card.name or '').strip()}{clean_prompt}")

        return "；".join([part for part in scene_parts if str(part or "").strip()])
    except Exception:
        return ""

def _resolve_storyboard2_scene_override_text(
    sub_shot: models.Storyboard2SubShot,
    storyboard2_shot: models.Storyboard2Shot,
    db: Session,
    fallback_selected_card_ids: Optional[List[int]] = None
) -> str:
    scene_override = str(getattr(sub_shot, "scene_override", "") or "").strip()
    scene_override_locked = bool(getattr(sub_shot, "scene_override_locked", False))
    if scene_override or scene_override_locked:
        return scene_override

    selected_card_ids = _parse_storyboard2_card_ids(getattr(sub_shot, "selected_card_ids", "[]"))
    if not selected_card_ids:
        if fallback_selected_card_ids is not None:
            selected_card_ids = list(fallback_selected_card_ids)
        else:
            selected_card_ids = _resolve_storyboard2_selected_card_ids(storyboard2_shot, db)

    scene_from_cards = _extract_scene_description_from_card_ids(selected_card_ids, db)
    if scene_from_cards:
        return scene_from_cards

    if storyboard2_shot and storyboard2_shot.source_shot_id:
        source_shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id == storyboard2_shot.source_shot_id
        ).first()
        if source_shot and (source_shot.scene_override or "").strip():
            return (source_shot.scene_override or "").strip()

    return ""

def _pick_storyboard2_source_shots(episode_id: int, db: Session):
    all_shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id
    ).order_by(
        models.StoryboardShot.shot_number.asc(),
        models.StoryboardShot.variant_index.asc(),
        models.StoryboardShot.id.asc()
    ).all()

    selected_by_number = {}
    for shot in all_shots:
        shot_number = int(shot.shot_number or 0)
        current = selected_by_number.get(shot_number)
        if not current:
            selected_by_number[shot_number] = shot
            continue

        current_variant = int(current.variant_index or 0)
        this_variant = int(shot.variant_index or 0)
        if current_variant != 0 and this_variant == 0:
            selected_by_number[shot_number] = shot

    ordered_numbers = sorted(selected_by_number.keys())
    return [selected_by_number[num] for num in ordered_numbers]

def _ensure_storyboard2_initialized(episode_id: int, db: Session) -> bool:
    existing_count = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.episode_id == episode_id
    ).count()

    if existing_count > 0:
        return False

    source_shots = _pick_storyboard2_source_shots(episode_id, db)
    if not source_shots:
        return False

    for order_index, source_shot in enumerate(source_shots, start=1):
        excerpt = (
            (source_shot.script_excerpt or "").strip()
            or (source_shot.scene_override or "").strip()
            or (source_shot.storyboard_dialogue or "").strip()
            or f"镜头{source_shot.shot_number}原文描述"
        )
        initial_selected_card_ids = _parse_storyboard2_card_ids(source_shot.selected_card_ids)

        storyboard2_shot = models.Storyboard2Shot(
            episode_id=episode_id,
            source_shot_id=source_shot.id,
            shot_number=int(source_shot.shot_number or order_index),
            excerpt=excerpt,
            selected_card_ids=json.dumps(initial_selected_card_ids, ensure_ascii=False),
            display_order=order_index
        )
        db.add(storyboard2_shot)
        db.flush()

        initial_scene_override = (
            (source_shot.scene_override or "").strip()
            or _extract_scene_description_from_card_ids(initial_selected_card_ids, db)
        )

        # 初始化仅保留一条空分镜，后续由“批量生成Sora提示词”再细化
        db.add(models.Storyboard2SubShot(
            storyboard2_shot_id=storyboard2_shot.id,
            sub_shot_index=1,
            time_range="",
            visual_text="",
            audio_text="",
            sora_prompt="",
            scene_override=initial_scene_override,
            scene_override_locked=False
        ))

    db.commit()
    return True

def _recover_orphan_storyboard2_image_tasks(episode_id: int, db: Session) -> int:
    """回收故事板2镜头图孤儿任务（服务重启后遗留processing）。"""
    processing_rows = db.query(models.Storyboard2SubShot).join(
        models.Storyboard2Shot,
        models.Storyboard2SubShot.storyboard2_shot_id == models.Storyboard2Shot.id
    ).filter(
        models.Storyboard2Shot.episode_id == episode_id,
        models.Storyboard2SubShot.image_generate_status == "processing"
    ).all()

    if not processing_rows:
        return 0

    with storyboard2_active_image_tasks_lock:
        active_ids = set(storyboard2_active_image_tasks)

    recovered_count = 0
    for row in processing_rows:
        if row.id in active_ids:
            continue
        row.image_generate_status = "failed"
        row.image_generate_progress = ""
        current_error = str(getattr(row, "image_generate_error", "") or "").strip()
        if not current_error:
            row.image_generate_error = "服务重启后任务中断，请重新生成"
        recovered_count += 1

    if recovered_count > 0:
        db.commit()

    return recovered_count

def _serialize_storyboard2_board(episode_id: int, db: Session):
    storyboard2_shots = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.episode_id == episode_id
    ).order_by(
        models.Storyboard2Shot.display_order.asc(),
        models.Storyboard2Shot.shot_number.asc(),
        models.Storyboard2Shot.id.asc()
    ).all()

    source_shot_ids = [shot.source_shot_id for shot in storyboard2_shots if shot.source_shot_id]
    source_shot_map = {}
    if source_shot_ids:
        source_shots = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id.in_(source_shot_ids)
        ).all()
        source_shot_map = {item.id: item for item in source_shots}

    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == episode_id
    ).first()
    library_id = library.id if library else None

    all_library_cards = []
    card_map = {}
    if library_id:
        all_library_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library_id,
            models.SubjectCard.card_type.in_(ALLOWED_CARD_TYPES)
        ).all()
        all_library_cards.sort(
            key=lambda card: (
                _subject_type_sort_key(card.card_type),
                (card.name or ""),
                card.id
            )
        )
        card_map = {card.id: card for card in all_library_cards}

    selected_card_ids_by_storyboard2_shot = {}
    for shot in storyboard2_shots:
        selected_ids = _parse_storyboard2_card_ids(shot.selected_card_ids)
        if not selected_ids:
            source_shot = source_shot_map.get(shot.source_shot_id)
            if source_shot:
                selected_ids = _parse_storyboard2_card_ids(source_shot.selected_card_ids)

        if card_map:
            selected_ids = [card_id for card_id in selected_ids if card_id in card_map]

        selected_card_ids_by_storyboard2_shot[shot.id] = selected_ids

    reference_image_map = {}
    uploaded_image_map = {}
    all_card_ids = list(card_map.keys())
    if all_card_ids:
        reference_images = db.query(models.GeneratedImage).filter(
            models.GeneratedImage.card_id.in_(all_card_ids),
            models.GeneratedImage.is_reference == True,
            models.GeneratedImage.status == "completed"
        ).order_by(
            models.GeneratedImage.created_at.desc(),
            models.GeneratedImage.id.desc()
        ).all()
        for image in reference_images:
            if image.card_id not in reference_image_map and image.image_path:
                reference_image_map[image.card_id] = image.image_path

        uploaded_images = db.query(models.CardImage).filter(
            models.CardImage.card_id.in_(all_card_ids)
        ).order_by(
            models.CardImage.order.desc(),
            models.CardImage.created_at.desc(),
            models.CardImage.id.desc()
        ).all()
        for image in uploaded_images:
            if image.card_id not in uploaded_image_map and image.image_path:
                uploaded_image_map[image.card_id] = image.image_path

    all_images = db.query(models.Storyboard2SubShotImage).join(
        models.Storyboard2SubShot,
        models.Storyboard2SubShotImage.sub_shot_id == models.Storyboard2SubShot.id
    ).join(
        models.Storyboard2Shot,
        models.Storyboard2SubShot.storyboard2_shot_id == models.Storyboard2Shot.id
    ).filter(
        models.Storyboard2Shot.episode_id == episode_id
    ).order_by(
        models.Storyboard2SubShotImage.id.desc()
    ).all()

    image_map = {img.id: img for img in all_images}
    images_by_sub_shot = {}
    for img in all_images:
        images_by_sub_shot.setdefault(img.sub_shot_id, []).append(img)

    all_videos = db.query(models.Storyboard2SubShotVideo).join(
        models.Storyboard2SubShot,
        models.Storyboard2SubShotVideo.sub_shot_id == models.Storyboard2SubShot.id
    ).join(
        models.Storyboard2Shot,
        models.Storyboard2SubShot.storyboard2_shot_id == models.Storyboard2Shot.id
    ).filter(
        models.Storyboard2Shot.episode_id == episode_id,
        models.Storyboard2SubShotVideo.is_deleted == False
    ).order_by(
        models.Storyboard2SubShotVideo.created_at.asc(),
        models.Storyboard2SubShotVideo.id.asc()
    ).all()

    videos_by_sub_shot = {}
    for video in all_videos:
        videos_by_sub_shot.setdefault(video.sub_shot_id, []).append(video)

    shot_payload = []
    for shot in storyboard2_shots:
        sub_shots = sorted(list(shot.sub_shots or []), key=lambda x: (x.sub_shot_index, x.id))
        sub_payload = []

        for sub in sub_shots:
            candidates = images_by_sub_shot.get(sub.id, [])
            candidate_count = len(candidates)
            candidate_payload = []
            for idx, candidate in enumerate(candidates, start=1):
                candidate_size = _normalize_jimeng_ratio(getattr(candidate, "size", None), default_ratio="9:16")
                candidate_payload.append({
                    "id": candidate.id,
                    "label": f"候选{idx}",
                    "image_url": candidate.image_url,
                    "size": candidate_size,
                    "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
                    "deletable": candidate_count > 1 and sub.current_image_id != candidate.id
                })

            current_image = image_map.get(sub.current_image_id) if sub.current_image_id else None
            current_payload = None
            if current_image:
                current_size = _normalize_jimeng_ratio(getattr(current_image, "size", None), default_ratio="9:16")
                current_payload = {
                    "id": current_image.id,
                    "image_url": current_image.image_url,
                    "size": current_size,
                    "source_sub_shot_id": current_image.sub_shot_id,
                    "created_at": current_image.created_at.isoformat() if current_image.created_at else None
                }

            sub_videos = videos_by_sub_shot.get(sub.id, [])
            video_payload = []
            for video in sub_videos:
                normalized_video_status = _normalize_storyboard2_video_status(
                    str(video.status or "pending"),
                    default_value="processing"
                )
                video_payload.append({
                    "id": video.id,
                    "task_id": video.task_id or "",
                    "model_name": video.model_name or "grok",
                    "duration": int(video.duration or 6),
                    "aspect_ratio": _normalize_jimeng_ratio(getattr(video, "aspect_ratio", None), default_ratio="9:16"),
                    "status": normalized_video_status,
                    "progress": int(video.progress or 0),
                    "video_url": video.video_url or "",
                    "thumbnail_url": video.thumbnail_url or "",
                    "error_message": video.error_message or "",
                    "cdn_uploaded": bool(video.cdn_uploaded),
                    "created_at": video.created_at.isoformat() if video.created_at else None
                })

            latest_video = sub_videos[-1] if sub_videos else None
            processing_video = next(
                (
                    item for item in reversed(sub_videos)
                    if _is_storyboard2_video_processing(str(item.status or ""))
                ),
                None
            )
            if processing_video:
                video_generate_status = "processing"
                video_generate_progress = max(0, min(int(processing_video.progress or 0), 99))
                video_generate_error = processing_video.error_message or ""
            elif latest_video and _normalize_storyboard2_video_status(str(latest_video.status or ""), default_value="processing") == "failed":
                video_generate_status = "failed"
                video_generate_progress = 0
                video_generate_error = latest_video.error_message or ""
            else:
                video_generate_status = "idle"
                video_generate_progress = 0
                video_generate_error = ""

            sub_selected_card_ids = _parse_storyboard2_card_ids(getattr(sub, "selected_card_ids", "[]"))
            if not sub_selected_card_ids:
                sub_selected_card_ids = list(selected_card_ids_by_storyboard2_shot.get(shot.id, []))
            if card_map:
                sub_selected_card_ids = [card_id for card_id in sub_selected_card_ids if card_id in card_map]
            sub_scene_override_locked = bool(getattr(sub, "scene_override_locked", False))
            sub_scene_override = _resolve_storyboard2_scene_override_text(
                sub_shot=sub,
                storyboard2_shot=shot,
                db=db,
                fallback_selected_card_ids=sub_selected_card_ids
            )

            sub_subjects_payload = []
            for card_id in sub_selected_card_ids:
                card = card_map.get(card_id)
                if not card:
                    continue
                preview_image = reference_image_map.get(card_id) or uploaded_image_map.get(card_id) or ""
                sub_subjects_payload.append({
                    "id": card.id,
                    "name": card.name or "",
                    "alias": card.alias or "",
                    "card_type": card.card_type or "",
                    "preview_image": preview_image
                })

            sub_payload.append({
                "id": sub.id,
                "order": sub.sub_shot_index,
                "time_range": sub.time_range or "",
                "visual_text": sub.visual_text or "",
                "audio_text": sub.audio_text or "",
                "sora_prompt": sub.sora_prompt or "",
                "scene_override": sub_scene_override,
                "scene_override_locked": sub_scene_override_locked,
                "selected_card_ids": sub_selected_card_ids,
                "subjects": sub_subjects_payload,
                "image_generate_status": sub.image_generate_status or "idle",
                "image_generate_progress": sub.image_generate_progress or "",
                "image_generate_error": sub.image_generate_error or "",
                "video_generate_status": video_generate_status,
                "video_generate_progress": video_generate_progress,
                "video_generate_error": video_generate_error,
                "current_image": current_payload,
                "candidates": candidate_payload,
                "videos": video_payload
            })

        subjects_payload = []
        for card_id in selected_card_ids_by_storyboard2_shot.get(shot.id, []):
            card = card_map.get(card_id)
            if not card:
                continue
            preview_image = reference_image_map.get(card_id) or uploaded_image_map.get(card_id) or ""
            subjects_payload.append({
                "id": card.id,
                "name": card.name or "",
                "alias": card.alias or "",
                "card_type": card.card_type or "",
                "preview_image": preview_image
            })

        shot_payload.append({
            "id": shot.id,
            "source_shot_id": shot.source_shot_id,
            "shot_label": str(shot.shot_number),
            "excerpt": shot.excerpt or "",
            "selected_card_ids": selected_card_ids_by_storyboard2_shot.get(shot.id, []),
            "subjects": subjects_payload,
            "sub_shots": sub_payload
        })

    available_subjects = []
    for card in all_library_cards:
        preview_image = reference_image_map.get(card.id) or uploaded_image_map.get(card.id) or ""
        available_subjects.append({
            "id": card.id,
            "name": card.name or "",
            "alias": card.alias or "",
            "card_type": card.card_type or "",
            "preview_image": preview_image
        })

    return {
        "episode_id": episode_id,
        "available_subjects": available_subjects,
        "shots": shot_payload
    }

def _resolve_storyboard2_selected_card_ids(storyboard2_shot: models.Storyboard2Shot, db: Session) -> List[int]:
    selected_card_ids = _parse_storyboard2_card_ids(storyboard2_shot.selected_card_ids)
    if selected_card_ids:
        return selected_card_ids

    if storyboard2_shot.source_shot_id:
        source_shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id == storyboard2_shot.source_shot_id
        ).first()
        if source_shot:
            return _parse_storyboard2_card_ids(source_shot.selected_card_ids)

    return []

def _is_scene_subject_card_type(card_type: str) -> bool:
    card_type_text = str(card_type or "").strip().lower()
    if not card_type_text:
        return False
    if card_type_text == "scene":
        return True
    if "场景" in card_type_text:
        return True
    if "鍦烘櫙" in card_type_text:
        return True
    return False

def _subject_type_sort_key(card_type: str) -> int:
    normalized = str(card_type or "").strip()
    if normalized == "角色":
        return 0
    if normalized == "场景":
        return 1
    if normalized == "道具":
        return 2
    if normalized == SOUND_CARD_TYPE:
        return 3
    return 9

def _normalize_storyboard2_video_status(status: str, default_value: str = "processing") -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"completed", "success", "succeeded", "done"}:
        return "completed"
    if normalized in {"failed", "failure", "error", "cancelled", "canceled", "timeout", "timed_out"}:
        return "failed"
    if normalized in {"submitted", "pending", "queued", "waiting"}:
        return "pending"
    if normalized in {"processing", "running", "in_progress", "preparing", "starting"}:
        return "processing"
    return default_value

def _is_storyboard2_video_processing(status: str) -> bool:
    return _normalize_storyboard2_video_status(status, default_value="processing") in {"pending", "processing"}

def _build_storyboard2_video_name_tag(video_record: models.Storyboard2SubShotVideo, db: Session) -> str:
    default_tag = f"storyboard2_subshot_{video_record.sub_shot_id}_video_{video_record.id}"
    try:
        sub_shot = db.query(models.Storyboard2SubShot).filter(
            models.Storyboard2SubShot.id == video_record.sub_shot_id
        ).first()
        if not sub_shot:
            return default_tag

        storyboard2_shot = db.query(models.Storyboard2Shot).filter(
            models.Storyboard2Shot.id == sub_shot.storyboard2_shot_id
        ).first()
        shot_label = str(getattr(storyboard2_shot, "shot_number", "x"))
        sub_index = str(getattr(sub_shot, "sub_shot_index", "x"))
        return f"storyboard2_shot_{shot_label}_sub_{sub_index}_video_{video_record.id}"
    except Exception:
        return default_tag

def _process_storyboard2_video_cover_and_cdn(
    video_record: models.Storyboard2SubShotVideo,
    db: Session,
    upstream_video_url: str,
    task_id: str,
    debug_dir: Optional[str] = None
):
    source_url = str(upstream_video_url or "").strip()
    if not source_url:
        return source_url, source_url, False, {"success": False, "error": "empty video url"}

    name_tag = _build_storyboard2_video_name_tag(video_record, db)
    task_id_value = str(task_id or video_record.task_id or "").strip()
    process_result = process_and_upload_video_with_cover(
        remote_url=source_url,
        task_id=task_id_value,
        name_tag=name_tag
    )

    if process_result.get("success") and str(process_result.get("cdn_url") or "").strip():
        final_url = str(process_result.get("cdn_url")).strip()
        return final_url, final_url, True, process_result

    return source_url, source_url, False, process_result

def _sync_storyboard2_processing_videos(episode_id: int, db: Session, max_count: int = 20) -> int:
    """
    兜底同步故事板2视频状态。
    作用：当后台轮询线程中断（例如服务重启）时，前端拉取故事板2数据仍能推进状态。
    """
    from video_service import check_video_status

    processing_videos = db.query(models.Storyboard2SubShotVideo).join(
        models.Storyboard2SubShot,
        models.Storyboard2SubShotVideo.sub_shot_id == models.Storyboard2SubShot.id
    ).join(
        models.Storyboard2Shot,
        models.Storyboard2SubShot.storyboard2_shot_id == models.Storyboard2Shot.id
    ).filter(
        models.Storyboard2Shot.episode_id == episode_id,
        models.Storyboard2SubShotVideo.is_deleted == False,
        models.Storyboard2SubShotVideo.status.in_(["submitted", "pending", "processing"])
    ).order_by(
        models.Storyboard2SubShotVideo.created_at.asc(),
        models.Storyboard2SubShotVideo.id.asc()
    ).limit(max_count).all()

    if not processing_videos:
        return 0

    updated_count = 0
    for video in processing_videos:
        task_id = (video.task_id or "").strip()
        if not task_id:
            if (video.status or "").strip().lower() != "failed":
                video.status = "failed"
                video.error_message = "缺少task_id，无法查询任务状态"
                video.progress = 0
                updated_count += 1
            continue

        try:
            status_info = check_video_status(task_id)
        except Exception as e:
            status_info = {
                "status": "query_failed",
                "video_url": "",
                "error_message": f"查询异常: {str(e)}",
                "progress": 0,
                "cdn_uploaded": False,
                "query_ok": False,
                "query_transient": True
            }

        if is_transient_video_status_error(status_info):
            continue

        normalized_status = _normalize_storyboard2_video_status(
            status_info.get("status"),
            default_value="processing"
        )
        try:
            progress = int(status_info.get("progress", 0) or 0)
        except Exception:
            progress = 0
        progress = max(0, min(progress, 100))
        error_message = str(status_info.get("error_message") or "").strip()
        video_url = str(status_info.get("video_url") or "").strip()
        cdn_uploaded = bool(status_info.get("cdn_uploaded", False))

        if normalized_status == "completed":
            if not video_url:
                normalized_status = "failed"
                error_message = error_message or "任务完成但未返回视频地址"
            else:
                final_video_url = video_url
                final_thumbnail_url = video_url
                final_cdn_uploaded = cdn_uploaded

                if not final_cdn_uploaded:
                    processed_video_url, processed_thumbnail_url, processed_cdn_uploaded, _process_meta = _process_storyboard2_video_cover_and_cdn(
                        video_record=video,
                        db=db,
                        upstream_video_url=video_url,
                        task_id=task_id,
                        debug_dir=None
                    )
                    final_video_url = processed_video_url or final_video_url
                    final_thumbnail_url = processed_thumbnail_url or final_thumbnail_url
                    final_cdn_uploaded = bool(processed_cdn_uploaded)

                if (
                    (video.status or "").strip().lower() != "completed"
                    or (video.video_url or "").strip() != final_video_url
                    or int(video.progress or 0) != 100
                    or bool(video.cdn_uploaded) != final_cdn_uploaded
                    or (video.error_message or "")
                ):
                    video.status = "completed"
                    video.video_url = final_video_url
                    if final_thumbnail_url:
                        video.thumbnail_url = final_thumbnail_url
                    video.progress = 100
                    video.error_message = ""
                    video.cdn_uploaded = final_cdn_uploaded
                    updated_count += 1
                billing_service.finalize_charge_entry(
                    db,
                    billing_key=f"video:storyboard2:{video.sub_shot_id}:task:{task_id}",
                )
                continue

        if normalized_status == "failed":
            final_error = error_message or "任务失败"
            if (
                (video.status or "").strip().lower() != "failed"
                or (video.error_message or "") != final_error
                or int(video.progress or 0) != 0
            ):
                video.status = "failed"
                video.error_message = final_error
                video.progress = 0
                updated_count += 1
            billing_service.reverse_charge_entry(
                db,
                billing_key=f"video:storyboard2:{video.sub_shot_id}:task:{task_id}",
                reason="provider_failed",
            )
            continue

        # pending / processing
        target_status = normalized_status if normalized_status in {"pending", "processing"} else "processing"
        target_progress = max(0, min(progress, 99))
        if (
            (video.status or "").strip().lower() != target_status
            or int(video.progress or 0) != target_progress
            or (video.error_message or "")
        ):
            video.status = target_status
            video.progress = target_progress
            video.error_message = ""
            updated_count += 1

    if updated_count > 0:
        db.commit()

    return updated_count

@router.get("/api/episodes/{episode_id}/storyboard2")

async def get_storyboard2_data(
    episode_id: int,
    initialize_if_empty: bool = True,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取故事板2数据，首次为空时从详细分镜初始化"""
    _verify_episode_permission(episode_id, user, db)

    initialized_now = False
    if initialize_if_empty:
        initialized_now = _ensure_storyboard2_initialized(episode_id, db)

    # 回收服务重启后遗留的镜头图processing状态，避免前端一直显示“生成中”。
    try:
        recovered_images = _recover_orphan_storyboard2_image_tasks(episode_id, db)
        if recovered_images > 0:
            print(f"[故事板2镜头图状态回收] episode_id={episode_id} recovered={recovered_images}")
    except Exception as e:
        print(f"[故事板2镜头图状态回收] episode_id={episode_id} 回收失败: {str(e)}")

    # 兜底同步：即使后台轮询线程中断，也能在页面轮询时推进状态
    try:
        _sync_storyboard2_processing_videos(episode_id, db)
    except Exception as e:
        print(f"[故事板2视频状态同步] episode_id={episode_id} 同步失败: {str(e)}")

    payload = _serialize_storyboard2_board(episode_id, db)
    payload["initialized_now"] = initialized_now
    return payload

@router.post("/api/episodes/{episode_id}/storyboard2/batch-generate-sora-prompts")

async def batch_generate_storyboard2_sora_prompts(
    episode_id: int,
    request: Storyboard2BatchGenerateSoraPromptsRequest,
    background_tasks: BackgroundTasks,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """批量生成故事板2分镜的Sora提示词（后台任务）"""
    episode = _verify_episode_permission(episode_id, user, db)

    # 首次为空时自动初始化，确保可生成对象存在
    _ensure_storyboard2_initialized(episode_id, db)

    query = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.episode_id == episode_id
    )
    if request.shot_ids:
        query = query.filter(models.Storyboard2Shot.id.in_(request.shot_ids))

    shot_count = query.count()
    if shot_count == 0:
        raise HTTPException(status_code=400, detail="没有选择有效的镜头")

    print(
        f"[SoraSubjectDebug][storyboard2_batch_request] episode_id={episode_id} "
        f"requested_shot_ids={request.shot_ids if request.shot_ids else 'ALL'} "
        f"matched_shot_count={shot_count}"
    )

    episode.batch_generating_storyboard2_prompts = True
    submitted_count = 0
    storyboard2_shots = query.order_by(
        models.Storyboard2Shot.display_order.asc(),
        models.Storyboard2Shot.shot_number.asc(),
        models.Storyboard2Shot.id.asc()
    ).all()
    for storyboard2_shot in storyboard2_shots:
        try:
            _submit_storyboard2_prompt_task(db, storyboard2_shot=storyboard2_shot)
            submitted_count += 1
        except Exception as exc:
            print(f"[故事板2批量Sora提交失败] shot_id={storyboard2_shot.id} error={str(exc)}")

    _refresh_storyboard2_prompt_batch_state(episode_id, db)
    db.commit()

    return {
        "message": f"故事板2批量生成任务已提交，共 {submitted_count} 个镜头。",
        "total_count": shot_count,
        "submitted_count": submitted_count,
    }
