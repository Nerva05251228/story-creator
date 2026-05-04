import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from io import BytesIO
from threading import Lock, Thread
from functools import partial
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
import requests
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import func
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import billing_service
import image_platform_client
import models
from api.services import managed_generation
from api.routers import storyboard2
from ai_config import get_ai_config
from ai_service import get_prompt_by_key
from auth import get_current_user
from dashboard_service import log_file_task_event, sync_managed_task_to_dashboard
from database import SessionLocal, get_db
from managed_generation_service import ACTIVE_MANAGED_SESSION_STATUSES
from api.services import billing_charges
from api.services import storyboard_defaults
from api.services import storyboard_reference_assets
from api.services import storyboard_sync
from api.services import storyboard_video_generation_limits
from api.services import storyboard_video_settings
from api.services import storyboard_video_payload
from api.services import voiceover_data
from api.services.simple_storyboard_batches import (
    _get_simple_storyboard_batch_summary,
)
from storyboard_prompt_templates import inject_large_shot_template_content
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
from video_api_config import get_video_api_headers, get_video_task_create_url, get_video_task_status_url
from video_service import (
    check_video_status,
    is_transient_video_status_error,
    process_and_upload_video_with_cover,
)
from image_generation_service import (
    download_and_upload_image,
    get_image_status_api_url,
    get_image_submit_api_url,
    jimeng_generate_image_with_polling,
)
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
    Storyboard2GenerateImagesRequest,
    Storyboard2GenerateVideoRequest,
    Storyboard2SetCurrentImageRequest,
    Storyboard2UpdateShotRequest,
    Storyboard2UpdateSubShotRequest,
    StoryboardAnalyzeResponse,
)


router = APIRouter()


executor = ThreadPoolExecutor(max_workers=10)

storyboard2_active_image_tasks = set()

storyboard2_active_image_tasks_lock = Lock()

STORYBOARD2_IMAGE_PROMPT_KEY = "storyboard2_image_prompt_prefix"
STORYBOARD2_IMAGE_PROMPT_DEFAULT = "生成动漫风格的图片"
STORYBOARD2_VIDEO_PROMPT_KEY = "generate_storyboard2_video_prompts"
GROK_RULE_DEFAULT = "严格按照提示词生视频，不要出现其他人物"

ALLOWED_CARD_TYPES = storyboard_sync.ALLOWED_CARD_TYPES

SOUND_CARD_TYPE = "声音"

SQLITE_LOCK_RETRY_DELAYS = (0.3, 0.8, 1.5, 3.0)

_SUBJECT_MATCH_STOP_FRAGMENTS = storyboard_sync.SUBJECT_MATCH_STOP_FRAGMENTS

VOICEOVER_TTS_METHOD_SAME = voiceover_data.VOICEOVER_TTS_METHOD_SAME
VOICEOVER_TTS_METHOD_VECTOR = voiceover_data.VOICEOVER_TTS_METHOD_VECTOR
VOICEOVER_TTS_METHOD_EMO_TEXT = voiceover_data.VOICEOVER_TTS_METHOD_EMO_TEXT
VOICEOVER_TTS_METHOD_AUDIO = voiceover_data.VOICEOVER_TTS_METHOD_AUDIO
VOICEOVER_TTS_ALLOWED_METHODS = voiceover_data.VOICEOVER_TTS_ALLOWED_METHODS
VOICEOVER_TTS_VECTOR_KEYS = voiceover_data.VOICEOVER_TTS_VECTOR_KEYS

SIMPLE_STORYBOARD_TIMEOUT_SECONDS = 3600

SIMPLE_STORYBOARD_TIMEOUT_ERROR = "简单分镜生成超时（超过 1 小时），已自动标记为失败，请重新生成。"

SORA_REFERENCE_PROMPT_INSTRUCTION = "请你参考这段提示词中的人物站位进行编写新的提示词："

ACTIVE_VIDEO_GENERATION_STATUSES = storyboard_video_generation_limits.ACTIVE_VIDEO_GENERATION_STATUSES

ACTIVE_MANAGED_TASK_STATUSES = storyboard_video_generation_limits.ACTIVE_MANAGED_TASK_STATUSES

MAX_ACTIVE_VIDEO_GENERATIONS_PER_SHOT = storyboard_video_generation_limits.MAX_ACTIVE_VIDEO_GENERATIONS_PER_SHOT


_safe_json_dumps = billing_charges.safe_json_dumps
_record_storyboard2_video_charge = billing_charges.record_storyboard2_video_charge
_record_storyboard2_image_charge = billing_charges.record_storyboard2_image_charge


def _resolve_storyboard_video_billing_model(shot: models.StoryboardShot) -> str:
    return billing_charges.resolve_storyboard_video_billing_model(
        shot,
        resolve_model_by_provider=_resolve_storyboard_video_model_by_provider,
        default_model=DEFAULT_STORYBOARD_VIDEO_MODEL,
    )


def _record_storyboard_video_charge(
    db: Session,
    *,
    shot: models.StoryboardShot,
    task_id: str,
    stage: str = "video_generate",
    detail_payload: Optional[Dict[str, Any]] = None,
):
    return billing_charges.record_storyboard_video_charge(
        db,
        shot=shot,
        task_id=task_id,
        model_name=_resolve_storyboard_video_billing_model(shot),
        stage=stage,
        detail_payload=detail_payload,
    )


_STORYBOARD_VIDEO_MODEL_CONFIG = storyboard_video_settings.STORYBOARD_VIDEO_MODEL_CONFIG
MOTI_STORYBOARD_VIDEO_MODELS = storyboard_video_settings.MOTI_STORYBOARD_VIDEO_MODELS
_normalize_storyboard_video_appoint_account = storyboard_video_settings.normalize_storyboard_video_appoint_account
_normalize_storyboard_video_model = storyboard_video_settings.normalize_storyboard_video_model
_normalize_storyboard_video_aspect_ratio = storyboard_video_settings.normalize_storyboard_video_aspect_ratio
_normalize_storyboard_video_duration = storyboard_video_settings.normalize_storyboard_video_duration
_normalize_storyboard_video_resolution_name = storyboard_video_settings.normalize_storyboard_video_resolution_name
_resolve_storyboard_video_provider = storyboard_video_settings.resolve_storyboard_video_provider
_is_moti_storyboard_video_model = storyboard_video_settings.is_moti_storyboard_video_model
_resolve_storyboard_video_model_by_provider = storyboard_video_settings.resolve_storyboard_video_model_by_provider
_map_storyboard_prompt_template_duration = storyboard_video_settings.map_storyboard_prompt_template_duration
_is_storyboard_shot_duration_override_enabled = storyboard_video_settings.is_storyboard_shot_duration_override_enabled
_is_storyboard_shot_model_override_enabled = storyboard_video_settings.is_storyboard_shot_model_override_enabled
_get_episode_storyboard_video_settings = storyboard_video_settings.get_episode_storyboard_video_settings
_get_effective_storyboard_video_settings_for_shot = storyboard_video_settings.get_effective_storyboard_video_settings_for_shot
_build_unified_storyboard_video_task_payload = storyboard_video_payload._build_unified_storyboard_video_task_payload
_resolve_selected_cards = storyboard_reference_assets.resolve_selected_cards

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

_normalize_subject_detail_entry = storyboard_sync.normalize_subject_detail_entry

_build_subject_detail_map = storyboard_sync.build_subject_detail_map

_normalize_storyboard_generation_subjects = storyboard_sync.normalize_storyboard_generation_subjects

_find_meaningful_common_fragment = storyboard_sync.find_meaningful_common_fragment

_infer_storyboard_role_name_from_shot = storyboard_sync.infer_storyboard_role_name_from_shot

_resolve_storyboard_subject_name = storyboard_sync.resolve_storyboard_subject_name

_reconcile_storyboard_shot_subjects = storyboard_sync.reconcile_storyboard_shot_subjects

def _safe_audio_duration_seconds(value: Any) -> float:
    try:
        duration_seconds = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return duration_seconds if duration_seconds > 0 else 0.0

_voiceover_shot_match_key = voiceover_data.voiceover_shot_match_key
_merge_voiceover_line_preserving_tts = voiceover_data.merge_voiceover_line_preserving_tts
_merge_voiceover_dialogue_preserving_tts = voiceover_data.merge_voiceover_dialogue_preserving_tts
_merge_voiceover_shots_preserving_extensions = voiceover_data.merge_voiceover_shots_preserving_extensions

_voiceover_default_test_mp3_path = partial(voiceover_data.voiceover_default_test_mp3_path, __file__)

_voiceover_default_vector_config = voiceover_data.voiceover_default_vector_config

_voiceover_default_shared_data = voiceover_data.voiceover_default_shared_data
_voiceover_default_reference_item = partial(
    voiceover_data.voiceover_default_reference_item,
    _voiceover_default_test_mp3_path,
)

_safe_float = voiceover_data.safe_float
_normalize_voiceover_vector_config = voiceover_data.normalize_voiceover_vector_config
_normalize_voiceover_setting_template_payload = voiceover_data.normalize_voiceover_setting_template_payload

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

_voiceover_default_line_tts = voiceover_data.voiceover_default_line_tts
_normalize_voiceover_line_tts = voiceover_data.normalize_voiceover_line_tts
_ensure_voiceover_shot_line_fields = voiceover_data.ensure_voiceover_shot_line_fields
_normalize_voiceover_shots_for_tts = voiceover_data.normalize_voiceover_shots_for_tts
_extract_voiceover_tts_line_states = voiceover_data.extract_voiceover_tts_line_states
_find_voiceover_line_entry = voiceover_data.find_voiceover_line_entry
_parse_episode_voiceover_payload = voiceover_data.parse_episode_voiceover_payload
_voiceover_first_reference_id = voiceover_data.voiceover_first_reference_id
_iter_voiceover_lines = voiceover_data.iter_voiceover_lines

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

_DETAIL_IMAGES_MODEL_CONFIG = {
    "seedream-4.0": {},
    "seedream-4.1": {},
    "seedream-4.5": {},
    "seedream-4.6": {},
    "nano-banana-2": {},
    "nano-banana-pro": {},
    "gpt-image-2": {},
}


_get_pydantic_fields_set = storyboard_defaults.get_pydantic_fields_set
_normalize_detail_images_provider = storyboard_defaults.normalize_detail_images_provider
_resolve_episode_detail_images_provider = storyboard_defaults.resolve_episode_detail_images_provider
_normalize_detail_images_model = storyboard_defaults.normalize_detail_images_model


def _build_image_generation_debug_meta(
    model_key: Optional[str],
    provider: Optional[str] = None,
    actual_model: Optional[str] = None,
    has_reference_images: bool = False,
) -> dict:
    normalized_model = _normalize_detail_images_model(model_key, default_model="seedream-4.0")
    try:
        route = image_platform_client.resolve_image_route(normalized_model, provider=provider)
    except Exception:
        route = {}
    resolved_provider = str(
        provider
        or route.get("provider")
        or (_DETAIL_IMAGES_MODEL_CONFIG.get(normalized_model) or {}).get("provider")
        or ""
    ).strip().lower()
    resolved_actual_model = str(
        actual_model
        or route.get("model")
        or (_DETAIL_IMAGES_MODEL_CONFIG.get(normalized_model) or {}).get("actual_model")
        or normalized_model
    ).strip()
    return {
        "requested_model": normalized_model,
        "provider": resolved_provider,
        "actual_model": resolved_actual_model,
        "submit_api_url": get_image_submit_api_url(
            model_name=normalized_model,
            provider=resolved_provider,
            has_reference_images=has_reference_images,
        ),
        "status_api_url_template": get_image_status_api_url(
            task_id="{task_id}",
            model_name=normalized_model,
            provider=resolved_provider,
        ),
    }


_normalize_storyboard2_video_duration = storyboard_defaults.normalize_storyboard2_video_duration
_normalize_storyboard2_image_cw = storyboard_defaults.normalize_storyboard2_image_cw
_get_first_episode_for_storyboard_defaults = storyboard_defaults.get_first_episode_for_storyboard_defaults
_build_episode_storyboard_sora_create_values = (
    lambda script_id, episode_payload, db: storyboard_defaults.build_episode_storyboard_sora_create_values(
        script_id,
        episode_payload,
        db,
        default_storyboard_video_model=DEFAULT_STORYBOARD_VIDEO_MODEL,
        storyboard_video_model_config=_STORYBOARD_VIDEO_MODEL_CONFIG,
        normalize_storyboard_video_model=_normalize_storyboard_video_model,
        normalize_storyboard_video_aspect_ratio=_normalize_storyboard_video_aspect_ratio,
        normalize_storyboard_video_duration=_normalize_storyboard_video_duration,
        normalize_storyboard_video_resolution_name=_normalize_storyboard_video_resolution_name,
        normalize_jimeng_ratio=_normalize_jimeng_ratio,
        normalize_storyboard_video_appoint_account=_normalize_storyboard_video_appoint_account,
    )
)


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

_sync_subjects_to_database = storyboard_sync.sync_subjects_to_database

_sync_storyboard_to_shots = storyboard_sync.sync_storyboard_to_shots

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

_get_next_managed_reserved_variant_index = managed_generation._get_next_managed_reserved_variant_index
_create_managed_reserved_shot = managed_generation._create_managed_reserved_shot
_reserve_legacy_managed_session_slots = managed_generation._reserve_legacy_managed_session_slots
stop_managed_generation = managed_generation.stop_managed_generation
get_managed_tasks = managed_generation.get_managed_tasks
get_managed_session_status = managed_generation.get_managed_session_status


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


_get_storyboard_shot_family_identity = storyboard_video_generation_limits.get_storyboard_shot_family_identity
_get_storyboard_shot_family_filters = storyboard_video_generation_limits.get_storyboard_shot_family_filters
_count_active_video_generations_for_shot_family = storyboard_video_generation_limits.count_active_video_generations_for_shot_family
_is_storyboard_shot_generation_active = storyboard_video_generation_limits.is_storyboard_shot_generation_active
_build_active_video_generation_limit_message = storyboard_video_generation_limits.build_active_video_generation_limit_message
_ensure_storyboard_video_generation_slots_available = storyboard_video_generation_limits.ensure_storyboard_video_generation_slots_available

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

def _build_storyboard_video_text_and_images_content(full_prompt: str, image_urls: List[str]) -> list:
    content = [{"type": "text", "text": full_prompt}]
    for url in image_urls or []:
        image_url = str(url or "").strip()
        if image_url:
            content.append({"type": "image_url", "image_url": image_url})
    return content


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

# Compatibility exports for direct callers while storyboard2 routes live in api.routers.storyboard2.
Storyboard2SetCurrentImageRequest = storyboard2.Storyboard2SetCurrentImageRequest
Storyboard2BatchGenerateSoraPromptsRequest = storyboard2.Storyboard2BatchGenerateSoraPromptsRequest
Storyboard2GenerateImagesRequest = storyboard2.Storyboard2GenerateImagesRequest
Storyboard2GenerateVideoRequest = storyboard2.Storyboard2GenerateVideoRequest
Storyboard2UpdateShotRequest = storyboard2.Storyboard2UpdateShotRequest
Storyboard2UpdateSubShotRequest = storyboard2.Storyboard2UpdateSubShotRequest
_verify_episode_permission = storyboard2._verify_episode_permission
_parse_storyboard2_card_ids = storyboard2._parse_storyboard2_card_ids
_clean_scene_ai_prompt_text = storyboard2._clean_scene_ai_prompt_text
_extract_scene_description_from_card_ids = storyboard2._extract_scene_description_from_card_ids
_resolve_storyboard2_scene_override_text = storyboard2._resolve_storyboard2_scene_override_text
_pick_storyboard2_source_shots = storyboard2._pick_storyboard2_source_shots
_ensure_storyboard2_initialized = storyboard2._ensure_storyboard2_initialized
_mark_storyboard2_image_task_active = storyboard2._mark_storyboard2_image_task_active
_mark_storyboard2_image_task_inactive = storyboard2._mark_storyboard2_image_task_inactive
_is_storyboard2_image_task_active = storyboard2._is_storyboard2_image_task_active
_recover_orphan_storyboard2_image_tasks = storyboard2._recover_orphan_storyboard2_image_tasks
_serialize_storyboard2_board = storyboard2._serialize_storyboard2_board
_get_storyboard2_sub_shot_with_permission = storyboard2._get_storyboard2_sub_shot_with_permission
_get_storyboard2_shot_with_permission = storyboard2._get_storyboard2_shot_with_permission
_resolve_storyboard2_selected_card_ids = storyboard2._resolve_storyboard2_selected_card_ids
_is_scene_subject_card_type = storyboard2._is_scene_subject_card_type
_subject_type_sort_key = storyboard2._subject_type_sort_key
_get_optional_prompt_config_content = storyboard2._get_optional_prompt_config_content
_save_storyboard2_image_debug = storyboard2._save_storyboard2_image_debug
_save_storyboard2_video_debug = storyboard2._save_storyboard2_video_debug
_normalize_storyboard2_video_status = storyboard2._normalize_storyboard2_video_status
_is_storyboard2_video_processing = storyboard2._is_storyboard2_video_processing
_build_storyboard2_video_name_tag = storyboard2._build_storyboard2_video_name_tag
_process_storyboard2_video_cover_and_cdn = storyboard2._process_storyboard2_video_cover_and_cdn
_sync_storyboard2_processing_videos = storyboard2._sync_storyboard2_processing_videos
_recover_storyboard2_video_polling = storyboard2._recover_storyboard2_video_polling
_build_storyboard2_subject_text = storyboard2._build_storyboard2_subject_text
_refresh_storyboard2_prompt_batch_state = storyboard2._refresh_storyboard2_prompt_batch_state
_submit_storyboard2_prompt_task = storyboard2._submit_storyboard2_prompt_task
get_storyboard2_data = storyboard2.get_storyboard2_data
batch_generate_storyboard2_sora_prompts = storyboard2.batch_generate_storyboard2_sora_prompts
generate_storyboard2_sub_shot_images = storyboard2.generate_storyboard2_sub_shot_images
generate_storyboard2_sub_shot_video = storyboard2.generate_storyboard2_sub_shot_video
update_storyboard2_shot = storyboard2.update_storyboard2_shot
update_storyboard2_sub_shot = storyboard2.update_storyboard2_sub_shot
delete_storyboard2_video = storyboard2.delete_storyboard2_video
set_storyboard2_current_image = storyboard2.set_storyboard2_current_image
delete_storyboard2_image = storyboard2.delete_storyboard2_image
