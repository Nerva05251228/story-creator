from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Header
from sqlalchemy.orm import Session
from sqlalchemy import text, func, or_, and_, case
from sqlalchemy.exc import OperationalError
from typing import List, Optional, Dict, Any, Tuple
from pydantic import BaseModel
from datetime import datetime
import os
import shutil
import uuid
import json
import requests
import mimetypes
import asyncio
import re
import time
import hashlib
import subprocess
import tempfile
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse

from api.routers import image_generation, media, pages, public
from env_config import get_env, is_placeholder_env_value, load_app_env


load_app_env()
try:
    import msvcrt
except ImportError:
    msvcrt = None
try:
    import fcntl
except ImportError:
    fcntl = None

import models
import billing_service
import image_platform_client
from database import IS_SQLITE, engine, get_db, SessionLocal
from db_compat import boolean_sql, datetime_sql, get_table_columns, rename_column_if_needed, table_exists
from runtime_load import request_load_tracker
from startup_external_prewarms import start_external_cache_prewarms
from startup_runtime import should_enable_background_pollers
from startup_schema_policy import should_apply_runtime_postgres_alter
from auth import get_current_user, verify_library_owner
from ai_config import (
    DEFAULT_TEXT_MODEL_ID,
    RELAY_PROVIDER_KEY,
    build_ai_debug_config,
    get_ai_config,
    get_ai_provider_catalog,
    get_ai_provider_public_configs,
    get_default_ai_provider_key,
    get_provider_model_options,
    normalize_ai_provider_key,
    resolve_ai_model_option,
)
from text_relay_service import (
    get_cached_models_payload,
    submit_and_persist_text_task,
    sync_models_from_upstream,
    text_relay_poller,
)
from ai_service import (
    generate_storyboard_prompts,
    stage1_generate_initial_storyboard, stage2_generate_subject_prompts,
    get_prompt_by_key
)
from storyboard_prompt_templates import (
    build_large_shot_prompt_rule,
    get_default_large_shot_templates,
    inject_large_shot_template_content,
    is_legacy_large_shot_prompt_rule,
)
from storyboard_variant import (
    build_duplicate_shot_payload,
    build_storyboard_image_variant_payload,
    build_storyboard_sync_variant_payload,
    choose_storyboard_reference_source,
)
from dashboard_service import (
    DASHBOARD_STATUS_LABELS,
    DASHBOARD_TASK_TYPE_LABELS,
    log_debug_task_event,
    log_file_task_event,
    summarize_dashboard_batch_events,
    sync_managed_task_to_dashboard,
    sync_voiceover_tts_task_to_dashboard,
)
from utils import upload_to_cdn
from PIL import Image, ImageDraw, ImageFont
from video_service import (
    poller,
    update_shot_status,
    process_and_upload_video_with_cover,
    check_video_status,
    is_transient_video_status_error,
    normalize_video_generation_status,
)
from video_api_config import (
    get_required_video_api_base_url,
    get_video_api_headers,
    get_video_provider_stats_url,
    get_video_task_create_url,
    get_video_task_status_url,
    get_video_tasks_cancel_url,
)
from video_provider_accounts import (
    get_cached_video_provider_accounts,
)
from image_generation_service import (
    image_poller, MODEL_CONFIGS, submit_image_generation,
    check_task_status, download_and_upload_image, jimeng_generate_image_with_polling,
    create_jimeng_image_task, get_image_task_status, is_jimeng_image_model,
    normalize_image_model_key, is_moti_image_model, is_transient_image_status_error,
    submit_moti_standard_image_generation, get_image_submit_api_url,
    get_image_status_api_url, resolve_jimeng_actual_model
)
from dashboard_query_service import (
    is_dashboard_task_query_supported,
    query_dashboard_task,
)
from managed_generation_service import managed_poller, ACTIVE_MANAGED_SESSION_STATUSES
from model_pricing_poller import model_pricing_poller
from text_llm_queue import run_text_llm_request
from simple_storyboard_rules import (
    generate_simple_storyboard_shots,
    get_default_rule_config,
    normalize_rule_config,
)
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
from threading import Thread, Lock

# 创建线程池（用于处理同步阻塞调用）
executor = ThreadPoolExecutor(max_workers=10)

# 故事板2镜头图运行中任务（进程内）
storyboard2_active_image_tasks = set()
storyboard2_active_image_tasks_lock = Lock()
background_poller_lock = Lock()
simple_storyboard_batch_update_lock = Lock()
background_pollers_started = False
startup_bootstrap_lock_handle = None


def _safe_json_dumps(payload: Any) -> str:
    try:
        return json.dumps(payload or {}, ensure_ascii=False)
    except Exception:
        return ""


def _resolve_storyboard_video_billing_model(shot: models.StoryboardShot) -> str:
    provider = str(getattr(shot, "provider", "") or "").strip().lower()
    if provider == "yijia-grok":
        provider = "yijia"
    return str(
        _resolve_storyboard_video_model_by_provider(
            provider,
            default_model=getattr(shot, "storyboard_video_model", None) or getattr(shot, "provider", None) or DEFAULT_STORYBOARD_VIDEO_MODEL,
        )
    )


def _record_card_image_charge(
    db: Session,
    *,
    card: models.SubjectCard,
    model_name: str,
    provider: str,
    resolution: str = "",
    task_id: str,
    quantity: int,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    return None
    context = billing_service.get_card_episode_context(db, card_id=int(card.id))
    if not context:
        return None
    try:
        return billing_service.create_charge_entry(
            db,
            user_id=int(context["user_id"]),
            script_id=int(context["script_id"]),
            episode_id=int(context["episode_id"]),
            category="image",
            stage="card_image_generate",
            provider=str(provider or ""),
            model_name=str(model_name or ""),
            resolution=str(resolution or ""),
            quantity=max(1, int(quantity or 1)),
            billing_key=f"image:card:{card.id}:task:{task_id}",
            operation_key=f"image:card:{card.id}",
            initial_status="pending",
            card_id=int(card.id),
            attempt_index=1,
            external_task_id=str(task_id or ""),
            detail_json=_safe_json_dumps(detail_payload),
        )
    except ValueError:
        return None


def _record_storyboard_image_charge(
    db: Session,
    *,
    shot: models.StoryboardShot,
    model_name: str,
    provider: str,
    resolution: str = "",
    task_id: str,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    return None
    context = billing_service.get_shot_episode_context(db, shot_id=int(shot.id))
    if not context:
        return None
    try:
        return billing_service.create_charge_entry(
            db,
            user_id=int(context["user_id"]),
            script_id=int(context["script_id"]),
            episode_id=int(context["episode_id"]),
            category="image",
            stage="storyboard_image_generate",
            provider=str(provider or ""),
            model_name=str(model_name or ""),
            resolution=str(resolution or ""),
            quantity=1,
            billing_key=f"image:storyboard:{shot.id}:task:{task_id}",
            operation_key=f"image:storyboard:{shot.id}",
            initial_status="pending",
            shot_id=int(shot.id),
            attempt_index=1,
            external_task_id=str(task_id or ""),
            detail_json=_safe_json_dumps(detail_payload),
        )
    except ValueError:
        return None


def _record_detail_image_charge(
    db: Session,
    *,
    detail_img: models.ShotDetailImage,
    shot: models.StoryboardShot,
    model_name: str,
    provider: str,
    resolution: str = "",
    task_id: str,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    return None
    context = billing_service.get_shot_episode_context(db, shot_id=int(shot.id))
    if not context:
        return None
    try:
        return billing_service.create_charge_entry(
            db,
            user_id=int(context["user_id"]),
            script_id=int(context["script_id"]),
            episode_id=int(context["episode_id"]),
            category="image",
            stage="detail_images",
            provider=str(provider or ""),
            model_name=str(model_name or ""),
            resolution=str(resolution or ""),
            quantity=1,
            billing_key=f"image:detail:{detail_img.id}:task:{task_id}",
            operation_key=f"image:detail:{shot.id}:sub{detail_img.sub_shot_index}",
            initial_status="pending",
            shot_id=int(shot.id),
            sub_shot_id=int(detail_img.id),
            attempt_index=1,
            external_task_id=str(task_id or ""),
            detail_json=_safe_json_dumps(detail_payload),
        )
    except ValueError:
        return None


def _record_storyboard_video_charge(
    db: Session,
    *,
    shot: models.StoryboardShot,
    task_id: str,
    stage: str = "video_generate",
    detail_payload: Optional[Dict[str, Any]] = None,
):
    context = billing_service.get_shot_episode_context(db, shot_id=int(shot.id))
    if not context:
        return None
    try:
        return billing_service.create_charge_entry(
            db,
            user_id=int(context["user_id"]),
            script_id=int(context["script_id"]),
            episode_id=int(context["episode_id"]),
            category="video",
            stage=stage,
            provider=str(getattr(shot, "provider", "") or ""),
            model_name=_resolve_storyboard_video_billing_model(shot),
            quantity=max(1, int(getattr(shot, "duration", 0) or 0)),
            billing_key=f"video:shot:{shot.id}:task:{task_id}",
            operation_key=f"video:shot:{shot.id}",
            initial_status="pending",
            shot_id=int(shot.id),
            attempt_index=1,
            external_task_id=str(task_id or ""),
            detail_json=_safe_json_dumps(detail_payload),
        )
    except ValueError:
        return None


def _record_storyboard2_video_charge(
    db: Session,
    *,
    sub_shot: models.Storyboard2SubShot,
    storyboard2_shot: models.Storyboard2Shot,
    task_id: str,
    model_name: str,
    duration: int,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    context = billing_service.get_storyboard2_sub_shot_context(db, sub_shot_id=int(sub_shot.id))
    if not context:
        return None
    try:
        return billing_service.create_charge_entry(
            db,
            user_id=int(context["user_id"]),
            script_id=int(context["script_id"]),
            episode_id=int(context["episode_id"]),
            category="video",
            stage="storyboard2_video_generate",
            provider="yijia",
            model_name=str(model_name or "grok"),
            quantity=max(1, int(duration or 0)),
            billing_key=f"video:storyboard2:{sub_shot.id}:task:{task_id}",
            operation_key=f"video:storyboard2:{storyboard2_shot.id}:sub{sub_shot.id}",
            initial_status="pending",
            storyboard2_shot_id=int(storyboard2_shot.id),
            sub_shot_id=int(sub_shot.id),
            attempt_index=1,
            external_task_id=str(task_id or ""),
            detail_json=_safe_json_dumps(detail_payload),
        )
    except ValueError:
        return None


def _record_storyboard2_image_charge(
    db: Session,
    *,
    sub_shot: models.Storyboard2SubShot,
    storyboard2_shot: models.Storyboard2Shot,
    task_id: str,
    model_name: str,
    resolution: str = "",
    quantity: int,
    detail_payload: Optional[Dict[str, Any]] = None,
):
    return None
    context = billing_service.get_storyboard2_sub_shot_context(db, sub_shot_id=int(sub_shot.id))
    if not context:
        return None
    try:
        return billing_service.create_charge_entry(
            db,
            user_id=int(context["user_id"]),
            script_id=int(context["script_id"]),
            episode_id=int(context["episode_id"]),
            category="image",
            stage="storyboard2_image_generate",
            provider="jimeng",
            model_name=str(model_name or "图片 4.0"),
            resolution=str(resolution or ""),
            quantity=max(1, int(quantity or 1)),
            billing_key=f"image:storyboard2:{sub_shot.id}:task:{task_id}",
            operation_key=f"image:storyboard2:{storyboard2_shot.id}:sub{sub_shot.id}",
            initial_status="pending",
            storyboard2_shot_id=int(storyboard2_shot.id),
            sub_shot_id=int(sub_shot.id),
            attempt_index=1,
            external_task_id=str(task_id or ""),
            detail_json=_safe_json_dumps(detail_payload),
        )
    except ValueError:
        return None


def _read_utf8_text_file(file_path: str) -> str:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _load_storyboard2_prompt_base() -> str:
    return """原剧本段落：
{script_excerpt}

场景描述：
{scene_description}

候选主体（只能从这里选择）：
{subject_text}

说明：
- 角色主体会按“主体名-性格描述”的格式提供，场景/道具主体只写名称
- 生成结果里的 subjects 数组只填写“-”前的主体名称，不要把后面的性格描述写进去

输出 JSON，格式如下：
{{
  "timeline": [
    {{
      "time": "00s-03s",
      "subjects": ["主体A", "场景B"],
      "visual": "[镜头类型][景别/拍摄角度] 画面描述（至少20字）",
      "audio": "[角色] 说：\"台词\" 或 旁白：\"内容\""
    }},
    {{
      "time": "03s-06s",
      "subjects": ["主体A"],
      "visual": "[镜头类型][景别/拍摄角度] 画面描述（至少20字）",
      "audio": "(SFX:音效) 或 [角色] 说：\"台词\""
    }},
    {{
      "time": "06s-{safe_duration:02d}s",
      "subjects": ["主体A", "主体C"],
      "visual": "[镜头类型][景别/拍摄角度] 画面描述（至少20字）",
      "audio": "[角色] 说：\"台词\""
    }}
  ]
}}

要求：
1. 时长总计 {safe_duration} 秒，按剧情拆分为 3-5 个连续时间段
2. time 字段必须连续不重叠，最后一个时间段必须覆盖到 {safe_duration} 秒
3. 每个时间段必须包含 subjects 字段，类型为字符串数组
4. subjects 里的名称只能从“候选主体”中选择，禁止编造新主体名称
5. visual 只描述镜头、动作、微动作、情绪和场景，不改写剧情核心事件
6. audio 保留原文台词；有音效可用 (SFX:...)；没有台词可用旁白
7. 只输出 JSON，不要其他说明

{extra_style}"""


STORYBOARD2_IMAGE_PROMPT_KEY = "storyboard2_image_prompt_prefix"
STORYBOARD2_IMAGE_PROMPT_DEFAULT = "生成动漫风格的图片"
CHARACTER_THREE_VIEW_PROMPT_KEY = "character_three_view_image_prompt"
CHARACTER_THREE_VIEW_PROMPT_DEFAULT = "生人物三视图，生成全身三视图以及一张面部特写(最左边占满三分之一的位置是超大的面部特写，右边三分之二放正视图、侧视图、后视图，（正视图、侧视图、后视图并排）纯白背景"
GROK_RULE_KEY = "grok_rule"
GROK_RULE_DEFAULT = "严格按照提示词生视频，不要出现其他人物"
STORYBOARD2_VIDEO_PROMPT_KEY = "generate_storyboard2_video_prompts"
STORYBOARD2_VIDEO_PROMPT_DEFAULT = _load_storyboard2_prompt_base()
MANAGED_PROMPT_OPTIMIZE_KEY = "managed_retry_optimize_prompt"
MANAGED_PROMPT_OPTIMIZE_DEFAULT = """你是一位视频生成提示词优化助手。请在尽量保留原意、人物、场景、镜头顺序、时长和关键信息的前提下，改写下面这段完整视频提示词，使其更容易通过平台文字审核。

要求：
1. 不改变剧情核心、镜头时序、人物关系、时长和主要动作。
2. 删除、弱化或替换可能触发审核的问题表达，改为克制、中性、合规的说法。
3. 尽量保留原有的段落结构、镜头编号、旁白/台词格式。
4. 不要输出解释、分析、备注或 markdown，只输出修改后的完整提示词文本。
5. 如果原文包含过于激烈、违规、血腥、暴力、色情、仇恨、违法或明显不当的描述，请改写为平台更容易接受的表达。

当前失败原因：
{error_message}

原始完整提示词：
{full_prompt}"""

ALLOWED_CARD_TYPES = ("角色", "场景", "道具")
ALL_SUBJECT_CARD_TYPES = ("角色", "场景", "道具", "声音")
SOUND_CARD_TYPE = "声音"
SEEDANCE_AUDIO_MAX_COUNT = 3
SEEDANCE_AUDIO_MAX_TOTAL_SECONDS = 15.0
SEEDANCE_AUDIO_COUNT_ERROR = "请检查音频总数是否不超过3个"
SEEDANCE_AUDIO_DURATION_ERROR = "请检查音频时长总和是否小于15s"
SEEDANCE_AUDIO_VALIDATION_ERRORS = {
    SEEDANCE_AUDIO_COUNT_ERROR,
    SEEDANCE_AUDIO_DURATION_ERROR,
}

# 不可通过管理界面操作、且通用密码无效的保留账号
HIDDEN_USERS = {"test", "9f3a7c2e4b6d8a1c"}


def _get_private_password_env(name: str) -> str:
    value = (get_env(name, "") or "").strip()
    if is_placeholder_env_value(value):
        return ""
    return value


# 管理员通用密码（可登录任意非保留账号）
MASTER_PASSWORD = _get_private_password_env("MASTER_PASSWORD")
ADMIN_PANEL_PASSWORD = _get_private_password_env("ADMIN_PANEL_PASSWORD")
DEFAULT_STORYBOARD_VIDEO_MODEL = "Seedance 2.0 Fast"
MOTI_STORYBOARD_VIDEO_MODELS = (
    "Seedance 2.0 Fast VIP",
    "Seedance 2.0 Fast",
    "Seedance 2.0 VIP",
    "Seedance 2.0",
)
SQLITE_LOCK_RETRY_DELAYS = (0.3, 0.8, 1.5, 3.0)
STARTUP_BOOTSTRAP_LOCK_PATH = os.path.join(os.path.dirname(__file__), ".startup_bootstrap.lock")


def start_background_pollers(force: bool = False):
    global background_pollers_started
    enabled = force or should_enable_background_pollers()
    if not enabled:
        print("[startup] background pollers disabled for this process")
        return False

    with background_poller_lock:
        if background_pollers_started:
            return True
        poller.start()
        image_poller.start()
        managed_poller.start()
        text_relay_poller.start()
        voiceover_tts_poller.start()
        model_pricing_poller.start()
        _recover_storyboard2_video_polling()
        background_pollers_started = True
        print("[startup] background pollers enabled for this process")
        return True


def stop_background_pollers():
    global background_pollers_started
    with background_poller_lock:
        if not background_pollers_started:
            return
        poller.stop()
        image_poller.stop()
        managed_poller.stop()
        text_relay_poller.stop()
        voiceover_tts_poller.stop()
        model_pricing_poller.stop()
        background_pollers_started = False


def acquire_startup_bootstrap_lock(timeout_seconds: float = 300.0):
    global startup_bootstrap_lock_handle
    if startup_bootstrap_lock_handle is not None:
        return startup_bootstrap_lock_handle

    lock_file = open(STARTUP_BOOTSTRAP_LOCK_PATH, "a+b")
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"0")
        lock_file.flush()

    start_time = time.time()
    while True:
        try:
            lock_file.seek(0)
            if os.name == "nt" and msvcrt is not None:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            elif fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                startup_bootstrap_lock_handle = lock_file
                return lock_file
            startup_bootstrap_lock_handle = lock_file
            print(f"[startup] bootstrap lock acquired by pid={os.getpid()}")
            return lock_file
        except OSError:
            if time.time() - start_time >= timeout_seconds:
                lock_file.close()
                raise TimeoutError(f"Timed out waiting for startup bootstrap lock: {STARTUP_BOOTSTRAP_LOCK_PATH}")
            time.sleep(0.2)


def release_startup_bootstrap_lock():
    global startup_bootstrap_lock_handle
    lock_file = startup_bootstrap_lock_handle
    if lock_file is None:
        return
    try:
        lock_file.seek(0)
        if os.name == "nt" and msvcrt is not None:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()
        startup_bootstrap_lock_handle = None


def run_startup_bootstrap():
    acquire_startup_bootstrap_lock()
    try:
        ensure_storyboard_columns()
        ensure_storyboard_sora_prompt_full_flag()
        ensure_image_model_keys()
        ensure_storyboard_shot_family_stable_ids()
        ensure_managed_session_variant_count_column()
        ensure_managed_task_prompt_text_column()
        ensure_subject_card_columns()
        ensure_subject_card_audio_duration_column()
        ensure_subject_card_generating_columns()
        ensure_subject_card_protagonist_columns()
        ensure_subject_card_linked_columns()
        ensure_subject_card_personality_columns()
        ensure_script_columns()
        ensure_storyboard2_shot_columns()
        ensure_storyboard2_subshot_columns()
        ensure_storyboard2_subshot_image_columns()
        ensure_storyboard2_subshot_video_columns()
        ensure_shot_detail_image_columns()
        ensure_episode_columns()
        ensure_simple_storyboard_batch_columns()
        ensure_billing_columns()
        ensure_billing_defaults()
        ensure_video_model_pricing()
        ensure_video_style_templates()
        ensure_prompt_config_table()
        ensure_function_model_config_columns()
        ensure_stage2_refine_shot_prompt_config()
        ensure_prop_subject_prompt_configs()
        ensure_character_three_view_prompt_config()
        ensure_storyboard2_prompt_config()
        ensure_shot_duration_template_config_json()
        ensure_shot_duration_template_without_scene_description()
        ensure_generate_video_prompt_config_without_scene_description()
        ensure_shot_duration_template_large_shot_rule()
        ensure_generate_large_shot_prompt_config()
        ensure_large_shot_templates()
        ensure_shot_duration_template_subject_personality()
        ensure_remove_legacy_duration_templates()
        ensure_video_prompt_subject_personality_configs()
        ensure_style_template_variant_columns()
        ensure_default_style_templates()
        ensure_hit_drama_columns()
        ensure_user_password_column()
    finally:
        release_startup_bootstrap_lock()


def _hash_password(password: str) -> str:
    """sha256 哈希密码"""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


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

# 补充新增字段（兼容旧数据库）
def ensure_storyboard_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "storyboard_shots")
            if not columns:
                return
            has_variant = "variant_index" in columns

            if "script_excerpt" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN script_excerpt TEXT DEFAULT ''")
                )
                print("??? storyboard_shots.script_excerpt ??")
            if "storyboard_video_prompt" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN storyboard_video_prompt TEXT DEFAULT ''")
                )
                print("??? storyboard_shots.storyboard_video_prompt ??")
            if "storyboard_audio_prompt" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN storyboard_audio_prompt TEXT DEFAULT ''")
                )
                print("??? storyboard_shots.storyboard_audio_prompt ??")
            if "storyboard_dialogue" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN storyboard_dialogue TEXT DEFAULT ''")
                )
                print("??? storyboard_shots.storyboard_dialogue ??")
            if "sora_prompt" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN sora_prompt TEXT DEFAULT ''")
                )
                print("??? storyboard_shots.sora_prompt ??")
            if "sora_prompt_status" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN sora_prompt_status TEXT DEFAULT 'idle'")
                )
                print("??? storyboard_shots.sora_prompt_status ??")
            if "selected_sound_card_ids" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN selected_sound_card_ids TEXT")
                )
                print("已添加 storyboard_shots.selected_sound_card_ids 字段")
            if "thumbnail_video_path" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN thumbnail_video_path TEXT DEFAULT ''")
                )
                print("??? storyboard_shots.thumbnail_video_path ??")
            if "detail_image_prompt_overrides" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN detail_image_prompt_overrides TEXT DEFAULT '{}'")
                )
                print("已添加 storyboard_shots.detail_image_prompt_overrides 字段")
            if "storyboard_image_model" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN storyboard_image_model TEXT DEFAULT ''")
                )
                print("已添加 storyboard_shots.storyboard_image_model 字段")
                columns.add("storyboard_image_model")
            if "first_frame_reference_image_url" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN first_frame_reference_image_url TEXT DEFAULT ''")
                )
                print("已添加 storyboard_shots.first_frame_reference_image_url 字段")
                columns.add("first_frame_reference_image_url")
            if "uploaded_first_frame_reference_image_url" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN uploaded_first_frame_reference_image_url TEXT DEFAULT ''")
                )
                print("已添加 storyboard_shots.uploaded_first_frame_reference_image_url 字段")
                columns.add("uploaded_first_frame_reference_image_url")
            if "uploaded_scene_image_url" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN uploaded_scene_image_url TEXT DEFAULT ''")
                )
                print("已添加 storyboard_shots.uploaded_scene_image_url 字段")
                columns.add("uploaded_scene_image_url")
            if "use_uploaded_scene_image" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN use_uploaded_scene_image BOOLEAN DEFAULT FALSE")
                )
                print("已添加 storyboard_shots.use_uploaded_scene_image 字段")
                columns.add("use_uploaded_scene_image")
            if "duration_override_enabled" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN duration_override_enabled BOOLEAN DEFAULT FALSE")
                )
                print("已添加 storyboard_shots.duration_override_enabled 字段")
                columns.add("duration_override_enabled")
            if "storyboard_video_model" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN storyboard_video_model TEXT DEFAULT ''")
                )
                print("已添加 storyboard_shots.storyboard_video_model 字段")
                columns.add("storyboard_video_model")
            if "storyboard_video_model_override_enabled" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN storyboard_video_model_override_enabled BOOLEAN DEFAULT FALSE")
                )
                print("已添加 storyboard_shots.storyboard_video_model_override_enabled 字段")
                columns.add("storyboard_video_model_override_enabled")
            if not has_variant:
                conn.execute(
                    text("ALTER TABLE storyboard_shots ADD COLUMN variant_index INTEGER DEFAULT 0")
                )
                print("??? storyboard_shots.variant_index ??")
                has_variant = True
                columns.add("variant_index")
            if has_variant:
                conn.execute(
                    text("UPDATE storyboard_shots SET variant_index = 0 WHERE variant_index IS NULL")
                )
            conn.execute(
                text("UPDATE storyboard_shots SET detail_image_prompt_overrides = '{}' WHERE detail_image_prompt_overrides IS NULL OR TRIM(detail_image_prompt_overrides) = ''")
            )
            conn.execute(
                text("UPDATE storyboard_shots SET storyboard_image_model = '' WHERE storyboard_image_model IS NULL")
            )
            conn.execute(
                text("UPDATE storyboard_shots SET first_frame_reference_image_url = '' WHERE first_frame_reference_image_url IS NULL")
            )
            conn.execute(
                text("UPDATE storyboard_shots SET uploaded_first_frame_reference_image_url = '' WHERE uploaded_first_frame_reference_image_url IS NULL")
            )
            conn.execute(
                text("UPDATE storyboard_shots SET uploaded_scene_image_url = '' WHERE uploaded_scene_image_url IS NULL")
            )
            conn.execute(
                text("UPDATE storyboard_shots SET use_uploaded_scene_image = FALSE WHERE use_uploaded_scene_image IS NULL")
            )
            conn.execute(
                text("UPDATE storyboard_shots SET duration_override_enabled = FALSE WHERE duration_override_enabled IS NULL")
            )
            conn.execute(
                text("UPDATE storyboard_shots SET storyboard_video_model = '' WHERE storyboard_video_model IS NULL")
            )
            conn.execute(
                text("UPDATE storyboard_shots SET storyboard_video_model_override_enabled = FALSE WHERE storyboard_video_model_override_enabled IS NULL")
            )
            if (
                "use_uploaded_scene_image" in columns
                and not IS_SQLITE
                and should_apply_runtime_postgres_alter("storyboard_shots", "use_uploaded_scene_image")
            ):
                conn.execute(
                    text("ALTER TABLE storyboard_shots ALTER COLUMN use_uploaded_scene_image SET DEFAULT FALSE")
                )
                conn.execute(
                    text("ALTER TABLE storyboard_shots ALTER COLUMN use_uploaded_scene_image SET NOT NULL")
                )
            if (
                "duration_override_enabled" in columns
                and not IS_SQLITE
                and should_apply_runtime_postgres_alter("storyboard_shots", "duration_override_enabled")
            ):
                conn.execute(
                    text("ALTER TABLE storyboard_shots ALTER COLUMN duration_override_enabled SET DEFAULT FALSE")
                )
                conn.execute(
                    text("ALTER TABLE storyboard_shots ALTER COLUMN duration_override_enabled SET NOT NULL")
                )
            if (
                "storyboard_video_model_override_enabled" in columns
                and not IS_SQLITE
                and should_apply_runtime_postgres_alter("storyboard_shots", "storyboard_video_model_override_enabled")
            ):
                conn.execute(
                    text("ALTER TABLE storyboard_shots ALTER COLUMN storyboard_video_model_override_enabled SET DEFAULT FALSE")
                )
                conn.execute(
                    text("ALTER TABLE storyboard_shots ALTER COLUMN storyboard_video_model_override_enabled SET NOT NULL")
                )
            conn.execute(
                text("UPDATE storyboard_shots SET selected_sound_card_ids = NULL WHERE selected_sound_card_ids IS NOT NULL AND TRIM(selected_sound_card_ids) = ''")
            )

    except Exception as e:
        print(f"???? storyboard_shots ??: {str(e)}")



def ensure_image_model_keys():
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE generated_images "
                    "SET model_name = 'jimeng-4.5' "
                    "WHERE LOWER(TRIM(model_name)) IN ('seedream-4-5', 'doubao-seedance-4-5')"
                )
            )
            conn.execute(
                text(
                    "UPDATE storyboard_shots "
                    "SET storyboard_image_model = 'jimeng-4.5' "
                    "WHERE LOWER(TRIM(storyboard_image_model)) IN ('seedream-4-5', 'doubao-seedance-4-5')"
                )
            )
            conn.execute(
                text(
                    "UPDATE generated_images "
                    "SET model_name = 'banana2' "
                    "WHERE LOWER(TRIM(model_name)) IN ('banana2', 'nano-banana-2.5', 'nanobanana2.5', 'nano banana2.5', 'gemini-2.5-flash-image-preview')"
                )
            )
            conn.execute(
                text(
                    "UPDATE generated_images "
                    "SET model_name = 'banana2-moti' "
                    "WHERE LOWER(TRIM(model_name)) IN ('banana2-moti', 'nano-banana-2.5-moti', 'nanobanana2.5moti', 'nano banana2.5 moti', 'nano-banana2.5-moti')"
                )
            )
            conn.execute(
                text(
                    "UPDATE generated_images "
                    "SET model_name = 'banana-pro' "
                    "WHERE LOWER(TRIM(model_name)) IN ('banana-pro', 'gemini-3-pro-image-preview', 'nanobanana3.0', 'nano banana3.0', 'nano-banana-3.0')"
                )
            )
            conn.execute(
                text(
                    "UPDATE storyboard_shots "
                    "SET storyboard_image_model = 'banana2' "
                    "WHERE LOWER(TRIM(storyboard_image_model)) IN ('banana2', 'nano-banana-2.5', 'nanobanana2.5', 'nano banana2.5', 'gemini-2.5-flash-image-preview')"
                )
            )
            conn.execute(
                text(
                    "UPDATE storyboard_shots "
                    "SET storyboard_image_model = 'banana2-moti' "
                    "WHERE LOWER(TRIM(storyboard_image_model)) IN ('banana2-moti', 'nano-banana-2.5-moti', 'nanobanana2.5moti', 'nano banana2.5 moti', 'nano-banana2.5-moti')"
                )
            )
            conn.execute(
                text(
                    "UPDATE storyboard_shots "
                    "SET storyboard_image_model = 'banana-pro' "
                    "WHERE LOWER(TRIM(storyboard_image_model)) IN ('banana-pro', 'gemini-3-pro-image-preview', 'nanobanana3.0', 'nano banana3.0', 'nano-banana-3.0')"
                )
            )
    except Exception as e:
        print(f"统一图片模型键失败: {str(e)}")




def ensure_storyboard_shot_family_stable_ids():
    try:
        db = SessionLocal()
        try:
            families = db.query(
                models.StoryboardShot.episode_id,
                models.StoryboardShot.shot_number
            ).distinct().all()
            updated_count = 0

            for episode_id, shot_number in families:
                family_shots = db.query(models.StoryboardShot).filter(
                    models.StoryboardShot.episode_id == episode_id,
                    models.StoryboardShot.shot_number == shot_number
                ).order_by(
                    models.StoryboardShot.variant_index.asc(),
                    models.StoryboardShot.id.asc()
                ).all()
                if not family_shots:
                    continue

                stable_id = ""
                for family_shot in family_shots:
                    stable_id = (family_shot.stable_id or "").strip()
                    if stable_id:
                        break
                if not stable_id:
                    stable_id = str(uuid.uuid4())
                for family_shot in family_shots:
                    if (family_shot.stable_id or "").strip():
                        continue
                    family_shot.stable_id = stable_id
                    updated_count += 1

            if updated_count > 0:
                db.commit()
                print(f"已对齐 storyboard_shots stable_id 家族: {updated_count} 条")
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as e:
        print(f"对齐 storyboard_shots stable_id 家族失败: {str(e)}")




def ensure_managed_session_variant_count_column():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "managed_sessions")
            if not columns:
                return

            if "variant_count" not in columns:
                conn.execute(
                    text("ALTER TABLE managed_sessions ADD COLUMN variant_count INTEGER DEFAULT 1")
                )
                print("已添加 managed_sessions.variant_count 字段")

            conn.execute(
                text(
                    """
                    UPDATE managed_sessions
                    SET variant_count = COALESCE(
                        (
                            SELECT MIN(task_count)
                            FROM (
                                SELECT COUNT(*) AS task_count
                                FROM managed_tasks
                                WHERE session_id = managed_sessions.id
                                GROUP BY shot_stable_id
                            )
                        ),
                        1
                    )
                    WHERE variant_count IS NULL OR variant_count < 1
                    """
                )
            )
    except Exception as e:
        print(f"检查/迁移 managed_sessions.variant_count 失败: {str(e)}")


def ensure_managed_task_prompt_text_column():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "managed_tasks")
            if not columns:
                return

            if "prompt_text" not in columns:
                conn.execute(
                    text("ALTER TABLE managed_tasks ADD COLUMN prompt_text TEXT DEFAULT ''")
                )
                print("已添加 managed_tasks.prompt_text 字段")

            conn.execute(
                text("UPDATE managed_tasks SET prompt_text = '' WHERE prompt_text IS NULL")
            )
    except Exception as e:
        print(f"检查/迁移 managed_tasks.prompt_text 失败: {str(e)}")


def ensure_storyboard_sora_prompt_full_flag():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "storyboard_shots")
            if not columns:
                return

            if "sora_prompt_is_full" not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE storyboard_shots ADD COLUMN sora_prompt_is_full BOOLEAN DEFAULT {boolean_sql(False)}"
                    )
                )
                print("已添加 storyboard_shots.sora_prompt_is_full 字段")

            conn.execute(
                text(
                    f"UPDATE storyboard_shots SET sora_prompt_is_full = {boolean_sql(False)} "
                    "WHERE sora_prompt_is_full IS NULL"
                )
            )
    except Exception as e:
        print(f"检查/迁移 storyboard_shots.sora_prompt_is_full 失败: {str(e)}")





def ensure_subject_card_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "subject_cards")
            if not columns:
                return
            if "alias" not in columns:
                conn.execute(
                    text("ALTER TABLE subject_cards ADD COLUMN alias TEXT DEFAULT ''")
                )
                print("已添加 subject_cards.alias 字段")
            if "ai_prompt_status" not in columns:
                conn.execute(
                    text("ALTER TABLE subject_cards ADD COLUMN ai_prompt_status TEXT DEFAULT NULL")
                )
                print("已添加 subject_cards.ai_prompt_status 字段")
    except Exception as e:
        print(f"检查/迁移 subject_cards 失败: {str(e)}")




def ensure_subject_card_audio_duration_column():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "subject_card_audios")
            if not columns:
                return

            if "duration_seconds" not in columns:
                conn.execute(
                    text("ALTER TABLE subject_card_audios ADD COLUMN duration_seconds FLOAT DEFAULT 0")
                )
                print("已添加 subject_card_audios.duration_seconds 字段")

            conn.execute(
                text(
                    "UPDATE subject_card_audios "
                    "SET duration_seconds = 0 "
                    "WHERE duration_seconds IS NULL"
                )
            )
    except Exception as e:
        print(f"检查/迁移 subject_card_audios.duration_seconds 失败: {str(e)}")



def ensure_subject_card_generating_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "subject_cards")
            if not columns:
                return

            if "is_generating_images" not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE subject_cards ADD COLUMN is_generating_images BOOLEAN DEFAULT {boolean_sql(False)}"
                    )
                )
                print("已添加 subject_cards.is_generating_images 字段")

            if "generating_count" not in columns:
                conn.execute(
                    text("ALTER TABLE subject_cards ADD COLUMN generating_count INTEGER DEFAULT 0")
                )
                print("已添加 subject_cards.generating_count 字段")

            # 确保旧数据的默认值
            conn.execute(
                text(
                    f"UPDATE subject_cards SET is_generating_images = {boolean_sql(False)} "
                    "WHERE is_generating_images IS NULL"
                )
            )
            conn.execute(
                text("UPDATE subject_cards SET generating_count = 0 WHERE generating_count IS NULL")
            )
    except Exception as e:
        print(f"检查/迁移 subject_cards 生成状态字段失败: {str(e)}")


def ensure_subject_card_protagonist_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "subject_cards")
            if not columns:
                return

            if "is_protagonist" not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE subject_cards ADD COLUMN is_protagonist BOOLEAN DEFAULT {boolean_sql(False)}"
                    )
                )
                print("Added subject_cards.is_protagonist column")

            if "protagonist_gender" not in columns:
                conn.execute(
                    text("ALTER TABLE subject_cards ADD COLUMN protagonist_gender TEXT DEFAULT ''")
                )
                print("Added subject_cards.protagonist_gender column")

            conn.execute(
                text(
                    f"UPDATE subject_cards SET is_protagonist = {boolean_sql(False)} "
                    "WHERE is_protagonist IS NULL"
                )
            )
            conn.execute(
                text("UPDATE subject_cards SET protagonist_gender = '' WHERE protagonist_gender IS NULL")
            )
            conn.execute(
                text("UPDATE subject_cards SET protagonist_gender = '' WHERE protagonist_gender NOT IN ('male', 'female', '')")
            )
            conn.execute(
                text(
                    f"UPDATE subject_cards SET is_protagonist = {boolean_sql(False)}, protagonist_gender = '' "
                    "WHERE card_type != '角色'"
                )
            )
    except Exception as e:
        print(f"Failed to ensure subject_cards protagonist columns: {str(e)}")


def ensure_subject_card_linked_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "subject_cards")
            if not columns:
                return

            if "linked_card_id" not in columns:
                conn.execute(
                    text("ALTER TABLE subject_cards ADD COLUMN linked_card_id INTEGER")
                )
                print("Added subject_cards.linked_card_id column")

            conn.execute(
                text("UPDATE subject_cards SET linked_card_id = NULL WHERE linked_card_id = 0")
            )
            conn.execute(
                text("UPDATE subject_cards SET linked_card_id = NULL WHERE card_type != '声音'")
            )
    except Exception as e:
        print(f"Failed to ensure subject_cards linked columns: {str(e)}")


def ensure_subject_card_personality_columns():
    try:
        if rename_column_if_needed(engine, "subject_cards", "role_personality_en", "role_personality"):
            print("Renamed subject_cards.role_personality_en to role_personality")
        with engine.begin() as conn:
            columns = get_table_columns(engine, "subject_cards")
            if not columns:
                return

            if "role_personality" not in columns:
                conn.execute(
                    text("ALTER TABLE subject_cards ADD COLUMN role_personality TEXT DEFAULT ''")
                )
                print("Added subject_cards.role_personality column")

            conn.execute(
                text("UPDATE subject_cards SET role_personality = '' WHERE role_personality IS NULL")
            )
            conn.execute(
                text("UPDATE subject_cards SET role_personality = '' WHERE card_type != '角色'")
            )
    except Exception as e:
        print(f"Failed to ensure subject_cards personality columns: {str(e)}")


def ensure_script_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "scripts")
            if not columns:
                return
            has_prompt_style = "sora_prompt_style" in columns
            if not has_prompt_style:
                conn.execute(
                    text("ALTER TABLE scripts ADD COLUMN sora_prompt_style TEXT DEFAULT ''")
                )
                print("已添加 scripts.sora_prompt_style 字段")
                has_prompt_style = True
            if has_prompt_style:
                conn.execute(
                    text("UPDATE scripts SET sora_prompt_style = '' WHERE sora_prompt_style IS NULL")
                )

            has_video_prompt_template = "video_prompt_template" in columns
            if not has_video_prompt_template:
                conn.execute(
                    text("ALTER TABLE scripts ADD COLUMN video_prompt_template TEXT DEFAULT ''")
                )
                print("已添加 scripts.video_prompt_template 字段")
                has_video_prompt_template = True
            if has_video_prompt_template:
                conn.execute(
                    text("UPDATE scripts SET video_prompt_template = '' WHERE video_prompt_template IS NULL")
                )

            # 添加 style_template 字段
            has_style_template = "style_template" in columns
            if not has_style_template:
                conn.execute(
                    text("ALTER TABLE scripts ADD COLUMN style_template TEXT DEFAULT ''")
                )
                print("已添加 scripts.style_template 字段")
                has_style_template = True
            if has_style_template:
                conn.execute(
                    text("UPDATE scripts SET style_template = '' WHERE style_template IS NULL")
                )
            has_voiceover_shared_data = "voiceover_shared_data" in columns
            if not has_voiceover_shared_data:
                conn.execute(
                    text("ALTER TABLE scripts ADD COLUMN voiceover_shared_data TEXT DEFAULT ''")
                )
                print("Added scripts.voiceover_shared_data column")
                has_voiceover_shared_data = True
            if has_voiceover_shared_data:
                conn.execute(
                    text("UPDATE scripts SET voiceover_shared_data = '' WHERE voiceover_shared_data IS NULL")
                )
    except Exception as e:
        print(f"检查/迁移 scripts 失败: {str(e)}")




def ensure_storyboard2_shot_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "storyboard2_shots")
            if not columns:
                return

            if "selected_card_ids" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard2_shots ADD COLUMN selected_card_ids TEXT DEFAULT '[]'")
                )
                print("已添加 storyboard2_shots.selected_card_ids 字段")

            conn.execute(
                text("UPDATE storyboard2_shots SET selected_card_ids = '[]' WHERE selected_card_ids IS NULL")
            )
    except Exception as e:
        print(f"检查/迁移 storyboard2_shots 失败: {str(e)}")




def ensure_storyboard2_subshot_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "storyboard2_subshots")
            if not columns:
                return

            if "selected_card_ids" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard2_subshots ADD COLUMN selected_card_ids TEXT DEFAULT '[]'")
                )
                print("已添加 storyboard2_subshots.selected_card_ids 字段")
            if "image_generate_status" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard2_subshots ADD COLUMN image_generate_status TEXT DEFAULT 'idle'")
                )
                print("已添加 storyboard2_subshots.image_generate_status 字段")
            if "image_generate_progress" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard2_subshots ADD COLUMN image_generate_progress TEXT DEFAULT ''")
                )
                print("已添加 storyboard2_subshots.image_generate_progress 字段")
            if "image_generate_error" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard2_subshots ADD COLUMN image_generate_error TEXT DEFAULT ''")
                )
                print("已添加 storyboard2_subshots.image_generate_error 字段")
            if "scene_override" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard2_subshots ADD COLUMN scene_override TEXT DEFAULT ''")
                )
                print("已添加 storyboard2_subshots.scene_override 字段")
            if "scene_override_locked" not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE storyboard2_subshots ADD COLUMN scene_override_locked BOOLEAN DEFAULT {boolean_sql(False)}"
                    )
                )
                print("已添加 storyboard2_subshots.scene_override_locked 字段")

            conn.execute(
                text("UPDATE storyboard2_subshots SET selected_card_ids = '[]' WHERE selected_card_ids IS NULL")
            )
            conn.execute(
                text("UPDATE storyboard2_subshots SET image_generate_status = 'idle' WHERE image_generate_status IS NULL")
            )
            conn.execute(
                text("UPDATE storyboard2_subshots SET image_generate_progress = '' WHERE image_generate_progress IS NULL")
            )
            conn.execute(
                text("UPDATE storyboard2_subshots SET image_generate_error = '' WHERE image_generate_error IS NULL")
            )
            conn.execute(
                text("UPDATE storyboard2_subshots SET scene_override = '' WHERE scene_override IS NULL")
            )
            conn.execute(
                text(
                    f"UPDATE storyboard2_subshots SET scene_override_locked = {boolean_sql(False)} "
                    "WHERE scene_override_locked IS NULL"
                )
            )
    except Exception as e:
        print(f"检查/迁移 storyboard2_subshots 失败: {str(e)}")



def ensure_storyboard2_subshot_image_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "storyboard2_subshot_images")
            if not columns:
                return

            if "size" not in columns:
                conn.execute(
                    text("ALTER TABLE storyboard2_subshot_images ADD COLUMN size TEXT DEFAULT '9:16'")
                )
                print("已添加 storyboard2_subshot_images.size 字段")

            conn.execute(
                text("UPDATE storyboard2_subshot_images SET size = '9:16' WHERE size IS NULL OR TRIM(size) = '' OR size = '1:2'")
            )
            conn.execute(
                text("UPDATE storyboard2_subshot_images SET size = '16:9' WHERE size = '2:1'")
            )
    except Exception as e:
        print(f"检查/迁移 storyboard2_subshot_images 失败: {str(e)}")



def ensure_storyboard2_subshot_video_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "storyboard2_subshot_videos")
            if not columns:
                return

            if "is_deleted" not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE storyboard2_subshot_videos ADD COLUMN is_deleted BOOLEAN DEFAULT {boolean_sql(False)}"
                    )
                )
                print("已添加 storyboard2_subshot_videos.is_deleted 字段")
            if "deleted_at" not in columns:
                conn.execute(
                    text(f"ALTER TABLE storyboard2_subshot_videos ADD COLUMN deleted_at {datetime_sql(engine)}")
                )
                print("已添加 storyboard2_subshot_videos.deleted_at 字段")

            conn.execute(
                text(
                    f"UPDATE storyboard2_subshot_videos SET is_deleted = {boolean_sql(False)} "
                    "WHERE is_deleted IS NULL"
                )
            )
    except Exception as e:
        print(f"检查/迁移 storyboard2_subshot_videos 失败: {str(e)}")

def ensure_shot_detail_image_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "shot_detail_images")
            if not columns:
                return

            if "task_id" not in columns:
                conn.execute(text("ALTER TABLE shot_detail_images ADD COLUMN task_id TEXT DEFAULT ''"))
                print("已添加 shot_detail_images.task_id 字段")
            if "provider" not in columns:
                conn.execute(text("ALTER TABLE shot_detail_images ADD COLUMN provider TEXT DEFAULT ''"))
                print("已添加 shot_detail_images.provider 字段")
            if "model_name" not in columns:
                conn.execute(text("ALTER TABLE shot_detail_images ADD COLUMN model_name TEXT DEFAULT ''"))
                print("已添加 shot_detail_images.model_name 字段")
            if "submit_api_url" not in columns:
                conn.execute(text("ALTER TABLE shot_detail_images ADD COLUMN submit_api_url TEXT DEFAULT ''"))
                print("已添加 shot_detail_images.submit_api_url 字段")
            if "status_api_url" not in columns:
                conn.execute(text("ALTER TABLE shot_detail_images ADD COLUMN status_api_url TEXT DEFAULT ''"))
                print("已添加 shot_detail_images.status_api_url 字段")
            if "query_error_count" not in columns:
                conn.execute(text("ALTER TABLE shot_detail_images ADD COLUMN query_error_count INTEGER DEFAULT 0"))
                print("已添加 shot_detail_images.query_error_count 字段")
            if "last_query_error" not in columns:
                conn.execute(text("ALTER TABLE shot_detail_images ADD COLUMN last_query_error TEXT DEFAULT ''"))
                print("已添加 shot_detail_images.last_query_error 字段")
            if "submitted_at" not in columns:
                conn.execute(text(f"ALTER TABLE shot_detail_images ADD COLUMN submitted_at {datetime_sql(engine)}"))
                print("已添加 shot_detail_images.submitted_at 字段")
            if "last_query_at" not in columns:
                conn.execute(text(f"ALTER TABLE shot_detail_images ADD COLUMN last_query_at {datetime_sql(engine)}"))
                print("已添加 shot_detail_images.last_query_at 字段")

            conn.execute(text("UPDATE shot_detail_images SET task_id = '' WHERE task_id IS NULL"))
            conn.execute(text("UPDATE shot_detail_images SET provider = '' WHERE provider IS NULL"))
            conn.execute(text("UPDATE shot_detail_images SET model_name = '' WHERE model_name IS NULL"))
            conn.execute(text("UPDATE shot_detail_images SET submit_api_url = '' WHERE submit_api_url IS NULL"))
            conn.execute(text("UPDATE shot_detail_images SET status_api_url = '' WHERE status_api_url IS NULL"))
            conn.execute(text("UPDATE shot_detail_images SET query_error_count = 0 WHERE query_error_count IS NULL"))
            conn.execute(text("UPDATE shot_detail_images SET last_query_error = '' WHERE last_query_error IS NULL"))

            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_shot_detail_images_task_id ON shot_detail_images (task_id)"))
            except Exception as index_error:
                print(f"创建 shot_detail_images.task_id 索引失败: {index_error}")
    except Exception as e:
        print(f"检查/迁移 shot_detail_images 失败: {str(e)}")


def ensure_episode_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "episodes")
            if not columns:
                return

            if "shot_image_size" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN shot_image_size TEXT DEFAULT '9:16'")
                )
                print("已添加 episodes.shot_image_size 字段")
            if "voiceover_data" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN voiceover_data TEXT DEFAULT ''")
                )
                print("Added episodes.voiceover_data column")
            if "narration_converting" not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE episodes ADD COLUMN narration_converting BOOLEAN DEFAULT {boolean_sql(False)}"
                    )
                )
                print("Added episodes.narration_converting column")
            if "narration_error" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN narration_error TEXT DEFAULT ''")
                )
                print("Added episodes.narration_error column")
            if "detail_images_model" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN detail_images_model TEXT DEFAULT 'seedream-4.0'")
                )
                print("已添加 episodes.detail_images_model 字段")
            if "detail_images_provider" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN detail_images_provider TEXT DEFAULT ''")
                )
                print("已添加 episodes.detail_images_provider 字段")

            if "batch_generating_storyboard2_prompts" not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE episodes ADD COLUMN batch_generating_storyboard2_prompts BOOLEAN DEFAULT {boolean_sql(False)}"
                    )
                )
            if "storyboard2_video_duration" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN storyboard2_video_duration INTEGER DEFAULT 6")
                )
            if "storyboard2_duration" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN storyboard2_duration INTEGER DEFAULT 15")
                )
            if "storyboard2_image_cw" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN storyboard2_image_cw INTEGER DEFAULT 50")
                )
            if "storyboard2_include_scene_references" not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE episodes ADD COLUMN storyboard2_include_scene_references BOOLEAN DEFAULT {boolean_sql(False)}"
                    )
                )
                print("已添加 episodes.batch_generating_storyboard2_prompts 字段")
            if "storyboard_video_model" not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE episodes ADD COLUMN storyboard_video_model TEXT DEFAULT '{DEFAULT_STORYBOARD_VIDEO_MODEL}'"
                    )
                )
                print("已添加 episodes.storyboard_video_model 字段")
            if "storyboard_video_aspect_ratio" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN storyboard_video_aspect_ratio TEXT DEFAULT '16:9'")
                )
                print("已添加 episodes.storyboard_video_aspect_ratio 字段")
            if "storyboard_video_duration" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN storyboard_video_duration INTEGER DEFAULT 15")
                )
                print("已添加 episodes.storyboard_video_duration 字段")
            if "storyboard_video_resolution_name" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN storyboard_video_resolution_name TEXT DEFAULT '720p'")
                )
                print("已添加 episodes.storyboard_video_resolution_name 字段")
            if "storyboard_video_appoint_account" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN storyboard_video_appoint_account TEXT DEFAULT ''")
                )
                print("已添加 episodes.storyboard_video_appoint_account 字段")

            conn.execute(
                text("UPDATE episodes SET shot_image_size = '9:16' WHERE shot_image_size IS NULL OR TRIM(shot_image_size) = '' OR shot_image_size = '1:2'")
            )
            conn.execute(
                text("UPDATE episodes SET shot_image_size = '16:9' WHERE shot_image_size = '2:1'")
            )
            conn.execute(
                text("UPDATE episodes SET voiceover_data = '' WHERE voiceover_data IS NULL")
            )
            conn.execute(
                text(
                    f"UPDATE episodes SET narration_converting = {boolean_sql(False)} "
                    "WHERE narration_converting IS NULL"
                )
            )
            conn.execute(
                text("UPDATE episodes SET narration_error = '' WHERE narration_error IS NULL")
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET detail_images_model = 'seedream-4.0' "
                    "WHERE detail_images_model IS NULL "
                    "OR TRIM(detail_images_model) = ''"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET detail_images_model = 'seedream-4.0' "
                    "WHERE LOWER(TRIM(detail_images_model)) IN ('jimeng', 'jimeng-4.0', 'seedream-4-0')"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET detail_images_model = 'seedream-4.5' "
                    "WHERE LOWER(TRIM(detail_images_model)) IN ('jimeng-4.5', 'seedream-4-5', 'doubao-seedance-4-5')"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET detail_images_model = 'seedream-4.6' "
                    "WHERE LOWER(TRIM(detail_images_model)) IN ('jimeng-4.6', 'seedream-4-6')"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET detail_images_model = 'seedream-4.1' "
                    "WHERE LOWER(TRIM(detail_images_model)) IN ('jimeng-4.1', 'seedream-4-1')"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET detail_images_model = 'nano-banana-2' "
                    "WHERE LOWER(TRIM(detail_images_model)) IN ('banana2', 'banana2-moti', 'nano-banana-2', 'nano-banana-2.5', 'nanobanana2.5', 'nano banana2.5', 'gemini-2.5-flash-image-preview', 'nano-banana-2.5-moti', 'nanobanana2.5moti', 'nano banana2.5 moti', 'nano-banana2.5-moti')"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET detail_images_model = 'nano-banana-pro' "
                    "WHERE LOWER(TRIM(detail_images_model)) IN ('banana-pro', 'nano-banana-pro', 'gemini-3-pro-image-preview', 'nanobanana3.0', 'nano banana3.0', 'nano-banana-3.0')"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET detail_images_provider = '' "
                    "WHERE detail_images_provider IS NULL"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET detail_images_provider = 'jimeng' "
                    "WHERE (detail_images_provider IS NULL OR TRIM(detail_images_provider) = '') "
                    "AND LOWER(TRIM(detail_images_model)) IN ('seedream-4.0', 'seedream-4.1', 'seedream-4.5', 'seedream-4.6')"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET detail_images_provider = 'momo' "
                    "WHERE (detail_images_provider IS NULL OR TRIM(detail_images_provider) = '') "
                    "AND LOWER(TRIM(detail_images_model)) IN ('nano-banana-2', 'nano-banana-pro', 'gpt-image-2')"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET detail_images_provider = 'momo' "
                    "WHERE LOWER(TRIM(detail_images_provider)) IN ('banana', 'moti', 'moapp', 'gettoken')"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    f"SET batch_generating_storyboard2_prompts = {boolean_sql(False)} "
                    "WHERE batch_generating_storyboard2_prompts IS NULL"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET storyboard2_video_duration = 6 "
                    "WHERE storyboard2_video_duration IS NULL "
                    "OR storyboard2_video_duration NOT IN (6, 10)"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET storyboard2_duration = 15 "
                    "WHERE storyboard2_duration IS NULL "
                    "OR storyboard2_duration NOT IN (15, 25)"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET storyboard2_image_cw = 50 "
                    "WHERE storyboard2_image_cw IS NULL "
                    "OR storyboard2_image_cw < 1 "
                    "OR storyboard2_image_cw > 100"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    f"SET storyboard2_include_scene_references = {boolean_sql(False)} "
                    "WHERE storyboard2_include_scene_references IS NULL"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    f"SET storyboard_video_model = '{DEFAULT_STORYBOARD_VIDEO_MODEL}' "
                    "WHERE storyboard_video_model IS NULL "
                    "OR TRIM(storyboard_video_model) = '' "
                    "OR storyboard_video_model NOT IN ('sora-2', 'grok', 'Seedance 2.0 Fast VIP', 'Seedance 2.0 Fast', 'Seedance 2.0 VIP', 'Seedance 2.0')"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET storyboard_video_aspect_ratio = '16:9' "
                    "WHERE storyboard_video_model = 'sora-2' "
                    "AND (storyboard_video_aspect_ratio IS NULL "
                    "OR TRIM(storyboard_video_aspect_ratio) = '' "
                    "OR storyboard_video_aspect_ratio NOT IN ('16:9', '9:16'))"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET storyboard_video_aspect_ratio = '9:16' "
                    "WHERE storyboard_video_model = 'grok' "
                    "AND (storyboard_video_aspect_ratio IS NULL "
                    "OR TRIM(storyboard_video_aspect_ratio) = '' "
                    "OR storyboard_video_aspect_ratio NOT IN ('21:9','16:9','3:2','4:3','1:1','3:4','2:3','9:16'))"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET storyboard_video_duration = 15 "
                    "WHERE storyboard_video_model = 'sora-2' "
                    "AND (storyboard_video_duration IS NULL "
                    "OR storyboard_video_duration NOT IN (10, 15, 25))"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET storyboard_video_duration = 10 "
                    "WHERE storyboard_video_model = 'grok' "
                    "AND (storyboard_video_duration IS NULL "
                    "OR storyboard_video_duration NOT IN (10, 20, 30))"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET storyboard_video_resolution_name = '720p' "
                    "WHERE storyboard_video_resolution_name IS NULL "
                    "OR TRIM(storyboard_video_resolution_name) = '' "
                    "OR LOWER(TRIM(storyboard_video_resolution_name)) NOT IN ('480p', '720p')"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET storyboard_video_appoint_account = '' "
                    "WHERE storyboard_video_appoint_account IS NULL"
                )
            )

            if "video_style_template_id" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN video_style_template_id INTEGER")
                )
                print("已添加 episodes.video_style_template_id 字段")
            if "video_prompt_template" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN video_prompt_template TEXT DEFAULT ''")
                )
                print("已添加 episodes.video_prompt_template 字段")
            if "billing_version" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN billing_version INTEGER DEFAULT 0")
                )
                print("已添加 episodes.billing_version 字段")
            conn.execute(
                text("UPDATE episodes SET video_prompt_template = '' WHERE video_prompt_template IS NULL")
            )
            conn.execute(
                text("UPDATE episodes SET billing_version = 0 WHERE billing_version IS NULL")
            )
            conn.execute(
                text(
                    "UPDATE episodes "
                    "SET video_prompt_template = ("
                    "  SELECT COALESCE(scripts.video_prompt_template, '') "
                    "  FROM scripts "
                    "  WHERE scripts.id = episodes.script_id"
                    ") "
                    "WHERE TRIM(COALESCE(episodes.video_prompt_template, '')) = '' "
                    "AND TRIM(COALESCE(("
                    "  SELECT scripts.video_prompt_template "
                    "  FROM scripts "
                    "  WHERE scripts.id = episodes.script_id"
                    "), '')) <> ''"
                )
            )

    except Exception as e:
        print(f"检查/迁移 episodes 失败: {str(e)}")


def ensure_simple_storyboard_batch_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "simple_storyboard_batches")
            if not columns:
                return
            if "source_text" not in columns:
                conn.execute(text("ALTER TABLE simple_storyboard_batches ADD COLUMN source_text TEXT DEFAULT ''"))
            if "shots_data" not in columns:
                conn.execute(text("ALTER TABLE simple_storyboard_batches ADD COLUMN shots_data TEXT DEFAULT ''"))
            if "error_message" not in columns:
                conn.execute(text("ALTER TABLE simple_storyboard_batches ADD COLUMN error_message TEXT DEFAULT ''"))
            if "last_attempt" not in columns:
                conn.execute(text("ALTER TABLE simple_storyboard_batches ADD COLUMN last_attempt INTEGER DEFAULT 0"))
            if "retry_count" not in columns:
                conn.execute(text("ALTER TABLE simple_storyboard_batches ADD COLUMN retry_count INTEGER DEFAULT 0"))
            if "total_batches" not in columns:
                conn.execute(text("ALTER TABLE simple_storyboard_batches ADD COLUMN total_batches INTEGER DEFAULT 0"))
            if "updated_at" not in columns:
                conn.execute(text("ALTER TABLE simple_storyboard_batches ADD COLUMN updated_at DATETIME"))
            conn.execute(text("UPDATE simple_storyboard_batches SET source_text = '' WHERE source_text IS NULL"))
            conn.execute(text("UPDATE simple_storyboard_batches SET shots_data = '' WHERE shots_data IS NULL"))
            conn.execute(text("UPDATE simple_storyboard_batches SET error_message = '' WHERE error_message IS NULL"))
            conn.execute(text("UPDATE simple_storyboard_batches SET last_attempt = 0 WHERE last_attempt IS NULL"))
            conn.execute(text("UPDATE simple_storyboard_batches SET retry_count = 0 WHERE retry_count IS NULL"))
            conn.execute(text("UPDATE simple_storyboard_batches SET total_batches = 0 WHERE total_batches IS NULL"))
            conn.execute(text("UPDATE simple_storyboard_batches SET updated_at = created_at WHERE updated_at IS NULL"))
    except Exception as e:
        print(f"检查/迁移 simple_storyboard_batches 失败: {str(e)}")


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


def _split_simple_storyboard_batches(content: str, batch_size: int) -> List[str]:
    paragraphs = [p.strip() for p in str(content or "").split('\n') if p.strip()]
    if not paragraphs:
        return []

    split_batches: List[str] = []
    current_batch: List[str] = []
    current_length = 0
    normalized_batch_size = max(1, int(batch_size or 1))

    for para in paragraphs:
        para_length = len(para)
        if current_length + para_length >= normalized_batch_size and current_batch:
            split_batches.append('\n\n'.join(current_batch))
            current_batch = [para]
            current_length = para_length
        else:
            current_batch.append(para)
            current_length += para_length

    if current_batch:
        split_batches.append('\n\n'.join(current_batch))

    return split_batches


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


def _reset_simple_storyboard_batches_for_episode(episode_id: int, total_batches: int, batch_texts: List[str], db: Session) -> None:
    db.query(models.SimpleStoryboardBatch).filter(models.SimpleStoryboardBatch.episode_id == episode_id).delete()
    now = datetime.utcnow()
    for index, batch_text in enumerate(batch_texts, start=1):
        db.add(models.SimpleStoryboardBatch(
            episode_id=episode_id,
            batch_index=index,
            total_batches=total_batches,
            status="pending",
            source_text=str(batch_text or ""),
            shots_data="",
            error_message="",
            last_attempt=0,
            retry_count=0,
            created_at=now,
            updated_at=now,
        ))




def _touch_episode_simple_storyboard_activity(episode_id: int, db: Session) -> None:
    try:
        db.query(models.Episode).filter(models.Episode.id == episode_id).update({
            models.Episode.created_at: models.Episode.created_at
        }, synchronize_session=False)
        db.flush()
    except Exception:
        pass


def _apply_simple_storyboard_batch_update(episode_id: int, payload: Dict[str, Any]) -> None:
    with simple_storyboard_batch_update_lock:
        local_db = SessionLocal()
        try:
            episode = local_db.query(models.Episode).filter(models.Episode.id == episode_id).first()
            if not episode:
                return
            batch_index = int(payload.get("batch_index") or 0)
            if batch_index <= 0:
                return
            row = local_db.query(models.SimpleStoryboardBatch).filter(
                models.SimpleStoryboardBatch.episode_id == episode_id,
                models.SimpleStoryboardBatch.batch_index == batch_index
            ).first()
            if not row:
                return
            row.status = str(payload.get("status") or row.status or "pending").strip() or "pending"
            if "shots" in payload:
                row.shots_data = json.dumps({"shots": payload.get("shots") or []}, ensure_ascii=False)
            if "error_message" in payload:
                row.error_message = str(payload.get("error_message") or "")
            if "last_attempt" in payload:
                row.last_attempt = int(payload.get("last_attempt") or 0)
            if "retry_count" in payload:
                row.retry_count = int(payload.get("retry_count") or 0)
            row.updated_at = datetime.utcnow()
            _refresh_episode_simple_storyboard_from_batches(episode, local_db)
            _touch_episode_simple_storyboard_activity(episode_id, local_db)
            local_db.commit()
        except Exception:
            local_db.rollback()
            raise
        finally:
            local_db.close()


def _build_simple_storyboard_batch_runtime_items(batch_rows: List[models.SimpleStoryboardBatch]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in batch_rows:
        status = str(getattr(row, "status", "") or "").strip()
        if status == "completed":
            continue
        items.append({
            "batch_index": int(getattr(row, "batch_index", 0) or 0),
            "content": str(getattr(row, "source_text", "") or ""),
            "retry_count": int(getattr(row, "retry_count", 0) or 0),
        })
    return items
def ensure_billing_columns():
    try:
        with engine.begin() as conn:
            price_rule_columns = get_table_columns(engine, "billing_price_rules")
            if price_rule_columns and "resolution" not in price_rule_columns:
                conn.execute(text("ALTER TABLE billing_price_rules ADD COLUMN resolution TEXT DEFAULT ''"))
                print("已添加 billing_price_rules.resolution 字段")
            if price_rule_columns:
                conn.execute(text("UPDATE billing_price_rules SET resolution = '' WHERE resolution IS NULL"))

            ledger_columns = get_table_columns(engine, "billing_ledger_entries")
            if ledger_columns and "resolution" not in ledger_columns:
                conn.execute(text("ALTER TABLE billing_ledger_entries ADD COLUMN resolution TEXT DEFAULT ''"))
                print("已添加 billing_ledger_entries.resolution 字段")
            if ledger_columns:
                conn.execute(text("UPDATE billing_ledger_entries SET resolution = '' WHERE resolution IS NULL"))
    except Exception as e:
        print(f"检查/迁移 billing 表失败: {str(e)}")


def ensure_billing_defaults():
    try:
        db = SessionLocal()
        try:
            created_count = billing_service.ensure_default_pricing_rules(db)
            db.commit()
            if created_count:
                print(f"已初始化 billing_price_rules 默认规则: {created_count} 条")
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as e:
        print(f"初始化计费默认规则失败: {str(e)}")


def ensure_video_model_pricing():
    """Ensure video_model_pricing table has correct schema (with provider column)."""
    try:
        columns = get_table_columns(engine, "video_model_pricing")
        if columns and "provider" not in columns:
            with engine.begin() as conn:
                print("[pricing] dropping old video_model_pricing table (missing provider column)")
                conn.execute(text("DROP TABLE video_model_pricing"))
            models.VideoModelPricing.__table__.create(engine)
            print("[pricing] recreated video_model_pricing table with provider column")
    except Exception as e:
        print(f"[pricing] ensure_video_model_pricing: {str(e)}")



# Initialize default video style templates
def ensure_video_style_templates():
    db = SessionLocal()
    try:
        existing = db.query(models.VideoStyleTemplate).count()
        if existing > 0:
            has_2d = db.query(models.VideoStyleTemplate).filter(models.VideoStyleTemplate.name == "2D").first()
            if not has_2d:
                db.add(models.VideoStyleTemplate(
                    name="2D",
                    sora_rule="准则：\n1 不要出现字幕。\n2 对话用中文。\n3 旁白时，人物嘴巴不要说话\n4 除场景声音外，不要有背景音乐",
                    style_prompt="严格按照逐帧2d日式动画的形式，赛璐璐着色风格，手绘动漫风格，强调帧间的手绘/精细绘制属性，帧间动作连贯流畅，每帧保留细腻的线条勾勒与沉稳质感的色彩，光影自然柔和，整体呈现影视动画的叙事分镜感\n线稿清晰，但最终效果趋向于无痕的上色，2D全彩动画，电影感。\nAvoid (Negative Prompts): Low quality, bad anatomy, deformed, grainy, text, watermark, 3D render , western style, cartoonish",
                    is_default=False
                ))

            preferred_template = db.query(models.VideoStyleTemplate).filter(
                models.VideoStyleTemplate.name == "3D国漫"
            ).first()
            if not preferred_template:
                preferred_template = models.VideoStyleTemplate(
                    name="3D国漫",
                    sora_rule="准则：\n1 不要出现字幕。\n2 对话用中文。\n3 旁白时，人物嘴巴不要说话\n4 除场景声音外，不要有背景音乐",
                    style_prompt="3D玄幻国漫",
                    is_default=True
                )
                db.add(preferred_template)

            db.flush()
            existing_default = db.query(models.VideoStyleTemplate).filter(
                models.VideoStyleTemplate.is_default == True
            ).first()
            if not existing_default:
                preferred_template.is_default = True
            db.commit()
            active_default = db.query(models.VideoStyleTemplate).filter(
                models.VideoStyleTemplate.is_default == True
            ).first()
            if active_default:
                print(f"[video-style] keep default template as {active_default.name}")
            else:
                print(f"[video-style] no explicit default template found; kept templates without override")
            return

        templates = [
            models.VideoStyleTemplate(
                name="2D",
                sora_rule="准则：\n1 不要出现字幕。\n2 对话用中文。\n3 旁白时，人物嘴巴不要说话\n4 除场景声音外，不要有背景音乐",
                style_prompt="严格按照逐帧2d日式动画的形式，赛璐璐着色风格，手绘动漫风格，强调帧间的手绘/精细绘制属性，帧间动作连贯流畅，每帧保留细腻的线条勾勒与沉稳质感的色彩，光影自然柔和，整体呈现影视动画的叙事分镜感\n线稿清晰，但最终效果趋向于无痕的上色，2D全彩动画，电影感。\nAvoid (Negative Prompts): Low quality, bad anatomy, deformed, grainy, text, watermark, 3D render , western style, cartoonish",
                is_default=False
            ),
            models.VideoStyleTemplate(
                name="3D国漫",
                sora_rule="准则：\n1 不要出现字幕。\n2 对话用中文。\n3 旁白时，人物嘴巴不要说话\n4 除场景声音外，不要有背景音乐",
                style_prompt="3D玄幻国漫",
                is_default=True
            ),
            models.VideoStyleTemplate(
                name="3D（半真实）",
                sora_rule="准则：\n1 不要出现字幕。\n2 对话用中文。\n3 旁白时，人物嘴巴不要说话\n4 除场景声音外，不要有背景音乐\n5 只能出现亚洲人面孔",
                style_prompt="仙侠3D半写实风格，PBR着色，次表面散射，环境光遮蔽，电影级景深，体积光照，暖色调，黄金时刻光晕，玄幻氛围。",
                is_default=False
            ),
            models.VideoStyleTemplate(
                name="真人",
                sora_rule="准则：\n1 不要出现字幕。\n2 对话用中文。\n3 旁白时，人物嘴巴不要说话\n4 除场景声音外，不要有背景音乐\n5 只能出现亚洲人面孔",
                style_prompt="皮肤质感：真实皮肤纹理，可见毛孔，自然皮肤瑕疵，皮肤细节，次表面散射，服饰为真实织物纹理，发丝为自然发质纹理，真实发丝，发量丰盈，光泽头发，自然光照，轮廓光，黄金时刻，自然人类表情，真实情感",
                is_default=False
            ),
        ]
        for t in templates:
            db.add(t)
        db.commit()
        print(f"[video-style] initialized {len(templates)} default video style templates")
    except Exception as e:
        print(f"[video-style] init failed: {e}")
        db.rollback()
    finally:
        db.close()


DEFAULT_PROMPTS = [
    {
        "key": "stage1_initial_storyboard",
        "name": "阶段1：初步分镜生成",
        "description": "按500字分批，生成约9个镜头的初步分镜",
        "content": """你是一位专业的影视分镜师。我将给你一段剧本内容，请将其拆分为大约9个镜头的分镜表。

【要求】
1. 每个镜头需要输出：镜号、主体（角色/场景/道具）、语音内容、原剧本段落
2. 主体类型只有三类：角色 / 场景 / 道具。
3. 主体只保留该镜头最主要的1-2个角色 + 1个场景 + 0-2个关键道具，避免过度细分。
4. 根据内容自然分镜，大约9个镜头（可以略多或略少）
5. 确保镜头之间连贯流畅

【关于语音内容的处理】
- **旁白**：第一人称的内心独白或解说
  - 使用具体人名作为说话人（如：李馨儿、萧景珩）
  - 第三方视角 → 使用"旁白"
  - 需要标注性别（女/男/中性）
  - 需要标注情绪（如：平静、悲伤、愤怒、带着哭腔等）

- **对话**：角色之间的对话
  - 按对话顺序列出，每句包含：说话人、对方、性别、情绪、对话内容
  - 示例：李馨儿对萧景珩说（带着哭腔）：你当真要我走？

【剧本内容】
{content}

【输出格式】
请严格按照以下JSON格式输出：
```json
{{
  "shots": [
    {{
      "shot_number": 1,
      "subjects": [
        {{"name": "柳如烟", "type": "角色"}},
        {{"name": "书房", "type": "场景"}},
        {{"name": "铜镜", "type": "道具"}}
      ],
      "original_text": "该镜头对应的原文片段",
      "voice_type": "narration 或 dialogue 或 none",
      "narration": {{
        "speaker": "具体人名（如：李馨儿）或 旁白",
        "gender": "女 或 男 或 中性",
        "emotion": "情绪描述",
        "text": "旁白内容"
      }},
      "dialogue": [
        {{
          "speaker": "说话人名字",
          "target": "对方名字（如果是对某人说）或 null",
          "gender": "女 或 男",
          "emotion": "情绪描述",
          "text": "对话内容"
        }}
      ]
    }}
  ]
}}
```

**注意**：
- 如果 voice_type 为 "narration"，则 dialogue 为 null
- 如果 voice_type 为 "dialogue"，则 narration 为 null
- 如果 voice_type 为 "none"（无语音），则 narration 和 dialogue 都为 null
- dialogue 数组可以包含多轮对话
- target 如果不是对特定人物说话（如自言自语、对众人说），则为 null

请开始分析。"""
    },
    {
        "key": "stage2_refine_shot",
        "name": "阶段2：主体绘画提示词",
        "description": "基于完整分镜表生成主体绘画提示词与别名",
        "content": """你是一位专业的AI绘画提示词工程师。以下是用户输入的分镜表：

  【分镜表】（共{total_shots}个镜头）
  {full_storyboard_json}

  【任务】
  1. 主体类型只有三类：角色 / 场景 / 道具。
  2. 智能识别重复和相似的主体：
     - 如果不同名称明显指向同一角色（昵称、称号、全名等），只保留一个规范名
     - 如果不同名称指向相似场景或同一关键道具，可以合并为一个
  3. 生成主体名称映射表（name_mappings）：
     - 将所有被合并的原始名称映射到规范名称
     - 映射表格式为JSON对象，键为原始名称，值为规范名称
  4. 为每个主体生成绘画提示词与别名。
     - 角色 ai_prompt：年龄 + 性别 + 表情 + 眼睛 + 发型 + 配饰 + 衣服 + 细节
     - 角色 role_personality：用中文一句话描述角色性格，场景/道具留空字符串
     - 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征
     - 道具 ai_prompt：材质、造型、颜色、结构、使用痕迹、细节特征
     - alias 为简短描述（10-20字）

  【输出格式】
  请严格按照以下JSON格式输出：
  {{
    "subjects": [
      {{
        "name": "规范主体名称",
        "type": "角色 / 场景 / 道具",
        "ai_prompt": "详细的绘画提示词",
        "role_personality": "角色性格（中文一句话），场景/道具填空字符串",
        "alias": "简短描述（10-20字）"
      }}
    ],
    "name_mappings": {{
      "原始名称1": "规范名称1",
      "原始名称2": "规范名称1"
    }}
  }}

  **重要提示**：
  - subjects 数组中只包含去重后的规范主体
  - name_mappings 必须包含所有被合并的原始名称到规范名称的映射
  - 如果某个主体没有被合并，则不需要在 name_mappings 中出现
  - 角色 ai_prompt：年龄 + 性别 + 表情 + 眼睛 + 发型 + 配饰 + 衣服 + 细节
  - 角色 role_personality：仅角色填写中文一句话性格描述，场景/道具必须为空字符串
  - 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征
  - 道具 ai_prompt：材质、造型、颜色、结构、使用痕迹、细节特征
  - 直接输出JSON对象，不要用markdown代码块包裹"""
    },
    {
        "key": "generate_subject_ai_prompt",
        "name": "生成主体绘画提示词",
        "description": "为单个主体生成AI绘画提示词与别名",
        "content": """你是一位专业的AI绘画提示词工程师。请为指定主体生成详细的绘画提示词。

【主体信息】
- 名称：{subject_name}
- 类型：{subject_type}

【分镜表上下文】
{storyboard_context}

【任务】
根据分镜表中该主体出现的场景和描述，生成详细的绘画提示词与别名。
- 角色 ai_prompt：年龄 + 性别 + 表情 + 眼睛 + 发型 + 配饰 + 衣服 + 细节
- 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征
- 道具 ai_prompt：材质、造型、颜色、结构、使用痕迹、细节特征
- alias 为简短描述（10-20字）
- ai_prompt需要是纯中文的

【输出格式】
请严格按照以下JSON格式输出：
{{
  "ai_prompt": "详细的绘画提示词（纯中文）",
  "alias": "简短描述（10-20字）"
}}

**重要提示**：
- 角色 ai_prompt：年龄 + 性别 + 表情 + 眼睛 + 发型 + 配饰 + 衣服 + 细节
- 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征
- 道具 ai_prompt：材质、造型、颜色、结构、使用痕迹、细节特征
- ai_prompt必须是纯中文
- alias为10-20字的简短描述
- 直接输出JSON对象，不要用markdown代码块包裹"""
    },
    {
        "key": "detailed_storyboard_content_analysis",
        "name": "详细分镜：内容分析",
        "description": "对已划分的镜头进行详细内容分析，提取主体、对白、旁白等信息",
        "content": """你是一位专业的影视分镜师。我将给你一组已经划分好的镜头（每个镜头包含镜号和原剧本段落），请为每个镜头提取详细的分镜信息。

【镜头列表】
{shots_content}

【分镜拆分要求】
每个镜头必须输出以下内容：
- 镜号（保持输入的一致）
- 主体（角色、场景、道具）
- 旁白或对白
- 原剧本段落（保持输入的一致）

【主体提取原则】
主体类型只能输出三类：角色、场景、道具。
主体必须包含至少一个场景（即：故事发生的地点）
主体只保留该镜头最主要的1-2个角色 + 1个场景 + 0-2个关键道具，避免把所有背景摆设都当成道具。
当原文出现对剧情推进、人物动作、画面构图或视觉识别有直接影响的具体物件时，必须提取为道具。
服装、发型、人物身体部位、纯抽象概念、普通环境装饰默认不要当作道具；只有被角色拿着、使用、注视，或被原文重点描述的关键物件才算道具。

【核心总原则（非常重要）】
严格保持输入数据的一致
如果输入不明确时，优先使用描述性文字。
只能严格使用剧本原文中的角色名字，如果角色没有姓名，也不要生成"女主角"、"男配角"。
你可以输入"警察"等职业常识性名词或用"警察应该叫做谁"来描述。
对原文未命名的，如果必须命名，可以使用唯一指代性的代词描述（例如"旗袍女""老板娘""母亲"等）。

【处理旁白及对白的处理】
- **旁白**：第三人称的旁述或独白或心理描述
  - 使用句号断句，句尾作为说话人，例如："某某内心道……"
  - 如果语句接近 → 使用"旁白"
  - 需要填写注意性别（女/男/无性别）
  - 需要填写注意情绪（例如：平静、伤心、悲愤、惊恐、兴奋等）
  - 角色内心的独白法则需注意叙事话语、细节等

- **对话**：角色之间的对话
  - 按对话顺序列出，每个对象包含说话人、对方名字（或描述）、对话内容
  - 示例：【某某内心道："……"，惊恐地（询问）："你当真要……？"】

【输出格式】
请严格按照以下JSON格式输出：
```json
{{
  "shots": [
    {{
      "shot_number": 1,
      "subjects": [
        {{"name": "角色名", "type": "角色"}},
        {{"name": "场景名", "type": "场景"}},
        {{"name": "关键道具", "type": "道具"}}
      ],
      "original_text": "该镜头对应的原文片段",
      "voice_type": "narration 或 dialogue 或 none",
      "narration": {{
        "speaker": "旁白角色名 或 旁白",
        "gender": "女 或 男 或 无 或 未知",
        "emotion": "情绪描述",
        "text": "旁白内容"
      }},
      "dialogue": [
        {{
          "speaker": "说话人名字",
          "target": "对方名字（如果是对某人说的）或 null",
          "gender": "女 或 男",
          "emotion": "情绪描述",
          "text": "对话内容"
        }}
      ]
    }}
  ]
}}
```

**注意**：
- 如果 voice_type 为 "narration"，则 dialogue 为 null
- 如果 voice_type 为 "dialogue"，则 narration 为 null
- 如果 voice_type 为 "none"，则两者（两个都是 narration 和 dialogue 均为 null
- dialogue 可以是旁白，可以是多段对话
- target 代表是对谁说的（如果对特定人说的），如果是广播性质或说话人为 null
- 保持 original_text 与输入完全一致，不要修改
- 保持 shot_number 与输入完全一致
- 直接输出JSON对象，不要用markdown代码块包裹

请开始分析。"""
    },
    {
        "key": CHARACTER_THREE_VIEW_PROMPT_KEY,
        "name": "角色三视图生图提示词",
        "description": "主体界面角色卡片点击“生成三视图”时使用的基础提示词",
        "content": CHARACTER_THREE_VIEW_PROMPT_DEFAULT
    },
    {
        "key": MANAGED_PROMPT_OPTIMIZE_KEY,
        "name": "优化提示词",
        "description": "托管任务因文字描述审核不通过时，用于一次性改写完整视频提示词",
        "content": MANAGED_PROMPT_OPTIMIZE_DEFAULT
    },
    {
        "key": "generate_video_prompts",
        "name": "分镜视频提示词生成",
        "description": "生成分镜的视频/音频/台词提示词",
        "content": """你是专业的分镜提示词生成助手。请根据以下信息生成分镜时间轴。

原剧本段落：
{script_excerpt}

出镜主体：
{subject_text}

说明：
- 角色主体会按“主体名-性格描述”的格式提供，场景/道具主体只写名称
- 请在画面设计、动作、表情和情绪表现中参考对应角色的性格信息

输出 JSON，格式如下：
{{
  "timeline": [
    {{
      "time": "00s-04s",
      "visual": "[推镜] [中景] 萧景珩翻阅书卷，眉头紧锁，手指轻抚纸页",
      "audio": "[萧景珩] 说：\\"此事不简单\\""
    }},
    {{
      "time": "04s-08s",
      "visual": "[特写] [近景] 手指停在某页，眼神凝重，面部微表情",
      "audio": "(SFX:翻页声、环境音)"
    }}
  ]
}}

要求：
1. 时长总计 {safe_duration} 秒，分为3-4个时间段
2. time字段格式：00s-04s、04s-08s（连续不重叠，覆盖完整时长）
3. visual字段包含：
   - 镜头类型：如[推镜][拉镜][摇镜][跟镜][正反打][切镜]等（自由发挥）
   - 景别：如[远景][全景][中景][近景][特写][大特写]等（自由发挥）
   - 画面描述：忠实描述原剧本段落的动作和情绪，不添加额外内容
4. audio字段包含：
   - 角色台词：格式为 [角色名] 说："台词内容"（保留原文台词）
   - 音效标记：格式为 (SFX:具体音效描述)
   - 如果既有台词又有音效，用顿号分隔：[角色] 说："台词"、(SFX:音效)
5. 每个时间段专注一个镜头焦点，保持连贯性
6. 只输出 JSON，不要其他说明

{extra_style}"""
    },
    {
        "key": "generate_large_shot_prompts",
        "name": "大镜头提示词生成",
        "description": "生成更偏电影化、镜头语言更强的大镜头时间轴提示词",
        "content": build_large_shot_prompt_rule(15, 4)
    },
    {
        "key": STORYBOARD2_VIDEO_PROMPT_KEY,
        "name": "故事板2分镜视频提示词生成",
        "description": "生成故事板2分镜的视频/音频/台词提示词",
        "content": STORYBOARD2_VIDEO_PROMPT_DEFAULT
    },
    {
        "key": STORYBOARD2_IMAGE_PROMPT_KEY,
        "name": "故事板2镜头图生图提示词",
        "description": "故事板2生成镜头图时自动前置的提示词",
        "content": STORYBOARD2_IMAGE_PROMPT_DEFAULT
    }
]

def ensure_prompt_config_table():
    """确保提示词配置表存在并初始化默认数据"""
    try:
        if not table_exists(engine, "prompt_configs"):
            print("prompt_configs 表不存在，将由 SQLAlchemy 创建")
            return

        # 表存在，补齐默认提示词（不覆盖已有配置）
        db = SessionLocal()
        try:
            existing_keys = {
                row[0] for row in db.query(models.PromptConfig.key).all()
            }
            inserted_count = 0
            for prompt_data in DEFAULT_PROMPTS:
                if prompt_data["key"] in existing_keys:
                    continue
                db.add(models.PromptConfig(**prompt_data))
                inserted_count += 1

            if inserted_count > 0:
                db.commit()
                print(f"prompt_configs 已补齐 {inserted_count} 条默认配置")
            else:
                total_count = db.query(models.PromptConfig).count()
                print(f"prompt_configs 表已有 {total_count} 条数据，无需补齐")
        except Exception as e:
            print(f"初始化默认提示词失败: {str(e)}")
            db.rollback()
        finally:
            db.close()

    except Exception as e:
        print(f"检查/初始化 prompt_configs 表失败: {str(e)}")



def upgrade_stage2_refine_shot_prompt_content(content: str) -> str:
    content_text = str(content or "")
    if not content_text:
        return content_text

    updated = content_text

    updated = updated.replace(
        "主体类型只有两类：角色 / 场景。",
        "主体类型只有三类：角色 / 场景 / 道具。"
    )
    updated = updated.replace(
        "如果不同名称指向相似场景，可以合并为一个",
        "如果不同名称指向相似场景或同一关键道具，可以合并为一个"
    )
    updated = updated.replace(
        "角色 role_personality_en：英文描述角色性格特征（如：calm, proud, stubborn），场景留空字符串",
        "角色 role_personality：用中文一句话描述角色性格，场景/道具留空字符串"
    )
    updated = updated.replace(
        '"role_personality_en": "角色性格（英文），场景填空字符串",',
        '"role_personality": "角色性格（中文一句话），场景/道具填空字符串",'
    )
    updated = updated.replace(
        "角色 role_personality_en：仅角色填写英文性格特征，场景必须为空字符串",
        "角色 role_personality：仅角色填写中文一句话性格描述，场景/道具必须为空字符串"
    )
    updated = updated.replace(
        "角色 role_personality：用中文一句话描述角色性格，场景留空字符串",
        "角色 role_personality：用中文一句话描述角色性格，场景/道具留空字符串"
    )
    updated = updated.replace(
        '"role_personality": "角色性格（中文一句话），场景填空字符串",',
        '"role_personality": "角色性格（中文一句话），场景/道具填空字符串",'
    )
    updated = updated.replace(
        "角色 role_personality：仅角色填写中文一句话性格描述，场景必须为空字符串",
        "角色 role_personality：仅角色填写中文一句话性格描述，场景/道具必须为空字符串"
    )
    updated = updated.replace(
        '"type": "角色 或 场景",',
        '"type": "角色 / 场景 / 道具",'
    )

    task_line = "     - 角色 ai_prompt：年龄 + 性别 + 表情 + 眼睛 + 发型 + 配饰 + 衣服 + 细节"
    scene_line = "     - 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征"
    prop_line = "     - 道具 ai_prompt：材质、造型、颜色、结构、使用痕迹、细节特征"
    if "角色 role_personality：" not in updated and "角色 role_personality_en：" not in updated:
        task_insert = task_line + "\n     - 角色 role_personality：用中文一句话描述角色性格，场景/道具留空字符串"
        updated = updated.replace(task_line, task_insert, 1)
    if prop_line not in updated and scene_line in updated:
        updated = updated.replace(scene_line, scene_line + "\n" + prop_line, 1)

    output_line = '        "ai_prompt": "详细的绘画提示词",\n        "alias": "简短描述（10-20字）"'
    if '"role_personality":' not in updated and '"role_personality_en":' not in updated:
        output_insert = '        "ai_prompt": "详细的绘画提示词",\n        "role_personality": "角色性格（中文一句话），场景/道具填空字符串",\n        "alias": "简短描述（10-20字）"'
        updated = updated.replace(output_line, output_insert, 1)

    hint_line = "  - 角色 ai_prompt：年龄 + 性别 + 表情 + 眼睛 + 发型 + 配饰 + 衣服 + 细节"
    scene_hint_line = "  - 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征"
    prop_hint_line = "  - 道具 ai_prompt：材质、造型、颜色、结构、使用痕迹、细节特征"
    if "角色 role_personality：" not in updated and "角色 role_personality_en：" not in updated:
        hint_insert = hint_line + "\n  - 角色 role_personality：仅角色填写中文一句话性格描述，场景/道具必须为空字符串"
        updated = updated.replace(hint_line, hint_insert, 1)
    if prop_hint_line not in updated and scene_hint_line in updated:
        updated = updated.replace(scene_hint_line, scene_hint_line + "\n" + prop_hint_line, 1)

    return updated


def ensure_stage2_refine_shot_prompt_config():
    """补齐阶段2提示词中的角色性格字段和道具主体规则，不影响其他自定义内容。"""
    db = SessionLocal()
    try:
        config = db.query(models.PromptConfig).filter(
            models.PromptConfig.key == "stage2_refine_shot"
        ).first()

        if not config:
            return

        upgraded = upgrade_stage2_refine_shot_prompt_content(config.content)
        if upgraded != (config.content or ""):
            config.content = upgraded
            db.commit()
            print("已补齐 stage2_refine_shot 的角色性格字段和道具主体规则")
    except Exception as e:
        db.rollback()
        print(f"更新 stage2_refine_shot 提示词失败: {str(e)}")
    finally:
        db.close()


def upgrade_subject_prompt_content_for_props(content: str) -> str:
    content_text = str(content or "")
    if not content_text:
        return content_text

    updated = content_text
    replacements = (
        ("主体（仅角色/场景）", "主体（角色/场景/道具）"),
        ("主体类型只有两类：角色 / 场景。不输出道具。", "主体类型只有三类：角色 / 场景 / 道具。"),
        ("主体只保留该镜头最主要的1-2个角色 + 1个场景，避免过度细分。", "主体只保留该镜头最主要的1-2个角色 + 1个场景 + 0-2个关键道具，避免过度细分。"),
        ("主体（角色和场景）", "主体（角色、场景、道具）"),
        ("主体类型只能输出两类：角色、场景。", "主体类型只能输出三类：角色、场景、道具。"),
        ("- 角色主体会按“主体名-性格描述”的格式提供，场景主体只写名称", "- 角色主体会按“主体名-性格描述”的格式提供，场景/道具主体只写名称"),
        ("角色 role_personality：用中文一句话描述角色性格，场景留空字符串", "角色 role_personality：用中文一句话描述角色性格，场景/道具留空字符串"),
        ('"role_personality": "角色性格（中文一句话），场景填空字符串",', '"role_personality": "角色性格（中文一句话），场景/道具填空字符串",'),
        ("角色 role_personality：仅角色填写中文一句话性格描述，场景必须为空字符串", "角色 role_personality：仅角色填写中文一句话性格描述，场景/道具必须为空字符串"),
    )
    for old_text, new_text in replacements:
        updated = updated.replace(old_text, new_text)

    if "角色 ai_prompt：年龄 + 性别 + 表情 + 眼睛 + 发型 + 配饰 + 衣服 + 细节" in updated and "道具 ai_prompt：" not in updated:
        updated = updated.replace(
            "- 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征",
            "- 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征\n- 道具 ai_prompt：材质、造型、颜色、结构、使用痕迹、细节特征"
        )

    if '{{"name": "铜镜", "type": "道具"}}' not in updated:
        updated = updated.replace(
            '{{"name": "书房", "type": "场景"}}',
            '{{"name": "书房", "type": "场景"}},\n        {{"name": "铜镜", "type": "道具"}}'
        )
    if '{{"name": "关键道具", "type": "道具"}}' not in updated:
        updated = updated.replace(
            '{{"name": "场景名", "type": "场景"}}',
            '{{"name": "场景名", "type": "场景"}},\n        {{"name": "关键道具", "type": "道具"}}'
        )

    updated = re.sub(
        r'(\{\{"name": "书房", "type": "场景"\}\})(?:,\s*\{\{"name": "铜镜", "type": "道具"\}\})+',
        r'\1,\n        {{"name": "铜镜", "type": "道具"}}',
        updated,
    )
    updated = re.sub(
        r'(\{\{"name": "场景名", "type": "场景"\}\})(?:,\s*\{\{"name": "关键道具", "type": "道具"\}\})+',
        r'\1,\n        {{"name": "关键道具", "type": "道具"}}',
        updated,
    )
    return updated


def upgrade_detailed_storyboard_content_analysis_prompt_content(content: str) -> str:
    content_text = upgrade_subject_prompt_content_for_props(content)
    if not content_text:
        return content_text

    updated = content_text

    scene_rule = "主体必须包含至少一个场景（即：故事发生的地点）"
    prop_scope_rule = "主体只保留该镜头最主要的1-2个角色 + 1个场景 + 0-2个关键道具，避免把所有背景摆设都当成道具。"
    if prop_scope_rule not in updated and scene_rule in updated:
        updated = updated.replace(scene_rule, scene_rule + "\n" + prop_scope_rule, 1)

    must_extract_prop_rule = "当原文出现对剧情推进、人物动作、画面构图或视觉识别有直接影响的具体物件时，必须提取为道具。"
    skip_noise_prop_rule = "服装、发型、人物身体部位、纯抽象概念、普通环境装饰默认不要当作道具；只有被角色拿着、使用、注视，或被原文重点描述的关键物件才算道具。"
    principles_header = "【主体提取原则】"
    if must_extract_prop_rule not in updated and principles_header in updated:
        updated = updated.replace(
            principles_header,
            principles_header + "\n" + must_extract_prop_rule + "\n" + skip_noise_prop_rule,
            1,
        )

    if "关键道具" not in updated and '"subjects": [' in updated:
        updated = updated.replace(
            '{{"name": "场景名", "type": "场景"}}',
            '{{"name": "场景名", "type": "场景"}},\n        {{"name": "关键道具", "type": "道具"}}',
            1,
        )

    return updated


def ensure_prop_subject_prompt_configs():
    """补齐主体相关提示词中的道具规则，不覆盖其他自定义内容。"""
    target_keys = (
        "stage1_initial_storyboard",
        "generate_subject_ai_prompt",
        "detailed_storyboard_content_analysis",
    )

    db = SessionLocal()
    try:
        configs = db.query(models.PromptConfig).filter(
            models.PromptConfig.key.in_(target_keys)
        ).all()

        updated_count = 0
        for config in configs:
            if config.key == "detailed_storyboard_content_analysis":
                upgraded = upgrade_detailed_storyboard_content_analysis_prompt_content(config.content)
            else:
                upgraded = upgrade_subject_prompt_content_for_props(config.content)
            if upgraded != (config.content or ""):
                config.content = upgraded
                updated_count += 1

        if updated_count:
            db.commit()
            print(f"已补齐 {updated_count} 条主体相关提示词的道具规则")
    except Exception as e:
        db.rollback()
        print(f"更新主体相关提示词失败: {str(e)}")
    finally:
        db.close()


def _is_placeholder_question_text(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    return "???" in value and not re.search(r"[\u4e00-\u9fff]", value)


def ensure_character_three_view_prompt_config():
    """修复角色三视图提示词配置中的乱码占位符，不覆盖正常自定义内容。"""
    db = SessionLocal()
    try:
        config = db.query(models.PromptConfig).filter(
            models.PromptConfig.key == CHARACTER_THREE_VIEW_PROMPT_KEY
        ).first()
        if not config:
            return

        should_update = False

        if _is_placeholder_question_text(config.name):
            config.name = "角色三视图生图提示词"
            should_update = True

        if _is_placeholder_question_text(config.description):
            config.description = "主体界面角色卡片点击“生成三视图”时使用的基础提示词"
            should_update = True

        if _is_placeholder_question_text(config.content):
            config.content = CHARACTER_THREE_VIEW_PROMPT_DEFAULT
            should_update = True

        if should_update:
            db.commit()
            print("已修复角色三视图提示词配置中的乱码占位符")
    except Exception as e:
        db.rollback()
        print(f"修复角色三视图提示词配置失败: {str(e)}")
    finally:
        db.close()




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


def _normalize_stage2_subjects(subjects: Optional[list]) -> list:
    return list(_build_subject_detail_map(subjects).values())


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


def ensure_storyboard2_prompt_config():
    """确保故事板2分镜提示词配置存在（仅初始化，不覆盖用户已保存内容）。"""
    db = SessionLocal()
    try:
        config = db.query(models.PromptConfig).filter(
            models.PromptConfig.key == STORYBOARD2_VIDEO_PROMPT_KEY
        ).first()

        if not config:
            db.add(models.PromptConfig(
                key=STORYBOARD2_VIDEO_PROMPT_KEY,
                name="故事板2分镜视频提示词生成",
                description="生成故事板2分镜的视频/音频/台词提示词",
                content=STORYBOARD2_VIDEO_PROMPT_DEFAULT
            ))
            db.commit()
            print("已初始化故事板2分镜提示词配置")
            return

        content_text = str(config.content or "").strip()
        has_subjects_field = "\"subjects\"" in content_text
        has_legacy_subject_marker = "[SB2_SUBJECTS_RULES_V2]" in content_text
        looks_like_stage1_prompt = (
            "阶段1：初步分镜生成" in content_text
            or ("\"shots\"" in content_text and "\"timeline\"" not in content_text)
            or "你是一位专业的影视分镜师" in content_text
        )
        missing_core_placeholders = (
            "{script_excerpt}" not in content_text
            or "{subject_text}" not in content_text
            or "{scene_description}" not in content_text
            or "{safe_duration}" not in content_text
        )

        if (
            looks_like_stage1_prompt
            or missing_core_placeholders
            or (not has_subjects_field)
            or has_legacy_subject_marker
        ):
            # 避免在服务重启时覆盖管理页已保存的自定义模板，只给出提示。
            print("检测到故事板2分镜提示词可能是旧版格式，已跳过自动覆盖，请在管理页手动更新。")
    except Exception as e:
        db.rollback()
        print(f"升级故事板2分镜提示词配置失败: {str(e)}")
    finally:
        db.close()




def _remove_scene_description_placeholder_block(prompt_text: str) -> str:
    """Remove the storyboard(sora) scene_description block without touching other content."""
    text_value = str(prompt_text or "")
    if not text_value:
        return text_value

    replacements = (
        (
            "{script_excerpt}\n\n场景描述：\n{scene_description}\n\n出镜主体：\n{subject_text}",
            "{script_excerpt}\n\n出镜主体：\n{subject_text}",
        ),
        (
            "{script_excerpt}\r\n\r\n场景描述：\r\n{scene_description}\r\n\r\n出镜主体：\r\n{subject_text}",
            "{script_excerpt}\r\n\r\n出镜主体：\r\n{subject_text}",
        ),
        ("\n\n场景描述：\n{scene_description}", ""),
        ("\r\n\r\n场景描述：\r\n{scene_description}", ""),
    )
    updated = text_value
    for old_value, new_value in replacements:
        updated = updated.replace(old_value, new_value, 1)
    return updated


def _subject_personality_hint_text(for_storyboard2: bool = False) -> str:
    if for_storyboard2:
        return (
            "说明：\n"
            "- 角色主体会按“主体名-性格描述”的格式提供，场景/道具主体只写名称\n"
            "- 生成结果里的 subjects 数组只填写“-”前的主体名称，不要把后面的性格描述写进去"
        )
    return (
        "说明：\n"
        "- 角色主体会按“主体名-性格描述”的格式提供，场景/道具主体只写名称\n"
        "- 请在画面设计、动作、表情和情绪表现中参考对应角色的性格信息"
    )


def _is_subject_personality_hint_line(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False

    if stripped == "说明：":
        return True

    hint_keywords = (
        "主体名-性格描述",
        "角色主体会按",
        "场景主体只写名称",
        "场景/道具主体只写名称",
        "道具主体只写名称",
        "画面设计",
        "动作、表情和情绪表现",
        "性格信息",
        "subjects 数组",
        "后面的性格描述写进去",
    )
    if any(keyword in stripped for keyword in hint_keywords):
        return True

    normalized = stripped.replace(" ", "").replace("-", "").replace("·", "").replace(":", "").replace("：", "")
    if not normalized:
        return False

    question_ratio = normalized.count("?") / len(normalized)
    return question_ratio >= 0.4


def _find_subject_personality_section_end(text_value: str, start_index: int) -> int:
    section_markers = (
        "\n\n输出 JSON",
        "\n\n可选的景别",
        "\n\n要求：",
        "\n\n输出要求",
        "\n\n输出格式",
        "\n\n请输出",
        "\n\n示例：",
    )
    positions = []
    for marker in section_markers:
        marker_index = text_value.find(marker, start_index)
        if marker_index != -1:
            positions.append(marker_index)
    return min(positions) if positions else -1


def _inject_subject_personality_hint(prompt_text: str, for_storyboard2: bool = False) -> str:
    text_value = str(prompt_text or "")
    if not text_value:
        return text_value

    hint_text = _subject_personality_hint_text(for_storyboard2)

    if "{subject_text}" in text_value:
        subject_end = text_value.find("{subject_text}") + len("{subject_text}")
        section_end = _find_subject_personality_section_end(text_value, subject_end)
        if section_end != -1:
            existing_block = text_value[subject_end:section_end]
            preserved_lines = []
            for raw_line in existing_block.splitlines():
                stripped = raw_line.strip()
                if not stripped:
                    continue
                if _is_subject_personality_hint_line(stripped):
                    continue
                preserved_lines.append(raw_line.rstrip())

            normalized_block = "\n\n" + hint_text
            if preserved_lines:
                normalized_block += "\n\n" + "\n".join(preserved_lines)
            return text_value[:subject_end] + normalized_block + text_value[section_end:]

        return text_value.replace("{subject_text}", "{subject_text}\n\n" + hint_text, 1)

    if "说明：" in text_value or "主体名-性格描述" in text_value or "subjects 数组" in text_value:
        cleaned_lines = []
        for raw_line in text_value.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                cleaned_lines.append("")
                continue
            if _is_subject_personality_hint_line(stripped):
                continue
            cleaned_lines.append(raw_line.rstrip())
        text_value = "\n".join(cleaned_lines).strip()

    return f"{text_value.rstrip()}\n\n{hint_text}".strip()


def ensure_shot_duration_template_without_scene_description():
    """Remove the storyboard(sora) scene_description block from duration templates."""
    db = SessionLocal()
    try:
        templates = db.query(models.ShotDurationTemplate).all()
        updated_count = 0

        for template in templates:
            old_rule = template.video_prompt_rule or ""
            new_rule = _remove_scene_description_placeholder_block(old_rule)
            if new_rule != old_rule:
                template.video_prompt_rule = new_rule
                updated_count += 1

        if updated_count > 0:
            db.commit()
            print(f"已更新时长配置模板 video_prompt_rule，移除 scene_description 段落: {updated_count} 条")
    except Exception as e:
        db.rollback()
        print(f"更新时长配置模板 scene_description 段落失败: {str(e)}")
    finally:
        db.close()


def ensure_generate_video_prompt_config_without_scene_description():
    """Remove the storyboard(sora) scene_description block from the default video prompt config."""
    db = SessionLocal()
    try:
        config = db.query(models.PromptConfig).filter(
            models.PromptConfig.key == "generate_video_prompts"
        ).first()
        if not config:
            return

        old_content = config.content or ""
        new_content = _remove_scene_description_placeholder_block(old_content)
        if new_content != old_content:
            config.content = new_content
            db.commit()
            print("已更新 generate_video_prompts，移除 scene_description 段落")
    except Exception as e:
        db.rollback()
        print(f"更新 generate_video_prompts 的 scene_description 段落失败: {str(e)}")
    finally:
        db.close()


def _build_default_simple_storyboard_config_payload(duration: int) -> Dict[str, Any]:
    return get_default_rule_config(duration).to_dict()


def ensure_shot_duration_template_config_json():
    """Ensure shot duration templates expose structured simple-storyboard rule configs."""
    try:
        columns = get_table_columns(engine, "shot_duration_templates")
        if not columns:
            return
        if "simple_storyboard_config_json" not in columns:
            with engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE shot_duration_templates ADD COLUMN simple_storyboard_config_json TEXT DEFAULT ''")
                )
            print("已添加 shot_duration_templates.simple_storyboard_config_json 字段")
    except Exception as e:
        print(f"补充 shot_duration_templates.simple_storyboard_config_json 字段失败: {str(e)}")
        return

    db = SessionLocal()
    try:
        templates = db.query(models.ShotDurationTemplate).all()
        updated_count = 0
        for template in templates:
            current_value = str(getattr(template, "simple_storyboard_config_json", "") or "").strip()
            if current_value:
                try:
                    normalize_rule_config(json.loads(current_value), int(getattr(template, "duration", 15) or 15))
                    continue
                except Exception:
                    pass
            template.simple_storyboard_config_json = json.dumps(
                _build_default_simple_storyboard_config_payload(int(getattr(template, "duration", 15) or 15)),
                ensure_ascii=False,
            )
            updated_count += 1
        if updated_count > 0:
            db.commit()
            print(f"已补齐简单分镜结构化规则配置: {updated_count} 条")
    except Exception as e:
        db.rollback()
        print(f"补齐简单分镜结构化规则配置失败: {str(e)}")
    finally:
        db.close()


def ensure_shot_duration_template_large_shot_rule():
    """Ensure shot duration templates expose a dedicated large-shot prompt rule."""
    try:
        columns = get_table_columns(engine, "shot_duration_templates")
        if not columns:
            return

        if "large_shot_prompt_rule" not in columns:
            with engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE shot_duration_templates ADD COLUMN large_shot_prompt_rule TEXT DEFAULT ''")
                )
            print("已添加 shot_duration_templates.large_shot_prompt_rule 字段")
    except Exception as e:
        print(f"补充 shot_duration_templates.large_shot_prompt_rule 字段失败: {str(e)}")
        return

    db = SessionLocal()
    try:
        templates = db.query(models.ShotDurationTemplate).all()
        updated_count = 0

        for template in templates:
            existing_rule = (getattr(template, "large_shot_prompt_rule", "") or "").strip()
            if existing_rule and not is_legacy_large_shot_prompt_rule(existing_rule):
                continue
            template.large_shot_prompt_rule = build_large_shot_prompt_rule(
                template.duration,
                template.time_segments
            )
            updated_count += 1

        if updated_count > 0:
            db.commit()
            print(f"已补齐大镜头提示词规则: {updated_count} 条")
    except Exception as e:
        db.rollback()
        print(f"补齐大镜头提示词规则失败: {str(e)}")
    finally:
        db.close()


def ensure_generate_large_shot_prompt_config():
    """Repair or seed the default large-shot prompt config."""
    db = SessionLocal()
    try:
        config = db.query(models.PromptConfig).filter(
            models.PromptConfig.key == "generate_large_shot_prompts"
        ).first()
        if not config:
            return

        default_content = build_large_shot_prompt_rule(15, 4)
        current_content = (config.content or "").strip()
        if current_content and not is_legacy_large_shot_prompt_rule(current_content):
            return

        config.content = default_content
        db.commit()
        print("已修复 generate_large_shot_prompts 默认模板")
    except Exception as e:
        db.rollback()
        print(f"修复 generate_large_shot_prompts 默认模板失败: {str(e)}")
    finally:
        db.close()


def ensure_large_shot_templates():
    """Seed the global large-shot template library without overwriting user edits."""
    try:
        if not table_exists(engine, "large_shot_templates"):
            return
    except Exception:
        return

    db = SessionLocal()
    try:
        seeded_templates = get_default_large_shot_templates()
        existing_templates = db.query(models.LargeShotTemplate).order_by(
            models.LargeShotTemplate.created_at.asc(),
            models.LargeShotTemplate.id.asc()
        ).all()
        removed_legacy_count = 0
        for template in existing_templates:
            if (template.name or "").strip() == "综合默认":
                db.delete(template)
                removed_legacy_count += 1

        if removed_legacy_count > 0:
            db.commit()
            existing_templates = db.query(models.LargeShotTemplate).order_by(
                models.LargeShotTemplate.created_at.asc(),
                models.LargeShotTemplate.id.asc()
            ).all()

        existing_by_name = {
            (template.name or "").strip(): template
            for template in existing_templates
            if (template.name or "").strip()
        }

        inserted_count = 0
        for template_data in seeded_templates:
            template_name = (template_data.get("name") or "").strip()
            if not template_name or template_name in existing_by_name:
                continue
            db.add(models.LargeShotTemplate(
                name=template_name,
                content=template_data.get("content") or "",
                is_default=False,
            ))
            inserted_count += 1

        if inserted_count > 0:
            db.commit()
            existing_templates = db.query(models.LargeShotTemplate).order_by(
                models.LargeShotTemplate.created_at.asc(),
                models.LargeShotTemplate.id.asc()
            ).all()

        default_template = next((template for template in existing_templates if template.is_default), None)
        if not default_template and existing_templates:
            preferred_name = ((seeded_templates[0] or {}).get("name") or "").strip() if seeded_templates else ""
            preferred = next(
                (
                    template
                    for template in existing_templates
                    if preferred_name and (template.name or "").strip() == preferred_name
                ),
                existing_templates[0]
            )
            db.query(models.LargeShotTemplate).update({"is_default": False})
            preferred.is_default = True
            db.commit()
            print("已设置默认大镜头模板")
        elif inserted_count > 0 or removed_legacy_count > 0:
            print(f"已同步大镜头模板库: 新增 {inserted_count} 条，移除 {removed_legacy_count} 条")
    except Exception as e:
        db.rollback()
        print(f"初始化大镜头模板库失败: {str(e)}")
    finally:
        db.close()




def ensure_shot_duration_template_subject_personality():
    """Upgrade shot duration video prompt rules to mention role personality context."""
    db = SessionLocal()
    try:
        templates = db.query(models.ShotDurationTemplate).all()
        updated_count = 0

        for template in templates:
            old_rule = template.video_prompt_rule or ""
            new_rule = _inject_subject_personality_hint(old_rule, for_storyboard2=False)
            if new_rule != old_rule:
                template.video_prompt_rule = new_rule
                updated_count += 1

        if updated_count > 0:
            db.commit()
            print(f"已升级时长配置模板 video_prompt_rule，新增角色性格说明: {updated_count} 条")
    except Exception as e:
        db.rollback()
        print(f"升级时长配置模板角色性格说明失败: {str(e)}")
    finally:
        db.close()




def ensure_remove_legacy_duration_templates():
    """Delete 6s and 10s shot duration templates which are no longer in use."""
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM shot_duration_templates WHERE duration IN (6, 10)"))
        print("已删除废弃的 6s / 10s 时长配置模板")
    except Exception as e:
        print(f"删除废弃时长配置模板失败: {str(e)}")


def ensure_video_prompt_subject_personality_configs():
    """Upgrade video prompt configs so manage UI exposes role personality context."""
    db = SessionLocal()
    try:
        updated_count = 0
        config_rules = {
            "generate_video_prompts": False,
            "generate_large_shot_prompts": False,
            STORYBOARD2_VIDEO_PROMPT_KEY: True,
        }

        for key, for_storyboard2 in config_rules.items():
            config = db.query(models.PromptConfig).filter(
                models.PromptConfig.key == key
            ).first()
            if not config:
                continue
            old_content = config.content or ""
            new_content = _inject_subject_personality_hint(old_content, for_storyboard2=for_storyboard2)
            if new_content != old_content:
                config.content = new_content
                updated_count += 1

        if updated_count > 0:
            db.commit()
            print(f"已升级视频提示词配置，新增角色性格说明: {updated_count} 条")
    except Exception as e:
        db.rollback()
        print(f"升级视频提示词配置角色性格说明失败: {str(e)}")
    finally:
        db.close()


_STYLE_TEMPLATE_REMOVAL_PHRASES = [
    "保持与该风格角色模板一致的整体画风与审美基调：",
    "人物高清画质",
    "人物冷白皮",
    "照片级写实肖像",
    "真实人类",
    "专业人像摄影",
    "专业人像",
    "纯白色背景",
    "纯白背景",
    "白底",
    "全身",
    "半身",
    "正视图",
    "正面角度",
    "正面",
    "面部特写",
    "面部",
    "人物",
    "角色",
    "人像",
    "肖像",
    "人类",
    "冷白皮",
    "伪bjd脸型",
    "bjd脸型",
    "立体五官",
    "五官",
    "皮肤纹理",
    "皮肤细节",
    "自然皮肤瑕疵",
    "皮肤",
    "可见毛孔",
    "站立",
    "站姿",
]


_STYLE_TEMPLATE_BANNED_SEGMENT_KEYWORDS = [
    "人物",
    "角色",
    "人像",
    "男人",
    "女人",
    "少年",
    "少女",
    "男生",
    "女生",
    "男",
    "女",
    "脸",
    "面部",
    "五官",
    "皮肤",
    "毛孔",
    "冷白皮",
    "发型",
    "发丝",
    "头发",
    "发量",
    "眼睛",
    "瞳孔",
    "嘴唇",
    "表情",
    "眼神",
    "凝视",
    "情感",
    "模特",
    "样貌",
    "站立",
    "站姿",
    "全身",
    "半身",
    "白底",
    "正视图",
    "正面",
    "穿搭",
    "妆容",
]

_STYLE_TEMPLATE_SKIP_EXACT_CHUNKS = {
    "核心风格",
    "建模技术",
    "质感",
    "服饰细节",
    "光影效果",
}

_STYLE_TEMPLATE_LABEL_PREFIXES = [
    "核心风格",
    "建模技术",
    "质感",
    "皮肤质感",
    "服饰细节",
    "光影效果",
]


def _extract_style_core_from_character_template(character_content: str) -> str:
    text_content = str(character_content or "").strip()
    if not text_content:
        return ""

    normalized = text_content
    for phrase in _STYLE_TEMPLATE_REMOVAL_PHRASES:
        normalized = normalized.replace(phrase, "")

    raw_parts = re.split(r"[\n\r,，。；;、]+", normalized)
    cleaned_parts: List[str] = []
    seen_parts = set()

    for raw_part in raw_parts:
        chunk = re.sub(r"^[\-\s]+", "", str(raw_part or "").strip())
        chunk = chunk.strip(" ：:，,。；;、")
        chunk = re.sub(r"[＋+]{2,}", "＋", chunk)
        chunk = re.sub(r"^(?:[＋+]\s*)+", "", chunk)
        chunk = re.sub(r"(?:\s*[＋+])+$", "", chunk)
        for label_prefix in _STYLE_TEMPLATE_LABEL_PREFIXES:
            chunk = re.sub(rf"^{re.escape(label_prefix)}\s*[：:]\s*", "", chunk)
        if not chunk:
            continue
        if chunk in _STYLE_TEMPLATE_SKIP_EXACT_CHUNKS:
            continue
        if any(keyword in chunk for keyword in _STYLE_TEMPLATE_BANNED_SEGMENT_KEYWORDS):
            continue
        normalized_key = re.sub(r"\s+", "", chunk)
        if not normalized_key or normalized_key in seen_parts:
            continue
        seen_parts.add(normalized_key)
        cleaned_parts.append(chunk)

    return "，".join(cleaned_parts[:16]).strip("，")


def _build_scene_style_template_content(character_content: str) -> str:
    style_core = _extract_style_core_from_character_template(character_content)
    if not style_core:
        return (
            "突出环境设计、空间层次、光影氛围、建筑与陈设细节、材质肌理与镜头感。"
            "不要出现人物，不要纯白背景，不要角色定妆式构图。"
        )
    return (
        f"{style_core}\n"
        "突出环境设计、空间层次、光影氛围、建筑与陈设细节、材质肌理与镜头感。"
        "不要出现人物，不要纯白背景，不要角色定妆式构图。"
    )


def _build_prop_style_template_content(character_content: str) -> str:
    style_core = _extract_style_core_from_character_template(character_content)
    if not style_core:
        return (
            "突出道具材质、结构造型、轮廓识别度、工艺细节、使用痕迹与局部特写。"
            "不要出现人物，不要纯白背景，不要角色站姿或人像式构图。"
        )
    return (
        f"{style_core}\n"
        "突出道具材质、结构造型、轮廓识别度、工艺细节、使用痕迹与局部特写。"
        "不要出现人物，不要纯白背景，不要角色站姿或人像式构图。"
    )


def _style_template_variant_needs_regeneration(content: str) -> bool:
    text_content = str(content or "").strip()
    if not text_content:
        return True
    if "??????????" in text_content or "?????" in text_content[:20]:
        return True
    if text_content.startswith("保持与该风格角色模板一致的整体画风与审美基调："):
        return True
    artifact_markers = [
        "＋＋",
        "核心风格，",
        "建模技术，",
        "发量丰盈",
        "质感：",
        "服饰细节：",
        "光影效果：",
        "真实情感",
        "写实凝视",
    ]
    return any(marker in text_content for marker in artifact_markers)


def ensure_style_template_variant_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "style_templates")
            if not columns:
                return

            if "scene_content" not in columns:
                conn.execute(
                    text("ALTER TABLE style_templates ADD COLUMN scene_content TEXT DEFAULT ''")
                )
                print("已添加 style_templates.scene_content 字段")
            if "prop_content" not in columns:
                conn.execute(
                    text("ALTER TABLE style_templates ADD COLUMN prop_content TEXT DEFAULT ''")
                )
                print("已添加 style_templates.prop_content 字段")

            conn.execute(text("UPDATE style_templates SET scene_content = '' WHERE scene_content IS NULL"))
            conn.execute(text("UPDATE style_templates SET prop_content = '' WHERE prop_content IS NULL"))

        db = SessionLocal()
        try:
            updated_count = 0
            templates = db.query(models.StyleTemplate).all()
            for template in templates:
                if _style_template_variant_needs_regeneration(template.scene_content):
                    template.scene_content = _build_scene_style_template_content(template.content)
                    updated_count += 1
                if _style_template_variant_needs_regeneration(template.prop_content):
                    template.prop_content = _build_prop_style_template_content(template.content)
                    updated_count += 1

            if updated_count > 0:
                db.commit()
                print(f"已补齐风格模板场景/道具版本: {updated_count} 处")
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as e:
        print(f"检查/迁移 style_templates 场景/道具版本失败: {str(e)}")


def ensure_default_style_templates():
    """初始化默认的全局风格模板"""
    try:
        db = SessionLocal()
        try:
            default_style_name = "日漫风格"
            default_style_content = "日漫，天官赐福风格，一人之下风格，大理寺日志风格，百妖谱风格，仙王的日常生活风格，二次元画风，古风，平涂，赛璐璐上色，低饱和度"

            # 检查全局模板是否已存在
            existing = db.query(models.StyleTemplate).filter(
                models.StyleTemplate.name == default_style_name
            ).first()

            if not existing:
                # 创建全局模板
                new_template = models.StyleTemplate(
                    name=default_style_name,
                    content=default_style_content,
                    scene_content=_build_scene_style_template_content(default_style_content),
                    prop_content=_build_prop_style_template_content(default_style_content),
                )
                db.add(new_template)
                db.commit()
                print(f"✓ 创建全局风格模板: {default_style_name}")

        except Exception as e:
            db.rollback()
            print(f"初始化风格模板失败: {e}")
        finally:
            db.close()
    except Exception as e:
        print(f"初始化风格模板出错: {e}")



def ensure_user_password_column():
    """迁移：确保 users 表包含 password_hash/password_plain，并补齐默认值"""
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "users")
            if not columns:
                return
            if "password_hash" not in columns:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN password_hash VARCHAR NOT NULL DEFAULT ''")
                )
                print("已添加 users.password_hash 字段")
            if "password_plain" not in columns:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN password_plain VARCHAR NOT NULL DEFAULT '123456'")
                )
                print("已添加 users.password_plain 字段")

        # 为无密码的已有用户设置默认密码 123456，并补齐明文字段
        default_hash = _hash_password("123456")
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE users SET password_hash = :h WHERE password_hash = '' OR password_hash IS NULL"),
                {"h": default_hash}
            )
            conn.execute(
                text("UPDATE users SET password_plain = '123456' WHERE password_plain = '' OR password_plain IS NULL")
            )
    except Exception as e:
        print(f"迁移 users.password 字段失败: {str(e)}")


def ensure_function_model_config_columns():
    try:
        with engine.begin() as conn:
            columns = get_table_columns(engine, "function_model_configs")
            if not columns:
                return

            if "provider_key" not in columns:
                conn.execute(
                    text("ALTER TABLE function_model_configs ADD COLUMN provider_key VARCHAR DEFAULT 'openrouter'")
                )
                print("已添加 function_model_configs.provider_key 字段")
                columns.add("provider_key")

            if "model_key" not in columns:
                conn.execute(
                    text("ALTER TABLE function_model_configs ADD COLUMN model_key VARCHAR")
                )
                print("已添加 function_model_configs.model_key 字段")
                columns.add("model_key")

            conn.execute(
                text(
                    "UPDATE function_model_configs "
                    "SET provider_key = 'openrouter' "
                    "WHERE provider_key IS NULL OR TRIM(provider_key) = ''"
                )
            )
            conn.execute(
                text(
                    "UPDATE function_model_configs "
                    "SET model_key = model_id "
                    "WHERE (model_key IS NULL OR TRIM(model_key) = '') "
                    "AND model_id IS NOT NULL AND TRIM(model_id) <> ''"
                )
            )
            conn.execute(
                text(
                    "UPDATE function_model_configs "
                    "SET provider_key = 'openrouter', "
                    "    model_key = 'google/gemini-3.1-pro-preview', "
                    "    model_id = 'google/gemini-3.1-pro-preview' "
                    "WHERE function_key = 'video_prompt' "
                    "  AND LOWER(COALESCE(provider_key, '')) IN ('', 'openrouter', 'yyds') "
                    "  AND COALESCE(NULLIF(TRIM(model_key), ''), '') IN ('', 'google/gemini-3.1-pro-preview', 'google/gemini-3-pro-preview', 'gemini_pro_preview', 'gemini_pro_high') "
                    "  AND COALESCE(NULLIF(TRIM(model_id), ''), '') IN ('', 'google/gemini-3.1-pro-preview', 'google/gemini-3-pro-preview', 'gemini-3.1-pro-preview', 'gemini-3.1-pro-high')"
                )
            )
            conn.execute(
                text(
                    "UPDATE function_model_configs "
                    "SET model_key = 'gemini_pro_high', "
                    "    model_id = 'gemini-3.1-pro-high' "
                    "WHERE LOWER(COALESCE(provider_key, '')) = 'yyds' "
                    "  AND ("
                    "        COALESCE(NULLIF(TRIM(model_key), ''), '') IN ('gemini_pro_preview', 'gemini_pro_high') "
                    "     OR COALESCE(NULLIF(TRIM(model_id), ''), '') IN ('gemini-3.1-pro-preview', 'gemini-3.1-pro-high')"
                    "  )"
                )
            )
    except Exception as e:
        print(f"迁移 function_model_configs provider/model_key 字段失败: {str(e)}")


HIT_DRAMA_ONLINE_TIME_PATTERN = re.compile(r"^(?P<year>\d{4})[./-](?P<month>\d{1,2})[./-](?P<day>\d{1,2})$")


def normalize_hit_drama_online_time(value: Any) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return ""

    match = HIT_DRAMA_ONLINE_TIME_PATTERN.fullmatch(raw_value)
    if not match:
        raise ValueError("上线时间格式应为 YYYY.MM.DD")

    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))

    try:
        datetime(year, month, day)
    except ValueError as exc:
        raise ValueError("上线时间不是有效日期") from exc

    return f"{year:04d}.{month:02d}.{day:02d}"


def normalize_hit_drama_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    normalized_payload: Dict[str, str] = {}
    if "drama_name" in payload:
        normalized_payload["drama_name"] = str(payload.get("drama_name") or "").strip()
    if "view_count" in payload:
        normalized_payload["view_count"] = str(payload.get("view_count") or "").strip()
    if "opening_15_sentences" in payload:
        normalized_payload["opening_15_sentences"] = str(payload.get("opening_15_sentences") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if "first_episode_script" in payload:
        normalized_payload["first_episode_script"] = str(payload.get("first_episode_script") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if "online_time" in payload:
        normalized_payload["online_time"] = normalize_hit_drama_online_time(payload.get("online_time"))
    return normalized_payload


def ensure_hit_drama_columns():
    try:
        with engine.begin() as conn:
            if not table_exists(engine, "hit_dramas"):
                return

            columns = get_table_columns(engine, "hit_dramas")
            if not columns:
                return

            if "organizer" in columns:
                try:
                    conn.execute(text("ALTER TABLE hit_dramas DROP COLUMN organizer"))
                    print("已删除 hit_dramas.organizer 字段")
                except Exception as drop_error:
                    print(f"删除 hit_dramas.organizer 失败，等待下次启动重试: {drop_error}")
    except Exception as e:
        print(f"检查/迁移 hit_dramas 失败: {str(e)}")


# 确保必要的目录存在
os.makedirs("uploads", exist_ok=True)
os.makedirs("videos", exist_ok=True)
os.makedirs("../frontend", exist_ok=True)

app = FastAPI(title="Story Creator API")

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def track_request_load(request, call_next):
    path = request.url.path or ""
    if path.startswith("/static") or path.startswith("/uploads") or path.startswith("/videos"):
        return await call_next(request)

    request_load_tracker.request_started(path)
    try:
        return await call_next(request)
    finally:
        request_load_tracker.request_finished(path)

# 挂载静态文件目录
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/videos", StaticFiles(directory="videos"), name="videos")
app.mount("/static", StaticFiles(directory="../frontend"), name="static")
app.include_router(pages.router)
app.include_router(media.router)
app.include_router(public.router)
app.include_router(image_generation.router)

# AI调试信息保存函数
def save_ai_debug(
    stage: str,
    input_data: dict,
    output_data: dict = None,
    raw_response: dict = None,
    episode_id: int = None,
    shot_id: int = None,
    batch_id: str = None,
    task_folder: str = None,
    attempt: int = None
):
    """
    记录 AI 调试事件到 dashboard task log，并返回稳定的任务分组 key。

    Args:
        stage: 阶段名 ('stage1', 'stage2', 'video_generate')
        input_data: 输入数据
        output_data: 输出数据（可选）
        raw_response: 原始响应或错误信息（可选）
        episode_id: 片段ID
        shot_id: 镜头ID（用于视频或调试）
        batch_id: 批次ID（用于stage1）
        task_folder: 任务分组 key（可选，自动生成）
    """
    try:
        from datetime import datetime

        if not episode_id and not shot_id:
            return None

        # 生成task_folder
        if not task_folder:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            task_folder = f"episode_{episode_id}_{timestamp}" if episode_id else f"shot_{shot_id}_{timestamp}"

        # 生成文件名前缀（供log_debug_task_event使用）
        if (stage == 'stage1' or stage == 'simple_storyboard') and batch_id:
            file_prefix = f"{stage}_batch{batch_id}"
            if attempt is not None:
                file_prefix += f"_attempt{attempt}"
        elif stage == 'stage2':
            file_prefix = f"stage2_shot{shot_id}" if shot_id else "stage2"
            if attempt is not None:
                file_prefix += f"_attempt{attempt}"
        elif stage == 'video_generate' and shot_id:
            file_prefix = f"video_shot{shot_id}"
        else:
            file_prefix = stage
            if attempt is not None:
                file_prefix += f"_attempt{attempt}"

        try:
            log_debug_task_event(
                stage=stage,
                task_folder=task_folder,
                input_data=input_data,
                output_data=output_data,
                raw_response=raw_response,
                episode_id=episode_id,
                shot_id=shot_id,
                batch_id=batch_id,
                file_name=f"{file_prefix}_event.json",
            )
        except Exception as e:
            print(f"[dashboard] save_ai_debug sync failed: {str(e)}")

        return task_folder  # 返回文件夹名，供后续调用使用

    except Exception as e:
        import traceback
        traceback.print_exc()
        return None


def _extract_text_relay_result_content(upstream_payload: Dict[str, Any]) -> str:
    result = upstream_payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("relay result payload missing result object")
    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
    raise ValueError("relay result payload missing assistant content")


def _parse_text_relay_result_json(upstream_payload: Dict[str, Any]) -> Dict[str, Any]:
    content = _extract_text_relay_result_content(upstream_payload)
    normalized = str(content or "").strip()
    if normalized.startswith("```json"):
        normalized = normalized.split("```json", 1)[1].split("```", 1)[0].strip()
    elif normalized.startswith("```"):
        normalized = normalized.split("```", 1)[1].split("```", 1)[0].strip()
    return json.loads(normalized or "{}")


def handle_text_relay_task_success(db: Session, task: models.TextRelayTask, upstream_payload: Dict[str, Any]):
    task_payload = {}
    try:
        task_payload = json.loads(str(getattr(task, "task_payload", "") or "{}"))
    except Exception:
        task_payload = {}

    task_type = str(getattr(task, "task_type", "") or "").strip()

    if task_type == "opening":
        episode_id = int(task_payload.get("episode_id") or task.owner_id or 0)
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            episode.opening_content = _extract_text_relay_result_content(upstream_payload)
            episode.opening_generating = False
            episode.opening_error = ""
        return

    if task_type == "narration":
        episode_id = int(task_payload.get("episode_id") or task.owner_id or 0)
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            episode.content = _extract_text_relay_result_content(upstream_payload)
            episode.narration_converting = False
            episode.narration_error = ""
        return

    if task_type == "subject_prompt":
        card_id = int(task_payload.get("card_id") or task.owner_id or 0)
        card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
        if not card:
            return
        parsed = _parse_text_relay_result_json(upstream_payload)
        card.ai_prompt = str(parsed.get("ai_prompt") or "")
        alias = str(parsed.get("alias") or "").strip()
        if alias:
            card.alias = alias
        if hasattr(card, "ai_prompt_status"):
            card.ai_prompt_status = "completed"
        return

    if task_type == "simple_storyboard_batch":
        raise ValueError("简单分镜已迁移为本地程序生成，请重新发起整次简单分镜。")

    if task_type == "detailed_storyboard_stage1":
        episode_id = int(task_payload.get("episode_id") or task.owner_id or 0)
        parsed = _parse_text_relay_result_json(upstream_payload)
        detailed_shots = parsed.get("shots") or []
        if not detailed_shots:
            raise ValueError("详细分镜 Stage 1 未返回镜头数据")

        final_shots = []
        for shot in detailed_shots:
            shot["subjects"] = _normalize_storyboard_generation_subjects(shot.get("subjects", []))
            final_shots.append(shot)
        _submit_detailed_storyboard_stage2_task(db, episode_id=episode_id, final_shots=final_shots)
        return

    if task_type == "detailed_storyboard_stage2":
        episode_id = int(task_payload.get("episode_id") or task.owner_id or 0)
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if not episode:
            return
        final_shots = list(task_payload.get("final_shots") or [])
        parsed = _parse_text_relay_result_json(upstream_payload)
        stage2_subjects = _normalize_stage2_subjects(parsed.get("subjects", []))
        name_mappings = parsed.get("name_mappings", {}) or {}
        canonical_subject_map = _build_subject_detail_map(stage2_subjects)
        for shot in final_shots:
            shot["subjects"] = _reconcile_storyboard_shot_subjects(
                shot,
                canonical_subject_map,
                name_mappings=name_mappings,
            )

        final_data = {
            "shots": final_shots,
            "subjects": stage2_subjects,
        }
        voiceover_shots = []
        for shot in final_shots:
            voiceover_shots.append({
                "shot_number": shot.get("shot_number"),
                "voice_type": shot.get("voice_type"),
                "narration": shot.get("narration"),
                "dialogue": shot.get("dialogue"),
            })
        merged_voiceover_data = _merge_voiceover_shots_preserving_extensions(
            episode.voiceover_data,
            voiceover_shots
        )

        episode.storyboard_data = json.dumps(final_data, ensure_ascii=False)
        episode.voiceover_data = json.dumps(merged_voiceover_data, ensure_ascii=False)
        episode.storyboard_generating = False
        episode.storyboard_error = ""
        _create_shots_from_storyboard_data(episode_id, db)
        return

    if task_type == "sora_prompt":
        shot_id = int(task_payload.get("shot_id") or task.owner_id or 0)
        shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
        if not shot:
            return
        parsed = _parse_text_relay_result_json(upstream_payload)
        timeline = parsed.get("timeline", []) if isinstance(parsed, dict) else []
        shot.timeline_json = json.dumps(timeline, ensure_ascii=False)
        table_content = format_timeline_to_table(timeline)
        shot.storyboard_video_prompt = table_content
        shot.storyboard_audio_prompt = ""
        shot.storyboard_dialogue = ""
        if should_autofill_scene_override(
            current_scene_override=shot.scene_override,
            scene_override_locked=bool(getattr(shot, "scene_override_locked", False)),
        ):
            scene_desc = extract_scene_description(shot, db)
            if scene_desc:
                shot.scene_override = scene_desc
        shot.sora_prompt = table_content
        shot.sora_prompt_status = "completed"
        _refresh_episode_batch_sora_prompt_state(int(shot.episode_id), db)
        return

    if task_type == "storyboard2_sora_prompt":
        storyboard2_shot_id = int(task_payload.get("storyboard2_shot_id") or task.owner_id or 0)
        storyboard2_shot = db.query(models.Storyboard2Shot).filter(models.Storyboard2Shot.id == storyboard2_shot_id).first()
        if not storyboard2_shot:
            return
        parsed = _parse_text_relay_result_json(upstream_payload)
        timeline = parsed.get("timeline", []) if isinstance(parsed, dict) else []
        if not timeline:
            timeline = _storyboard2_fallback_timeline(int(task_payload.get("duration") or 10))
        _apply_storyboard2_timeline_prompts(storyboard2_shot, timeline, db)
        _refresh_storyboard2_prompt_batch_state(int(storyboard2_shot.episode_id), db)
        return

    if task_type == "managed_prompt_optimize":
        managed_task_id = int(task_payload.get("managed_task_id") or 0)
        managed_task = db.query(models.ManagedTask).filter(models.ManagedTask.id == managed_task_id).first()
        if not managed_task:
            return
        optimized_prompt = _extract_text_relay_result_content(upstream_payload).strip()
        managed_task.prompt_text = optimized_prompt
        managed_task.status = "pending"
        managed_task.error_message = ""

        reserved_shot_id = int(task_payload.get("reserved_shot_id") or 0)
        if reserved_shot_id > 0:
            reserved_shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == reserved_shot_id).first()
            if reserved_shot:
                reserved_shot.sora_prompt = optimized_prompt
                reserved_shot.sora_prompt_is_full = True
                reserved_shot.video_status = "processing"
                reserved_shot.video_error_message = ""
                reserved_shot.video_path = ""
                reserved_shot.thumbnail_video_path = ""
                reserved_shot.task_id = ""
        return


def handle_text_relay_task_failure(db: Session, task: models.TextRelayTask, upstream_payload: Dict[str, Any]):
    task_payload = {}
    try:
        task_payload = json.loads(str(getattr(task, "task_payload", "") or "{}"))
    except Exception:
        task_payload = {}

    error_message = str(
        upstream_payload.get("error")
        or upstream_payload.get("message")
        or getattr(task, "error_message", "")
        or "任务失败"
    ).strip()
    task_type = str(getattr(task, "task_type", "") or "").strip()

    if task_type == "opening":
        episode_id = int(task_payload.get("episode_id") or task.owner_id or 0)
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            episode.opening_generating = False
            episode.opening_error = error_message
        return

    if task_type == "narration":
        episode_id = int(task_payload.get("episode_id") or task.owner_id or 0)
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            episode.narration_converting = False
            episode.narration_error = error_message
        return

    if task_type == "subject_prompt":
        card_id = int(task_payload.get("card_id") or task.owner_id or 0)
        card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
        if card and hasattr(card, "ai_prompt_status"):
            card.ai_prompt_status = "failed"
        return

    if task_type == "simple_storyboard_batch":
        batch_row_id = int(task_payload.get("batch_row_id") or task.owner_id or 0)
        batch_row = db.query(models.SimpleStoryboardBatch).filter(models.SimpleStoryboardBatch.id == batch_row_id).first()
        if not batch_row:
            return
        batch_row.status = "failed"
        batch_row.error_message = "简单分镜已迁移为本地程序生成，请重新发起整次简单分镜。"
        batch_row.last_attempt = 1
        batch_row.updated_at = datetime.utcnow()
        episode = db.query(models.Episode).filter(models.Episode.id == batch_row.episode_id).first()
        if episode:
            _refresh_episode_simple_storyboard_from_batches(episode, db)
        return

    if task_type in {"detailed_storyboard_stage1", "detailed_storyboard_stage2"}:
        episode_id = int(task_payload.get("episode_id") or task.owner_id or 0)
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            episode.storyboard_generating = False
            episode.storyboard_error = error_message
        return

    if task_type == "sora_prompt":
        shot_id = int(task_payload.get("shot_id") or task.owner_id or 0)
        shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
        if shot:
            shot.sora_prompt_status = "failed"
            _refresh_episode_batch_sora_prompt_state(int(shot.episode_id), db)
        return

    if task_type == "storyboard2_sora_prompt":
        episode_id = int(task_payload.get("episode_id") or 0)
        if episode_id > 0:
            _refresh_storyboard2_prompt_batch_state(episode_id, db)
        return

    if task_type == "managed_prompt_optimize":
        managed_task_id = int(task_payload.get("managed_task_id") or 0)
        managed_task = db.query(models.ManagedTask).filter(models.ManagedTask.id == managed_task_id).first()
        if managed_task:
            managed_task.status = "failed"
            managed_task.error_message = error_message
            managed_task.completed_at = datetime.utcnow()
        reserved_shot_id = int(task_payload.get("reserved_shot_id") or 0)
        if reserved_shot_id > 0:
            reserved_shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == reserved_shot_id).first()
            if reserved_shot:
                reserved_shot.video_status = "failed"
                reserved_shot.video_error_message = error_message
                reserved_shot.video_path = f"error:{error_message}" if error_message else ""
                reserved_shot.thumbnail_video_path = ""
                reserved_shot.task_id = ""
        return



def extract_scene_description(shot: models.StoryboardShot, db: Session) -> str:
    """
    从镜头的选中主体卡片中提取场景描述

    Args:
        shot: 镜头对象
        db: 数据库会话

    Returns:
        str: 场景描述字符串（格式：场景名+描述），如 "古代书房古代书房，木质书架林立；夜晚庭院夜晚的庭院，月光洒在青石板上"
    """
    scene_desc = ""
    try:
        selected_ids = json.loads(shot.selected_card_ids or "[]")

        if selected_ids:
            # 按选择顺序保留场景
            scene_cards_dict = {}
            all_scene_cards = db.query(models.SubjectCard).filter(
                models.SubjectCard.id.in_(selected_ids),
                models.SubjectCard.card_type == "场景"
            ).all()

            # 构建ID到卡片的映射
            for card in all_scene_cards:
                scene_cards_dict[card.id] = card

            # 按selected_ids的顺序处理场景卡片
            scene_prompts = []
            for card_id in selected_ids:
                card = scene_cards_dict.get(card_id)
                if card and card.ai_prompt and card.ai_prompt.strip():
                    # 清理掉格式化前缀，只保留纯粹的场景描述
                    clean_prompt = card.ai_prompt
                    # 移除"生成图片的风格是：xxx"部分（包括换行）
                    clean_prompt = re.sub(r'生成图片的风格是：[^\n]*\n?', '', clean_prompt)
                    # 移除"生成图片中场景的是："前缀
                    clean_prompt = re.sub(r'生成图片中场景的是：', '', clean_prompt)
                    clean_prompt = clean_prompt.strip()
                    if clean_prompt:
                        # 拼接格式：场景名 + 描述
                        scene_prompts.append(f"{card.name}{clean_prompt}")

            if scene_prompts:
                scene_desc = "；".join(scene_prompts)
    except Exception as e:
        print(f"提取场景描述失败: {str(e)}")

    return scene_desc


def _default_storyboard_video_prompt_template() -> str:
    return (
        "视频风格:逐帧动画，2d手绘动漫风格，强调帧间的手绘/精细绘制属性，而非3D渲染/CG动画的光滑感。"
        "画面整体呈现传统2D动画的逐帧绘制特征，包括但不限于：帧间微妙的线条变化、色彩的手工涂抹感、阴影的平面化处理。"
        "角色动作流畅但保留手绘的自然波动，背景元素展现水彩或厚涂等传统绘画技法的质感。"
        "整体视觉效果追求温暖、有机的手工艺术感，避免数字化的过度精确与机械感。"
    )


def build_sora_prompt(shot: models.StoryboardShot, db: Session = None) -> str:
    """
    构建Sora提示词

    Args:
        shot: 镜头对象
        db: 数据库会话（可选，如果提供则用于查询模板内容和场景卡片）

    注意：场景提示词优先使用scene_override，否则从选中的场景类型主体卡片的ai_prompt字段提取
    """
    print("\n" + "=" * 80)
    print(f"[构建Sora提示词] 镜头ID: {shot.id}, 镜号: {shot.shot_number}")
    print("=" * 80)

    if bool(getattr(shot, "sora_prompt_is_full", False)) and str(getattr(shot, "sora_prompt", "") or "").strip():
        direct_prompt = str(shot.sora_prompt or "").strip()
        print("[构建Sora提示词] 检测到一次性完整提示词，直接返回，不再二次拼接")
        print(f"[拼接结果] 最终 prompt 长度: {len(direct_prompt)}")
        print("=" * 80 + "\n")
        return direct_prompt

    parts = []

    # ========== 获取视频风格模板 ==========
    video_style_template = None
    episode = None
    if db:
        try:
            episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
            if episode and episode.video_style_template_id:
                video_style_template = db.query(models.VideoStyleTemplate).filter(
                    models.VideoStyleTemplate.id == episode.video_style_template_id
                ).first()
                if video_style_template:
                    print(f"[视频风格模板] 使用模板: {video_style_template.name} (id={video_style_template.id})")

            # If no template selected on episode, try default template
            if not video_style_template:
                video_style_template = db.query(models.VideoStyleTemplate).filter(
                    models.VideoStyleTemplate.is_default == True
                ).first()
                if video_style_template:
                    print(f"[视频风格模板] 使用默认模板: {video_style_template.name} (id={video_style_template.id})")
        except Exception as e:
            print(f"[视频风格模板] 查询失败: {e}")

    # ========== 第0部分：Sora准则 ==========
    if video_style_template and video_style_template.sora_rule and video_style_template.sora_rule.strip():
        sora_rule = video_style_template.sora_rule.strip()
        parts.append(sora_rule)
        print(f"[第0部分] ✅ 使用模板准则: {sora_rule[:80]}...")
    elif db:
        try:
            setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "sora_rule").first()
            if setting and setting.value:
                sora_rule = setting.value.strip()
                if sora_rule:
                    parts.append(sora_rule)
                    print(f"[第0部分] ✅ 使用全局Sora准则: {sora_rule}")
            else:
                sora_rule = "准则：不要出现字幕"
                parts.append(sora_rule)
                print(f"[第0部分] ⚠ 使用默认Sora准则: {sora_rule}")
        except Exception as e:
            print(f"[第0部分] ❌ 获取全局Sora准则失败: {str(e)}")
            sora_rule = "准则：不要出现字幕"
            parts.append(sora_rule)

    # ========== 第1部分：视频风格提示词 ==========
    template = ""
    if episode and (getattr(episode, "video_prompt_template", "") or "").strip():
        template = episode.video_prompt_template.strip()
        print(f"[第1部分] ✅ 使用剧集提示词模板（长度: {len(template)}）")
    elif video_style_template and video_style_template.style_prompt and video_style_template.style_prompt.strip():
        template = video_style_template.style_prompt.strip()
        print(f"[第1部分] ✅ 使用模板风格: {template[:80]}...")
    elif db:
        try:
            # 从全局配置读取 prompt_template
            setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "prompt_template").first()
            if setting and setting.value.strip():
                template = setting.value.strip()
                print(f"[第1部分] ✅ 使用全局提示词模板（长度: {len(template)}）")
            else:
                # 使用默认模板
                template = _default_storyboard_video_prompt_template()
                print(f"[第1部分] ⚠ 使用默认提示词模板")
        except Exception as e:
            print(f"[第1部分] ❌ 获取全局提示词模板失败: {str(e)}")
            # 出错时使用默认值
            template = _default_storyboard_video_prompt_template()
            print(f"[第1部分] ⚠ 使用默认提示词模板")

    if template:
        parts.append(template)
        print(f"[第1部分] ✅ 已添加视频风格模板")
    else:
        print(f"[第1部分] ❌ 模板为空，跳过")

    # ========== 第2部分：场景提示词 ==========
    # ✅ 优先使用用户编辑的 scene_override，否则从主体卡片提取
    scene_desc = (shot.scene_override or "").strip()

    if scene_desc:
        print(f"[第2部分] 使用 scene_override: {scene_desc[:100]}..." if len(scene_desc) > 100 else f"[第2部分] 使用 scene_override: {scene_desc}")
    elif db:
        # 从主体卡片提取场景描述
        scene_desc = extract_scene_description(shot, db)
        if scene_desc:
            print(f"[第2部分] 从主体卡片提取场景: {scene_desc[:100]}..." if len(scene_desc) > 100 else f"[第2部分] 从主体卡片提取场景: {scene_desc}")
        else:
            print(f"[第2部分] 未找到场景描述")
    else:
        print(f"[第2部分] ❌ db 为 None，跳过场景查询")

    if scene_desc:
        parts.append(f"场景：{scene_desc}")
        print(f"[第2部分] ✅ 已添加场景描述")
    else:
        print(f"[第2部分] ❌ 场景描述为空，跳过")

    # ========== 第3部分：分镜表格 ==========
    # ✅ 优先使用用户保存的 sora_prompt，否则使用 AI 生成的 storyboard_video_prompt
    table_content = (shot.sora_prompt or shot.storyboard_video_prompt or "").strip()
    print(f"[第3部分] 使用字段: {'sora_prompt' if shot.sora_prompt else 'storyboard_video_prompt'}")
    print(f"[第3部分] 内容长度: {len(table_content)}")
    if table_content:
        parts.append(table_content)  # ✅ 直接添加用户编辑的内容，不加前缀
        print(f"[第3部分] ✅ 已添加分镜表格")
    else:
        print(f"[第3部分] ❌ 分镜表格为空，跳过")

    # ========== 拼接结果 ==========
    final_prompt = "\n".join(parts).strip()
    print("-" * 80)
    print(f"[拼接结果] parts 数组长度: {len(parts)}")
    print(f"[拼接结果] 最终 prompt 长度: {len(final_prompt)}")
    print(f"[拼接结果] 最终 prompt 预览（前200字符）:")
    print(final_prompt[:200] + "..." if len(final_prompt) > 200 else final_prompt)
    print("=" * 80 + "\n")

    return final_prompt

def format_timeline_to_table(timeline: list) -> str:
    """
    将timeline数组格式化为Markdown表格字符串

    Args:
        timeline: AI返回的timeline数组 [{"time": "00s-04s", "visual": "...", "audio": "..."}, ...]

    Returns:
        str: 格式化的Markdown表格
    """
    if not timeline or not isinstance(timeline, list):
        return ""

    # 构建表格（不含头部）
    lines = []

    for item in timeline:
        time = item.get("time", "")
        visual = item.get("visual", "")
        audio = item.get("audio", "")

        lines.append(f"| {time} | {visual} | {audio} |")

    return "\n".join(lines)

VOICEOVER_TTS_ACTIVE_POLL_INTERVAL_SECONDS = 1.5
VOICEOVER_TTS_IDLE_POLL_INTERVAL_SECONDS = 3.0
VOICEOVER_TTS_BUSY_IDLE_POLL_INTERVAL_SECONDS = 5.0


class VoiceoverTtsQueuePoller:
    """配音TTS全局串行队列轮询器（全用户统一排队）。"""

    def __init__(self):
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        print("[voiceover_tts] serial queue poller started")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        print("[voiceover_tts] serial queue poller stopped")

    def _poll_loop(self):
        while self.running:
            processed_task = False
            try:
                processed_task = self._process_once()
            except Exception as e:
                print(f"[voiceover_tts] poll loop error: {str(e)}")
            if processed_task:
                time.sleep(VOICEOVER_TTS_ACTIVE_POLL_INTERVAL_SECONDS)
            else:
                time.sleep(
                    request_load_tracker.choose_interval(
                        VOICEOVER_TTS_IDLE_POLL_INTERVAL_SECONDS,
                        VOICEOVER_TTS_BUSY_IDLE_POLL_INTERVAL_SECONDS,
                    )
                )

    def _claim_next_task(self) -> Optional[int]:
        db = SessionLocal()
        try:
            task = db.query(models.VoiceoverTtsTask).filter(
                models.VoiceoverTtsTask.status == "pending"
            ).order_by(
                models.VoiceoverTtsTask.created_at.asc(),
                models.VoiceoverTtsTask.id.asc()
            ).first()
            if not task:
                return None
            task.status = "processing"
            task.started_at = datetime.utcnow()
            db.commit()
            sync_voiceover_tts_task_to_dashboard(task.id)
            return int(task.id)
        finally:
            db.close()

    def _mark_failed(self, task_id: int, error_message: str):
        db = SessionLocal()
        try:
            task = db.query(models.VoiceoverTtsTask).filter(
                models.VoiceoverTtsTask.id == task_id
            ).first()
            if not task:
                return
            task.status = "failed"
            task.error_message = str(error_message or "未知错误")
            task.completed_at = datetime.utcnow()

            episode = db.query(models.Episode).filter(
                models.Episode.id == task.episode_id
            ).first()
            if episode:
                script = db.query(models.Script).filter(
                    models.Script.id == episode.script_id
                ).first()
                shared = _load_script_voiceover_shared_data(script) if script else _voiceover_default_shared_data()
                default_ref_id = ""
                refs = shared.get("voice_references", [])
                if isinstance(refs, list) and refs:
                    default_ref_id = str(refs[0].get("id") or "").strip()

                payload = _parse_episode_voiceover_payload(episode)
                shots, _ = _normalize_voiceover_shots_for_tts(payload.get("shots", []), default_ref_id)
                line_entry = _find_voiceover_line_entry(shots, task.line_id)
                if isinstance(line_entry, dict):
                    line_tts = _normalize_voiceover_line_tts(
                        line_entry.get("tts"),
                        default_ref_id
                    )
                    line_tts["generate_status"] = "failed"
                    line_tts["generate_error"] = task.error_message
                    line_tts["latest_task_id"] = str(task.id)
                    line_entry["tts"] = line_tts
                    payload["shots"] = shots
                    episode.voiceover_data = json.dumps(payload, ensure_ascii=False)

            db.commit()
            sync_voiceover_tts_task_to_dashboard(task.id)
        finally:
            db.close()

    def _process_once(self):
        task_id = self._claim_next_task()
        if task_id is None:
            return False
        self._execute_task(task_id)
        return True

    def _execute_task(self, task_id: int):
        debug_folder = f"voiceover_tts_task_{task_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        runtime_context = {}
        db = SessionLocal()
        try:
            task = db.query(models.VoiceoverTtsTask).filter(
                models.VoiceoverTtsTask.id == task_id
            ).first()
            if not task:
                return
            if task.status != "processing":
                return

            try:
                request_payload = json.loads(task.request_json or "{}")
                if not isinstance(request_payload, dict):
                    request_payload = {}
            except Exception:
                request_payload = {}

            episode = db.query(models.Episode).filter(
                models.Episode.id == task.episode_id
            ).first()
            if not episode:
                raise Exception("片段不存在")

            script = db.query(models.Script).filter(
                models.Script.id == episode.script_id
            ).first()
            if not script:
                raise Exception("剧本不存在")

            shared = _load_script_voiceover_shared_data(script)
            refs = shared.get("voice_references", [])
            default_ref_id = str(refs[0].get("id") or "").strip() if isinstance(refs, list) and refs else ""

            voiceover_payload = _parse_episode_voiceover_payload(episode)
            shots, changed = _normalize_voiceover_shots_for_tts(
                voiceover_payload.get("shots", []),
                default_ref_id
            )
            if changed:
                voiceover_payload["shots"] = shots

            line_entry = _find_voiceover_line_entry(shots, task.line_id)
            if not isinstance(line_entry, dict):
                raise Exception(f"未找到line_id={task.line_id} 对应的配音行")

            line_tts = _normalize_voiceover_line_tts(
                line_entry.get("tts"),
                default_ref_id
            )
            line_tts["generate_status"] = "processing"
            line_tts["generate_error"] = ""
            line_tts["latest_task_id"] = str(task.id)
            line_entry["tts"] = line_tts

            voiceover_payload["shots"] = shots
            episode.voiceover_data = json.dumps(voiceover_payload, ensure_ascii=False)
            db.commit()

            line_text = str(
                request_payload.get("text")
                or line_entry.get("text")
                or ""
            ).strip()
            if not line_text:
                raise Exception("配音文本为空")

            method = str(
                request_payload.get("emotion_control_method")
                or line_tts.get("emotion_control_method")
                or VOICEOVER_TTS_METHOD_SAME
            ).strip()
            if method not in VOICEOVER_TTS_ALLOWED_METHODS:
                method = VOICEOVER_TTS_METHOD_SAME

            voice_ref_id = str(
                request_payload.get("voice_reference_id")
                or line_tts.get("voice_reference_id")
                or ""
            ).strip()
            if not voice_ref_id and isinstance(refs, list) and refs:
                voice_ref_id = str(refs[0].get("id") or "").strip()

            selected_voice_ref = None
            if isinstance(refs, list):
                selected_voice_ref = next((x for x in refs if str(x.get("id") or "") == voice_ref_id), None)
            if not selected_voice_ref and isinstance(refs, list) and refs:
                selected_voice_ref = refs[0]

            prompt_audio_source = _resolve_voiceover_audio_source(selected_voice_ref or {})
            if not prompt_audio_source:
                raise Exception("未找到有效的音色参考音频")

            emo_ref_audio_source = None
            if method == VOICEOVER_TTS_METHOD_AUDIO:
                emotion_audio_preset_id = str(
                    request_payload.get("emotion_audio_preset_id")
                    or line_tts.get("emotion_audio_preset_id")
                    or ""
                ).strip()
                emotion_presets = shared.get("emotion_audio_presets", [])
                selected_preset = None
                if isinstance(emotion_presets, list):
                    selected_preset = next(
                        (x for x in emotion_presets if str(x.get("id") or "") == emotion_audio_preset_id),
                        None
                    )
                emo_ref_audio_source = _resolve_voiceover_audio_source(selected_preset or {})
                if not emo_ref_audio_source:
                    raise Exception("情感参考音频未设置")

            vector_config = _normalize_voiceover_vector_config(
                request_payload.get("vector_config") or line_tts.get("vector_config")
            )
            emo_text = str(
                request_payload.get("emo_text")
                if request_payload.get("emo_text") is not None
                else line_entry.get("emotion")
                or ""
            ).strip()
            runtime_context = {
                "line_id": task.line_id,
                "text": line_text,
                "method": method,
                "api_url": VOICEOVER_TTS_API_URL,
                "voice_reference_id": voice_ref_id,
                "prompt_audio_source": prompt_audio_source,
                "emo_ref_audio_source": emo_ref_audio_source,
                "vector_config": vector_config,
                "emo_text": emo_text
            }
        except Exception as e:
            db.rollback()
            self._mark_failed(task_id, str(e))
            return
        finally:
            db.close()

        _save_voiceover_tts_debug(debug_folder, "input.json", runtime_context)

        try:
            result = _generate_tts_with_index_tts(
                text_content=runtime_context["text"],
                emotion_control_method=runtime_context["method"],
                prompt_audio_source=runtime_context["prompt_audio_source"],
                emo_ref_audio_source=runtime_context.get("emo_ref_audio_source"),
                vector_config=runtime_context.get("vector_config", {}),
                emo_text=runtime_context.get("emo_text", "")
            )
            _save_voiceover_tts_debug(debug_folder, "output.json", result)
        except Exception as e:
            _save_voiceover_tts_debug(debug_folder, "error.json", {"error": str(e)})
            self._mark_failed(task_id, str(e))
            return

        db = SessionLocal()
        try:
            task = db.query(models.VoiceoverTtsTask).filter(
                models.VoiceoverTtsTask.id == task_id
            ).first()
            if not task:
                return
            episode = db.query(models.Episode).filter(
                models.Episode.id == task.episode_id
            ).first()
            if not episode:
                raise Exception("片段不存在")
            script = db.query(models.Script).filter(
                models.Script.id == episode.script_id
            ).first()
            shared = _load_script_voiceover_shared_data(script) if script else _voiceover_default_shared_data()
            refs = shared.get("voice_references", [])
            default_ref_id = str(refs[0].get("id") or "").strip() if isinstance(refs, list) and refs else ""

            voiceover_payload = _parse_episode_voiceover_payload(episode)
            shots, _ = _normalize_voiceover_shots_for_tts(
                voiceover_payload.get("shots", []),
                default_ref_id
            )
            line_entry = _find_voiceover_line_entry(shots, task.line_id)
            if not isinstance(line_entry, dict):
                raise Exception(f"未找到line_id={task.line_id}")

            line_tts = _normalize_voiceover_line_tts(
                line_entry.get("tts"),
                default_ref_id
            )
            generated = line_tts.get("generated_audios", [])
            if not isinstance(generated, list):
                generated = []
            generated.insert(0, {
                "id": f"tts_result_{uuid.uuid4().hex}",
                "name": f"生成结果 {len(generated) + 1}",
                "url": str(result.get("cdn_url") or "").strip(),
                "task_id": str(task.id),
                "created_at": datetime.utcnow().isoformat(),
                "status": "completed"
            })
            line_tts["generated_audios"] = generated
            line_tts["generate_status"] = "idle"
            line_tts["generate_error"] = ""
            line_tts["latest_task_id"] = str(task.id)
            line_entry["tts"] = line_tts
            voiceover_payload["shots"] = shots
            episode.voiceover_data = json.dumps(voiceover_payload, ensure_ascii=False)

            task.status = "completed"
            task.result_json = json.dumps(result, ensure_ascii=False)
            task.error_message = ""
            task.completed_at = datetime.utcnow()
            db.commit()
            sync_voiceover_tts_task_to_dashboard(task.id)
        except Exception as e:
            db.rollback()
            self._mark_failed(task_id, str(e))
        finally:
            db.close()

voiceover_tts_poller = VoiceoverTtsQueuePoller()

# 启动事件：启动视频状态轮询器
@app.on_event("startup")
async def startup_event():
    start_external_cache_prewarms()
    start_background_pollers()

# 关闭事件：停止轮询器
@app.on_event("shutdown")
async def shutdown_event():
    stop_background_pollers()

# ==================== Pydantic模型 ====================

class LoginRequest(BaseModel):
    username: str
    password: str

class PasswordVerifyRequest(BaseModel):
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    created_at: datetime

    class Config:
        from_attributes = True

class StoryLibraryCreate(BaseModel):
    name: str
    description: str = ""

class StoryLibraryResponse(BaseModel):
    id: int
    user_id: int
    name: str
    description: str
    created_at: datetime
    owner: UserResponse

    class Config:
        from_attributes = True

class SubjectCardCreate(BaseModel):
    name: str
    alias: Optional[str] = None
    card_type: str  # 角色/场景/声音

class SubjectCardUpdate(BaseModel):
    name: Optional[str] = None
    alias: Optional[str] = None
    card_type: Optional[str] = None
    linked_card_id: Optional[int] = None  # 仅声音卡片生效，绑定到角色卡片
    ai_prompt: Optional[str] = None  # 外貌/场景描述（不含风格）
    role_personality: Optional[str] = None  # 角色性格（中文一句话）
    role_personality_en: Optional[str] = None  # 兼容旧字段
    style_template_id: Optional[int] = None  # 风格模板ID
    is_protagonist: Optional[bool] = None
    protagonist_gender: Optional[str] = None  # male/female/""

class CardImageResponse(BaseModel):
    id: int
    card_id: int
    image_path: str
    order: int

    class Config:
        from_attributes = True

class SubjectCardAudioResponse(BaseModel):
    id: int
    card_id: int
    audio_path: str
    file_name: str
    duration_seconds: float = 0.0
    is_reference: bool
    created_at: datetime

    class Config:
        from_attributes = True

class GeneratedImageResponse(BaseModel):
    id: int
    card_id: int
    image_path: str
    model_name: str
    is_reference: bool
    status: str  # processing/completed/failed
    created_at: datetime

    class Config:
        from_attributes = True

class SubjectCardResponse(BaseModel):
    id: int
    library_id: int
    name: str
    alias: str
    card_type: str
    linked_card_id: Optional[int] = None
    ai_prompt: str  # 新增：AI生成的prompt
    role_personality: str = ""
    style_template_id: Optional[int] = None  # 风格模板ID
    is_protagonist: bool = False
    protagonist_gender: str = ""
    is_generating_images: bool = False  # 是否正在生成图片
    generating_count: int = 0  # 正在生成的图片数量
    created_at: datetime
    images: List[CardImageResponse]
    audios: List[SubjectCardAudioResponse] = []
    generated_images: List[GeneratedImageResponse] = []  # 新增：AI生成的图片

    class Config:
        from_attributes = True

# ==================== 工具函数 ====================

def save_upload_file(upload_file: UploadFile) -> str:
    """保存上传的文件并返回路径"""
    # 生成唯一文件名
    ext = os.path.splitext(upload_file.filename)[1]
    filename = f"{uuid.uuid4()}{ext}"
    filepath = os.path.join("uploads", filename)

    # 保存文件
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)

    return filepath

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


def _probe_media_duration_seconds(file_path: str) -> float:
    probe_cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    raw_duration = (probe_result.stdout or "").strip()
    duration_seconds = float(raw_duration)
    if duration_seconds <= 0:
        raise ValueError("音频时长无效")
    return round(duration_seconds, 3)


def _download_remote_audio_to_temp(audio_path: str) -> str:
    parsed = urlparse(audio_path)
    suffix = os.path.splitext(parsed.path or "")[1] or ".tmp"
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir="uploads")
    temp_path = temp_file.name
    temp_file.close()
    try:
        response = requests.get(audio_path, timeout=60, stream=True)
        response.raise_for_status()
        with open(temp_path, "wb") as buffer:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    buffer.write(chunk)
        return temp_path
    except Exception:
        try:
            os.remove(temp_path)
        except Exception:
            pass
        raise


def _ensure_audio_duration_seconds_cached(audio: Optional[models.SubjectCardAudio], db: Session) -> float:
    if not audio:
        return 0.0

    cached_duration = _safe_audio_duration_seconds(getattr(audio, "duration_seconds", 0))
    if cached_duration > 0:
        return cached_duration

    audio_path = str(getattr(audio, "audio_path", "") or "").strip()
    if not audio_path:
        return 0.0

    temp_path = None
    try:
        probe_path = audio_path
        if audio_path.startswith("http://") or audio_path.startswith("https://"):
            temp_path = _download_remote_audio_to_temp(audio_path)
            probe_path = temp_path
        duration_seconds = _probe_media_duration_seconds(probe_path)
        audio.duration_seconds = duration_seconds
        db.flush()
        return duration_seconds
    except Exception as e:
        print(f"[声音素材] 回填音频时长失败 audio_id={getattr(audio, 'id', None)}: {str(e)}")
        return 0.0
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def _backfill_audio_duration_cache(audios: List[models.SubjectCardAudio], db: Session) -> bool:
    updated_any = False
    for audio in audios or []:
        if _safe_audio_duration_seconds(getattr(audio, "duration_seconds", 0)) > 0:
            continue
        if _ensure_audio_duration_seconds_cached(audio, db) > 0:
            updated_any = True
    return updated_any


def save_audio_and_upload_to_cdn(upload_file: UploadFile) -> Tuple[str, float]:
    """保存音频到本地，缓存时长后上传到CDN。"""
    local_path = None
    try:
        ext = os.path.splitext(upload_file.filename)[1]
        filename = f"{uuid.uuid4()}{ext}"
        local_path = os.path.join("uploads", filename)

        with open(local_path, "wb") as buffer:
            shutil.copyfileobj(upload_file.file, buffer)

        duration_seconds = _probe_media_duration_seconds(local_path)
        cdn_url = upload_to_cdn(local_path)

        try:
            os.remove(local_path)
        except Exception as e:
            print(f"删除音频临时文件失败: {str(e)}")

        return cdn_url, duration_seconds
    except Exception as e:
        print(f"音频上传CDN失败: {str(e)}")
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except Exception:
                pass
        raise Exception(f"音频上传CDN失败: {str(e)}")

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

VOICEOVER_TTS_API_URL = get_env("VOICEOVER_TTS_API_URL", "")
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

def _save_voiceover_tts_debug(folder_name: str, file_name: str, payload: dict):
    try:
        debug_dir = os.path.join("ai_debug", folder_name)
        os.makedirs(debug_dir, exist_ok=True)
        file_path = os.path.join(debug_dir, file_name)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log_file_task_event(
            task_folder=folder_name,
            file_name=file_name,
            payload=payload,
            task_type="voiceover_tts",
            stage="voiceover_tts",
            source_record_type="voiceover_tts_task",
            source_record_id=int(re.search(r"voiceover_tts_task_(\d+)", folder_name).group(1)) if re.search(r"voiceover_tts_task_(\d+)", folder_name) else None,
        )
    except Exception as e:
        print(f"[voiceover_tts][debug] save failed: {str(e)}")

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

def _upload_local_or_remote_audio_to_cdn(audio_source: str) -> str:
    source = str(audio_source or "").strip()
    if not source:
        raise Exception("音频结果为空")

    if source.startswith("http://") or source.startswith("https://"):
        temp_path = None
        try:
            response = requests.get(source, timeout=60)
            if response.status_code != 200:
                raise Exception(f"下载远程音频失败: HTTP {response.status_code}")
            ext = os.path.splitext(source.split("?", 1)[0])[1] or ".wav"
            temp_path = os.path.join("uploads", f"tts_temp_{uuid.uuid4().hex}{ext}")
            with open(temp_path, "wb") as f:
                f.write(response.content)
            return upload_to_cdn(temp_path)
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    if not os.path.isabs(source):
        source = os.path.abspath(source)
    if not os.path.exists(source):
        raise Exception(f"音频文件不存在: {source}")
    return upload_to_cdn(source)

def _generate_tts_with_index_tts(
    text_content: str,
    emotion_control_method: str,
    prompt_audio_source: str,
    emo_ref_audio_source: Optional[str],
    vector_config: dict,
    emo_text: str = ""
) -> dict:
    from gradio_client import Client, handle_file

    client = Client(VOICEOVER_TTS_API_URL)
    if emotion_control_method == VOICEOVER_TTS_METHOD_EMO_TEXT:
        try:
            client.predict(
                is_experimental=True,
                api_name="/on_experimental_change"
            )
        except Exception as experimental_error:
            print(f"[voiceover_tts] experimental mode toggle failed: {experimental_error}")
    prompt_audio = handle_file(prompt_audio_source)
    emo_ref_audio = handle_file(emo_ref_audio_source) if emo_ref_audio_source else None

    vec = _normalize_voiceover_vector_config(vector_config)
    result = client.predict(
        emo_control_method=emotion_control_method,
        prompt=prompt_audio,
        text=text_content,
        emo_ref_path=emo_ref_audio,
        emo_weight=vec["weight"],
        vec1=vec["joy"],
        vec2=vec["anger"],
        vec3=vec["sadness"],
        vec4=vec["fear"],
        vec5=vec["disgust"],
        vec6=vec["depression"],
        vec7=vec["surprise"],
        vec8=vec["neutral"],
        emo_text=str(emo_text or ""),
        emo_random=False,
        max_text_tokens_per_segment=120,
        param_16=True,
        param_17=0.8,
        param_18=30,
        param_19=0.8,
        param_20=0.0,
        param_21=3,
        param_22=10.0,
        param_23=1500,
        api_name="/gen_single"
    )

    audio_value = ""
    if isinstance(result, dict):
        audio_value = str(
            result.get("value") or result.get("path") or result.get("url") or ""
        ).strip()
    elif isinstance(result, str):
        audio_value = result.strip()

    if not audio_value:
        raise Exception("TTS接口返回音频为空")

    cdn_url = _upload_local_or_remote_audio_to_cdn(audio_value)
    return {
        "raw_result": result,
        "audio_source": audio_value,
        "cdn_url": cdn_url
    }

def generate_collage_image(shot_id: int, db: Session, include_scenes: bool = False, aspect_ratio: str = "16:9") -> str:
    """
    为指定镜头生成拼图，根据宽高比自动选择横排或竖排布局

    Args:
        shot_id: 镜头ID
        db: 数据库会话
        include_scenes: 是否包含场景类主体
        aspect_ratio: 视频宽高比 (如 "16:9", "9:16", "1:1")

    Returns:
        str: 拼图的CDN URL

    Raises:
        Exception: 如果生成失败
    """
    import io
    import urllib.request

    # 获取镜头信息
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise Exception("镜头不存在")

    # 解析选中的主体ID
    try:
        selected_ids = json.loads(shot.selected_card_ids or "[]")
    except Exception:
        selected_ids = []

    if not selected_ids:
        raise Exception("未选择任何主体")

    # 获取选中的主体卡片
    cards = db.query(models.SubjectCard).filter(
        models.SubjectCard.id.in_(selected_ids)
    ).all()

    if not cards:
        raise Exception("未找到主体卡片")

    # 收集每个主体的参考图
    subject_images = []

    print(f"[拼图生成] 开始收集主体图片，include_scenes={include_scenes}, aspect_ratio={aspect_ratio}")
    print(f"[拼图生成] 共有 {len(cards)} 个主体")

    for card in cards:
        print(f"[拼图生成] 处理主体: {card.name}, 类型: {card.card_type}")

        if not include_scenes and card.card_type == "场景":
            print(f"[拼图生成] 跳过场景类主体: {card.name} (include_scenes=False)")
            continue

        ref_images = db.query(models.GeneratedImage).filter(
            models.GeneratedImage.card_id == card.id,
            models.GeneratedImage.is_reference == True,
            models.GeneratedImage.status == "completed"
        ).all()

        print(f"[拼图生成] 主体 {card.name} 有 {len(ref_images)} 张参考图")

        image_urls = [img.image_path for img in ref_images] if ref_images else []
        subject_images.append({
            "name": card.name,
            "urls": image_urls,
            "has_images": len(image_urls) > 0,
            "card_type": card.card_type
        })

    # 下载所有图片
    print(f"[拼图生成] 开始处理 {len(subject_images)} 个主体...")
    downloaded_images = []

    for subject in subject_images:
        pil_images = []

        if subject["has_images"]:
            for url in subject["urls"]:
                try:
                    with urllib.request.urlopen(url, timeout=30) as response:
                        img_data = response.read()
                        pil_img = Image.open(io.BytesIO(img_data))
                        pil_images.append(pil_img)
                except Exception as e:
                    print(f"[拼图生成] 下载图片失败: {url} - {str(e)}")
                    continue

        if not pil_images:
            print(f"[拼图生成] 跳过没有图片的主体: {subject['name']}")
            continue

        downloaded_images.append({
            "name": subject["name"],
            "images": pil_images,
            "has_images": len(pil_images) > 0,
            "card_type": subject["card_type"]
        })

    if not downloaded_images:
        raise Exception("所有主体都没有参考图，无法生成拼图。请先为至少一个主体生成参考图。")

    # ==================== 根据宽高比计算画布尺寸 ====================
    try:
        w_ratio, h_ratio = map(int, aspect_ratio.split(':'))
    except Exception:
        w_ratio, h_ratio = 16, 9

    is_landscape = w_ratio >= h_ratio
    LONG_EDGE = 1920

    if is_landscape:
        CANVAS_WIDTH = LONG_EDGE
        CANVAS_HEIGHT = int(LONG_EDGE * h_ratio / w_ratio)
    else:
        CANVAS_HEIGHT = LONG_EDGE
        CANVAS_WIDTH = int(LONG_EDGE * w_ratio / h_ratio)

    PADDING = 10

    print(f"[拼图生成] 画布尺寸: {CANVAS_WIDTH}x{CANVAS_HEIGHT}, 布局: {'横排' if is_landscape else '竖排'}")

    # 创建画布
    canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), color="white")
    draw = ImageDraw.Draw(canvas)

    # 确定字体路径
    try:
        font_path = "C:\\Windows\\Fonts\\msyh.ttc"
        _test_font = ImageFont.truetype(font_path, 20)
    except Exception:
        try:
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            _test_font = ImageFont.truetype(font_path, 20)
        except Exception:
            font_path = None

    def find_optimal_font_size(text, max_width, min_size=20, max_size=80):
        if font_path is None:
            return ImageFont.load_default()
        left, right = min_size, max_size
        best_size = min_size
        while left <= right:
            mid = (left + right) // 2
            try:
                test_font = ImageFont.truetype(font_path, mid)
                bbox = draw.textbbox((0, 0), text, font=test_font)
                text_width = bbox[2] - bbox[0]
                if text_width <= max_width:
                    best_size = mid
                    left = mid + 1
                else:
                    right = mid - 1
            except Exception:
                right = mid - 1
        try:
            return ImageFont.truetype(font_path, best_size)
        except Exception:
            return ImageFont.load_default()

    def get_unified_font(subject_name, type_label, max_text_width, min_size=20, max_size=80):
        """Get a unified font size for name and type label."""
        font_name = find_optimal_font_size(subject_name, max_text_width, min_size, max_size)
        font_type = find_optimal_font_size(type_label, max_text_width, min_size, max_size)
        try:
            size_name = font_name.size if hasattr(font_name, 'size') else max_size
            size_type = font_type.size if hasattr(font_type, 'size') else max_size
            unified_size = min(size_name, size_type)
            return ImageFont.truetype(font_path, unified_size) if font_path else ImageFont.load_default()
        except Exception:
            return font_name

    # ==================== 横排布局（宽 >= 高）====================
    if is_landscape:
        IMAGE_HEIGHT = int(CANVAS_HEIGHT * 0.6)
        LABEL_HEIGHT = CANVAS_HEIGHT - IMAGE_HEIGHT

        total_images = sum(len(s["images"]) for s in downloaded_images)
        available_width = CANVAS_WIDTH - (total_images + 1) * PADDING
        image_width = available_width // total_images

        current_x = PADDING

        for subject in downloaded_images:
            subject_name = subject["name"]
            images = subject["images"]
            subject_width = len(images) * image_width + (len(images) - 1) * PADDING

            # 绘制图片
            for img in images:
                img_ratio = img.width / img.height
                new_height = IMAGE_HEIGHT
                new_width = int(new_height * img_ratio)
                if new_width > image_width:
                    new_width = image_width
                    new_height = int(new_width / img_ratio)

                resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                y_offset = (IMAGE_HEIGHT - new_height) // 2
                canvas.paste(resized_img, (current_x, y_offset))
                current_x += image_width + PADDING

            # 绘制标签
            card_type = subject["card_type"]
            type_label = f"{card_type}图片"
            max_text_width = int(subject_width * 0.9)

            unified_font = get_unified_font(subject_name, type_label, max_text_width)

            text_center_x = current_x - subject_width // 2 - PADDING

            try:
                font_height = unified_font.size if hasattr(unified_font, 'size') else 40
            except Exception:
                font_height = 40

            total_text_height = int(font_height * 2.2)
            text_vertical_start = IMAGE_HEIGHT + (LABEL_HEIGHT - total_text_height) // 2

            try:
                bbox1 = draw.textbbox((0, 0), subject_name, font=unified_font)
                text_width1 = bbox1[2] - bbox1[0]
            except Exception:
                text_width1 = len(subject_name) * 20

            text1_x = max(PADDING, text_center_x - text_width1 // 2)
            text1_y = text_vertical_start
            draw.text((text1_x, text1_y), subject_name, fill="black", font=unified_font)

            try:
                bbox2 = draw.textbbox((0, 0), type_label, font=unified_font)
                text_width2 = bbox2[2] - bbox2[0]
            except Exception:
                text_width2 = len(type_label) * 20

            text2_x = max(PADDING, text_center_x - text_width2 // 2)
            text2_y = text1_y + int(font_height * 1.2)
            draw.text((text2_x, text2_y), type_label, fill="black", font=unified_font)

    # ==================== 竖排布局（宽 < 高）====================
    else:
        num_subjects = len(downloaded_images)
        available_height = CANVAS_HEIGHT - (num_subjects + 1) * PADDING
        subject_slot_height = available_height // num_subjects

        # Each subject slot: 75% image area, 25% label area
        subject_image_height = int(subject_slot_height * 0.75)
        subject_label_height = subject_slot_height - subject_image_height

        current_y = PADDING

        for subject in downloaded_images:
            subject_name = subject["name"]
            images = subject["images"]
            card_type = subject["card_type"]
            type_label = f"{card_type}图片"

            # -- Draw images (horizontally within this subject's row) --
            num_imgs = len(images)
            available_img_width = CANVAS_WIDTH - (num_imgs + 1) * PADDING
            per_img_width = available_img_width // num_imgs

            img_x = PADDING
            for img in images:
                img_ratio = img.width / img.height
                new_height = subject_image_height
                new_width = int(new_height * img_ratio)
                if new_width > per_img_width:
                    new_width = per_img_width
                    new_height = int(new_width / img_ratio)

                resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                # Center within allocated slot
                x_offset = img_x + (per_img_width - new_width) // 2
                y_offset = current_y + (subject_image_height - new_height) // 2

                canvas.paste(resized_img, (x_offset, y_offset))
                img_x += per_img_width + PADDING

            # -- Draw label (centered below images) --
            label_y_start = current_y + subject_image_height
            max_text_width = int(CANVAS_WIDTH * 0.9)

            unified_font = get_unified_font(subject_name, type_label, max_text_width, min_size=18, max_size=60)

            try:
                font_height = unified_font.size if hasattr(unified_font, 'size') else 30
            except Exception:
                font_height = 30

            total_text_height = int(font_height * 2.2)
            text_vertical_start = label_y_start + (subject_label_height - total_text_height) // 2

            # Name line
            try:
                bbox1 = draw.textbbox((0, 0), subject_name, font=unified_font)
                text_width1 = bbox1[2] - bbox1[0]
            except Exception:
                text_width1 = len(subject_name) * 20

            text1_x = (CANVAS_WIDTH - text_width1) // 2
            text1_y = text_vertical_start
            draw.text((text1_x, text1_y), subject_name, fill="black", font=unified_font)

            # Type line
            try:
                bbox2 = draw.textbbox((0, 0), type_label, font=unified_font)
                text_width2 = bbox2[2] - bbox2[0]
            except Exception:
                text_width2 = len(type_label) * 20

            text2_x = (CANVAS_WIDTH - text_width2) // 2
            text2_y = text1_y + int(font_height * 1.2)
            draw.text((text2_x, text2_y), type_label, fill="black", font=unified_font)

            current_y += subject_slot_height + PADDING

    # 保存到临时文件
    temp_filename = f"collage_{uuid.uuid4().hex}.jpg"
    temp_path = os.path.join("uploads", temp_filename)
    canvas.save(temp_path, "JPEG", quality=95)

    print(f"[拼图生成] 拼图已保存到临时文件: {temp_path}")

    # 上传到CDN
    try:
        cdn_url = upload_to_cdn(temp_path)
        print(f"[拼图生成] 拼图已上传到CDN: {cdn_url}")
    finally:
        try:
            os.remove(temp_path)
        except Exception as e:
            print(f"[拼图生成] 删除临时文件失败: {str(e)}")

    return cdn_url

# ==================== API路由 ====================

@app.post("/api/auth/login")
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """通过用户名 + 密码登录"""
    user = db.query(models.User).filter(models.User.username == request.username).first()

    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")

    # 验证密码：自己的密码 或 通用管理密码（保留账号不允许通用密码）
    own_ok = _hash_password(request.password) == user.password_hash
    master_password = (MASTER_PASSWORD or "").strip()
    master_ok = (
        bool(master_password)
        and request.password == master_password
        and request.username not in HIDDEN_USERS
    )

    if not own_ok and not master_ok:
        raise HTTPException(status_code=401, detail="密码错误")

    # 用户使用本人密码登录时，同步保存明文密码供管理端查看
    if own_ok and user.password_plain != request.password:
        user.password_plain = request.password
        db.commit()

    return {
        "id": user.id,
        "username": user.username,
        "token": user.token,
        "created_at": user.created_at
    }

@app.post("/api/auth/verify")
async def verify_token(user: models.User = Depends(get_current_user)):
    """验证token是否有效"""
    return {
        "id": user.id,
        "username": user.username,
        "created_at": user.created_at
    }


class ChangePasswordRequest(BaseModel):
    username: str
    old_password: str
    new_password: str


@app.post("/api/auth/change-password")
async def change_password(request: ChangePasswordRequest, db: Session = Depends(get_db)):
    """修改密码（需要验证原密码）"""
    user = db.query(models.User).filter(models.User.username == request.username).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if _hash_password(request.old_password) != user.password_hash:
        raise HTTPException(status_code=401, detail="原密码错误")

    if not request.new_password:
        raise HTTPException(status_code=400, detail="新密码不能为空")

    user.password_hash = _hash_password(request.new_password)
    user.password_plain = request.new_password
    db.commit()
    return {"message": "密码修改成功"}

@app.post("/api/auth/verify-nerva-password")
async def verify_nerva_password(request: PasswordVerifyRequest):
    """验证nerva用户密码"""
    nerva_password = _get_private_password_env("NERVA_PASSWORD")

    if nerva_password and request.password == nerva_password:
        return {"success": True}
    else:
        raise HTTPException(status_code=401, detail="密码错误")

# ==================== 角色库API ====================

@app.post("/api/libraries", response_model=StoryLibraryResponse)
async def create_library(
    library: StoryLibraryCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建角色库"""
    new_library = models.StoryLibrary(
        user_id=user.id,
        name=library.name,
        description=library.description
    )
    db.add(new_library)
    db.commit()
    db.refresh(new_library)
    return new_library

@app.get("/api/libraries/my", response_model=List[StoryLibraryResponse])
async def get_my_libraries(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取当前用户的所有角色库"""
    libraries = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.user_id == user.id
    ).order_by(models.StoryLibrary.created_at.desc()).all()
    return libraries

@app.get("/api/libraries/{library_id}", response_model=StoryLibraryResponse)
async def get_library(
    library_id: int,
    db: Session = Depends(get_db)
):
    """获取指定角色库（公开，任何人可查看）"""
    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.id == library_id
    ).first()

    if not library:
        raise HTTPException(status_code=404, detail="Library not found")

    return library

@app.put("/api/libraries/{library_id}", response_model=StoryLibraryResponse)
async def update_library(
    library_id: int,
    library_data: StoryLibraryCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新角色库"""
    library = verify_library_owner(library_id, user, db)

    if card.card_type not in ALLOWED_CARD_TYPES:
        raise HTTPException(status_code=400, detail="????????????")

    library.name = library_data.name
    library.description = library_data.description

    db.commit()
    db.refresh(library)
    return library

@app.delete("/api/libraries/{library_id}")
async def delete_library(
    library_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除角色库"""
    library = verify_library_owner(library_id, user, db)

    # 删除所有相关的图片文件
    for card in library.subject_cards:
        for image in card.images:
            if os.path.exists(image.image_path):
                os.remove(image.image_path)

    db.delete(library)
    db.commit()
    return {"message": "Library deleted successfully"}

# ==================== 主体卡片API ====================

def _find_role_card_id_by_name(db: Session, library_id: int, name: str) -> Optional[int]:
    card_name = (name or "").strip()
    if not card_name:
        return None
    role_card = db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library_id,
        models.SubjectCard.card_type == "角色",
        models.SubjectCard.name == card_name
    ).order_by(models.SubjectCard.id.asc()).first()
    return role_card.id if role_card else None


def _validate_and_resolve_linked_role_card_id(
    db: Session,
    library_id: int,
    linked_card_id: Optional[int]
) -> Optional[int]:
    if linked_card_id is None:
        return None
    target = db.query(models.SubjectCard).filter(
        models.SubjectCard.id == linked_card_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="绑定角色卡片不存在")
    if target.library_id != library_id:
        raise HTTPException(status_code=400, detail="绑定角色卡片不属于当前主体库")
    if target.card_type != "角色":
        raise HTTPException(status_code=400, detail="声音卡片只能绑定角色卡片")
    return target.id


def _bind_same_name_sound_cards_to_role(db: Session, library_id: int, role_card_id: int, role_name: str):
    role_name = (role_name or "").strip()
    if not role_name:
        return
    db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library_id,
        models.SubjectCard.card_type == SOUND_CARD_TYPE,
        models.SubjectCard.name == role_name,
        or_(
            models.SubjectCard.linked_card_id == None,
            models.SubjectCard.linked_card_id == 0
        )
    ).update(
        {"linked_card_id": role_card_id},
        synchronize_session=False
    )

@app.post("/api/libraries/{library_id}/cards", response_model=SubjectCardResponse)
async def create_card(
    library_id: int,
    card: SubjectCardCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建主体卡片"""
    library = verify_library_owner(library_id, user, db)

    if card.card_type not in ALL_SUBJECT_CARD_TYPES:
        raise HTTPException(status_code=400, detail="卡片类型不合法")

    # 获取默认风格模板（优先使用标记为默认的模板）
    default_template = db.query(models.StyleTemplate).filter(
        models.StyleTemplate.is_default == True
    ).first()

    # 如果没有设置默认模板，使用第一个可用模板
    if not default_template:
        default_template = db.query(models.StyleTemplate).order_by(
            models.StyleTemplate.created_at.asc()
        ).first()

    card_name = (card.name or "").strip()
    if not card_name:
        raise HTTPException(status_code=400, detail="卡片名称不能为空")

    is_sound_card = card.card_type == SOUND_CARD_TYPE
    linked_card_id = _find_role_card_id_by_name(db, library.id, card_name) if is_sound_card else None

    new_card = models.SubjectCard(
        library_id=library.id,
        name=card_name,
        alias=card.alias or "",
        card_type=card.card_type,
        linked_card_id=linked_card_id,
        role_personality="",
        style_template_id=None if is_sound_card else (default_template.id if default_template else None),
        is_protagonist=False,
        protagonist_gender=""
    )
    db.add(new_card)
    db.flush()

    if new_card.card_type == "角色":
        _bind_same_name_sound_cards_to_role(db, library.id, new_card.id, new_card.name)

    db.commit()
    db.refresh(new_card)
    return new_card

@app.get("/api/libraries/{library_id}/cards", response_model=List[SubjectCardResponse])
async def get_library_cards(
    library_id: int,
    include_sound: bool = False,
    db: Session = Depends(get_db)
):
    """获取角色库的所有卡片（公开，任何人可查看）"""
    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.id == library_id
    ).first()

    if not library:
        raise HTTPException(status_code=404, detail="Library not found")

    allowed_types = ALL_SUBJECT_CARD_TYPES if include_sound else ALLOWED_CARD_TYPES
    cards = db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library_id,
        models.SubjectCard.card_type.in_(allowed_types)
    ).order_by(models.SubjectCard.created_at.asc()).all()

    audio_cache_updated = False
    for card in cards:
        if getattr(card, "card_type", "") != SOUND_CARD_TYPE:
            continue
        if _backfill_audio_duration_cache(getattr(card, "audios", []) or [], db):
            audio_cache_updated = True

    if audio_cache_updated:
        db.commit()

    return cards

@app.put("/api/cards/{card_id}", response_model=SubjectCardResponse)
async def update_card(
    card_id: int,
    card_data: SubjectCardUpdate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新主体卡片"""
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()

    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    # 验证权限
    verify_library_owner(card.library_id, user, db)

    update_payload = card_data.dict(exclude_unset=True)
    original_card_type = card.card_type

    target_card_type = card.card_type
    if card_data.card_type is not None:
        if card_data.card_type not in ALL_SUBJECT_CARD_TYPES:
            raise HTTPException(status_code=400, detail="卡片类型不合法")
        target_card_type = card_data.card_type

    if "name" in update_payload:
        normalized_name = (card_data.name or "").strip()
        if not normalized_name:
            raise HTTPException(status_code=400, detail="卡片名称不能为空")
        card.name = normalized_name
    if card_data.alias is not None:
        card.alias = card_data.alias
    if card_data.card_type is not None:
        card.card_type = card_data.card_type
    if card_data.ai_prompt is not None:
        card.ai_prompt = card_data.ai_prompt
    role_personality_value = None
    if card_data.role_personality is not None:
        role_personality_value = card_data.role_personality
    elif card_data.role_personality_en is not None:
        role_personality_value = card_data.role_personality_en
    if role_personality_value is not None:
        card.role_personality = (role_personality_value or "").strip()
    if card_data.style_template_id is not None:
        card.style_template_id = card_data.style_template_id

    linked_card_id_specified = "linked_card_id" in update_payload
    requested_linked_card_id = card_data.linked_card_id if linked_card_id_specified else None
    if linked_card_id_specified and requested_linked_card_id in (0, "0"):
        requested_linked_card_id = None

    if target_card_type == SOUND_CARD_TYPE:
        if linked_card_id_specified:
            card.linked_card_id = _validate_and_resolve_linked_role_card_id(
                db,
                card.library_id,
                requested_linked_card_id
            )
        else:
            auto_linked_card_id = _find_role_card_id_by_name(db, card.library_id, card.name)
            card.linked_card_id = auto_linked_card_id
        card.style_template_id = None
    else:
        if linked_card_id_specified and requested_linked_card_id is not None:
            raise HTTPException(status_code=400, detail="只有声音卡片支持绑定角色")
        card.linked_card_id = None

    normalized_gender = None
    if card_data.protagonist_gender is not None:
        normalized_gender = (card_data.protagonist_gender or "").strip().lower()
        if normalized_gender not in ("", "male", "female"):
            raise HTTPException(status_code=400, detail="主角性别仅支持 male/female")

    should_set_protagonist = (
        card_data.is_protagonist is True
        or normalized_gender in ("male", "female")
    )
    should_clear_protagonist = (
        card_data.is_protagonist is False
        or normalized_gender == ""
    )

    if should_set_protagonist and target_card_type != "角色":
        raise HTTPException(status_code=400, detail="只有角色卡片可以设置男主/女主")

    if target_card_type != "角色":
        card.is_protagonist = False
        card.protagonist_gender = ""
        card.role_personality = ""
    elif should_set_protagonist:
        gender = normalized_gender or (card.protagonist_gender or "").strip().lower()
        if gender not in ("male", "female"):
            raise HTTPException(status_code=400, detail="设置主角时必须指定男主或女主")
        card.is_protagonist = True
        card.protagonist_gender = gender
    elif should_clear_protagonist:
        card.is_protagonist = False
        card.protagonist_gender = ""

    if target_card_type == "角色":
        _bind_same_name_sound_cards_to_role(db, card.library_id, card.id, card.name)
    elif original_card_type == "角色" and target_card_type != "角色":
        db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == card.library_id,
            models.SubjectCard.card_type == SOUND_CARD_TYPE,
            models.SubjectCard.linked_card_id == card.id
        ).update({"linked_card_id": None}, synchronize_session=False)

    db.commit()
    db.refresh(card)
    return card

@app.delete("/api/cards/{card_id}")
async def delete_card(
    card_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除主体卡片"""
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()

    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    # 验证权限
    verify_library_owner(card.library_id, user, db)

    # 删除所有相关的图片文件
    for image in card.images:
        if os.path.exists(image.image_path):
            os.remove(image.image_path)

    if card.card_type == "角色":
        db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == card.library_id,
            models.SubjectCard.card_type == SOUND_CARD_TYPE,
            models.SubjectCard.linked_card_id == card.id
        ).update({"linked_card_id": None}, synchronize_session=False)

    db.delete(card)
    db.commit()
    return {"message": "Card deleted successfully"}

@app.get("/api/cards/{card_id}")
async def get_card(
    card_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取单个主体卡片信息"""
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()

    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    # 验证权限
    verify_library_owner(card.library_id, user, db)

    return {
        "id": card.id,
        "name": card.name,
        "card_type": card.card_type,
        "linked_card_id": getattr(card, "linked_card_id", None),
        "ai_prompt": card.ai_prompt,
        "role_personality": getattr(card, "role_personality", "") or "",
        "alias": card.alias,
        "is_protagonist": bool(getattr(card, "is_protagonist", False)),
        "protagonist_gender": (getattr(card, "protagonist_gender", "") or ""),
        "ai_prompt_status": getattr(card, 'ai_prompt_status', None)
    }

@app.post("/api/cards/{card_id}/generate-ai-prompt")
async def generate_card_ai_prompt(
    card_id: int,
    background_tasks: BackgroundTasks,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """为单个主体卡片生成AI绘画提示词（异步）"""
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()

    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    # 验证权限
    library = verify_library_owner(card.library_id, user, db)

    # 检查是否正在生成中
    if hasattr(card, 'ai_prompt_status') and card.ai_prompt_status == 'generating':
        return {"message": "该主体正在生成中，请稍候", "status": "generating"}

    # 设置状态为生成中
    if hasattr(card, 'ai_prompt_status'):
        card.ai_prompt_status = 'generating'
    try:
        relay_task = _submit_subject_prompt_task(db, card)
        db.commit()
    except Exception as exc:
        db.rollback()
        if hasattr(card, 'ai_prompt_status'):
            card.ai_prompt_status = 'failed'
            db.commit()
        raise HTTPException(status_code=502, detail=f"提交文本任务失败: {str(exc)}")

    return {"message": "已开始生成AI提示词", "status": "generating", "task_id": relay_task.external_task_id}


def _build_subject_prompt_storyboard_context(episode: models.Episode) -> str:
    all_shots = []
    if episode.storyboard_data:
        try:
            storyboard = json.loads(episode.storyboard_data)
            shots = storyboard.get("shots", [])
            all_shots.extend(shots)
        except Exception:
            pass

    if not all_shots:
        return episode.content if episode.content else "暂无剧集内容"
    return json.dumps({"shots": all_shots}, ensure_ascii=False, indent=2)


def _submit_subject_prompt_task(db: Session, card: models.SubjectCard):
    library = db.query(models.StoryLibrary).filter(models.StoryLibrary.id == card.library_id).first()
    if not library or not library.episode_id:
        raise ValueError("主体库未关联剧集")

    episode = db.query(models.Episode).filter(models.Episode.id == library.episode_id).first()
    if not episode:
        raise ValueError("关联剧集不存在")

    storyboard_context = _build_subject_prompt_storyboard_context(episode)
    prompt_template = get_prompt_by_key("generate_subject_ai_prompt")
    prompt = prompt_template.format(
        subject_name=card.name,
        subject_type=card.card_type,
        storyboard_context=storyboard_context
    )
    config = get_ai_config("subject_prompt")
    request_data = {
        "model": config["model"],
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "response_format": {"type": "json_object"},
        "stream": False
    }
    task_payload = {
        "card_id": int(card.id),
        "episode_id": int(episode.id),
        "card_name": str(card.name or ""),
    }
    return submit_and_persist_text_task(
        db,
        task_type="subject_prompt",
        owner_type="card",
        owner_id=int(card.id),
        stage_key="subject_prompt",
        function_key="subject_prompt",
        request_payload=request_data,
        task_payload=task_payload,
    )


@app.post("/api/libraries/{library_id}/batch-generate-prompts")
async def batch_generate_prompts(
    library_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """批量为主体库中的所有主体生成AI提示词"""
    library = verify_library_owner(library_id, user, db)

    # 获取所有没有ai_prompt或ai_prompt为空的主体
    cards = db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library_id,
        models.SubjectCard.card_type.in_(ALLOWED_CARD_TYPES),
        or_(
            models.SubjectCard.ai_prompt == None,
            models.SubjectCard.ai_prompt == ""
        )
    ).all()

    if not cards:
        return {"message": "没有需要生成AI提示词的主体", "generated_count": 0}

    # 获取关联的剧集和剧本
    if not library.episode_id:
        raise HTTPException(status_code=400, detail="该主体库未关联剧集，无法生成AI提示词")

    episode = db.query(models.Episode).filter(models.Episode.id == library.episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="关联的剧集不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="关联的剧本不存在")

    # 获取当前剧集的分镜表JSON
    all_shots = []
    if episode.storyboard_data:
        try:
            storyboard = json.loads(episode.storyboard_data)
            shots = storyboard.get("shots", [])
            all_shots.extend(shots)
        except:
            pass

    # 如果没有分镜表数据，使用片段文案
    if not all_shots:
        storyboard_context = episode.content if episode.content else "暂无剧集内容"
    else:
        # 将完整分镜表转为JSON字符串
        full_storyboard = {"shots": all_shots}
        storyboard_context = json.dumps(full_storyboard, ensure_ascii=False, indent=2)

    submitted_count = 0
    failed_cards = []

    for card in cards:
        try:
            if hasattr(card, 'ai_prompt_status'):
                card.ai_prompt_status = 'generating'
            _submit_subject_prompt_task(db, card)
            submitted_count += 1
        except Exception as e:
            if hasattr(card, 'ai_prompt_status'):
                card.ai_prompt_status = 'failed'
            failed_cards.append(card.name)
            print(f"  ✗ 提交失败: {str(e)}")

    db.commit()

    result_message = f"成功提交 {submitted_count} 个主体的AI提示词任务"
    if failed_cards:
        result_message += f"，失败 {len(failed_cards)} 个: {', '.join(failed_cards)}"

    return {
        "message": result_message,
        "generated_count": submitted_count,
        "failed_count": len(failed_cards),
        "failed_cards": failed_cards
    }

# ==================== 图片API ====================

@app.post("/api/cards/{card_id}/images")
async def upload_image(
    card_id: int,
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """上传图片到卡片并上传到CDN"""
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()

    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    # 验证权限
    verify_library_owner(card.library_id, user, db)

    # 上传到CDN（使用线程池避免阻塞）
    try:
        loop = asyncio.get_event_loop()
        cdn_url = await loop.run_in_executor(
            executor,
            save_and_upload_to_cdn,
            file
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传图片失败: {str(e)}")

    # 获取当前最大order
    max_order = db.query(models.CardImage).filter(
        models.CardImage.card_id == card_id
    ).count()

    # 创建图片记录（存储CDN URL）
    new_image = models.CardImage(
        card_id=card_id,
        image_path=cdn_url,
        order=max_order
    )
    db.add(new_image)

    if card.card_type != "场景":
        db.query(models.GeneratedImage).filter(
            models.GeneratedImage.card_id == card_id
        ).update({"is_reference": False})

    # 同步为主体素材图（场景卡不自动勾选）
    new_generated = models.GeneratedImage(
        card_id=card_id,
        image_path=cdn_url,
        model_name="upload",
        is_reference=(card.card_type != "场景"),
        status="completed",
        task_id=""
    )
    db.add(new_generated)

    db.commit()
    db.refresh(new_image)

    return {
        "id": new_image.id,
        "card_id": new_image.card_id,
        "image_path": new_image.image_path,
        "order": new_image.order
    }

@app.delete("/api/images/{image_id}")
async def delete_image(
    image_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除图片"""
    image = db.query(models.CardImage).filter(models.CardImage.id == image_id).first()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # 验证权限
    card = db.query(models.SubjectCard).filter(
        models.SubjectCard.id == image.card_id
    ).first()
    verify_library_owner(card.library_id, user, db)

    # ✅ 查找对应的上传素材图记录
    gen_img = db.query(models.GeneratedImage).filter(
        models.GeneratedImage.card_id == image.card_id,
        models.GeneratedImage.image_path == image.image_path,
        models.GeneratedImage.model_name == "upload"
    ).first()

    # ✅ 如果对应的素材图是参考图，需要特殊处理
    if gen_img and gen_img.is_reference and card.card_type != "场景":
        # 查找该卡片的所有其他完成的图片
        other_completed_images = db.query(models.GeneratedImage).filter(
            models.GeneratedImage.card_id == image.card_id,
            models.GeneratedImage.id != gen_img.id,
            models.GeneratedImage.status == "completed"
        ).all()

        # 如果这是最后一张完成的图片，不允许删除
        if len(other_completed_images) == 0:
            raise HTTPException(
                status_code=400,
                detail="不能删除最后一张主体素材图"
            )

        # 删除前，自动将第一张其他图片设为参考图
        other_completed_images[0].is_reference = True
        print(f"[删除上传参考图] 自动将图片 {other_completed_images[0].id} 设为新的参考图")

    # 删除文件
    if os.path.exists(image.image_path):
        os.remove(image.image_path)

    # 删除对应的上传素材图记录
    if gen_img:
        db.delete(gen_img)

    db.delete(image)
    db.commit()
    return {"message": "Image deleted successfully"}


@app.post("/api/cards/{card_id}/audios", response_model=SubjectCardAudioResponse)
async def upload_card_audio(
    card_id: int,
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """上传声音卡片音频（保留历史，新上传自动成为当前素材）"""
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    verify_library_owner(card.library_id, user, db)

    if card.card_type != SOUND_CARD_TYPE:
        raise HTTPException(status_code=400, detail="只有声音卡片支持上传音频")

    try:
        loop = asyncio.get_event_loop()
        cdn_url, duration_seconds = await loop.run_in_executor(
            executor,
            save_audio_and_upload_to_cdn,
            file
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传音频失败: {str(e)}")

    db.query(models.SubjectCardAudio).filter(
        models.SubjectCardAudio.card_id == card_id
    ).update({"is_reference": False})

    new_audio = models.SubjectCardAudio(
        card_id=card_id,
        audio_path=cdn_url,
        file_name=str(file.filename or "").strip(),
        duration_seconds=_safe_audio_duration_seconds(duration_seconds),
        is_reference=True
    )
    db.add(new_audio)
    db.commit()
    db.refresh(new_audio)
    return new_audio


@app.get("/api/cards/{card_id}/audios", response_model=List[SubjectCardAudioResponse])
async def get_card_audios(
    card_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取声音卡片音频列表"""
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    verify_library_owner(card.library_id, user, db)

    audios = db.query(models.SubjectCardAudio).filter(
        models.SubjectCardAudio.card_id == card_id
    ).order_by(
        models.SubjectCardAudio.created_at.desc(),
        models.SubjectCardAudio.id.desc()
    ).all()
    if _backfill_audio_duration_cache(audios, db):
        db.commit()
    return audios


@app.delete("/api/cards/{card_id}/audios/{audio_id}")
async def delete_card_audio(
    card_id: int,
    audio_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除声音卡片音频；若删除的是当前素材，则自动补一个新的当前素材。"""
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    verify_library_owner(card.library_id, user, db)

    if card.card_type != SOUND_CARD_TYPE:
        raise HTTPException(status_code=400, detail="只有声音卡片支持删除音频")

    audio = db.query(models.SubjectCardAudio).filter(
        models.SubjectCardAudio.id == audio_id,
        models.SubjectCardAudio.card_id == card_id
    ).first()
    if not audio:
        raise HTTPException(status_code=404, detail="音频不存在")

    was_reference = bool(audio.is_reference)
    db.delete(audio)
    db.flush()

    if was_reference:
        fallback_audio = db.query(models.SubjectCardAudio).filter(
            models.SubjectCardAudio.card_id == card_id
        ).order_by(
            models.SubjectCardAudio.created_at.desc(),
            models.SubjectCardAudio.id.desc()
        ).first()
        if fallback_audio:
            fallback_audio.is_reference = True

    db.commit()
    return {"message": "Audio deleted successfully"}

# ==================== AI生成图片API ====================

# 生成图片
class ImageGenerationRequest(BaseModel):
    provider: Optional[str] = None
    model: str
    size: str = "1:1"
    resolution: Optional[str] = None
    n: int = 1
    reference_image_ids: Optional[List[int]] = []  # 参考的GeneratedImage的ID列表
    generation_mode: str = "default"


def _build_card_image_prompt(
    card: models.SubjectCard,
    style_template: str,
    generation_mode: str
) -> str:
    normalized_mode = str(generation_mode or "default").strip().lower()

    if normalized_mode == "three_view":
        if card.card_type != "角色":
            raise HTTPException(status_code=400, detail="只有角色卡片支持生成三视图")
        prompt_prefix = _get_optional_prompt_config_content(
            CHARACTER_THREE_VIEW_PROMPT_KEY,
            CHARACTER_THREE_VIEW_PROMPT_DEFAULT
        )
        return prompt_prefix

    if card.card_type == "角色":
        final_prompt = "生成一张角色站立的图片，全身，正面角度，纯白色背景,带简单阴影。\n"
        if style_template:
            final_prompt += f"生成图片的风格是：{style_template}\n"
        final_prompt += card.ai_prompt
        return final_prompt

    final_prompt = ""
    if style_template:
        final_prompt += f"生成图片的风格是：{style_template}\n"
    if card.card_type == "场景":
        final_prompt += f"生成图片中场景的是：{card.ai_prompt}"
    else:
        final_prompt += card.ai_prompt
    return final_prompt


def _resolve_style_template_content_for_card_type(
    style_template_obj: Optional[models.StyleTemplate],
    card_type: str
) -> str:
    if not style_template_obj:
        return ""

    normalized_card_type = str(card_type or "").strip()
    if normalized_card_type == "场景":
        return str(
            getattr(style_template_obj, "scene_content", None)
            or getattr(style_template_obj, "content", "")
            or ""
        ).strip()
    if normalized_card_type == "道具":
        return str(
            getattr(style_template_obj, "prop_content", None)
            or getattr(style_template_obj, "content", "")
            or ""
        ).strip()
    return str(getattr(style_template_obj, "content", "") or "").strip()


def _resolve_card_reference_urls(
    db: Session,
    card_id: int,
    reference_image_ids: Optional[List[int]],
    generation_mode: str,
) -> List[str]:
    normalized_mode = str(generation_mode or "default").strip().lower()
    selected_ids = []
    for raw_id in reference_image_ids or []:
        try:
            image_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if image_id > 0 and image_id not in selected_ids:
            selected_ids.append(image_id)

    if normalized_mode == "three_view":
        if not selected_ids:
            raise HTTPException(status_code=400, detail="请先选择一张主体素材图，再生成三视图")

        reference_image = db.query(models.GeneratedImage).filter(
            models.GeneratedImage.id == selected_ids[0],
            models.GeneratedImage.card_id == card_id,
            models.GeneratedImage.is_reference == True,
            models.GeneratedImage.status == "completed"
        ).first()
        if not reference_image or not str(reference_image.image_path or "").strip():
            raise HTTPException(status_code=400, detail="请先选择一张主体素材图，再生成三视图")
        return [reference_image.image_path]

    reference_urls: List[str] = []
    for img_id in selected_ids:
        ref_img = db.query(models.GeneratedImage).filter(
            models.GeneratedImage.id == img_id,
            models.GeneratedImage.is_reference == True
        ).first()
        if ref_img and ref_img.status == "completed":
            reference_urls.append(ref_img.image_path)
    return reference_urls

@app.post("/api/cards/{card_id}/generate-image")
async def generate_image_for_card(
    card_id: int,
    request: ImageGenerationRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """为卡片生成AI图片"""
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    # 验证权限
    verify_library_owner(card.library_id, user, db)

    # 检查prompt
    if not card.ai_prompt:
        raise HTTPException(status_code=400, detail="请先设置AI Prompt")

    requested_model = normalize_image_model_key(request.model)

    image_debug_folder = save_ai_debug(
        "card_image_generate",
        {
            "card_id": card_id,
            "card_name": card.name,
            "card_type": card.card_type,
            "model": requested_model,
            "size": request.size,
            "resolution": request.resolution,
            "n": request.n,
            "generation_mode": request.generation_mode,
            "reference_image_ids": request.reference_image_ids or [],
            "requested_at": datetime.utcnow().isoformat()
        },
        output_data={"status": "request_received"},
        shot_id=card_id
    )

    # 获取风格模板：优先使用卡片自己的风格模板，如果没有则使用Script的全局风格
    style_template = ""
    style_source = "无"

    # 1. 优先：卡片自己的风格模板
    if card.style_template_id:
        style_template_obj = db.query(models.StyleTemplate).filter(
            models.StyleTemplate.id == card.style_template_id
        ).first()
        if style_template_obj:
            style_template = _resolve_style_template_content_for_card_type(style_template_obj, card.card_type)
            style_source = f"卡片风格模板 (ID: {card.style_template_id}, 名称: {style_template_obj.name})"

    # 2. 兜底：Script的全局风格模板
    if not style_template:
        library = db.query(models.StoryLibrary).filter(models.StoryLibrary.id == card.library_id).first()
        if library and library.episode_id:
            episode = db.query(models.Episode).filter(models.Episode.id == library.episode_id).first()
            if episode:
                script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
                if script and script.style_template:
                    style_template = script.style_template
                    style_source = "Script全局风格模板"


    final_prompt = _build_card_image_prompt(card, style_template, request.generation_mode)

    reference_urls = _resolve_card_reference_urls(
        db=db,
        card_id=card_id,
        reference_image_ids=request.reference_image_ids,
        generation_mode=request.generation_mode,
    )

    image_debug_meta = _build_image_generation_debug_meta(
        requested_model,
        provider=request.provider,
        has_reference_images=bool(reference_urls)
    )

    # 打印AI作图的最终prompt（用于调试）
    print("=" * 80)
    print(f"[AI作图] Card ID: {card_id}, 类型: {card.card_type}, 名称: {card.name}")
    print(f"[AI作图] 风格来源: {style_source}")
    print(f"[AI作图] 提供商: {image_debug_meta['provider']}, 模型: {requested_model}, 尺寸: {request.size}")
    if str(request.generation_mode or "").strip().lower() != "default":
        print(f"[AI作图] 生成模式: {request.generation_mode}")
    if reference_urls:
        print(f"[AI作图] 参考图数量: {len(reference_urls)}")
    print("-" * 80)
    print("[AI作图] 最终拼接的Prompt:")
    print(final_prompt)
    print("=" * 80)

    # 提交生成任务（使用线程池避免阻塞）
    try:
        request_name = f"card_{card_id}_{uuid.uuid4().hex[:8]}"
        request_payload = _build_image_generation_request_payload(
            provider=image_debug_meta["provider"],
            actual_model=image_debug_meta["actual_model"],
            prompt_text=final_prompt,
            ratio=request.size,
            reference_images=reference_urls,
            name=request_name,
            resolution=request.resolution,
        )
        debug_input = {
            "card_id": card_id,
            "card_name": card.name,
            "card_type": card.card_type,
            "style_source": style_source,
            "style_template": style_template,
            "generation_mode": request.generation_mode,
            "provider": image_debug_meta["provider"],
            "actual_model": image_debug_meta["actual_model"],
            "api_url": image_debug_meta["submit_api_url"],
            "status_api_url_template": image_debug_meta["status_api_url_template"],
            "request": {
                "model": requested_model,
                "size": request.size,
                "resolution": request.resolution,
                "n": request.n,
                "reference_image_ids": request.reference_image_ids or []
            },
            "request_payload": request_payload,
            "reference_urls": reference_urls,
            "final_prompt": final_prompt
        }
        loop = asyncio.get_event_loop()
        task_id = await loop.run_in_executor(
            executor,
            lambda: submit_image_generation(
                final_prompt,
                requested_model,
                request.size,
                request.resolution,
                request.n,
                reference_urls if reference_urls else None,
                request.provider,
            )
        )

        # 创建数据库记录
        new_generated_image = models.GeneratedImage(
            card_id=card_id,
            image_path="",  # 稍后通过轮询更新
            model_name=requested_model,
            is_reference=False,
            task_id=task_id,
            status="processing"
        )
        db.add(new_generated_image)
        db.flush()

        _record_card_image_charge(
            db,
            card=card,
            model_name=requested_model,
            provider=image_debug_meta["provider"],
            resolution=request.resolution,
            task_id=task_id,
            quantity=max(1, int(request.n or 1)),
            detail_payload={
                "generated_image_id": int(new_generated_image.id),
                "size": request.size,
                "resolution": request.resolution,
                "generation_mode": request.generation_mode,
            },
        )

        # ✅ 更新卡片的生成状态
        card.is_generating_images = True
        card.generating_count += request.n

        db.commit()
        db.refresh(new_generated_image)

        save_ai_debug(
            "card_image_generate",
            debug_input,
            {
                "generated_image_id": new_generated_image.id,
                "task_id": task_id,
                "status": "processing",
                "model_name": requested_model,
                "provider": image_debug_meta["provider"],
                "actual_model": image_debug_meta["actual_model"],
                "api_url": image_debug_meta["submit_api_url"],
                "status_api_url": get_image_status_api_url(
                    task_id=task_id,
                    model_name=requested_model,
                    provider=image_debug_meta["provider"]
                )
            },
            shot_id=card_id,
            task_folder=image_debug_folder
        )

        return {
            "message": "图片生成任务已提交",
            "generated_image_id": new_generated_image.id,
            "task_id": task_id
        }

    except Exception as e:
        image_debug_meta = _build_image_generation_debug_meta(
            requested_model,
            provider=request.provider,
            has_reference_images=bool(reference_urls)
        )
        request_name = f"card_{card_id}_{uuid.uuid4().hex[:8]}"
        save_ai_debug(
            "card_image_generate",
            {
                "card_id": card_id,
                "card_name": card.name,
                "card_type": card.card_type,
                "style_source": style_source,
                "style_template": style_template,
                "generation_mode": request.generation_mode,
                "provider": image_debug_meta["provider"],
                "actual_model": image_debug_meta["actual_model"],
                "api_url": image_debug_meta["submit_api_url"],
                "status_api_url_template": image_debug_meta["status_api_url_template"],
                "request": {
                    "model": requested_model,
                    "size": request.size,
                    "resolution": request.resolution,
                    "n": request.n,
                    "reference_image_ids": request.reference_image_ids or []
                },
                "request_payload": _build_image_generation_request_payload(
                    provider=image_debug_meta["provider"],
                    actual_model=image_debug_meta["actual_model"],
                    prompt_text=final_prompt,
                    ratio=request.size,
                    reference_images=reference_urls,
                    name=request_name,
                    resolution=request.resolution,
                ),
                "reference_urls": reference_urls,
                "final_prompt": final_prompt
            },
            {
                "error": str(e),
                "provider": image_debug_meta["provider"],
                "actual_model": image_debug_meta["actual_model"],
                "api_url": image_debug_meta["submit_api_url"]
            },
            shot_id=card_id,
            task_folder=image_debug_folder
        )
        raise HTTPException(status_code=500, detail=f"提交任务失败: {str(e)}")

# 获取卡片的生成图片列表
@app.get("/api/cards/{card_id}/generated-images", response_model=List[GeneratedImageResponse])
def get_card_generated_images(
    card_id: int,
    db: Session = Depends(get_db)
):
    """获取卡片的所有AI生成图片"""
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    generated_images = db.query(models.GeneratedImage).filter(
        models.GeneratedImage.card_id == card_id
    ).order_by(models.GeneratedImage.created_at.desc()).all()

    return generated_images

# 设置参考图
class SetReferenceRequest(BaseModel):
    generated_image_ids: List[int]  # 要设为参考图的ID列表

@app.put("/api/cards/{card_id}/reference-images")
async def set_reference_images(
    card_id: int,
    request: SetReferenceRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """设置卡片的参考图（支持多选）"""
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    # 验证权限
    verify_library_owner(card.library_id, user, db)

    # 先将所有该卡片的生成图片取消参考状态
    db.query(models.GeneratedImage).filter(
        models.GeneratedImage.card_id == card_id
    ).update({"is_reference": False})

    # 设置新的参考图
    if request.generated_image_ids:
        db.query(models.GeneratedImage).filter(
            models.GeneratedImage.id.in_(request.generated_image_ids),
            models.GeneratedImage.card_id == card_id
        ).update({"is_reference": True}, synchronize_session=False)

    db.commit()

    return {"message": "参考图已更新"}

# 删除生成的图片
@app.delete("/api/generated-images/{generated_image_id}")
async def delete_generated_image(
    generated_image_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除AI生成的图片"""
    gen_img = db.query(models.GeneratedImage).filter(
        models.GeneratedImage.id == generated_image_id
    ).first()

    if not gen_img:
        raise HTTPException(status_code=404, detail="Generated image not found")

    # 验证权限
    card = db.query(models.SubjectCard).filter(
        models.SubjectCard.id == gen_img.card_id
    ).first()
    verify_library_owner(card.library_id, user, db)

    # ✅ 如果删除的是参考图，需要特殊处理
    if gen_img.is_reference and card.card_type != "场景":
        # 查找该卡片的所有其他完成的图片
        other_completed_images = db.query(models.GeneratedImage).filter(
            models.GeneratedImage.card_id == gen_img.card_id,
            models.GeneratedImage.id != gen_img.id,
            models.GeneratedImage.status == "completed"
        ).all()

        # 如果这是最后一张完成的图片，不允许删除
        if len(other_completed_images) == 0:
            raise HTTPException(
                status_code=400,
                detail="不能删除最后一张主体素材图"
            )

        # 删除前，自动将第一张其他图片设为参考图
        other_completed_images[0].is_reference = True
        print(f"[删除参考图] 自动将图片 {other_completed_images[0].id} 设为新的参考图")

    # 删除记录（CDN图片不删除，因为可能还在其他地方使用）
    db.delete(gen_img)
    db.commit()

    return {"message": "Generated image deleted successfully"}

# ==================== 公开角色库API ====================

@app.get("/api/public/users/{user_id}/libraries", response_model=List[StoryLibraryResponse])
async def get_user_libraries(
    user_id: int,
    db: Session = Depends(get_db)
):
    """获取指定用户的所有角色库"""
    libraries = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.user_id == user_id
    ).order_by(models.StoryLibrary.created_at.desc()).all()

    return libraries

# ==================== 管理API ====================

class CreateUserRequest(BaseModel):
    username: str


class DashboardBulkDeleteRequest(BaseModel):
    ids: List[int] = []
    status: Optional[str] = None
    task_type: Optional[str] = None
    creator_username: Optional[str] = None
    keyword: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    delete_all: bool = False


def _verify_admin_panel_password(x_admin_password: Optional[str]) -> None:
    admin_panel_password = (ADMIN_PANEL_PASSWORD or "").strip()
    if (
        not admin_panel_password
        or (x_admin_password or "").strip() != admin_panel_password
    ):
        raise HTTPException(status_code=403, detail="管理员密码错误")


def _parse_dashboard_date(date_text: Optional[str], *, end_exclusive: bool = False) -> Optional[datetime]:
    text_value = str(date_text or "").strip()
    if not text_value:
        return None
    try:
        parsed = datetime.strptime(text_value, "%Y-%m-%d")
        if end_exclusive:
            return parsed + timedelta(days=1)
        return parsed
    except ValueError:
        raise HTTPException(status_code=400, detail=f"日期格式错误: {text_value}，请使用 YYYY-MM-DD")


def _apply_dashboard_query_filters(
    query,
    *,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    creator_username: Optional[str] = None,
    keyword: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    if status:
        query = query.filter(models.DashboardTaskLog.status == str(status).strip())
    if task_type:
        query = query.filter(models.DashboardTaskLog.task_type == str(task_type).strip())
    if creator_username:
        query = query.filter(models.DashboardTaskLog.creator_username.ilike(f"%{str(creator_username).strip()}%"))
    if keyword:
        like = f"%{str(keyword).strip()}%"
        query = query.filter(or_(
            models.DashboardTaskLog.title.ilike(like),
            models.DashboardTaskLog.task_key.ilike(like),
            models.DashboardTaskLog.script_name.ilike(like),
            models.DashboardTaskLog.episode_name.ilike(like),
            models.DashboardTaskLog.creator_username.ilike(like),
            models.DashboardTaskLog.error_message.ilike(like),
            models.DashboardTaskLog.result_summary.ilike(like),
            models.DashboardTaskLog.api_url.ilike(like),
        ))

    start_dt = _parse_dashboard_date(date_from, end_exclusive=False)
    end_dt = _parse_dashboard_date(date_to, end_exclusive=True)
    if start_dt:
        query = query.filter(models.DashboardTaskLog.created_at >= start_dt)
    if end_dt:
        query = query.filter(models.DashboardTaskLog.created_at < end_dt)
    return query


def _safe_parse_dashboard_json(payload_text: Any, default_value: Any):
    if not payload_text:
        return default_value
    if isinstance(payload_text, (dict, list)):
        return payload_text
    try:
        return json.loads(payload_text)
    except Exception:
        return payload_text


SIMPLE_STORYBOARD_TIMEOUT_SECONDS = 3600
SIMPLE_STORYBOARD_TIMEOUT_ERROR = "简单分镜生成超时（超过 1 小时），已自动标记为失败，请重新生成。"
DASHBOARD_SIMPLE_STORYBOARD_TIMEOUT_ERROR = "简单分镜任务超时（超过 1 小时），已自动标记为失败。"


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


def _mark_dashboard_simple_storyboard_timeout_if_needed(row: Optional[models.DashboardTaskLog], db: Session) -> bool:
    if not row:
        return False
    if str(getattr(row, "task_type", "") or "").strip() != "simple_storyboard":
        return False
    if str(getattr(row, "status", "") or "").strip() != "submitting":
        return False
    created_at = getattr(row, "created_at", None)
    if not created_at:
        return False
    if (datetime.utcnow() - created_at).total_seconds() < SIMPLE_STORYBOARD_TIMEOUT_SECONDS:
        return False
    row.status = "failed"
    if not str(getattr(row, "error_message", "") or "").strip():
        row.error_message = DASHBOARD_SIMPLE_STORYBOARD_TIMEOUT_ERROR
    if not str(getattr(row, "result_summary", "") or "").strip():
        row.result_summary = DASHBOARD_SIMPLE_STORYBOARD_TIMEOUT_ERROR
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return True


def _build_dashboard_batch_summary(events: Any, fallback_status: str) -> Dict[str, Any]:
    summary = summarize_dashboard_batch_events(events, fallback_status=fallback_status)
    if not summary.get("has_batches"):
        return summary

    items = summary.get("items") or []
    summary_lines: List[str] = []
    counts = summary.get("counts") or {}
    header_parts = [f"Batch {int(counts.get('total') or 0)}"]
    for status_key in ("completed", "processing", "submitting", "failed", "cancelled"):
        count = int(counts.get(status_key) or 0)
        if count <= 0:
            continue
        header_parts.append(f"{DASHBOARD_STATUS_LABELS.get(status_key, status_key)} {count}")
    summary_lines.append(" | ".join(header_parts))

    for item in items:
        status = str(item.get("latest_status") or "")
        batch_line_parts = [f"Batch {item.get('batch_id')}", DASHBOARD_STATUS_LABELS.get(status, status)]
        latest_attempt = item.get("latest_attempt")
        if latest_attempt is not None:
            batch_line_parts.append(f"尝试 {latest_attempt}")
        shots_count = item.get("shots_count")
        if shots_count is not None and status == "completed":
            batch_line_parts.append(f"{shots_count} 镜头")
        last_error = str(item.get("last_error") or "").strip()
        line = " | ".join(part for part in batch_line_parts if str(part).strip())
        if last_error and status != "completed":
            line = f"{line} | {last_error}"
        summary_lines.append(line)

    summary["summary_text"] = "\n".join(summary_lines)
    return summary


def _serialize_dashboard_task(row: models.DashboardTaskLog, include_payloads: bool = False) -> dict:
    parsed_events = _safe_parse_dashboard_json(row.events_json, [])
    batch_summary = _build_dashboard_batch_summary(parsed_events, row.status)
    resolved_status = batch_summary.get("overall_status") if batch_summary.get("has_batches") else row.status
    data = {
        "id": row.id,
        "task_key": row.task_key,
        "task_folder": row.task_folder,
        "source_type": row.source_type,
        "source_record_type": row.source_record_type,
        "source_record_id": row.source_record_id,
        "task_type": row.task_type,
        "task_type_label": DASHBOARD_TASK_TYPE_LABELS.get(row.task_type, row.task_type or "任务"),
        "stage": row.stage,
        "title": row.title,
        "status": resolved_status,
        "status_label": DASHBOARD_STATUS_LABELS.get(resolved_status, resolved_status),
        "stored_status": row.status,
        "stored_status_label": DASHBOARD_STATUS_LABELS.get(row.status, row.status),
        "creator_user_id": row.creator_user_id,
        "creator_username": row.creator_username,
        "script_id": row.script_id,
        "script_name": row.script_name,
        "episode_id": row.episode_id,
        "episode_name": row.episode_name,
        "shot_id": row.shot_id,
        "shot_number": row.shot_number,
        "batch_id": row.batch_id,
        "provider": row.provider,
        "model_name": row.model_name,
        "api_url": row.api_url,
        "status_api_url": row.status_api_url,
        "external_task_id": row.external_task_id,
        "query_supported": is_dashboard_task_query_supported(row),
        "error_message": row.error_message,
        "result_summary": row.result_summary,
        "batch_summary": batch_summary if batch_summary.get("has_batches") else None,
        "batch_summary_text": batch_summary.get("summary_text", ""),
        "latest_filename": row.latest_filename,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }
    if include_payloads:
        data.update({
            "input_payload": _safe_parse_dashboard_json(row.input_payload, {}),
            "output_payload": _safe_parse_dashboard_json(row.output_payload, {}),
            "raw_response_payload": _safe_parse_dashboard_json(row.raw_response_payload, {}),
            "result_payload": _safe_parse_dashboard_json(row.result_payload, {}),
            "latest_event_payload": _safe_parse_dashboard_json(row.latest_event_payload, {}),
            "events": parsed_events,
        })
    return data


def _get_today_video_counts_by_user(db: Session) -> Dict[int, int]:
    shanghai_offset = timedelta(hours=8)
    now_utc = datetime.utcnow()
    now_shanghai = now_utc + shanghai_offset
    start_of_day = (now_shanghai.replace(hour=0, minute=0, second=0, microsecond=0) - shanghai_offset)
    end_of_day = start_of_day + timedelta(days=1)
    counts: Dict[int, int] = {}

    shot_video_rows = db.query(
        models.Script.user_id,
        func.count(models.ShotVideo.id),
    ).join(
        models.Episode,
        models.Episode.script_id == models.Script.id,
    ).join(
        models.StoryboardShot,
        models.StoryboardShot.episode_id == models.Episode.id,
    ).join(
        models.ShotVideo,
        models.ShotVideo.shot_id == models.StoryboardShot.id,
    ).filter(
        models.ShotVideo.created_at >= start_of_day,
        models.ShotVideo.created_at < end_of_day,
    ).group_by(
        models.Script.user_id,
    ).all()

    for user_id, total_count in shot_video_rows:
        numeric_user_id = int(user_id or 0)
        counts[numeric_user_id] = counts.get(numeric_user_id, 0) + int(total_count or 0)

    storyboard2_rows = db.query(
        models.Script.user_id,
        func.count(models.Storyboard2SubShotVideo.id),
    ).join(
        models.Episode,
        models.Episode.script_id == models.Script.id,
    ).join(
        models.Storyboard2Shot,
        models.Storyboard2Shot.episode_id == models.Episode.id,
    ).join(
        models.Storyboard2SubShot,
        models.Storyboard2SubShot.storyboard2_shot_id == models.Storyboard2Shot.id,
    ).join(
        models.Storyboard2SubShotVideo,
        models.Storyboard2SubShotVideo.sub_shot_id == models.Storyboard2SubShot.id,
    ).filter(
        models.Storyboard2SubShotVideo.created_at >= start_of_day,
        models.Storyboard2SubShotVideo.created_at < end_of_day,
        models.Storyboard2SubShotVideo.status == "completed",
        models.Storyboard2SubShotVideo.video_url != "",
        models.Storyboard2SubShotVideo.is_deleted == False,
    ).group_by(
        models.Script.user_id,
    ).all()

    for user_id, total_count in storyboard2_rows:
        numeric_user_id = int(user_id or 0)
        counts[numeric_user_id] = counts.get(numeric_user_id, 0) + int(total_count or 0)

    return counts

@app.get("/api/admin/users")
async def get_all_users_admin(
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """获取所有用户（管理用，隐藏保留账号）"""
    _verify_admin_panel_password(x_admin_password)
    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    today_video_counts = _get_today_video_counts_by_user(db)
    return [{
        "id": user.id,
        "username": user.username,
        "password": user.password_plain,
        "created_at": user.created_at,
        "today_video_count": int(today_video_counts.get(int(user.id or 0), 0)),
    } for user in users if user.username not in HIDDEN_USERS]

@app.post("/api/admin/users")
async def create_user_admin(
    request: CreateUserRequest,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """创建新用户（管理用）"""
    import secrets
    _verify_admin_panel_password(x_admin_password)

    # 检查用户名是否已存在
    existing_user = db.query(models.User).filter(
        models.User.username == request.username
    ).first()

    if existing_user:
        raise HTTPException(status_code=400, detail="用户名已存在")

    # 生成token
    token = secrets.token_urlsafe(32)

    # 创建用户（默认密码 123456）
    new_user = models.User(
        username=request.username,
        token=token,
        password_hash=_hash_password("123456"),
        password_plain="123456"
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "id": new_user.id,
        "username": new_user.username,
        "password": new_user.password_plain,
        "created_at": new_user.created_at
    }

@app.delete("/api/admin/users/{user_id}")
async def delete_user_admin(
    user_id: int,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """删除用户（管理用，不允许删除保留账号）"""
    _verify_admin_panel_password(x_admin_password)
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if user.username in HIDDEN_USERS:
        raise HTTPException(status_code=403, detail="无法删除此用户")

    # 先显式清理该用户所有剧集下镜头的直接依赖，避免 PostgreSQL 外键拦截 ORM 级联删除。
    episode_ids = [
        episode_id
        for episode_id, in db.query(models.Episode.id)
        .join(models.Script, models.Episode.script_id == models.Script.id)
        .filter(models.Script.user_id == user_id)
        .all()
    ]
    for episode_id in episode_ids:
        _delete_episode_storyboard_shots(episode_id, db)
    episode_cleanup_stats = _clear_episode_dependencies(episode_ids, db)

    print(
        "[用户删除清理] "
        f"user_id={user_id} username={user.username} "
        f"episodes={len(episode_ids)} "
        f"managed_tasks={episode_cleanup_stats['deleted_managed_tasks']} "
        f"managed_sessions={episode_cleanup_stats['deleted_managed_sessions']} "
        f"voiceover_tts_tasks={episode_cleanup_stats['deleted_voiceover_tts_tasks']} "
        f"unlinked_libraries={episode_cleanup_stats['unlinked_libraries']}"
    )

    db.delete(user)
    db.commit()

    return {"message": "用户删除成功"}


@app.post("/api/admin/users/{user_id}/reset-password")
async def reset_user_password_admin(
    user_id: int,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """重置用户密码为 123456（管理用，不允许重置保留账号）"""
    _verify_admin_panel_password(x_admin_password)
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if user.username in HIDDEN_USERS:
        raise HTTPException(status_code=403, detail="无法操作此用户")

    user.password_hash = _hash_password("123456")
    user.password_plain = "123456"
    db.commit()
    return {"message": "密码已重置为 123456"}


@app.post("/api/admin/users/{user_id}/impersonate")
async def impersonate_user_admin(
    user_id: int,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    _verify_admin_panel_password(x_admin_password)
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if user.username in HIDDEN_USERS:
        raise HTTPException(status_code=403, detail="该用户不允许免密登录")

    return {
        "id": user.id,
        "username": user.username,
        "token": user.token,
        "created_at": user.created_at,
    }


@app.get("/api/dashboard/tasks")
async def list_dashboard_tasks(
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    creator_username: Optional[str] = None,
    keyword: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    size: int = 100,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    _verify_admin_panel_password(x_admin_password)
    normalized_page = max(1, int(page or 1))
    normalized_size = max(1, min(int(size or 100), 500))

    filtered_query = _apply_dashboard_query_filters(
        db.query(models.DashboardTaskLog),
        status=status,
        task_type=task_type,
        creator_username=creator_username,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
    )

    stale_rows = filtered_query.filter(
        models.DashboardTaskLog.task_type == "simple_storyboard",
        models.DashboardTaskLog.status == "submitting",
    ).all()
    for stale_row in stale_rows:
        _mark_dashboard_simple_storyboard_timeout_if_needed(stale_row, db)

    filtered_query = _apply_dashboard_query_filters(
        db.query(models.DashboardTaskLog),
        status=status,
        task_type=task_type,
        creator_username=creator_username,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
    )

    total = filtered_query.count()
    rows = filtered_query.order_by(
        models.DashboardTaskLog.created_at.desc(),
        models.DashboardTaskLog.id.desc(),
    ).offset((normalized_page - 1) * normalized_size).limit(normalized_size).all()

    status_rows = db.query(models.DashboardTaskLog.status).distinct().all()
    task_type_rows = db.query(models.DashboardTaskLog.task_type).distinct().all()

    return {
        "items": [_serialize_dashboard_task(row) for row in rows],
        "total": int(total or 0),
        "page": normalized_page,
        "size": normalized_size,
        "status_options": sorted({str(item[0] or "").strip() for item in status_rows if str(item[0] or "").strip()}),
        "task_type_options": sorted({str(item[0] or "").strip() for item in task_type_rows if str(item[0] or "").strip()}),
        "status_labels": DASHBOARD_STATUS_LABELS,
        "task_type_labels": DASHBOARD_TASK_TYPE_LABELS,
    }


@app.get("/api/dashboard/tasks/{task_id}")
async def get_dashboard_task_detail(
    task_id: int,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    _verify_admin_panel_password(x_admin_password)
    row = db.query(models.DashboardTaskLog).filter(models.DashboardTaskLog.id == task_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    _mark_dashboard_simple_storyboard_timeout_if_needed(row, db)
    return _serialize_dashboard_task(row, include_payloads=True)


@app.post("/api/dashboard/tasks/{task_id}/query-status")
async def query_dashboard_task_status(
    task_id: int,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    _verify_admin_panel_password(x_admin_password)
    row = db.query(models.DashboardTaskLog).filter(models.DashboardTaskLog.id == task_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        return query_dashboard_task(row)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/dashboard/tasks/{task_id}")
async def delete_dashboard_task(
    task_id: int,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    _verify_admin_panel_password(x_admin_password)
    row = db.query(models.DashboardTaskLog).filter(models.DashboardTaskLog.id == task_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    db.delete(row)
    db.commit()
    return {"message": "任务记录已删除", "deleted_count": 1}


@app.post("/api/dashboard/tasks/bulk-delete")
async def bulk_delete_dashboard_tasks(
    request: DashboardBulkDeleteRequest,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    _verify_admin_panel_password(x_admin_password)
    query = db.query(models.DashboardTaskLog)

    normalized_ids = [int(item) for item in (request.ids or []) if str(item).strip()]
    if normalized_ids:
        query = query.filter(models.DashboardTaskLog.id.in_(normalized_ids))
    else:
        query = _apply_dashboard_query_filters(
            query,
            status=request.status,
            task_type=request.task_type,
            creator_username=request.creator_username,
            keyword=request.keyword,
            date_from=request.date_from,
            date_to=request.date_to,
        )
        has_filters = any([
            request.status,
            request.task_type,
            request.creator_username,
            request.keyword,
            request.date_from,
            request.date_to,
        ])
        if not has_filters and not request.delete_all:
            raise HTTPException(status_code=400, detail="未指定删除条件")

    deleted_count = query.delete(synchronize_session=False)
    db.commit()
    return {"message": "任务记录已删除", "deleted_count": int(deleted_count or 0)}



# ==================== 模型选择 API ====================

FUNCTION_MODEL_DEFAULTS = [
    {"function_key": "detailed_storyboard_s1",  "function_name": "详细分镜生成（Stage 1 初始分析）"},
    {"function_key": "detailed_storyboard_s2",  "function_name": "详细分镜生成（Stage 2 主体提示词）"},
    {"function_key": "video_prompt",            "function_name": "Sora 视频提示词生成"},
    {"function_key": "opening",                 "function_name": "精彩开头生成"},
    {"function_key": "narration",               "function_name": "旁白/解说剧转换"},
    {"function_key": "managed_prompt_optimize", "function_name": "托管重试提示词优化"},
    {"function_key": "subject_prompt",          "function_name": "主体提示词生成"},
]

OBSOLETE_FUNCTION_MODEL_KEYS = {"simple_storyboard"}

LEGACY_TEXT_PROVIDER_KEYS = {"", "openrouter", "yyds"}
LEGACY_TEXT_MODEL_VALUES = {
    "",
    "google/gemini-3.1-pro-preview",
    "google/gemini-3-pro-preview",
    "gemini-3.1-pro-preview",
    "gemini-3.1-pro-high",
    "gemini-3.0-pro",
    "gemini_pro_preview",
    "gemini_pro_high",
    "gemini_pro_3_0",
}


def _get_function_model_default_selection(function_key: str) -> Dict[str, Optional[str]]:
    _ = str(function_key or "").strip()
    return {
        "provider_key": RELAY_PROVIDER_KEY,
        "model_key": DEFAULT_TEXT_MODEL_ID,
        "model_id": DEFAULT_TEXT_MODEL_ID,
    }


def _normalize_function_model_id(row: models.FunctionModelConfig) -> str:
    provider_key = str(getattr(row, "provider_key", None) or "").strip().lower()
    model_id = str(getattr(row, "model_id", None) or "").strip()
    model_key = str(getattr(row, "model_key", None) or "").strip()

    if provider_key and provider_key != RELAY_PROVIDER_KEY:
        return DEFAULT_TEXT_MODEL_ID

    candidate = model_id or model_key
    if not candidate or candidate in LEGACY_TEXT_MODEL_VALUES:
        return DEFAULT_TEXT_MODEL_ID
    return candidate


def _ensure_function_model_configs(db):
    """确保所有功能配置行都存在，并统一迁移到 model_id-only 结构。"""
    if OBSOLETE_FUNCTION_MODEL_KEYS:
        db.query(models.FunctionModelConfig).filter(
            models.FunctionModelConfig.function_key.in_(tuple(OBSOLETE_FUNCTION_MODEL_KEYS))
        ).delete(synchronize_session=False)
    for item in FUNCTION_MODEL_DEFAULTS:
        default_selection = _get_function_model_default_selection(item["function_key"])
        row = db.query(models.FunctionModelConfig).filter(
            models.FunctionModelConfig.function_key == item["function_key"]
        ).first()
        if not row:
            db.add(models.FunctionModelConfig(
                function_key=item["function_key"],
                function_name=item["function_name"],
                provider_key=default_selection["provider_key"],
                model_key=default_selection["model_key"],
                model_id=default_selection["model_id"]
            ))
            continue

        row.function_name = item["function_name"]
        normalized_model_id = _normalize_function_model_id(row)
        row.provider_key = RELAY_PROVIDER_KEY
        row.model_key = normalized_model_id
        row.model_id = normalized_model_id
    db.commit()


def _serialize_function_model_config(row: models.FunctionModelConfig, db: Session) -> Dict[str, Any]:
    resolved = resolve_ai_model_option(RELAY_PROVIDER_KEY, getattr(row, "model_id", None), db=db)
    return {
        "function_key": row.function_key,
        "function_name": row.function_name,
        "model_id": str(getattr(row, "model_id", None) or "").strip() or DEFAULT_TEXT_MODEL_ID,
        "resolved_model_key": resolved["model_key"],
        "resolved_model_id": resolved["model_id"],
        "resolved_model_label": resolved["label"],
    }


@app.get("/api/admin/model-configs")
async def get_model_configs(
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """返回模型选择页需要的缓存模型与功能分配。"""
    _verify_admin_panel_password(x_admin_password)
    _ensure_function_model_configs(db)
    rows = db.query(models.FunctionModelConfig).order_by(
        models.FunctionModelConfig.id.asc()
    ).all()
    cache_payload = get_cached_models_payload(db)
    return {
        "default_model": DEFAULT_TEXT_MODEL_ID,
        "models": cache_payload.get("models", []),
        "last_synced_at": cache_payload.get("last_synced_at"),
        "configs": [
            _serialize_function_model_config(r, db)
            for r in rows
        ]
    }


class UpdateModelConfigRequest(BaseModel):
    model_id: str = ""


@app.post("/api/admin/model-configs/sync-models")
async def sync_model_cache(
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    _verify_admin_panel_password(x_admin_password)
    sync_result = sync_models_from_upstream(db)
    db.commit()
    cache_payload = get_cached_models_payload(db)
    return {
        "message": "模型缓存已同步",
        "count": int(sync_result.get("count") or 0),
        "last_synced_at": cache_payload.get("last_synced_at"),
        "models": cache_payload.get("models", []),
    }


def _parse_optional_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    normalized = raw_value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


class BillingPriceRuleRequest(BaseModel):
    rule_name: str
    category: str
    stage: str = ""
    provider: str = ""
    model_name: str = ""
    resolution: str = ""
    billing_mode: str
    unit_price_rmb: float
    is_active: bool = True
    priority: int = 0
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None


@app.put("/api/admin/model-config/{function_key}")
async def update_model_config(
    function_key: str,
    request: UpdateModelConfigRequest,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """更新某功能的 model 分配。"""
    _verify_admin_panel_password(x_admin_password)
    _ensure_function_model_configs(db)
    row = db.query(models.FunctionModelConfig).filter(
        models.FunctionModelConfig.function_key == function_key
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="功能配置不存在")

    explicit_model_id = str(request.model_id or "").strip() or DEFAULT_TEXT_MODEL_ID
    resolved = resolve_ai_model_option(RELAY_PROVIDER_KEY, explicit_model_id, db=db)

    row.provider_key = RELAY_PROVIDER_KEY
    row.model_key = resolved["model_id"]
    row.model_id = resolved["model_id"]
    db.commit()
    db.refresh(row)
    return _serialize_function_model_config(row, db)


@app.get("/api/billing/users")
async def get_billing_users(
    month: Optional[str] = Query(None),
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db)
):
    """返回所有有账单的用户汇总。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    return {
        "users": billing_service.get_billing_user_list(db, month=month)
    }


@app.get("/api/billing/episodes")
async def get_billing_episodes(
    group_by: str = Query("script"),
    user_id: Optional[int] = Query(None),
    script_id: Optional[int] = Query(None),
    month: Optional[str] = Query(None),
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db)
):
    """返回管理员视图下的剧集汇总。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    episodes = billing_service.get_billing_episode_list(
        db,
        user_id=user_id,
        script_id=script_id,
        month=month,
    )
    return {
        "group_by": str(group_by or "script"),
        "episodes": episodes,
    }


@app.get("/api/billing/scripts")
async def get_billing_scripts(
    group_by: str = Query("script"),
    user_id: Optional[int] = Query(None),
    month: Optional[str] = Query(None),
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db)
):
    """返回管理员视图下的剧本汇总。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    return {
        "group_by": str(group_by or "script"),
        "scripts": billing_service.get_billing_script_list(db, user_id=user_id, month=month)
    }


@app.get("/api/billing/scripts/{script_id}")
async def get_billing_script_detail(
    script_id: int,
    month: Optional[str] = Query(None),
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db)
):
    """返回管理员视图下某个剧本的计费详情。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    detail = billing_service.get_script_billing_detail(
        db,
        script_id=int(script_id),
        month=month,
    )
    if not detail:
        raise HTTPException(status_code=404, detail="计费剧本不存在")
    return detail


@app.get("/api/billing/episodes/{episode_id}")
async def get_billing_episode_detail(
    episode_id: int,
    month: Optional[str] = Query(None),
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db)
):
    """返回管理员视图下某个剧集的计费详情。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    detail = billing_service.get_episode_billing_detail(
        db,
        episode_id=int(episode_id),
        month=month,
    )
    if not detail:
        raise HTTPException(status_code=404, detail="计费剧集不存在")
    return detail


@app.get("/api/billing/reimbursement-export")
async def get_billing_reimbursement_export(
    group_by: str = Query("script"),
    month: Optional[str] = Query(None),
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db)
):
    """返回报销用途的月度汇总数据。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    normalized_group_by = "user" if str(group_by or "").strip().lower() == "user" else "script"
    return {
        "group_by": normalized_group_by,
        "title": "按用户月度报销汇总" if normalized_group_by == "user" else "按剧本月度报销汇总",
        "month": month,
        "rows": billing_service.get_billing_reimbursement_rows(db, group_by=normalized_group_by, month=month),
    }


@app.get("/api/billing/rules")
async def get_billing_rules(
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db)
):
    """返回计费价格规则。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    return {
        "rules": billing_service.get_price_rules(db)
    }


@app.post("/api/billing/rules")
async def create_billing_rule(
    request: BillingPriceRuleRequest,
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db)
):
    """新增计费价格规则。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    try:
        row = billing_service.create_price_rule(
            db,
            rule_name=request.rule_name,
            category=request.category,
            stage=request.stage,
            provider=request.provider,
            model_name=request.model_name,
            resolution=request.resolution,
            billing_mode=request.billing_mode,
            unit_price_rmb=request.unit_price_rmb,
            is_active=request.is_active,
            priority=request.priority,
            effective_from=_parse_optional_iso_datetime(request.effective_from),
            effective_to=_parse_optional_iso_datetime(request.effective_to),
        )
        db.commit()
        db.refresh(row)
        return billing_service.serialize_price_rule(row)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"创建计费规则失败: {str(e)}")


@app.put("/api/billing/rules/{rule_id}")
async def update_billing_rule(
    rule_id: int,
    request: BillingPriceRuleRequest,
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db)
):
    """更新计费价格规则。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    try:
        row = billing_service.update_price_rule(
            db,
            rule_id=int(rule_id),
            rule_name=request.rule_name,
            category=request.category,
            stage=request.stage,
            provider=request.provider,
            model_name=request.model_name,
            resolution=request.resolution,
            billing_mode=request.billing_mode,
            unit_price_rmb=request.unit_price_rmb,
            is_active=request.is_active,
            priority=request.priority,
            effective_from=_parse_optional_iso_datetime(request.effective_from),
            effective_to=_parse_optional_iso_datetime(request.effective_to),
        )
        if not row:
            raise HTTPException(status_code=404, detail="计费规则不存在")
        db.commit()
        db.refresh(row)
        return billing_service.serialize_price_rule(row)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"更新计费规则失败: {str(e)}")

# ==================== Sora准则管理API ====================

# ==================== 视频生成准则API（统一管理Sora和Grok准则） ====================

@app.get("/api/video-generation-rules")
async def get_video_generation_rules(db: Session = Depends(get_db)):
    """获取全局视频生成准则（Sora准则和Grok准则）"""
    try:
        sora_setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "sora_rule").first()
        grok_setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "grok_rule").first()

        sora_rule = sora_setting.value if sora_setting else "准则：不要出现字幕"
        grok_rule = grok_setting.value if grok_setting else GROK_RULE_DEFAULT

        return {
            "sora_rule": sora_rule,
            "grok_rule": grok_rule
        }
    except Exception as e:
        # 如果表不存在（迁移前），返回默认值
        return {
            "sora_rule": "准则：不要出现字幕",
            "grok_rule": GROK_RULE_DEFAULT
        }

@app.put("/api/video-generation-rules")
async def update_video_generation_rules(request: dict, db: Session = Depends(get_db)):
    """更新全局视频生成准则（Sora准则和Grok准则）"""
    sora_rule = request.get("sora_rule", "准则：不要出现字幕")
    grok_rule = request.get("grok_rule", GROK_RULE_DEFAULT)

    try:
        # 更新或创建 Sora 准则
        sora_setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "sora_rule").first()
        if sora_setting:
            sora_setting.value = sora_rule
            sora_setting.updated_at = datetime.utcnow()
        else:
            sora_setting = models.GlobalSettings(
                key="sora_rule",
                value=sora_rule
            )
            db.add(sora_setting)

        # 更新或创建 Grok 准则
        grok_setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "grok_rule").first()
        if grok_setting:
            grok_setting.value = grok_rule
            grok_setting.updated_at = datetime.utcnow()
        else:
            grok_setting = models.GlobalSettings(
                key="grok_rule",
                value=grok_rule
            )
            db.add(grok_setting)

        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")

    return {
        "message": "视频生成准则更新成功",
        "sora_rule": sora_rule,
        "grok_rule": grok_rule
    }

@app.get("/api/video-model-pricing")
async def get_video_model_pricing(provider: str = "yijia", db: Session = Depends(get_db)):
    """Get video model pricing from database.

    Returns pricing grouped by front-end model name.
    sora-2-pro prices are merged into sora-2 (since 25s auto-maps to sora-2-pro).
    """
    try:
        pricing_records = db.query(models.VideoModelPricing).filter(
            models.VideoModelPricing.provider == provider
        ).all()

        # Group by front-end model name
        # sora-2-pro -> merge into sora-2 (front-end only knows sora-2)
        pricing_map = {}
        updated_at = None

        for record in pricing_records:
            # Map sora-2-pro back to sora-2 for front-end display
            display_model = record.model_name
            if display_model == "sora-2-pro":
                display_model = "sora-2"

            if display_model not in pricing_map:
                pricing_map[display_model] = {}

            key = f"{record.duration}_{record.aspect_ratio}"
            pricing_map[display_model][key] = {
                "duration": record.duration,
                "aspect_ratio": record.aspect_ratio,
                "price_yuan": record.price_yuan
            }

            if record.updated_at:
                updated_at = record.updated_at

        return {
            "pricing": pricing_map,
            "provider": provider,
            "last_updated": updated_at.isoformat() if updated_at else None
        }
    except Exception as e:
        return {
            "pricing": {},
            "last_updated": None,
            "error": str(e)
        }


@app.get("/api/video/providers/{provider}/accounts")
async def get_video_provider_accounts(
    provider: str,
    user: models.User = Depends(get_current_user),
):
    _ = user
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider != "moti":
        raise HTTPException(status_code=404, detail="不支持该视频服务商账号列表")
    return get_cached_video_provider_accounts(normalized_provider)


@app.get("/api/video/provider-stats")
def get_video_provider_stats(
    user: models.User = Depends(get_current_user),
):
    _ = user
    try:
        response = requests.get(
            get_video_provider_stats_url(),
            headers=get_video_api_headers(),
            timeout=5,
        )
        try:
            payload = response.json()
        except Exception:
            payload = {"raw_text": getattr(response, "text", "")}
        if int(getattr(response, "status_code", 0) or 0) >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {payload}")
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return {"providers": payload}
        return {"providers": []}
    except Exception as exc:
        print(f"[video-provider-stats] refresh failed: {str(exc)}")
        return {"providers": [], "error": str(exc)}


@app.get("/api/video/quota/{username}")
def get_video_quota(
    username: str,
    user: models.User = Depends(get_current_user),
):
    _ = user
    encoded_username = quote(str(username or "").strip(), safe="")
    if not encoded_username:
        return {}
    base_url = get_required_video_api_base_url().rstrip("/")
    try:
        response = requests.get(
            f"{base_url}/quota/{encoded_username}",
            headers=get_video_api_headers(),
            timeout=5,
        )
        try:
            payload = response.json()
        except Exception:
            payload = {"raw_text": getattr(response, "text", "")}
        if int(getattr(response, "status_code", 0) or 0) >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {payload}")
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        print(f"[video-quota] refresh failed for {encoded_username}: {str(exc)}")
        return {}

# ==================== 兼容旧版本 Sora准则 API ====================

@app.get("/api/sora-rule")
async def get_sora_rule(db: Session = Depends(get_db)):
    """获取全局Sora提示词准则（兼容旧版本）"""
    try:
        setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "sora_rule").first()

        if setting:
            return {"sora_rule": setting.value}
        else:
            # 如果不存在，返回默认值
            return {"sora_rule": "准则：不要出现字幕"}
    except Exception as e:
        # 如果表不存在（迁移前），返回默认值
        return {"sora_rule": "准则：不要出现字幕"}

@app.put("/api/sora-rule")
async def update_sora_rule(request: dict, db: Session = Depends(get_db)):
    """更新全局Sora提示词准则（兼容旧版本，实际调用新的统一API）"""
    sora_rule = request.get("sora_rule", "准则：不要出现字幕")

    try:
        setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "sora_rule").first()

        if setting:
            # 更新现有记录
            setting.value = sora_rule
            setting.updated_at = datetime.utcnow()
        else:
            # 创建新记录
            setting = models.GlobalSettings(
                key="sora_rule",
                value=sora_rule
            )
            db.add(setting)

        db.commit()
        db.refresh(setting)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")

    return {"message": "Sora准则更新成功", "sora_rule": sora_rule}

# 兼容旧版本 API（保留，但内部使用全局配置）
@app.get("/api/users/{user_id}/sora-rule")
async def get_sora_rule_legacy(user_id: int, db: Session = Depends(get_db)):
    """获取Sora提示词准则（兼容旧版本）"""
    return await get_sora_rule(db)

@app.put("/api/users/{user_id}/sora-rule")
async def update_sora_rule_legacy(user_id: int, request: dict, db: Session = Depends(get_db)):
    """更新Sora提示词准则（兼容旧版本）"""
    return await update_sora_rule(request, db)

# ==================== 全局提示词模板API ====================

@app.get("/api/global-settings/prompt_template")
async def get_prompt_template(db: Session = Depends(get_db)):
    """获取全局提示词模板"""
    try:
        setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "prompt_template").first()

        if setting:
            return {"value": setting.value}
        else:
            # 如果不存在，返回默认值
            default_value = _default_storyboard_video_prompt_template()
            return {"value": default_value}
    except Exception as e:
        # 如果表不存在（迁移前），返回默认值
        default_value = _default_storyboard_video_prompt_template()
        return {"value": default_value}

@app.put("/api/global-settings/prompt_template")
async def update_prompt_template(request: dict, db: Session = Depends(get_db)):
    """更新全局提示词模板"""
    value = request.get("value", "")

    try:
        setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "prompt_template").first()

        if setting:
            # 更新现有记录
            setting.value = value
            setting.updated_at = datetime.utcnow()
        else:
            # 创建新记录
            setting = models.GlobalSettings(
                key="prompt_template",
                value=value
            )
            db.add(setting)

        db.commit()
        db.refresh(setting)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")

    return {"message": "提示词模板更新成功", "value": value}

@app.get("/api/global-settings/narration_conversion_template")
async def get_narration_conversion_template(db: Session = Depends(get_db)):
    """获取文本转解说剧提示词模板"""
    try:
        setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "narration_conversion_template").first()

        if setting:
            return {"value": setting.value}
        else:
            # 如果不存在，返回默认值
            default_value = """1 读取文本文件并理解
 2 把故事改写成解说故事的形式，改写过程如下：
    （1）找到故事的第一主角
    （2）把故事用主角自述的方式讲出来，以第一人称视角讲述
    （3）保留少量精彩的对话即可
    （4）保留一些场景描述
    （5）文字风格要幽默"""
            return {"value": default_value}
    except Exception as e:
        # 如果表不存在（迁移前），返回默认值
        default_value = """1 读取文本文件并理解
 2 把故事改写成解说故事的形式，改写过程如下：
    （1）找到故事的第一主角
    （2）把故事用主角自述的方式讲出来，以第一人称视角讲述
    （3）保留少量精彩的对话即可
    （4）保留一些场景描述
    （5）文字风格要幽默"""
        return {"value": default_value}

@app.put("/api/global-settings/narration_conversion_template")
async def update_narration_conversion_template(request: dict, db: Session = Depends(get_db)):
    """更新文本转解说剧提示词模板"""
    value = request.get("value", "")

    try:
        setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "narration_conversion_template").first()

        if setting:
            # 更新现有记录
            setting.value = value
            setting.updated_at = datetime.utcnow()
        else:
            # 创建新记录
            setting = models.GlobalSettings(
                key="narration_conversion_template",
                value=value
            )
            db.add(setting)

        db.commit()
        db.refresh(setting)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")

    return {"message": "文本转解说剧提示词模板更新成功", "value": value}

@app.get("/api/global-settings/opening_generation_template")
async def get_opening_generation_template(db: Session = Depends(get_db)):
    """获取精彩开头生成提示词模板"""
    default_value = "我想把这个片段做成一个短视频，需要一个精彩吸引人的开头，请你帮我写一个开头"

    try:
        setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "opening_generation_template").first()

        if setting and setting.value and setting.value.strip():
            return {"value": setting.value}
        else:
            # 如果不存在或值为空，返回默认值
            return {"value": default_value}
    except Exception as e:
        # 如果表不存在（迁移前），返回默认值
        return {"value": default_value}

@app.put("/api/global-settings/opening_generation_template")
async def update_opening_generation_template(request: dict, db: Session = Depends(get_db)):
    """更新精彩开头生成提示词模板"""
    value = request.get("value", "")

    try:
        setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "opening_generation_template").first()

        if setting:
            # 更新现有记录
            setting.value = value
            setting.updated_at = datetime.utcnow()
        else:
            # 创建新记录
            setting = models.GlobalSettings(
                key="opening_generation_template",
                value=value
            )
            db.add(setting)

        db.commit()
        db.refresh(setting)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")

    return {"message": "精彩开头生成提示词模板更新成功", "value": value}


# ==================== 提示词管理API ====================

class PromptConfigResponse(BaseModel):
    id: int
    key: str
    name: str
    description: str
    content: str
    is_active: bool
    updated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class PromptConfigUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    is_active: Optional[bool] = None


PROMPT_CONFIG_DISPLAY_OVERRIDES = {
    "stage1_initial_storyboard": {
        "name": "阶段1：初步分镜生成",
        "sort_order": 100,
    },
    "detailed_storyboard_content_analysis": {
        "name": "阶段2-1：详细分镜内容分析",
        "sort_order": 110,
    },
    "stage2_refine_shot": {
        "name": "阶段2-2：详细分镜提取主体与去重",
        "sort_order": 120,
    },
}


def _prompt_config_sort_key(config: models.PromptConfig):
    override = PROMPT_CONFIG_DISPLAY_OVERRIDES.get(str(getattr(config, "key", "") or ""), {})
    created_at = getattr(config, "created_at", None) or datetime.min
    return (
        int(override.get("sort_order", 1000)),
        created_at,
        int(getattr(config, "id", 0) or 0),
    )


def _serialize_prompt_config(config: models.PromptConfig) -> dict:
    override = PROMPT_CONFIG_DISPLAY_OVERRIDES.get(str(getattr(config, "key", "") or ""), {})
    return {
        "id": config.id,
        "key": config.key,
        "name": str(override.get("name", config.name or "")),
        "description": str(config.description or ""),
        "content": str(config.content or ""),
        "is_active": bool(config.is_active),
        "updated_at": config.updated_at,
        "created_at": config.created_at,
    }


@app.get("/api/prompt-configs", response_model=List[PromptConfigResponse])
async def get_prompt_configs(db: Session = Depends(get_db)):
    """获取所有提示词配置"""
    configs = db.query(models.PromptConfig).all()
    configs = sorted(configs, key=_prompt_config_sort_key)
    return [_serialize_prompt_config(config) for config in configs]

@app.get("/api/prompt-configs/{config_id}", response_model=PromptConfigResponse)
async def get_prompt_config(config_id: int, db: Session = Depends(get_db)):
    """获取单个提示词配置"""
    config = db.query(models.PromptConfig).filter(models.PromptConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    return _serialize_prompt_config(config)

@app.put("/api/prompt-configs/{config_id}", response_model=PromptConfigResponse)
async def update_prompt_config(
    config_id: int,
    update_data: PromptConfigUpdate,
    db: Session = Depends(get_db)
):
    """更新提示词配置"""
    config = db.query(models.PromptConfig).filter(models.PromptConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    if update_data.name is not None:
        config.name = update_data.name
    if update_data.description is not None:
        config.description = update_data.description
    if update_data.content is not None:
        config.content = update_data.content
    if update_data.is_active is not None:
        config.is_active = update_data.is_active

    config.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(config)
    return _serialize_prompt_config(config)

@app.post("/api/prompt-configs/{config_id}/reset")
async def reset_prompt_config(config_id: int, db: Session = Depends(get_db)):
    """重置提示词配置为默认值"""
    config = db.query(models.PromptConfig).filter(models.PromptConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    # 从默认提示词中找到对应的配置
    default_prompts_map = {
        prompt["key"]: prompt["content"] for prompt in DEFAULT_PROMPTS
    }

    if config.key in default_prompts_map:
        config.content = default_prompts_map[config.key]
        config.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(config)
        return {"message": "已重置为默认值", "config": _serialize_prompt_config(config)}
    else:
        raise HTTPException(status_code=400, detail="无法重置：未找到默认配置")

# ==================== 时长配置模板API ====================

class ShotDurationTemplateResponse(BaseModel):
    id: int
    duration: int
    shot_count_min: int
    shot_count_max: int
    time_segments: int
    simple_storyboard_config: Dict[str, Any]
    video_prompt_rule: str
    large_shot_prompt_rule: str
    is_default: bool
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


def _serialize_shot_duration_template(template: models.ShotDurationTemplate) -> Dict[str, Any]:
    duration = int(getattr(template, "duration", 15) or 15)
    raw_config_text = str(getattr(template, "simple_storyboard_config_json", "") or "").strip()
    raw_config = None
    if raw_config_text:
        try:
            raw_config = json.loads(raw_config_text)
        except Exception:
            raw_config = None
    config = normalize_rule_config(raw_config, duration)
    return {
        "id": template.id,
        "duration": duration,
        "shot_count_min": template.shot_count_min,
        "shot_count_max": template.shot_count_max,
        "time_segments": template.time_segments,
        "simple_storyboard_config": config.to_dict(),
        "video_prompt_rule": template.video_prompt_rule,
        "large_shot_prompt_rule": getattr(template, "large_shot_prompt_rule", "") or "",
        "is_default": template.is_default,
        "created_at": template.created_at.isoformat() if template.created_at else None,
    }


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


def _normalize_simple_storyboard_config_payload(
    raw_value: Any,
    duration: int,
) -> Dict[str, Any]:
    if not isinstance(raw_value, dict):
        raise HTTPException(status_code=400, detail="simple_storyboard_config 必须为对象")
    try:
        return normalize_rule_config(raw_value, duration).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.get("/api/shot-duration-templates")
async def get_shot_duration_templates(db: Session = Depends(get_db)):
    """获取所有时长配置模板"""
    templates = db.query(models.ShotDurationTemplate).order_by(models.ShotDurationTemplate.duration.asc()).all()
    return [_serialize_shot_duration_template(t) for t in templates]

@app.get("/api/shot-duration-templates/{duration}")
async def get_shot_duration_template(duration: int, db: Session = Depends(get_db)):
    """获取指定时长的配置模板"""
    template = db.query(models.ShotDurationTemplate).filter(
        models.ShotDurationTemplate.duration == duration
    ).first()
    if not template:
        raise HTTPException(status_code=404, detail="该时长的模板不存在")
    return _serialize_shot_duration_template(template)

@app.put("/api/shot-duration-templates/{duration}")
async def update_shot_duration_template(
    duration: int,
    update_data: dict,
    db: Session = Depends(get_db)
):
    """更新时长配置模板"""
    template = db.query(models.ShotDurationTemplate).filter(
        models.ShotDurationTemplate.duration == duration
    ).first()
    if not template:
        raise HTTPException(status_code=404, detail="该时长的模板不存在")

    # 更新允许的字段
    if "shot_count_min" in update_data:
        template.shot_count_min = update_data["shot_count_min"]
    if "shot_count_max" in update_data:
        template.shot_count_max = update_data["shot_count_max"]
    if "simple_storyboard_config" in update_data:
        template.simple_storyboard_config_json = json.dumps(
            _normalize_simple_storyboard_config_payload(update_data["simple_storyboard_config"], duration),
            ensure_ascii=False,
        )
    if "video_prompt_rule" in update_data:
        template.video_prompt_rule = update_data["video_prompt_rule"]
    if "large_shot_prompt_rule" in update_data:
        template.large_shot_prompt_rule = update_data["large_shot_prompt_rule"]

    db.commit()
    db.refresh(template)
    return _serialize_shot_duration_template(template)

# ==================== 剧本管理API ====================

class ScriptCreate(BaseModel):
    name: str
    video_prompt_template: Optional[str] = ""
    style_template: Optional[str] = ""

class ScriptUpdate(BaseModel):
    name: Optional[str] = None
    sora_prompt_style: Optional[str] = None
    video_prompt_template: Optional[str] = None
    style_template: Optional[str] = None
    narration_template: Optional[str] = None

class ScriptResponse(BaseModel):
    id: int
    user_id: int
    name: str
    sora_prompt_style: str = ""
    video_prompt_template: str = ""
    style_template: str = ""
    narration_template: str = ""
    created_at: datetime

    class Config:
        from_attributes = True

@app.post("/api/scripts", response_model=ScriptResponse)
async def create_script(
    script: ScriptCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建剧本"""
    new_script = models.Script(
        user_id=user.id,
        name=script.name,
        video_prompt_template=script.video_prompt_template or "",
        style_template=script.style_template or ""
    )
    db.add(new_script)
    db.commit()
    db.refresh(new_script)

    # 不再在创建剧本时创建主体库，改为在创建episode时创建

    return new_script

@app.get("/api/scripts/my", response_model=List[ScriptResponse])
async def get_my_scripts(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取我的剧本列表"""
    scripts = db.query(models.Script).filter(
        models.Script.user_id == user.id
    ).order_by(models.Script.created_at.desc()).all()

    return scripts

@app.get("/api/scripts/{script_id}", response_model=ScriptResponse)
async def get_script(
    script_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取剧本详情"""
    script = db.query(models.Script).filter(models.Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="剧本不存在")
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    return script

@app.put("/api/scripts/{script_id}", response_model=ScriptResponse)
async def update_script(
    script_id: int,
    script_data: ScriptUpdate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新剧本信息"""
    script = db.query(models.Script).filter(models.Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="剧本不存在")
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    if script_data.name is not None:
        script.name = script_data.name
    if script_data.sora_prompt_style is not None:
        script.sora_prompt_style = script_data.sora_prompt_style
    if script_data.video_prompt_template is not None:
        script.video_prompt_template = script_data.video_prompt_template
    if script_data.style_template is not None:
        script.style_template = script_data.style_template
    if script_data.narration_template is not None:
        script.narration_template = script_data.narration_template

    db.commit()
    db.refresh(script)
    return script

@app.delete("/api/scripts/{script_id}")
async def delete_script(
    script_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除剧本（级联删除所有关联数据）"""
    script = db.query(models.Script).filter(models.Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="剧本不存在")
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    billing_service.ensure_deleted_billing_name_snapshots(
        db,
        script_id=int(script.id),
        username=str(getattr(user, "username", "") or ""),
        script_name=str(getattr(script, "name", "") or ""),
    )

    episode_ids = [
        episode_id
        for episode_id, in db.query(models.Episode.id).filter(
            models.Episode.script_id == int(script.id)
        ).all()
    ]
    episode_cleanup_stats = _clear_episode_dependencies(episode_ids, db)

    print(
        "[剧本删除清理] "
        f"script_id={script.id} episodes={len(episode_ids)} "
        f"simple_batches={episode_cleanup_stats['deleted_simple_storyboard_batches']} "
        f"managed_tasks={episode_cleanup_stats['deleted_managed_tasks']} "
        f"managed_sessions={episode_cleanup_stats['deleted_managed_sessions']} "
        f"voiceover_tts_tasks={episode_cleanup_stats['deleted_voiceover_tts_tasks']} "
        f"unlinked_libraries={episode_cleanup_stats['unlinked_libraries']}"
    )

    # 删除剧本（ORM 级联删除脚本/剧集/镜头等，非 ORM 级联依赖已提前清理）
    db.delete(script)
    db.commit()

    return {"message": "剧本删除成功", "script_id": script_id}

# 复制剧本给指定用户
class CopyScriptRequest(BaseModel):
    user_ids: List[int]  # 要复制给的用户ID列表

@app.post("/api/scripts/{script_id}/copy")
async def copy_script(
    script_id: int,
    request: CopyScriptRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """复制剧本给指定用户（深度复制）"""
    # 验证源剧本存在且有权限
    source_script = db.query(models.Script).filter(models.Script.id == script_id).first()
    if not source_script:
        raise HTTPException(status_code=404, detail="剧本不存在")
    if source_script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 验证目标用户存在
    if not request.user_ids:
        raise HTTPException(status_code=400, detail="请至少选择一个用户")

    valid_users = db.query(models.User).filter(models.User.id.in_(request.user_ids)).all()
    if len(valid_users) != len(request.user_ids):
        raise HTTPException(status_code=400, detail="部分用户不存在")

    success_count = 0
    failed_users = []

    for target_user in valid_users:
        try:
            # 1. 复制Script
            new_script = models.Script(
                user_id=target_user.id,
                name=source_script.name,
                sora_prompt_style=source_script.sora_prompt_style,
                video_prompt_template=source_script.video_prompt_template or "",
                style_template=source_script.style_template or "",
                narration_template=source_script.narration_template or "",
                voiceover_shared_data=source_script.voiceover_shared_data or ""
            )
            db.add(new_script)
            db.flush()  # 获取new_script.id

            # 2. 复制Episode（每个Episode有自己的主体库）
            source_episodes = db.query(models.Episode).filter(
                models.Episode.script_id == script_id
            ).all()

            for source_episode in source_episodes:
                # 复制Episode
                new_episode = models.Episode(
                    script_id=new_script.id,
                    name=source_episode.name,
                    content=source_episode.content,
                    video_prompt_template=getattr(source_episode, "video_prompt_template", "") or "",
                    batch_generating_prompts=False,
                    batch_generating_storyboard2_prompts=False,
                    shot_image_size=(source_episode.shot_image_size or "9:16"),
                    detail_images_model=_normalize_detail_images_model(
                        getattr(source_episode, "detail_images_model", None),
                        default_model="seedream-4.0"
                    ),
                    detail_images_provider=_resolve_episode_detail_images_provider(source_episode),
                    storyboard2_video_duration=_normalize_storyboard2_video_duration(
                        getattr(source_episode, "storyboard2_video_duration", None),
                        default_value=6
                    ),
                    storyboard2_image_cw=_normalize_storyboard2_image_cw(
                        getattr(source_episode, "storyboard2_image_cw", None),
                        default_value=50
                    ),
                    storyboard2_include_scene_references=bool(
                        getattr(source_episode, "storyboard2_include_scene_references", False)
                    ),
                    storyboard_video_model=_normalize_storyboard_video_model(
                        getattr(source_episode, "storyboard_video_model", None),
                        default_model=DEFAULT_STORYBOARD_VIDEO_MODEL
                    ),
                    storyboard_video_aspect_ratio=_normalize_storyboard_video_aspect_ratio(
                        getattr(source_episode, "storyboard_video_aspect_ratio", None),
                        model=_normalize_storyboard_video_model(
                            getattr(source_episode, "storyboard_video_model", None),
                            default_model=DEFAULT_STORYBOARD_VIDEO_MODEL
                        ),
                        default_ratio="16:9"
                    ),
                    storyboard_video_duration=_normalize_storyboard_video_duration(
                        getattr(source_episode, "storyboard_video_duration", None),
                        model=_normalize_storyboard_video_model(
                            getattr(source_episode, "storyboard_video_model", None),
                            default_model=DEFAULT_STORYBOARD_VIDEO_MODEL
                        ),
                        default_duration=15
                    ),
                    storyboard_video_resolution_name=_normalize_storyboard_video_resolution_name(
                        getattr(source_episode, "storyboard_video_resolution_name", None),
                        model=_normalize_storyboard_video_model(
                            getattr(source_episode, "storyboard_video_model", None),
                            default_model=DEFAULT_STORYBOARD_VIDEO_MODEL
                        ),
                        default_resolution="720p"
                    ),
                    storyboard_video_appoint_account=_normalize_storyboard_video_appoint_account(
                        getattr(source_episode, "storyboard_video_appoint_account", "")
                    )
                )
                db.add(new_episode)
                db.flush()  # 获取new_episode.id

                # 为新episode创建主体库并复制主体卡片
                source_library = db.query(models.StoryLibrary).filter(
                    models.StoryLibrary.episode_id == source_episode.id
                ).first()

                card_id_map = {}  # 旧ID -> 新ID映射
                shot_id_map = {}  # 旧shot_id -> 新shot_id映射（关键修复）

                if source_library:
                    # 创建新主体库
                    new_library = models.StoryLibrary(
                        user_id=target_user.id,
                        episode_id=new_episode.id,
                        name=source_library.name,
                        description=source_library.description
                    )
                    db.add(new_library)
                    db.flush()  # 获取new_library.id

                    # 复制SubjectCard（包括images和generated_images）
                    source_cards = db.query(models.SubjectCard).filter(
                        models.SubjectCard.library_id == source_library.id
                    ).all()
                    new_card_by_old_id = {}

                    for source_card in source_cards:
                        new_card = models.SubjectCard(
                            library_id=new_library.id,
                            name=source_card.name,
                            alias=source_card.alias,
                            card_type=source_card.card_type,
                            linked_card_id=None,
                            ai_prompt=source_card.ai_prompt,
                            role_personality=(getattr(source_card, "role_personality", "") or "")
                        )
                        db.add(new_card)
                        db.flush()
                        card_id_map[source_card.id] = new_card.id
                        new_card_by_old_id[source_card.id] = new_card

                        # 复制CardImage
                        for source_image in source_card.images:
                            new_image = models.CardImage(
                                card_id=new_card.id,
                                image_path=source_image.image_path,  # CDN URL直接复用
                                order=source_image.order
                            )
                            db.add(new_image)

                        # 复制GeneratedImage
                        for source_gen_img in source_card.generated_images:
                            new_gen_img = models.GeneratedImage(
                                card_id=new_card.id,
                                image_path=source_gen_img.image_path,  # CDN URL直接复用
                                model_name=source_gen_img.model_name,
                                is_reference=source_gen_img.is_reference,
                                task_id="",  # 清空task_id
                                status="completed"  # 已完成状态
                            )
                            db.add(new_gen_img)

                        # 复制声音素材
                        for source_audio in source_card.audios:
                            new_audio = models.SubjectCardAudio(
                                card_id=new_card.id,
                                audio_path=source_audio.audio_path,
                                file_name=source_audio.file_name,
                                duration_seconds=_safe_audio_duration_seconds(source_audio.duration_seconds),
                                is_reference=source_audio.is_reference
                            )
                            db.add(new_audio)

                    for source_card in source_cards:
                        source_linked_id = getattr(source_card, "linked_card_id", None)
                        if not source_linked_id:
                            continue
                        new_card = new_card_by_old_id.get(source_card.id)
                        mapped_linked_id = card_id_map.get(source_linked_id)
                        if new_card and mapped_linked_id:
                            new_card.linked_card_id = mapped_linked_id

                # 复制StoryboardShot（包括videos）
                source_shots = db.query(models.StoryboardShot).filter(
                    models.StoryboardShot.episode_id == source_episode.id
                ).all()

                for source_shot in source_shots:
                    # 更新selected_card_ids中的ID映射
                    selected_card_ids = source_shot.selected_card_ids or "[]"
                    try:
                        old_ids = json.loads(selected_card_ids)
                        new_ids = [card_id_map.get(old_id, old_id) for old_id in old_ids]
                        selected_card_ids = json.dumps(new_ids)
                    except Exception:
                        pass

                    new_shot = models.StoryboardShot(
                        episode_id=new_episode.id,
                        shot_number=source_shot.shot_number,
                        variant_index=source_shot.variant_index,
                        prompt_template=source_shot.prompt_template,
                        script_excerpt=source_shot.script_excerpt,
                        storyboard_video_prompt=source_shot.storyboard_video_prompt,
                        storyboard_audio_prompt=source_shot.storyboard_audio_prompt,
                        storyboard_dialogue=source_shot.storyboard_dialogue,
                        sora_prompt=source_shot.sora_prompt,
                        selected_card_ids=selected_card_ids,
                        selected_sound_card_ids=getattr(source_shot, "selected_sound_card_ids", None),
                        first_frame_reference_image_url=getattr(source_shot, "first_frame_reference_image_url", ""),
                        uploaded_scene_image_url=getattr(source_shot, "uploaded_scene_image_url", ""),
                        use_uploaded_scene_image=bool(getattr(source_shot, "use_uploaded_scene_image", False)),
                        video_path="",  # 清空视频路径
                        thumbnail_video_path="",  # 清空缩略图
                        video_status="idle",  # 重置状态
                        task_id="",  # 清空task_id
                        aspect_ratio=source_shot.aspect_ratio,
                        duration=source_shot.duration,
                        storyboard_video_model=getattr(source_shot, "storyboard_video_model", ""),
                        storyboard_video_model_override_enabled=bool(getattr(source_shot, "storyboard_video_model_override_enabled", False)),
                        duration_override_enabled=bool(getattr(source_shot, "duration_override_enabled", False)),
                        detail_image_prompt_overrides=source_shot.detail_image_prompt_overrides
                    )
                    db.add(new_shot)
                    db.flush()

                    # 记录shot ID映射
                    shot_id_map[source_shot.id] = new_shot.id

                    # 复制ShotVideo（如果需要保留历史视频）
                    for source_video in source_shot.videos:
                        new_video = models.ShotVideo(
                            shot_id=new_shot.id,
                            video_path=source_video.video_path  # CDN URL直接复用
                        )
                        db.add(new_video)

                # ========== 关键修复：复制并更新 storyboard_data ==========
                if source_episode.storyboard_data:
                    try:
                        # 解析原始 JSON
                        storyboard_data = json.loads(source_episode.storyboard_data)

                        # 更新 shots 数组中的 ID
                        if "shots" in storyboard_data:
                            for shot in storyboard_data["shots"]:
                                old_shot_id = shot.get("id")
                                if old_shot_id and old_shot_id in shot_id_map:
                                    # 用新的 shot ID 替换旧的
                                    shot["id"] = shot_id_map[old_shot_id]
                                    print(f"[复制剧本] 更新分镜表JSON中的shot ID: {old_shot_id} -> {shot_id_map[old_shot_id]}")

                        # 保存更新后的 storyboard_data 到新 episode
                        new_episode.storyboard_data = json.dumps(storyboard_data, ensure_ascii=False)
                        print(f"[复制剧本] 已复制并更新 storyboard_data，更新了 {len(shot_id_map)} 个镜头ID")
                    except Exception as e:
                        print(f"[复制剧本] 更新 storyboard_data 失败: {str(e)}")
                        # 失败时不保存 storyboard_data
                        pass

            db.commit()
            success_count += 1

        except Exception as e:
            failed_users.append(target_user.username)
            db.rollback()
            continue

    if success_count == 0:
        raise HTTPException(status_code=500, detail=f"复制失败: {', '.join(failed_users)}")

    message = f"成功复制给 {success_count} 个用户"
    if failed_users:
        message += f"，失败: {', '.join(failed_users)}"

    return {
        "message": message,
        "success_count": success_count,
        "failed_count": len(failed_users)
    }

# ==================== 片段管理API ====================

class EpisodeCreate(BaseModel):
    name: str
    content: str = ""
    batch_size: Optional[int] = 500  # 新增：分批字数
    shot_image_size: Optional[str] = "9:16"
    detail_images_model: Optional[str] = "seedream-4.0"
    detail_images_provider: Optional[str] = ""
    storyboard2_duration: Optional[int] = 15  # 新增：时长规格（6/10/15/25秒）
    storyboard2_video_duration: Optional[int] = 6
    storyboard2_image_cw: Optional[int] = 50
    storyboard2_include_scene_references: Optional[bool] = False
    storyboard_video_model: Optional[str] = DEFAULT_STORYBOARD_VIDEO_MODEL
    storyboard_video_aspect_ratio: Optional[str] = "16:9"
    storyboard_video_duration: Optional[int] = 15
    storyboard_video_resolution_name: Optional[str] = "720p"
    storyboard_video_appoint_account: Optional[str] = ""
    video_style_template_id: Optional[int] = None
    video_prompt_template: Optional[str] = ""
class EpisodeResponse(BaseModel):
    id: int
    script_id: int
    name: str
    content: str
    shot_image_size: str = "9:16"
    detail_images_model: str = "seedream-4.0"
    detail_images_provider: str = ""
    storyboard2_video_duration: int = 6
    storyboard2_image_cw: int = 50
    storyboard2_include_scene_references: bool = False
    storyboard_video_model: str = DEFAULT_STORYBOARD_VIDEO_MODEL
    storyboard_video_aspect_ratio: str = "16:9"
    storyboard_video_duration: int = 15
    storyboard_video_resolution_name: str = "720p"
    storyboard_video_appoint_account: str = ""
    video_style_template_id: Optional[int] = None
    video_prompt_template: str = ""
    batch_generating_prompts: bool = False
    batch_generating_storyboard2_prompts: bool = False
    narration_converting: bool = False  # 是否正在转换为解说剧
    narration_error: str = ""  # 转换错误信息
    opening_content: str = ""  # 精彩开头内容
    opening_generating: bool = False  # 是否正在生成精彩开头
    opening_error: str = ""  # 精彩开头生成错误信息
    library_id: Optional[int] = None  # 添加library_id字段，方便前端获取剧集主体库
    created_at: datetime

    class Config:
        from_attributes = True


class EpisodeShotImageSizeUpdateRequest(BaseModel):
    shot_image_size: Optional[str] = None
    detail_images_model: Optional[str] = None
    detail_images_provider: Optional[str] = None
    storyboard2_video_duration: Optional[int] = None
    storyboard2_image_cw: Optional[int] = None
    storyboard2_include_scene_references: Optional[bool] = None


class EpisodeStoryboardVideoSettingsUpdateRequest(BaseModel):
    detail_images_model: Optional[str] = None
    detail_images_provider: Optional[str] = None
    storyboard2_image_cw: Optional[int] = None
    storyboard2_include_scene_references: Optional[bool] = None
    model: Optional[str] = None
    aspect_ratio: Optional[str] = None
    duration: Optional[int] = None
    resolution_name: Optional[str] = None
    storyboard_video_appoint_account: Optional[str] = None
    video_style_template_id: Optional[int] = None
    video_prompt_template: Optional[str] = None


def _get_pydantic_fields_set(payload: Any) -> set:
    fields_set = getattr(payload, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(payload, "__fields_set__", set())
    return set(fields_set or set())


def _normalize_storyboard_video_appoint_account(value: Any, default_value: str = "") -> str:
    raw = str(value if value is not None else default_value or "").strip()
    return raw


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

@app.post("/api/scripts/{script_id}/episodes", response_model=EpisodeResponse)
async def create_episode(
    script_id: int,
    episode: EpisodeCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建片段"""
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
        batch_size=episode.batch_size or 500,  # 设置分批字数
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

    # 自动为新episode创建主体库
    library = models.StoryLibrary(
        user_id=user.id,
        episode_id=new_episode.id,
        name=f"{episode.name} - 主体库",
        description=f"{script.name} 的剧集主体库"
    )
    db.add(library)
    db.commit()

    return new_episode

@app.get("/api/scripts/{script_id}/episodes", response_model=List[EpisodeResponse])
async def get_script_episodes(
    script_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取剧本的所有片段"""
    script = db.query(models.Script).filter(models.Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="剧本不存在")
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")
    episodes = db.query(models.Episode).filter(
        models.Episode.script_id == script_id
    ).order_by(models.Episode.created_at.asc()).all()

    any_runtime_flag_changed = False

    # 为每个episode添加library_id
    result = []
    for episode in episodes:
        any_runtime_flag_changed = _reconcile_episode_runtime_flags(episode, db) or any_runtime_flag_changed
        library = db.query(models.StoryLibrary).filter(
            models.StoryLibrary.episode_id == episode.id
        ).first()

        result.append({
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
            "storyboard_video_model": _normalize_storyboard_video_model(
                getattr(episode, "storyboard_video_model", None),
                default_model=DEFAULT_STORYBOARD_VIDEO_MODEL
            ),
            "storyboard_video_aspect_ratio": _normalize_storyboard_video_aspect_ratio(
                getattr(episode, "storyboard_video_aspect_ratio", None),
                model=_normalize_storyboard_video_model(
                    getattr(episode, "storyboard_video_model", None),
                    default_model=DEFAULT_STORYBOARD_VIDEO_MODEL
                ),
                default_ratio="16:9"
            ),
            "storyboard_video_duration": _normalize_storyboard_video_duration(
                getattr(episode, "storyboard_video_duration", None),
                model=_normalize_storyboard_video_model(
                    getattr(episode, "storyboard_video_model", None),
                    default_model=DEFAULT_STORYBOARD_VIDEO_MODEL
                ),
                default_duration=15
            ),
            "storyboard_video_resolution_name": _normalize_storyboard_video_resolution_name(
                getattr(episode, "storyboard_video_resolution_name", None),
                model=_normalize_storyboard_video_model(
                    getattr(episode, "storyboard_video_model", None),
                    default_model=DEFAULT_STORYBOARD_VIDEO_MODEL
                ),
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
            "created_at": episode.created_at
        })

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

@app.post("/api/scripts/{script_id}/episodes/{episode_id}/convert-to-narration")
async def convert_to_narration(
    script_id: int,
    episode_id: int,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    将文本转换为解说剧形式（后台任务）
    """
    # 验证权限
    script = db.query(models.Script).filter(models.Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="剧本不存在")
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    episode = db.query(models.Episode).filter(
        models.Episode.id == episode_id,
        models.Episode.script_id == script_id
    ).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    # 检查是否正在转换
    if episode.narration_converting:
        raise HTTPException(status_code=400, detail="正在转换中，请稍后")

    # 获取文本内容（可选，如果不传则使用数据库中的）
    content = request.get("content", "")
    if content and content.strip():
        # 如果传了新内容，先更新到数据库
        episode.content = content.strip()

    # 检查文本是否为空
    if not episode.content or not episode.content.strip():
        raise HTTPException(status_code=400, detail="文本内容不能为空")

    # 获取临时模板（可选）
    template = request.get("template", None)

    resolved_template = _resolve_narration_template(episode, db, template)
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

@app.post("/api/scripts/{script_id}/episodes/{episode_id}/generate-opening")
async def generate_opening(
    script_id: int,
    episode_id: int,
    request: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    生成精彩开头（后台任务）
    """
    # 验证权限
    script = db.query(models.Script).filter(models.Script.id == script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="剧本不存在")
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    episode = db.query(models.Episode).filter(
        models.Episode.id == episode_id,
        models.Episode.script_id == script_id
    ).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    # 检查是否正在生成
    if episode.opening_generating:
        raise HTTPException(status_code=400, detail="正在生成中，请稍后")

    # 获取文本内容（可选，如果不传则使用数据库中的）
    content = request.get("content", "")
    if content and content.strip():
        # 如果传了新内容，先更新到数据库
        episode.content = content.strip()

    # 检查文本是否为空
    if not episode.content or not episode.content.strip():
        raise HTTPException(status_code=400, detail="文本内容不能为空")

    # 获取临时模板（可选）
    template = request.get("template", None)

    resolved_template = _resolve_opening_template(db, template)
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



@app.get("/api/episodes/{episode_id}", response_model=EpisodeResponse)
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


@app.get("/api/episodes/{episode_id}/poll-status")
def get_episode_poll_status(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    episode = _verify_episode_permission(episode_id, user, db)
    if _reconcile_episode_runtime_flags(episode, db):
        db.commit()
    return _build_episode_poll_status_payload(episode)

@app.get("/api/episodes/{episode_id}/total-cost")
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

@app.put("/api/episodes/{episode_id}", response_model=EpisodeResponse)
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


@app.put("/api/episodes/{episode_id}/storyboard2-duration")
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


@app.patch("/api/episodes/{episode_id}/shot-image-size")
async def update_episode_shot_image_size(
    episode_id: int,
    request: EpisodeShotImageSizeUpdateRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新剧集统一图视频设置（镜头图尺寸 + 镜头图模型 + 故事板2视频时长）"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if not script or script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    current_size = _normalize_jimeng_ratio(getattr(episode, "shot_image_size", None), default_ratio="9:16")
    normalized_size = _normalize_jimeng_ratio(request.shot_image_size, default_ratio=current_size)
    current_duration = _normalize_storyboard2_video_duration(
        getattr(episode, "storyboard2_video_duration", None),
        default_value=6
    )
    normalized_duration = _normalize_storyboard2_video_duration(
        request.storyboard2_video_duration,
        default_value=current_duration
    )
    current_detail_images_model = _normalize_detail_images_model(
        getattr(episode, "detail_images_model", None),
        default_model="seedream-4.0"
    )
    normalized_detail_images_model = _normalize_detail_images_model(
        request.detail_images_model,
        default_model=current_detail_images_model
    )
    current_detail_images_provider = _resolve_episode_detail_images_provider(episode)
    normalized_detail_images_provider = _normalize_detail_images_provider(
        request.detail_images_provider,
        default_provider=current_detail_images_provider
    )
    current_image_cw = _normalize_storyboard2_image_cw(
        getattr(episode, "storyboard2_image_cw", None),
        default_value=50
    )
    normalized_image_cw = _normalize_storyboard2_image_cw(
        request.storyboard2_image_cw,
        default_value=current_image_cw
    )
    current_include_scene = bool(getattr(episode, "storyboard2_include_scene_references", False))
    normalized_include_scene = current_include_scene if request.storyboard2_include_scene_references is None else bool(request.storyboard2_include_scene_references)
    episode.shot_image_size = normalized_size
    episode.detail_images_model = normalized_detail_images_model
    episode.detail_images_provider = normalized_detail_images_provider
    episode.storyboard2_video_duration = normalized_duration
    episode.storyboard2_image_cw = normalized_image_cw
    episode.storyboard2_include_scene_references = normalized_include_scene
    db.commit()

    return {
        "episode_id": episode.id,
        "shot_image_size": normalized_size,
        "detail_images_model": normalized_detail_images_model,
        "detail_images_provider": normalized_detail_images_provider,
        "storyboard2_video_duration": normalized_duration,
        "storyboard2_image_cw": normalized_image_cw,
        "storyboard2_include_scene_references": normalized_include_scene,
        "message": "图视频设置已保存"
    }


@app.patch("/api/episodes/{episode_id}/storyboard-video-settings")
async def update_episode_storyboard_video_settings(
    episode_id: int,
    request: EpisodeStoryboardVideoSettingsUpdateRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新剧集统一图视频设置（故事板sora视频 + 镜头图模型 + 参考图策略）"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if not script or script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    current_settings = _get_episode_storyboard_video_settings(episode)
    normalized_model = _normalize_storyboard_video_model(
        request.model,
        default_model=current_settings["model"]
    )
    normalized_aspect_ratio = _normalize_storyboard_video_aspect_ratio(
        request.aspect_ratio,
        model=normalized_model,
        default_ratio=current_settings["aspect_ratio"]
    )
    normalized_duration = _normalize_storyboard_video_duration(
        request.duration,
        model=normalized_model,
        default_duration=current_settings["duration"]
    )
    normalized_resolution_name = _normalize_storyboard_video_resolution_name(
        request.resolution_name,
        model=normalized_model,
        default_resolution=current_settings.get("resolution_name", "")
    )
    normalized_appoint_account = _normalize_storyboard_video_appoint_account(
        request.storyboard_video_appoint_account,
        default_value=getattr(episode, "storyboard_video_appoint_account", "") or ""
    )
    # 故事板sora中镜头图比例与视频比例保持一致，统一使用同一设置值。
    normalized_shot_image_size = normalized_aspect_ratio
    current_detail_images_model = _normalize_detail_images_model(
        getattr(episode, "detail_images_model", None),
        default_model="seedream-4.0"
    )
    normalized_detail_images_model = _normalize_detail_images_model(
        request.detail_images_model,
        default_model=current_detail_images_model
    )
    current_detail_images_provider = _resolve_episode_detail_images_provider(episode)
    normalized_detail_images_provider = _normalize_detail_images_provider(
        request.detail_images_provider,
        default_provider=current_detail_images_provider
    )
    current_image_cw = _normalize_storyboard2_image_cw(
        getattr(episode, "storyboard2_image_cw", None),
        default_value=50
    )
    normalized_image_cw = _normalize_storyboard2_image_cw(
        request.storyboard2_image_cw,
        default_value=current_image_cw
    )
    current_include_scene = bool(getattr(episode, "storyboard2_include_scene_references", False))
    normalized_include_scene = (
        current_include_scene
        if request.storyboard2_include_scene_references is None
        else bool(request.storyboard2_include_scene_references)
    )
    normalized_provider = _resolve_storyboard_video_provider(normalized_model)
    normalized_video_prompt_template = (
        (getattr(episode, "video_prompt_template", "") or "")
        if request.video_prompt_template is None
        else str(request.video_prompt_template or "")
    )

    episode.shot_image_size = normalized_shot_image_size
    episode.detail_images_model = normalized_detail_images_model
    episode.detail_images_provider = normalized_detail_images_provider
    episode.storyboard2_image_cw = normalized_image_cw
    episode.storyboard2_include_scene_references = normalized_include_scene
    episode.storyboard_video_model = normalized_model
    episode.storyboard_video_aspect_ratio = normalized_aspect_ratio
    episode.storyboard_video_duration = normalized_duration
    episode.storyboard_video_resolution_name = normalized_resolution_name
    episode.storyboard_video_appoint_account = normalized_appoint_account
    episode.video_prompt_template = normalized_video_prompt_template
    if request.video_style_template_id is not None:
        episode.video_style_template_id = request.video_style_template_id if request.video_style_template_id > 0 else None
    db.commit()

    return {
        "episode_id": episode.id,
        "shot_image_size": normalized_shot_image_size,
        "detail_images_model": normalized_detail_images_model,
        "detail_images_provider": normalized_detail_images_provider,
        "storyboard2_image_cw": normalized_image_cw,
        "storyboard2_include_scene_references": normalized_include_scene,
        "model": normalized_model,
        "aspect_ratio": normalized_aspect_ratio,
        "duration": normalized_duration,
        "resolution_name": normalized_resolution_name,
        "storyboard_video_appoint_account": normalized_appoint_account,
        "provider": normalized_provider,
        "video_style_template_id": episode.video_style_template_id,
        "video_prompt_template": normalized_video_prompt_template,
        "message": "视频设置已保存"
    }

# ==================== 辅助函数：从storyboard_data创建镜头记录 ====================

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


def _clear_episode_dependencies(episode_ids: List[int], db: Session) -> Dict[str, int]:
    normalized_episode_ids = []
    seen_ids = set()
    for raw_episode_id in episode_ids or []:
        try:
            episode_id = int(raw_episode_id or 0)
        except (TypeError, ValueError):
            continue
        if episode_id <= 0 or episode_id in seen_ids:
            continue
        seen_ids.add(episode_id)
        normalized_episode_ids.append(episode_id)

    if not normalized_episode_ids:
        return {
            "unlinked_libraries": 0,
            "deleted_simple_storyboard_batches": 0,
            "deleted_managed_tasks": 0,
            "deleted_managed_sessions": 0,
            "deleted_voiceover_tts_tasks": 0,
        }

    unlinked_libraries = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id.in_(normalized_episode_ids)
    ).update(
        {models.StoryLibrary.episode_id: None},
        synchronize_session=False
    )

    deleted_simple_storyboard_batches = db.query(models.SimpleStoryboardBatch).filter(
        models.SimpleStoryboardBatch.episode_id.in_(normalized_episode_ids)
    ).delete(synchronize_session=False)

    managed_session_ids = [
        session_id
        for session_id, in db.query(models.ManagedSession.id).filter(
            models.ManagedSession.episode_id.in_(normalized_episode_ids)
        ).all()
    ]
    deleted_managed_tasks = 0
    if managed_session_ids:
        deleted_managed_tasks = db.query(models.ManagedTask).filter(
            models.ManagedTask.session_id.in_(managed_session_ids)
        ).delete(synchronize_session=False)

    deleted_managed_sessions = db.query(models.ManagedSession).filter(
        models.ManagedSession.episode_id.in_(normalized_episode_ids)
    ).delete(synchronize_session=False)
    deleted_voiceover_tts_tasks = db.query(models.VoiceoverTtsTask).filter(
        models.VoiceoverTtsTask.episode_id.in_(normalized_episode_ids)
    ).delete(synchronize_session=False)

    return {
        "unlinked_libraries": int(unlinked_libraries or 0),
        "deleted_simple_storyboard_batches": int(deleted_simple_storyboard_batches or 0),
        "deleted_managed_tasks": int(deleted_managed_tasks or 0),
        "deleted_managed_sessions": int(deleted_managed_sessions or 0),
        "deleted_voiceover_tts_tasks": int(deleted_voiceover_tts_tasks or 0),
    }


def _get_storyboard_shot_family_ids(shot: models.StoryboardShot, db: Session) -> List[int]:
    stable_id = str(getattr(shot, "stable_id", "") or "").strip()
    query = db.query(models.StoryboardShot.id).filter(
        models.StoryboardShot.episode_id == shot.episode_id
    )
    if stable_id:
        query = query.filter(
            or_(
                models.StoryboardShot.stable_id == stable_id,
                and_(
                    models.StoryboardShot.shot_number == shot.shot_number,
                    or_(
                        models.StoryboardShot.stable_id.is_(None),
                        models.StoryboardShot.stable_id == "",
                    ),
                ),
            )
        )
    else:
        query = query.filter(models.StoryboardShot.shot_number == shot.shot_number)
    return [
        family_shot_id
        for family_shot_id, in query.order_by(
            models.StoryboardShot.shot_number.asc(),
            models.StoryboardShot.variant_index.asc(),
            models.StoryboardShot.id.asc()
        ).all()
    ]


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

# ==================== 新三阶段分镜生成后台任务 ====================

def _submit_simple_storyboard_batch_task(
    db: Session,
    *,
    episode_id: int,
    batch_row: models.SimpleStoryboardBatch,
    duration: int,
):
    template = db.query(models.ShotDurationTemplate).filter(
        models.ShotDurationTemplate.duration == int(duration or 15)
    ).first()
    if not template:
        raise ValueError(f"未找到 {int(duration or 15)} 秒简单分镜模板")

    prompt = str(template.simple_storyboard_rule or "").format(content=str(batch_row.source_text or ""))
    config = get_ai_config("simple_storyboard")
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
        "batch_row_id": int(batch_row.id),
        "batch_index": int(batch_row.batch_index or 0),
    }
    relay_task = submit_and_persist_text_task(
        db,
        task_type="simple_storyboard_batch",
        owner_type="simple_storyboard_batch",
        owner_id=int(batch_row.id),
        stage_key="simple_storyboard",
        function_key="simple_storyboard",
        request_payload=request_data,
        task_payload=task_payload,
    )
    batch_row.status = "submitting"
    batch_row.error_message = ""
    batch_row.last_attempt = 1
    batch_row.updated_at = datetime.utcnow()
    return relay_task


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


def _submit_detailed_storyboard_stage2_task(db: Session, *, episode_id: int, final_shots: List[Dict[str, Any]]):
    full_storyboard_json = json.dumps({"shots": final_shots}, ensure_ascii=False, indent=2)
    prompt_template = get_prompt_by_key("stage2_refine_shot")
    prompt = prompt_template.format(
        total_shots=len(final_shots),
        full_storyboard_json=full_storyboard_json
    )
    config = get_ai_config("detailed_storyboard_s2")
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
        "final_shots": final_shots,
    }
    return submit_and_persist_text_task(
        db,
        task_type="detailed_storyboard_stage2",
        owner_type="episode",
        owner_id=int(episode_id),
        stage_key="detailed_storyboard_stage2",
        function_key="detailed_storyboard_s2",
        request_payload=request_data,
        task_payload=task_payload,
    )

# 后台任务：生成简单分镜
def generate_simple_storyboard_background(episode_id: int, content: str, batch_size: int, duration: int):
    """后台生成简单分镜（新阶段1：镜头划分）"""
    db = SessionLocal()
    try:
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if not episode:
            return

        batch_texts = _split_simple_storyboard_batches(content, batch_size)
        total_batches = len(batch_texts)

        def mark_simple_storyboard_generating():
            episode.batch_size = batch_size
            episode.simple_storyboard_data = json.dumps({"shots": []}, ensure_ascii=False)
            episode.simple_storyboard_generating = True
            episode.simple_storyboard_error = ""
            _reset_simple_storyboard_batches_for_episode(episode_id, total_batches, batch_texts, db)

        commit_with_retry(
            db,
            prepare_fn=mark_simple_storyboard_generating,
            context=f"simple_storyboard_start episode={episode_id}"
        )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        task_folder = f"simple_storyboard_episode_{episode_id}_{timestamp}"

        from ai_service import generate_simple_storyboard
        result, debug_info = generate_simple_storyboard(
            content,
            batch_size,
            duration=duration,
            episode_id=episode_id,
            task_folder=task_folder,
            batches=[
                {
                    "batch_index": index + 1,
                    "content": batch_content,
                    "retry_count": 0,
                }
                for index, batch_content in enumerate(batch_texts)
            ],
            batch_retry_limit=10,
            on_batch_result=lambda payload: _apply_simple_storyboard_batch_update(episode_id, payload),
        )

        refreshed_episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if refreshed_episode:
            def save_simple_storyboard_result():
                summary = _refresh_episode_simple_storyboard_from_batches(refreshed_episode, db)
                refreshed_episode.simple_storyboard_data = json.dumps(result, ensure_ascii=False)
                refreshed_episode.simple_storyboard_generating = False
                refreshed_episode.simple_storyboard_error = "" if not summary.get("has_failures") else refreshed_episode.simple_storyboard_error

            commit_with_retry(
                db,
                prepare_fn=save_simple_storyboard_result,
                context=f"simple_storyboard_finish episode={episode_id}"
            )

        print(f"✅ 简单分镜生成成功，共 {len(result.get('shots', []))} 个镜头")

    except Exception as e:
        print(f"❌ 简单分镜生成失败: {str(e)}")
        _rollback_quietly(db)
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            def mark_simple_storyboard_failed():
                batch_rows = _get_simple_storyboard_batch_rows(episode_id, db)
                if batch_rows:
                    for row in batch_rows:
                        if str(getattr(row, "status", "") or "").strip() in {"completed", "failed"}:
                            continue
                        row.status = "failed"
                        if not str(getattr(row, "error_message", "") or "").strip():
                            row.error_message = str(e)
                        row.updated_at = datetime.utcnow()
                    _refresh_episode_simple_storyboard_from_batches(episode, db)
                else:
                    episode.simple_storyboard_generating = False
                    episode.simple_storyboard_error = str(e)

            try:
                commit_with_retry(
                    db,
                    prepare_fn=mark_simple_storyboard_failed,
                    context=f"simple_storyboard_fail episode={episode_id}"
                )
            except Exception as commit_error:
                print(f"❌ 简单分镜失败状态写回失败: {str(commit_error)}")
    finally:
        db.close()


def retry_failed_simple_storyboard_batches_background(episode_id: int):
    db = SessionLocal()
    try:
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if not episode:
            return
        batch_rows = _get_simple_storyboard_batch_rows(episode_id, db)
        retry_items = [
            row for row in batch_rows
            if str(getattr(row, "status", "") or "").strip() in {"pending", "failed"}
            and int(getattr(row, "retry_count", 0) or 0) > 0
        ]
        if not retry_items:
            episode.simple_storyboard_generating = False
            db.commit()
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        task_folder = f"simple_storyboard_retry_episode_{episode_id}_{timestamp}"
        duration = episode.storyboard2_duration or 15
        batch_size = episode.batch_size or 500

        from ai_service import generate_simple_storyboard
        generate_simple_storyboard(
            str(getattr(episode, "content", "") or ""),
            batch_size,
            duration=duration,
            episode_id=episode_id,
            task_folder=task_folder,
            batches=_build_simple_storyboard_batch_runtime_items(retry_items),
            batch_retry_limit=1,
            on_batch_result=lambda payload: _apply_simple_storyboard_batch_update(episode_id, payload),
        )

        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            def finalize_retry_state():
                _refresh_episode_simple_storyboard_from_batches(episode, db)
            commit_with_retry(
                db,
                prepare_fn=finalize_retry_state,
                context=f"simple_storyboard_retry_finish episode={episode_id}"
            )
    except Exception as e:
        _rollback_quietly(db)
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            def mark_retry_failed():
                batch_rows = _get_simple_storyboard_batch_rows(episode_id, db)
                for row in batch_rows:
                    if str(getattr(row, "status", "") or "").strip() == "submitting":
                        row.status = "failed"
                        if not str(getattr(row, "error_message", "") or "").strip():
                            row.error_message = str(e)
                        row.updated_at = datetime.utcnow()
                _refresh_episode_simple_storyboard_from_batches(episode, db)
            try:
                commit_with_retry(
                    db,
                    prepare_fn=mark_retry_failed,
                    context=f"simple_storyboard_retry_fail episode={episode_id}"
                )
            except Exception as commit_error:
                print(f"❌ 简单分镜重试失败状态写回失败: {str(commit_error)}")
    finally:
        db.close()


# 后台任务：生成详细分镜
def generate_detailed_storyboard_background(episode_id: int):
    """后台生成详细分镜（新阶段2：内容分析 + 新阶段3：主体提示词）

    参数：
    - episode_id: 片段ID
    """
    db = SessionLocal()
    try:
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if not episode:
            return

        # 检查简单分镜是否存在
        if not episode.simple_storyboard_data:
            raise Exception("简单分镜数据不存在，请先生成简单分镜")

        def mark_detailed_storyboard_generating():
            episode.storyboard_generating = True
            episode.storyboard_error = ""

        commit_with_retry(
            db,
            prepare_fn=mark_detailed_storyboard_generating,
            context=f"detailed_storyboard_start episode={episode_id}"
        )

        # 创建任务文件夹
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        task_folder = f"detailed_storyboard_episode_{episode_id}_{timestamp}"

        # 读取简单分镜数据
        simple_storyboard = json.loads(episode.simple_storyboard_data)
        simple_shots = simple_storyboard.get("shots", [])

        if not simple_shots:
            raise Exception("简单分镜中没有镜头数据")

        # ==================== 新阶段2：详细分镜（内容分析） ====================

        from ai_service import generate_detailed_storyboard
        detailed_result, debug_info = generate_detailed_storyboard(
            simple_shots,
            episode_id=episode_id,
            task_folder=task_folder
        )

        detailed_shots = detailed_result.get("shots", [])
        if not detailed_shots:
            raise Exception("详细分镜生成失败：未生成任何镜头")

        # ==================== 新阶段3：主体绘画提示词生成 ====================

        from ai_service import stage2_generate_subject_prompts

        final_shots = []

        for shot in detailed_shots:
            shot["subjects"] = _normalize_storyboard_generation_subjects(shot.get("subjects", []))
            final_shots.append(shot)

        # 构建完整分镜表JSON（用于阶段3）
        full_storyboard_json = json.dumps({
            "shots": final_shots
        }, ensure_ascii=False, indent=2)

        # 调用阶段3生成主体提示词
        stage2_result, stage2_debug = stage2_generate_subject_prompts(
            full_storyboard_json,
            episode_id=episode_id,
            task_folder=task_folder
        )

        stage2_subjects = _normalize_stage2_subjects(stage2_result.get("subjects", []))
        name_mappings = stage2_result.get("name_mappings", {})
        canonical_subject_map = _build_subject_detail_map(stage2_subjects)
        for shot in final_shots:
            shot["subjects"] = _reconcile_storyboard_shot_subjects(
                shot,
                canonical_subject_map,
                name_mappings=name_mappings,
            )

        # 保存最终结果
        final_data = {
            "shots": final_shots,
            "subjects": stage2_subjects
        }

        voiceover_shots = []
        for shot in final_shots:
            voiceover_shot = {
                "shot_number": shot.get("shot_number"),
                "voice_type": shot.get("voice_type"),
                "narration": shot.get("narration"),
                "dialogue": shot.get("dialogue")
            }
            voiceover_shots.append(voiceover_shot)

        merged_voiceover_data = _merge_voiceover_shots_preserving_extensions(
            episode.voiceover_data,
            voiceover_shots
        )

        def save_detailed_storyboard_result():
            episode.storyboard_data = json.dumps(final_data, ensure_ascii=False)
            episode.voiceover_data = json.dumps(merged_voiceover_data, ensure_ascii=False)
            episode.storyboard_generating = False
            episode.storyboard_error = ""

        commit_with_retry(
            db,
            prepare_fn=save_detailed_storyboard_result,
            context=f"detailed_storyboard_finish episode={episode_id}"
        )

        print(f"✅ 详细分镜生成成功，共 {len(final_shots)} 个镜头，{len(stage2_subjects)} 个主体")

        # 自动创建镜头记录
        _create_shots_from_storyboard_data(episode_id, db)

    except Exception as e:
        print(f"❌ 详细分镜生成失败: {str(e)}")
        _rollback_quietly(db)
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            def mark_detailed_storyboard_failed():
                episode.storyboard_generating = False
                episode.storyboard_error = str(e)

            try:
                commit_with_retry(
                    db,
                    prepare_fn=mark_detailed_storyboard_failed,
                    context=f"detailed_storyboard_fail episode={episode_id}"
                )
            except Exception as commit_error:
                print(f"❌ 详细分镜失败状态写回失败: {str(commit_error)}")
    finally:
        db.close()


# 后台任务：生成分镜表（新两阶段流程）
def generate_storyboard_background(episode_id: int, content: str, prompt_style: str = None, append_mode: bool = False):
    """后台生成分镜表（两阶段流程）

    阶段1：按500字分批，生成初步分镜（约9个/批），并提取主体
    阶段2：基于完整分镜表生成主体绘画提示词与别名（规范角色名）

    参数：
    - episode_id: 片段ID
    - content: 文案内容
    - prompt_style: 提示词风格（用于角色prompt拼接）
    - append_mode: 保留参数但不使用（新流程不支持追加）
    """
    db = SessionLocal()
    try:
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if not episode:
            return

        def mark_storyboard_generating():
            episode.storyboard_generating = True

        commit_with_retry(
            db,
            prepare_fn=mark_storyboard_generating,
            context=f"storyboard_start episode={episode_id}"
        )


        # ✅ 创建任务文件夹（所有阶段的调试文件都保存在这里）
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        task_folder = f"episode_{episode_id}_{timestamp}"

        # ==================== 阶段1：初步分镜生成 ====================

        # 文本分批逻辑：按自然段累积，接近500字触发一批
        paragraphs = [p.strip() for p in content.split('\n') if p.strip()]
        batches = []  # 每批是一个段落列表
        current_batch = []
        current_length = 0

        for para in paragraphs:
            para_length = len(para)
            if current_length + para_length >= 500 and current_batch:
                # 触发处理
                batches.append('\n\n'.join(current_batch))
                current_batch = [para]
                current_length = para_length
            else:
                current_batch.append(para)
                current_length += para_length

        # 最后一批
        if current_batch:
            batches.append('\n\n'.join(current_batch))


        # 对每批调用阶段1 AI（并发执行）
        all_stage1_shots = []
        shot_counter = 1

        def process_batch(batch_idx, batch_content):
            """处理单个批次"""
            try:
                # ✅ 传递 episode_id, task_folder 和 batch_id，让 stage1 函数自己保存调试信息
                result, debug_info = stage1_generate_initial_storyboard(
                    batch_content,
                    episode_id=episode_id,
                    task_folder=task_folder,
                    batch_id=str(batch_idx + 1)
                )
                batch_shots = result.get("shots", [])
                return (batch_idx, batch_shots)
            except Exception as e:
                return (batch_idx, [])

        # 并发执行所有批次
        with ThreadPoolExecutor(max_workers=min(len(batches), 10)) as executor:
            futures = [executor.submit(process_batch, i, batch) for i, batch in enumerate(batches)]
            batch_results = {}
            for future in futures:
                batch_idx, batch_shots = future.result()
                batch_results[batch_idx] = batch_shots

        # 按批次顺序合并并重新编号
        for batch_idx in sorted(batch_results.keys()):
            batch_shots = batch_results[batch_idx]
            for shot in batch_shots:
                shot["shot_number"] = str(shot_counter)
                shot_counter += 1
            all_stage1_shots.extend(batch_shots)

        if not all_stage1_shots:
            raise Exception("阶段1未生成任何镜头")


        # ==================== 阶段2：主体绘画提示词 ====================

        final_shots = []
        for stage1_shot in all_stage1_shots:
            final_shots.append({
                "shot_number": stage1_shot["shot_number"],
                "subjects": _normalize_storyboard_generation_subjects(stage1_shot.get("subjects", [])),
                "original_text": stage1_shot.get("original_text", ""),
                "voice_type": stage1_shot.get("voice_type", "none"),
                "narration": stage1_shot.get("narration"),
                "dialogue": stage1_shot.get("dialogue"),
            })

        full_storyboard_json = json.dumps({"shots": final_shots}, ensure_ascii=False, indent=2)
        stage2_subjects = []

        try:
            # ✅ 传递 episode_id 和 task_folder，让 Stage2 自己保存每次尝试的调试信息
            result, debug_info = stage2_generate_subject_prompts(
                full_storyboard_json,
                episode_id=episode_id,
                task_folder=task_folder
            )
            stage2_subjects = _normalize_stage2_subjects(result.get("subjects", []))
            name_mappings = result.get("name_mappings", {})  # 获取AI生成的名称映射表

        except Exception as e:
            stage2_subjects = []
            name_mappings = {}
            # Stage2 已经在内部保存了每次尝试的调试信息，不需要额外保存

        # 过滤并去重主体结果
        stage2_subjects = _normalize_stage2_subjects(stage2_subjects)

        # 使用AI提供的映射表更新分镜表中的主体名称
        if name_mappings:
            print(f"  ✓ 应用主体名称映射表: {name_mappings}")
            for shot in final_shots:
                updated_subjects = []
                seen = set()
                for subj in shot.get("subjects", []):
                    original_name = subj.get("name", "")
                    subject_type = subj.get("type", "")

                    # 使用映射表查找规范名称
                    canonical_name = name_mappings.get(original_name, original_name)

                    # 构建规范主体
                    canonical_subj = {"name": canonical_name, "type": subject_type}
                    key = (canonical_name, subject_type)

                    # 去重
                    if key in seen:
                        continue
                    seen.add(key)
                    updated_subjects.append(canonical_subj)

                shot["subjects"] = updated_subjects

        # ==================== 保存最终结果 ====================
        # 注意：ai_prompt 只存储纯粹的描述（不含格式化前缀和风格）
        # 完整prompt的拼接在生成图片时进行（generate_image_for_card 函数）
        final_data = {
            "shots": final_shots,
            "subjects": stage2_subjects  # 包含ai_prompt和alias
        }

        def save_storyboard_result():
            episode.storyboard_data = json.dumps(final_data, ensure_ascii=False)
            episode.storyboard_generating = False
            episode.storyboard_error = ""

        commit_with_retry(
            db,
            prepare_fn=save_storyboard_result,
            context=f"storyboard_finish episode={episode_id}"
        )


        # ✅ 自动创建镜头记录到storyboard_shots表
        try:
            _create_shots_from_storyboard_data(episode_id, db)
        except Exception as e:
            pass

    except Exception as e:
        _rollback_quietly(db)
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            def mark_storyboard_failed():
                episode.storyboard_generating = False

                # 保存详细错误信息
                error_message = str(e)

                # 尝试从异常对象中提取更详细的信息
                if hasattr(e, 'debug_info'):
                    debug_info = e.debug_info
                    output = debug_info.get('output', {})

                    # 检查是否是content_filter错误
                    if 'full_response' in output:
                        full_response = output['full_response']
                        if isinstance(full_response, dict) and 'choices' in full_response:
                            choices = full_response.get('choices', [])
                            if choices and len(choices) > 0:
                                finish_reason = choices[0].get('finish_reason', '')
                                if finish_reason == 'content_filter':
                                    error_message = "AI内容审查拦截：生成的内容触发了安全过滤器。建议修改剧本中的敏感词汇或情绪描述。"
                                elif finish_reason and finish_reason != 'stop':
                                    error_message = f"AI生成异常：{finish_reason}"

                    # 如果有其他错误信息
                    if 'error' in output:
                        error_detail = output['error']
                        if 'JSON' in error_detail or 'json' in error_detail.lower():
                            error_message = f"AI响应格式错误：{error_detail}"

                episode.storyboard_error = error_message
                print(f"❌ 分镜表生成失败: {error_message}")

            try:
                commit_with_retry(
                    db,
                    prepare_fn=mark_storyboard_failed,
                    context=f"storyboard_fail episode={episode_id}"
                )
            except Exception as commit_error:
                print(f"❌ 分镜表失败状态写回失败: {str(commit_error)}")
    finally:
        db.close()

# 新增：分镜表分析响应模型
class StoryboardAnalyzeResponse(BaseModel):
    message: str  # 提示消息
    generating: bool  # 是否正在生成

# 新增：创建分镜表请求模型
class CreateStoryboardRequest(BaseModel):
    shots: List[dict]  # 编辑后的镜头列表

# ==================== 新三阶段分镜生成API ====================

class SimpleStoryboardRequest(BaseModel):
    content: Optional[str] = None  # 可选的自定义文案内容
    batch_size: Optional[int] = 500  # 分批字数，默认500

@app.post("/api/episodes/{episode_id}/generate-simple-storyboard")
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


@app.get("/api/episodes/{episode_id}/simple-storyboard")
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


@app.get("/api/episodes/{episode_id}/simple-storyboard/status")
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


@app.post("/api/episodes/{episode_id}/simple-storyboard/retry-failed-batches")
async def retry_failed_simple_storyboard_batches_api(
    episode_id: int,
    background_tasks: BackgroundTasks = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _verify_episode_permission(episode_id, user, db)
    raise HTTPException(status_code=400, detail="失败批次重试已移除，请重新发起整次简单分镜生成")


@app.put("/api/episodes/{episode_id}/simple-storyboard")
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


@app.post("/api/episodes/{episode_id}/generate-detailed-storyboard")
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


class AnalyzeStoryboardRequest(BaseModel):
    content: Optional[str] = None  # 可选的自定义文案内容
    append: Optional[bool] = False  # 是否追加模式（不清空旧数据）

@app.post("/api/episodes/{episode_id}/analyze-storyboard", response_model=StoryboardAnalyzeResponse)
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


# 新增：获取详细分镜的原始JSON数据（包含完整的voice_type、narration、dialogue）
@app.get("/api/episodes/{episode_id}/detailed-storyboard")
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


# 保存配音表数据（只更新voiceover_data字段）
@app.put("/api/episodes/{episode_id}/voiceover")
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


@app.get("/api/episodes/{episode_id}/voiceover/shared")
async def get_voiceover_shared_data(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _, script = _ensure_voiceover_permission(episode_id, user, db)
    shared = _load_script_voiceover_shared_data(script)
    return {"success": True, "shared": shared}


@app.post("/api/episodes/{episode_id}/voiceover/shared/voice-references")
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


@app.put("/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}")
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


@app.get("/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}/preview")
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


@app.delete("/api/episodes/{episode_id}/voiceover/shared/voice-references/{reference_id}")
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


@app.post("/api/episodes/{episode_id}/voiceover/shared/vector-presets")
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


@app.delete("/api/episodes/{episode_id}/voiceover/shared/vector-presets/{preset_id}")
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


@app.post("/api/episodes/{episode_id}/voiceover/shared/emotion-audio-presets")
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


@app.delete("/api/episodes/{episode_id}/voiceover/shared/emotion-audio-presets/{preset_id}")
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


@app.post("/api/episodes/{episode_id}/voiceover/shared/setting-templates")
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


@app.delete("/api/episodes/{episode_id}/voiceover/shared/setting-templates/{template_id}")
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


@app.post("/api/episodes/{episode_id}/voiceover/lines/{line_id}/generate")
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


@app.post("/api/episodes/{episode_id}/voiceover/generate-all")
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


@app.get("/api/episodes/{episode_id}/voiceover/tts-status")
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


@app.get("/api/episodes/{episode_id}/storyboard")
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


@app.get("/api/episodes/{episode_id}/storyboard/status")
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


@app.put("/api/episodes/{episode_id}/storyboard")
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

@app.post("/api/episodes/{episode_id}/create-from-storyboard")
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

# 更新卡片的ai_prompt
@app.put("/api/cards/{card_id}/prompt")
async def update_card_prompt(
    card_id: int,
    prompt_data: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新卡片的AI prompt"""
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="卡片不存在")

    # 验证权限
    verify_library_owner(card.library_id, user, db)

    card.ai_prompt = prompt_data.get('prompt', '')
    db.commit()

    return {"message": "更新成功", "ai_prompt": card.ai_prompt}

# ==================== 提示词模板API ====================

class TemplateCreate(BaseModel):
    name: str
    content: str

class TemplateResponse(BaseModel):
    id: int
    name: str
    content: str
    is_default: bool
    created_at: datetime

    class Config:
        from_attributes = True

@app.get("/api/templates", response_model=List[TemplateResponse])
async def get_templates(db: Session = Depends(get_db)):
    """获取所有提示词模板"""
    templates = db.query(models.PromptTemplate).order_by(
        models.PromptTemplate.is_default.desc(),
        models.PromptTemplate.created_at.desc()
    ).all()
    return templates

@app.post("/api/templates", response_model=TemplateResponse)
async def create_template(
    template: TemplateCreate,
    db: Session = Depends(get_db)
):
    """创建新模板（全局共享）"""
    new_template = models.PromptTemplate(
        name=template.name,
        content=template.content,
        is_default=False
    )
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return new_template

# ==================== 绘图风格模板API ====================

class StyleTemplateCreate(BaseModel):
    name: str
    content: str
    scene_content: Optional[str] = ""
    prop_content: Optional[str] = ""

class StyleTemplateResponse(BaseModel):
    id: int
    name: str
    content: str
    scene_content: str = ""
    prop_content: str = ""
    is_default: bool = False
    created_at: datetime

    class Config:
        from_attributes = True

STYLE_TEMPLATE_MIGRATION_PLACEHOLDER_PREFIX = "[迁移占位]已删除风格模板 "

def _visible_style_template_name_filter():
    return ~models.StyleTemplate.name.startswith(STYLE_TEMPLATE_MIGRATION_PLACEHOLDER_PREFIX)


def _normalize_style_template_payload(template: StyleTemplateCreate) -> dict:
    name = str(template.name or "").strip()
    content = str(template.content or "").strip()
    if not name or not content:
        raise HTTPException(status_code=400, detail="模板名称和角色风格提示词不能为空")

    scene_content = str(template.scene_content or "").strip()
    prop_content = str(template.prop_content or "").strip()

    return {
        "name": name,
        "content": content,
        "scene_content": scene_content or _build_scene_style_template_content(content),
        "prop_content": prop_content or _build_prop_style_template_content(content),
    }


def _serialize_style_template(template: models.StyleTemplate) -> dict:
    return {
        "id": template.id,
        "name": template.name,
        "content": template.content or "",
        "scene_content": getattr(template, "scene_content", "") or "",
        "prop_content": getattr(template, "prop_content", "") or "",
        "is_default": bool(template.is_default),
        "created_at": template.created_at,
    }

@app.get("/api/style-templates", response_model=List[StyleTemplateResponse])
async def get_style_templates(
    db: Session = Depends(get_db)
):
    """获取所有绘图风格模板（全局配置，不需要认证）"""
    templates = db.query(models.StyleTemplate).filter(
        _visible_style_template_name_filter()
    ).order_by(
        models.StyleTemplate.created_at.desc()
    ).all()
    return [_serialize_style_template(template) for template in templates]

@app.post("/api/style-templates", response_model=StyleTemplateResponse)
async def create_style_template(
    template: StyleTemplateCreate,
    db: Session = Depends(get_db)
):
    """创建新的绘图风格模板（全局配置）"""
    normalized_payload = _normalize_style_template_payload(template)
    new_template = models.StyleTemplate(
        **normalized_payload
    )
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return _serialize_style_template(new_template)

@app.put("/api/style-templates/{template_id}", response_model=StyleTemplateResponse)
async def update_style_template(
    template_id: int,
    template: StyleTemplateCreate,
    db: Session = Depends(get_db)
):
    """更新绘图风格模板（全局配置，不需要认证）"""
    db_template = db.query(models.StyleTemplate).filter(
        models.StyleTemplate.id == template_id
    ).first()

    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    normalized_payload = _normalize_style_template_payload(template)
    db_template.name = normalized_payload["name"]
    db_template.content = normalized_payload["content"]
    db_template.scene_content = normalized_payload["scene_content"]
    db_template.prop_content = normalized_payload["prop_content"]
    db.commit()
    db.refresh(db_template)
    return _serialize_style_template(db_template)

@app.delete("/api/style-templates/{template_id}")
async def delete_style_template(
    template_id: int,
    db: Session = Depends(get_db)
):
    """删除绘图风格模板（全局配置，不需要认证）"""
    db_template = db.query(models.StyleTemplate).filter(
        models.StyleTemplate.id == template_id
    ).first()

    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    db.query(models.SubjectCard).filter(
        models.SubjectCard.style_template_id == template_id
    ).update({"style_template_id": None}, synchronize_session=False)

    if db_template.is_default:
        replacement_template = db.query(models.StyleTemplate).filter(
            models.StyleTemplate.id != template_id,
            _visible_style_template_name_filter()
        ).order_by(
            models.StyleTemplate.created_at.desc(),
            models.StyleTemplate.id.desc()
        ).first()
        if replacement_template:
            replacement_template.is_default = True

    db.delete(db_template)
    db.commit()
    return {"message": "模板已删除"}

@app.post("/api/style-templates/{template_id}/set-default")
async def set_default_template(
    template_id: int,
    db: Session = Depends(get_db)
):
    """设置默认绘图风格模板"""
    db_template = db.query(models.StyleTemplate).filter(
        models.StyleTemplate.id == template_id
    ).first()

    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    # 取消所有其他模板的默认状态
    db.query(models.StyleTemplate).update({"is_default": False})

    # 设置当前模板为默认
    db_template.is_default = True
    db.commit()
    db.refresh(db_template)

    return {"message": "已设置为默认模板", "template_id": template_id}

@app.get("/api/style-templates/default", response_model=StyleTemplateResponse)
async def get_default_template(
    db: Session = Depends(get_db)
):
    """获取默认绘图风格模板"""
    default_template = db.query(models.StyleTemplate).filter(
        models.StyleTemplate.is_default == True,
        _visible_style_template_name_filter()
    ).first()

    if not default_template:
        default_template = db.query(models.StyleTemplate).filter(
            _visible_style_template_name_filter()
        ).order_by(
            models.StyleTemplate.created_at.desc(),
            models.StyleTemplate.id.desc()
        ).first()

    if not default_template:
        raise HTTPException(status_code=404, detail="未设置默认模板")

    return _serialize_style_template(default_template)

# ==================== 视频风格模板API ====================

class VideoStyleTemplateCreate(BaseModel):
    name: str
    sora_rule: str = ""
    style_prompt: str = ""

class VideoStyleTemplateResponse(BaseModel):
    id: int
    name: str
    sora_rule: str
    style_prompt: str
    is_default: bool = False
    created_at: datetime

    class Config:
        from_attributes = True

@app.get("/api/video-style-templates", response_model=List[VideoStyleTemplateResponse])
async def get_video_style_templates(db: Session = Depends(get_db)):
    templates = db.query(models.VideoStyleTemplate).order_by(
        models.VideoStyleTemplate.is_default.desc(),
        models.VideoStyleTemplate.created_at.desc()
    ).all()
    return templates

@app.post("/api/video-style-templates", response_model=VideoStyleTemplateResponse)
async def create_video_style_template(template: VideoStyleTemplateCreate, db: Session = Depends(get_db)):
    new_template = models.VideoStyleTemplate(
        name=template.name,
        sora_rule=template.sora_rule,
        style_prompt=template.style_prompt
    )
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return new_template

@app.put("/api/video-style-templates/{template_id}", response_model=VideoStyleTemplateResponse)
async def update_video_style_template(template_id: int, template: VideoStyleTemplateCreate, db: Session = Depends(get_db)):
    db_template = db.query(models.VideoStyleTemplate).filter(models.VideoStyleTemplate.id == template_id).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")
    db_template.name = template.name
    db_template.sora_rule = template.sora_rule
    db_template.style_prompt = template.style_prompt
    db.commit()
    db.refresh(db_template)
    return db_template

@app.delete("/api/video-style-templates/{template_id}")
async def delete_video_style_template(template_id: int, db: Session = Depends(get_db)):
    db_template = db.query(models.VideoStyleTemplate).filter(models.VideoStyleTemplate.id == template_id).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")
    db.delete(db_template)
    db.commit()
    return {"message": "模板已删除"}

@app.post("/api/video-style-templates/{template_id}/set-default")
async def set_default_video_style_template(template_id: int, db: Session = Depends(get_db)):
    db_template = db.query(models.VideoStyleTemplate).filter(models.VideoStyleTemplate.id == template_id).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")
    db.query(models.VideoStyleTemplate).update({"is_default": False})
    db_template.is_default = True
    db.commit()
    return {"message": "已设置为默认模板", "template_id": template_id}


class LargeShotTemplateCreate(BaseModel):
    name: str
    content: str


class LargeShotTemplateResponse(BaseModel):
    id: int
    name: str
    content: str
    is_default: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


@app.get("/api/large-shot-templates", response_model=List[LargeShotTemplateResponse])
async def get_large_shot_templates(db: Session = Depends(get_db)):
    templates = db.query(models.LargeShotTemplate).order_by(
        models.LargeShotTemplate.is_default.desc(),
        models.LargeShotTemplate.created_at.asc(),
        models.LargeShotTemplate.id.asc(),
    ).all()
    return templates


@app.post("/api/large-shot-templates", response_model=LargeShotTemplateResponse)
async def create_large_shot_template(template: LargeShotTemplateCreate, db: Session = Depends(get_db)):
    name = (template.name or "").strip()
    content = (template.content or "").strip()
    if not name or not content:
        raise HTTPException(status_code=400, detail="模板名称和内容不能为空")

    new_template = models.LargeShotTemplate(
        name=name,
        content=content,
        is_default=False,
    )
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return new_template


@app.put("/api/large-shot-templates/{template_id}", response_model=LargeShotTemplateResponse)
async def update_large_shot_template(
    template_id: int,
    template: LargeShotTemplateCreate,
    db: Session = Depends(get_db)
):
    db_template = db.query(models.LargeShotTemplate).filter(
        models.LargeShotTemplate.id == template_id
    ).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    name = (template.name or "").strip()
    content = (template.content or "").strip()
    if not name or not content:
        raise HTTPException(status_code=400, detail="模板名称和内容不能为空")

    db_template.name = name
    db_template.content = content
    db.commit()
    db.refresh(db_template)
    return db_template


@app.delete("/api/large-shot-templates/{template_id}")
async def delete_large_shot_template(template_id: int, db: Session = Depends(get_db)):
    db_template = db.query(models.LargeShotTemplate).filter(
        models.LargeShotTemplate.id == template_id
    ).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    was_default = bool(db_template.is_default)
    db.delete(db_template)
    db.commit()

    if was_default:
        replacement = db.query(models.LargeShotTemplate).order_by(
            models.LargeShotTemplate.created_at.asc(),
            models.LargeShotTemplate.id.asc()
        ).first()
        if replacement:
            replacement.is_default = True
            db.commit()

    return {"message": "模板已删除"}


@app.post("/api/large-shot-templates/{template_id}/set-default")
async def set_default_large_shot_template(template_id: int, db: Session = Depends(get_db)):
    db_template = db.query(models.LargeShotTemplate).filter(
        models.LargeShotTemplate.id == template_id
    ).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    db.query(models.LargeShotTemplate).update({"is_default": False})
    db_template.is_default = True
    db.commit()
    return {"message": "已设置为默认模板", "template_id": template_id}

# ==================== 分镜图模板API ====================

class StoryboardRequirementTemplateCreate(BaseModel):
    name: str
    content: str

class StoryboardRequirementTemplateResponse(BaseModel):
    id: int
    name: str
    content: str
    is_default: bool = False
    created_at: datetime

    class Config:
        from_attributes = True

class StoryboardStyleTemplateCreate(BaseModel):
    name: str
    content: str

class StoryboardStyleTemplateResponse(BaseModel):
    id: int
    name: str
    content: str
    is_default: bool = False
    created_at: datetime

    class Config:
        from_attributes = True

# 绘图要求模板 CRUD
@app.get("/api/storyboard-templates/requirements", response_model=List[StoryboardRequirementTemplateResponse])
async def get_storyboard_requirement_templates(db: Session = Depends(get_db)):
    """获取所有分镜图绘图要求模板"""
    templates = db.query(models.StoryboardRequirementTemplate).order_by(
        models.StoryboardRequirementTemplate.created_at.desc()
    ).all()
    return templates

@app.post("/api/storyboard-templates/requirements", response_model=StoryboardRequirementTemplateResponse)
async def create_storyboard_requirement_template(
    template: StoryboardRequirementTemplateCreate,
    db: Session = Depends(get_db)
):
    """创建新的分镜图绘图要求模板"""
    new_template = models.StoryboardRequirementTemplate(
        name=template.name,
        content=template.content
    )
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return new_template

@app.put("/api/storyboard-templates/requirements/{template_id}", response_model=StoryboardRequirementTemplateResponse)
async def update_storyboard_requirement_template(
    template_id: int,
    template: StoryboardRequirementTemplateCreate,
    db: Session = Depends(get_db)
):
    """更新分镜图绘图要求模板"""
    db_template = db.query(models.StoryboardRequirementTemplate).filter(
        models.StoryboardRequirementTemplate.id == template_id
    ).first()

    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    db_template.name = template.name
    db_template.content = template.content
    db.commit()
    db.refresh(db_template)
    return db_template

@app.delete("/api/storyboard-templates/requirements/{template_id}")
async def delete_storyboard_requirement_template(
    template_id: int,
    db: Session = Depends(get_db)
):
    """删除分镜图绘图要求模板"""
    db_template = db.query(models.StoryboardRequirementTemplate).filter(
        models.StoryboardRequirementTemplate.id == template_id
    ).first()

    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    db.delete(db_template)
    db.commit()
    return {"message": "模板已删除"}

@app.post("/api/storyboard-templates/requirements/{template_id}/set-default")
async def set_default_requirement_template(
    template_id: int,
    db: Session = Depends(get_db)
):
    """设置默认分镜图绘图要求模板"""
    db_template = db.query(models.StoryboardRequirementTemplate).filter(
        models.StoryboardRequirementTemplate.id == template_id
    ).first()

    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    # 取消所有其他模板的默认状态
    db.query(models.StoryboardRequirementTemplate).update({"is_default": False})

    # 设置当前模板为默认
    db_template.is_default = True
    db.commit()
    db.refresh(db_template)

    return {"message": "已设置为默认模板", "template_id": template_id}

# 绘画风格模板 CRUD
@app.get("/api/storyboard-templates/styles", response_model=List[StoryboardStyleTemplateResponse])
async def get_storyboard_style_templates(db: Session = Depends(get_db)):
    """获取所有分镜图绘画风格模板"""
    templates = db.query(models.StoryboardStyleTemplate).order_by(
        models.StoryboardStyleTemplate.created_at.desc()
    ).all()
    return templates

@app.post("/api/storyboard-templates/styles", response_model=StoryboardStyleTemplateResponse)
async def create_storyboard_style_template(
    template: StoryboardStyleTemplateCreate,
    db: Session = Depends(get_db)
):
    """创建新的分镜图绘画风格模板"""
    new_template = models.StoryboardStyleTemplate(
        name=template.name,
        content=template.content
    )
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return new_template

@app.put("/api/storyboard-templates/styles/{template_id}", response_model=StoryboardStyleTemplateResponse)
async def update_storyboard_style_template(
    template_id: int,
    template: StoryboardStyleTemplateCreate,
    db: Session = Depends(get_db)
):
    """更新分镜图绘画风格模板"""
    db_template = db.query(models.StoryboardStyleTemplate).filter(
        models.StoryboardStyleTemplate.id == template_id
    ).first()

    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    db_template.name = template.name
    db_template.content = template.content
    db.commit()
    db.refresh(db_template)
    return db_template

@app.delete("/api/storyboard-templates/styles/{template_id}")
async def delete_storyboard_style_template(
    template_id: int,
    db: Session = Depends(get_db)
):
    """删除分镜图绘画风格模板"""
    db_template = db.query(models.StoryboardStyleTemplate).filter(
        models.StoryboardStyleTemplate.id == template_id
    ).first()

    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    db.delete(db_template)
    db.commit()
    return {"message": "模板已删除"}

@app.post("/api/storyboard-templates/styles/{template_id}/set-default")
async def set_default_style_template(
    template_id: int,
    db: Session = Depends(get_db)
):
    """设置默认分镜图绘画风格模板"""
    db_template = db.query(models.StoryboardStyleTemplate).filter(
        models.StoryboardStyleTemplate.id == template_id
    ).first()

    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    # 取消所有其他模板的默认状态
    db.query(models.StoryboardStyleTemplate).update({"is_default": False})

    # 设置当前模板为默认
    db_template.is_default = True
    db.commit()
    db.refresh(db_template)

    return {"message": "已设置为默认模板", "template_id": template_id}

# ==================== 故事板镜头API ====================

class ShotCreate(BaseModel):
    shot_number: int
    prompt_template: str = ""
    storyboard_video_prompt: str = ""
    storyboard_audio_prompt: str = ""
    storyboard_dialogue: str = ""
    sora_prompt: str = ""
    selected_card_ids: List[int] = []
    selected_sound_card_ids: Optional[List[int]] = None
    aspect_ratio: str = "16:9"
    duration: int = 15

class ShotUpdate(BaseModel):
    prompt_template: Optional[str] = None
    script_excerpt: Optional[str] = None
    storyboard_video_prompt: Optional[str] = None
    storyboard_audio_prompt: Optional[str] = None
    storyboard_dialogue: Optional[str] = None
    scene_override: Optional[str] = None  # 场景描述（用户可编辑）
    scene_override_locked: Optional[bool] = None
    sora_prompt: Optional[str] = None
    sora_prompt_status: Optional[str] = None
    selected_card_ids: Optional[List[int]] = None
    selected_sound_card_ids: Optional[List[int]] = None
    aspect_ratio: Optional[str] = None
    duration: Optional[int] = None
    storyboard_video_model: Optional[str] = None
    storyboard_video_model_override_enabled: Optional[bool] = None
    duration_override_enabled: Optional[bool] = None
    provider: Optional[str] = None
    storyboard_image_path: Optional[str] = None
    storyboard_image_status: Optional[str] = None
    storyboard_image_model: Optional[str] = None
    first_frame_reference_image_url: Optional[str] = None
    uploaded_scene_image_url: Optional[str] = None
    use_uploaded_scene_image: Optional[bool] = None

class ManualSoraPromptRequest(BaseModel):
    sora_prompt: str

class ShotResponse(BaseModel):
    id: int
    episode_id: int
    shot_number: int
    variant_index: int
    prompt_template: str
    script_excerpt: str
    storyboard_video_prompt: str
    storyboard_audio_prompt: str
    storyboard_dialogue: str
    scene_override: str  # 场景描述（用户可编辑）
    scene_override_locked: bool = False  # 场景描述是否锁定（不再自动填充）
    sora_prompt: Optional[str]  # 允许为None，支持"生成中"状态
    sora_prompt_status: str  # idle/generating/completed/failed
    selected_card_ids: str
    selected_sound_card_ids: Optional[str] = None
    video_path: str
    thumbnail_video_path: str
    video_status: str  # idle/processing/completed/failed
    task_id: str  # Sora??ID
    managed_task_id: str = ""  # 托管镜头当前/最近一次上游任务ID（task_id 为空时用于展示）
    aspect_ratio: str
    duration: int
    storyboard_video_model: str = ""
    storyboard_video_model_override_enabled: bool = False
    duration_override_enabled: bool = False
    provider: str
    storyboard_image_path: str  # 分镜图路径
    storyboard_image_status: str  # 分镜图状态: idle/processing/completed/failed
    storyboard_image_task_id: str  # 分镜图任务ID
    first_frame_reference_image_url: str = ""  # 视频首帧参考图 URL（为空表示未选择）
    uploaded_scene_image_url: str = ""  # 镜头级上传的场景图片 URL
    use_uploaded_scene_image: bool = False  # 是否使用镜头级上传场景图
    selected_scene_image_url: str = ""  # 当前生效的场景图片 URL
    timeline_json: Optional[str] = ""  # Sora解析后的时间线JSON（用于镜头图弹窗选子镜头）
    detail_image_prompt_overrides: Optional[str] = "{}"  # 镜头图文案覆盖（JSON）
    detail_images_status: str  # 镜头细化图片状态: idle/processing/completed/failed
    detail_images_progress: Optional[str] = None  # 镜头细化图片进度: "3/5"
    detail_images_preview_path: Optional[str] = None  # 镜头细化图片预览（取第一张）
    created_at: datetime

    class Config:
        from_attributes = True

class ShotVideoResponse(BaseModel):
    id: int
    shot_id: int
    video_path: str
    created_at: datetime

    class Config:
        from_attributes = True

class GenerateVideoRequest(BaseModel):
    appoint_account: Optional[str] = None

class CancelVideoTasksRequest(BaseModel):
    task_ids: List[str]

class ThumbnailUpdate(BaseModel):
    video_id: int

class BatchGenerateSoraPromptsRequest(BaseModel):
    default_template: str = "2d漫画风格（细）"
    shot_ids: Optional[List[int]] = None  # 可选：指定要生成的镜头ID列表
    duration: Optional[int] = None  # 可选：视频时长（秒），用于选择对应的时长模板

class BatchGenerateSoraPromptsResponse(BaseModel):
    success_count: int
    failed_count: int
    total_count: int

class BatchGenerateSoraVideosRequest(BaseModel):
    # 以下字段保留兼容旧前端，实际以剧集「视频设置」为准
    aspect_ratio: Optional[str] = None
    duration: Optional[int] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    appoint_account: Optional[str] = None
    shot_ids: Optional[List[int]] = None  # 可选：指定要生成的镜头ID列表

# 托管生成相关模型
class ManagedTaskResponse(BaseModel):
    id: int
    session_id: int
    shot_id: int
    shot_stable_id: str
    shot_number: int
    variant_index: int
    video_path: str
    status: str  # pending/processing/completed/failed
    error_message: str
    task_id: str
    prompt_text: str = ""
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True

class StartManagedGenerationRequest(BaseModel):
    # 以下字段保留兼容旧前端，实际以剧集「视频设置」为准
    provider: Optional[str] = None
    model: Optional[str] = None
    aspect_ratio: Optional[str] = None
    duration: Optional[int] = None
    shot_ids: Optional[List[int]] = None  # 可选：指定镜头ID列表
    variant_count: int = 1  # 每个镜头生成的视频数量，默认1

class ManagedSessionStatusResponse(BaseModel):
    session_id: Optional[int]
    status: str  # running/detached/completed/failed/stopped/none
    total_shots: int
    completed_shots: int
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class VideoStatusInfoResponse(BaseModel):
    shot_id: int
    task_id: str
    status: str
    progress: int = 0
    info: str = ""
    error_message: str = ""

@app.post("/api/episodes/{episode_id}/shots", response_model=ShotResponse)
async def create_shot(
    episode_id: int,
    shot: ShotCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建或更新镜头"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 检查镜头是否已存在（查询所有变体）
    existing_shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id,
        models.StoryboardShot.shot_number == shot.shot_number,
        models.StoryboardShot.variant_index == 0
    ).all()

    sound_card_ids_in_payload = "selected_sound_card_ids" in getattr(shot, "model_fields_set", set())
    normalized_sound_card_ids = None
    if sound_card_ids_in_payload:
        normalized_sound_card_ids = _normalize_storyboard_selected_sound_card_ids(
            shot.selected_sound_card_ids,
            episode_id,
            db
        )

    if existing_shots:
        # 更新所有变体的镜头（保持右侧数据同步）
        for existing_shot in existing_shots:
            existing_shot.prompt_template = shot.prompt_template
            existing_shot.storyboard_video_prompt = shot.storyboard_video_prompt
            existing_shot.storyboard_audio_prompt = shot.storyboard_audio_prompt
            existing_shot.storyboard_dialogue = shot.storyboard_dialogue
            existing_shot.sora_prompt = shot.sora_prompt
            existing_shot.selected_card_ids = json.dumps(shot.selected_card_ids)
            if sound_card_ids_in_payload:
                existing_shot.selected_sound_card_ids = (
                    json.dumps(normalized_sound_card_ids, ensure_ascii=False)
                    if normalized_sound_card_ids is not None
                    else None
                )
            existing_shot.aspect_ratio = shot.aspect_ratio
            existing_shot.duration = shot.duration
        db.commit()
        db.refresh(existing_shots[0])

        # 计算detail_images_status
        detail_images = db.query(models.ShotDetailImage).filter(
            models.ShotDetailImage.shot_id == existing_shots[0].id
        ).all()

        if not detail_images:
            existing_shots[0].detail_images_status = 'idle'
        else:
            has_completed = any(img.status == 'completed' for img in detail_images)
            has_processing = any(img.status == 'processing' for img in detail_images)
            has_pending = any(img.status == 'pending' for img in detail_images)
            all_failed = all(img.status == 'failed' for img in detail_images)

            if has_completed:
                # 只要有成功的，就算completed（前端可以预览，会显示部分成功）
                existing_shots[0].detail_images_status = 'completed'
            elif has_processing or has_pending:
                existing_shots[0].detail_images_status = 'processing'
            elif all_failed:
                existing_shots[0].detail_images_status = 'failed'
            else:
                existing_shots[0].detail_images_status = 'idle'

        existing_shots[0].selected_scene_image_url = _resolve_selected_scene_reference_image_url(existing_shots[0], db)
        return existing_shots[0]
    else:
        created_shots = []
        for _ in [None]:
            new_shot = models.StoryboardShot(
                episode_id=episode_id,
                shot_number=shot.shot_number,
                stable_id=str(uuid.uuid4()),  # 生成stable_id
                variant_index=0,
                prompt_template=shot.prompt_template,
                storyboard_video_prompt=shot.storyboard_video_prompt,
                storyboard_audio_prompt=shot.storyboard_audio_prompt,
                storyboard_dialogue=shot.storyboard_dialogue,
                sora_prompt=shot.sora_prompt,
                selected_card_ids=json.dumps(shot.selected_card_ids),
                selected_sound_card_ids=(
                    json.dumps(normalized_sound_card_ids, ensure_ascii=False)
                    if sound_card_ids_in_payload and normalized_sound_card_ids is not None
                    else None
                ),
                aspect_ratio=shot.aspect_ratio,
                duration=shot.duration,
                storyboard_video_model="",
                storyboard_video_model_override_enabled=False,
                duration_override_enabled=False,
            )
            db.add(new_shot)
            created_shots.append(new_shot)
        db.commit()
        db.refresh(new_shot)

        # 新创建的镜头没有detail_images
        new_shot.detail_images_status = 'idle'
        new_shot.selected_scene_image_url = _resolve_selected_scene_reference_image_url(new_shot, db)

        return new_shot

def _sync_processing_storyboard_videos_for_episode(
    episode_id: int,
    db: Session,
    max_count: int = 12,
) -> int:
    """
    兜底同步故事板视频状态。
    作用：当独立 poller 落后或重启后，前端拉取镜头列表时仍能推进 processing -> failed/completed。
    """
    candidate_rows = db.query(models.StoryboardShot.id).filter(
        models.StoryboardShot.episode_id == episode_id,
        models.StoryboardShot.task_id != "",
        models.StoryboardShot.video_status.in_(["submitting", "preparing", "processing"]),
    ).order_by(
        models.StoryboardShot.video_submitted_at.asc().nullsfirst(),
        models.StoryboardShot.id.asc(),
    ).limit(max_count).all()

    if not candidate_rows:
        return 0

    updated_count = 0
    for row in candidate_rows:
        shot_id = int(getattr(row, "id", row) or 0)
        if shot_id <= 0:
            continue
        try:
            shot = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.id == shot_id
            ).first()
            if not shot:
                continue

            result = check_video_status(str(shot.task_id or "").strip())
            if is_transient_video_status_error(result):
                continue

            normalized_status = normalize_video_generation_status(
                result.get("status"),
                default_value="processing",
            )
            error_message = str(result.get("error_message") or "").strip()

            if normalized_status == "failed":
                target_video_path = f"error:{error_message}" if error_message else "error:任务失败"
                changed = False
                if (shot.video_status or "").strip().lower() != "failed":
                    shot.video_status = "failed"
                    changed = True
                if str(shot.video_path or "") != target_video_path:
                    shot.video_path = target_video_path
                    changed = True
                if str(shot.thumbnail_video_path or "").strip():
                    shot.thumbnail_video_path = ""
                    changed = True
                if str(shot.video_error_message or "") != error_message:
                    shot.video_error_message = error_message
                    changed = True
                if changed:
                    updated_count += 1
                continue

            if normalized_status == "completed":
                video_url = str(result.get("video_url") or "").strip()
                changed = False
                previous_video_path = str(shot.video_path or "")
                previous_thumbnail = str(shot.thumbnail_video_path or "")

                if (shot.video_status or "").strip().lower() != "completed":
                    shot.video_status = "completed"
                    changed = True
                if video_url and previous_video_path != video_url:
                    shot.video_path = video_url
                    changed = True
                if video_url and (
                    not previous_thumbnail or previous_thumbnail == previous_video_path
                ):
                    if previous_thumbnail != video_url:
                        shot.thumbnail_video_path = video_url
                        changed = True
                if str(shot.video_error_message or "").strip():
                    shot.video_error_message = ""
                    changed = True
                if changed:
                    updated_count += 1
                continue

            if normalized_status in {"pending", "processing"}:
                if (shot.video_status or "").strip().lower() != "processing":
                    shot.video_status = "processing"
                    updated_count += 1
                if not shot.video_submitted_at:
                    shot.video_submitted_at = datetime.utcnow()
                    updated_count += 1
                continue

        except Exception as e:
            print(f"[storyboard-video-sync] failed for shot {shot_id}: {str(e)}")

    if updated_count:
        db.commit()
        db.expire_all()
    return updated_count


@app.get("/api/episodes/{episode_id}/shots", response_model=List[ShotResponse])
def get_episode_shots(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取片段的所有镜头"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    _sync_processing_storyboard_videos_for_episode(episode_id, db)
    if _reconcile_episode_runtime_flags(episode, db):
        db.commit()


    # 构建查询
    query = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == episode_id
    )


    shots = query.order_by(
        models.StoryboardShot.shot_number.asc(),
        models.StoryboardShot.variant_index.asc()
    ).all()

    # ✅ 去重：确保每个(shot_number, variant_index)组合只保留一条记录
    seen_keys = set()
    unique_shots = []
    for shot in shots:
        key = (shot.shot_number, shot.variant_index)
        if key not in seen_keys:
            seen_keys.add(key)
            unique_shots.append(shot)
    shots = unique_shots

    shot_ids = [shot.id for shot in shots]
    managed_task_id_map = {}
    if shot_ids:
        latest_video_map = {}
        videos = db.query(models.ShotVideo).filter(
            models.ShotVideo.shot_id.in_(shot_ids)
        ).order_by(
            models.ShotVideo.shot_id.asc(),
            models.ShotVideo.created_at.desc()
        ).all()

        for video in videos:
            if video.shot_id not in latest_video_map:
                latest_video_map[video.shot_id] = video.video_path

        needs_commit = False
        for shot in shots:
            if not (shot.thumbnail_video_path or "").strip():
                latest_path = latest_video_map.get(shot.id)
                if latest_path:
                    shot.thumbnail_video_path = latest_path
                    needs_commit = True

        if needs_commit:
            db.commit()

        managed_tasks = db.query(models.ManagedTask).filter(
            models.ManagedTask.shot_id.in_(shot_ids),
            models.ManagedTask.task_id != ""
        ).order_by(
            models.ManagedTask.shot_id.asc(),
            models.ManagedTask.id.desc()
        ).all()
        for managed_task in managed_tasks:
            if managed_task.shot_id not in managed_task_id_map:
                managed_task_id_map[managed_task.shot_id] = managed_task.task_id or ""

    # 查询每个shot的detail_images状态
    detail_images_status_map = {}
    detail_images_preview_map = {}
    detail_images = []
    if shot_ids:
        # 批量查询所有shot的detail_images
        detail_images = db.query(models.ShotDetailImage).filter(
            models.ShotDetailImage.shot_id.in_(shot_ids)
        ).order_by(
            models.ShotDetailImage.shot_id.asc(),
            models.ShotDetailImage.sub_shot_index.asc(),
            models.ShotDetailImage.id.asc()
        ).all()

        # 按shot_id分组
        detail_images_by_shot = {}
        for detail_img in detail_images:
            if detail_img.shot_id not in detail_images_by_shot:
                detail_images_by_shot[detail_img.shot_id] = []
            detail_images_by_shot[detail_img.shot_id].append(detail_img)

        # 计算每个shot的状态
        for shot_id, imgs in detail_images_by_shot.items():
            # 优先检查是否有completed，如果有任何completed就可以预览
            has_completed = any(img.status == 'completed' for img in imgs)
            has_processing = any(img.status == 'processing' for img in imgs)
            has_pending = any(img.status == 'pending' for img in imgs)
            all_completed = all(img.status == 'completed' for img in imgs)
            all_failed = all(img.status == 'failed' for img in imgs)

            if has_completed:
                # 只要有成功的，就算completed（前端可以预览，会显示部分成功）
                detail_images_status_map[shot_id] = 'completed'
            elif has_processing or has_pending:
                # 没有成功的，但有进行中的
                detail_images_status_map[shot_id] = 'processing'
            elif all_failed:
                detail_images_status_map[shot_id] = 'failed'
            else:
                detail_images_status_map[shot_id] = 'idle'

            # 提取预览图：按子镜头顺序取第一条成功记录的第一张图
            preview_url = None
            for detail_img in imgs:
                if detail_img.status != 'completed' or not detail_img.images_json:
                    continue
                try:
                    parsed_images = json.loads(detail_img.images_json)
                except Exception:
                    parsed_images = []
                if not isinstance(parsed_images, list):
                    continue
                for image_url in parsed_images:
                    if isinstance(image_url, str) and image_url.strip():
                        preview_url = image_url.strip()
                        break
                if preview_url:
                    break

            if preview_url:
                detail_images_preview_map[shot_id] = preview_url

    # 添加detail_images_status和detail_images_progress到shot对象
    for shot in shots:
        shot.detail_images_status = detail_images_status_map.get(shot.id, 'idle')
        shot.detail_images_preview_path = detail_images_preview_map.get(shot.id)

        # 计算detail_images_progress
        shot_detail_images = [img for img in detail_images if img.shot_id == shot.id]
        if shot_detail_images:
            completed_count = sum(1 for img in shot_detail_images if img.status == 'completed')
            total_count = len(shot_detail_images)

            if shot.detail_images_status == 'processing':
                # 生成中，显示进度如"2/5"
                shot.detail_images_progress = f"{completed_count}/{total_count}"
            elif shot.detail_images_status == 'completed':
                # 已完成，判断是否全部成功
                if completed_count == total_count:
                    # 全部成功，不显示进度（前端会显示"点击查看"）
                    shot.detail_images_progress = None
                else:
                    # 部分失败，显示如"3/5"（有2个失败）
                    shot.detail_images_progress = f"{completed_count}/{total_count}"
            else:
                shot.detail_images_progress = None
        else:
            shot.detail_images_progress = None

    # 使用 model_validate 强制Pydantic读取动态属性
    response_shots = []
    for shot in shots:
        try:
            # 使用from_attributes确保动态属性被读取
            shot_dict = {
                'id': shot.id,
                'episode_id': shot.episode_id,
                'shot_number': shot.shot_number,
                'variant_index': shot.variant_index,
                'prompt_template': shot.prompt_template,
                'script_excerpt': shot.script_excerpt,
                'storyboard_video_prompt': shot.storyboard_video_prompt,
                'storyboard_audio_prompt': shot.storyboard_audio_prompt,
                'storyboard_dialogue': shot.storyboard_dialogue,
                'scene_override': shot.scene_override,
                'scene_override_locked': bool(getattr(shot, 'scene_override_locked', False)),
                'sora_prompt': shot.sora_prompt,
                'sora_prompt_status': shot.sora_prompt_status,
                'selected_card_ids': shot.selected_card_ids,
                'selected_sound_card_ids': shot.selected_sound_card_ids,
                'video_path': shot.video_path or "",
                'thumbnail_video_path': shot.thumbnail_video_path or "",
                'video_status': shot.video_status,
                'task_id': shot.task_id or "",
                'managed_task_id': managed_task_id_map.get(shot.id, ""),
                'aspect_ratio': shot.aspect_ratio,
                'duration': shot.duration,
                'storyboard_video_model': getattr(shot, 'storyboard_video_model', "") or "",
                'storyboard_video_model_override_enabled': bool(getattr(shot, 'storyboard_video_model_override_enabled', False)),
                'duration_override_enabled': bool(getattr(shot, 'duration_override_enabled', False)),
                'provider': shot.provider,
                'storyboard_image_path': shot.storyboard_image_path or "",
                'storyboard_image_status': shot.storyboard_image_status,
                'storyboard_image_task_id': shot.storyboard_image_task_id or "",
                'first_frame_reference_image_url': getattr(shot, 'first_frame_reference_image_url', "") or "",
                'uploaded_scene_image_url': getattr(shot, 'uploaded_scene_image_url', "") or "",
                'use_uploaded_scene_image': bool(getattr(shot, 'use_uploaded_scene_image', False)),
                'selected_scene_image_url': _resolve_selected_scene_reference_image_url(shot, db),
                'timeline_json': shot.timeline_json or "",
                'detail_image_prompt_overrides': shot.detail_image_prompt_overrides or "{}",
                'detail_images_status': shot.detail_images_status,
                'detail_images_progress': shot.detail_images_progress,
                'detail_images_preview_path': shot.detail_images_preview_path,
                'created_at': shot.created_at
            }
            shot_response = ShotResponse(**shot_dict)
            response_shots.append(shot_response)
        except Exception as e:
            print(f"[get_episode_shots] 错误: 转换shot对象失败 shot_id={shot.id}")
            print(f"[get_episode_shots] 错误详情: {str(e)}")
            print(f"[get_episode_shots] shot_dict: {shot_dict}")
            import traceback
            traceback.print_exc()
            # 如果转换失败，使用model_validate with默认值
            try:
                # 尝试使用from_attributes直接转换
                response_shots.append(ShotResponse.model_validate(shot, from_attributes=True))
            except:
                # 最后的尝试，手动设置所有可能为空的字段
                shot_dict['sora_prompt'] = shot_dict.get('sora_prompt') or ""
                shot_dict['video_path'] = shot_dict.get('video_path') or ""
                shot_dict['thumbnail_video_path'] = shot_dict.get('thumbnail_video_path') or ""
                response_shots.append(ShotResponse(**shot_dict))

    return response_shots


@app.get("/api/shots/{shot_id}/video-status-info", response_model=VideoStatusInfoResponse)
def get_shot_video_status_info(
    shot_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    shot = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.id == shot_id
    ).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if not script or script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    task_id = str((shot.task_id or "").strip() or "").strip()
    if not task_id:
        managed_task_row = (
            db.query(models.ManagedTask.task_id)
            .filter(
                models.ManagedTask.shot_id == shot.id,
                models.ManagedTask.task_id != ""
            )
            .order_by(models.ManagedTask.id.desc())
            .first()
        )
        task_id = str((managed_task_row[0] if managed_task_row else "") or "").strip()

    if not task_id:
        raise HTTPException(status_code=400, detail="当前镜头没有可查询的任务ID")

    result = check_video_status(task_id)
    if is_transient_video_status_error(result):
        raise HTTPException(status_code=503, detail=result.get("error_message") or "暂时无法获取任务信息")

    status = str(result.get("status") or "").strip().lower()
    progress = int(result.get("progress") or 0)
    info = str(result.get("info") or "").strip()
    error_message = str(result.get("error_message") or "").strip()

    if not info and status in {"pending", "queued", "processing"}:
        if progress > 0:
            info = f"当前任务正在处理中，进度 {progress}%"
        else:
            info = "当前任务正在处理中，服务商暂未返回更多排队信息。"

    return VideoStatusInfoResponse(
        shot_id=shot.id,
        task_id=task_id,
        status=status or "unknown",
        progress=progress,
        info=info,
        error_message=error_message
    )

@app.put("/api/shots/{shot_id}", response_model=ShotResponse)
async def update_shot(
    shot_id: int,
    shot_data: ShotUpdate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新镜头"""
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # ✅ 找到所有变体的对应镜头（右侧数据需要同步）
    all_variant_shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == shot.episode_id,
        models.StoryboardShot.shot_number == shot.shot_number,
        models.StoryboardShot.variant_index == shot.variant_index
    ).all()
    sound_card_ids_in_payload = "selected_sound_card_ids" in getattr(shot_data, "model_fields_set", set())
    normalized_sound_card_ids = None
    if sound_card_ids_in_payload:
        normalized_sound_card_ids = _normalize_storyboard_selected_sound_card_ids(
            shot_data.selected_sound_card_ids,
            shot.episode_id,
            db
        )

    # ✅ 更新所有变体的镜头（保持右侧数据同步）
    for variant_shot in all_variant_shots:
        if shot_data.prompt_template is not None:
            variant_shot.prompt_template = shot_data.prompt_template
        if shot_data.script_excerpt is not None:
            variant_shot.script_excerpt = shot_data.script_excerpt
        if shot_data.storyboard_video_prompt is not None:
            variant_shot.storyboard_video_prompt = shot_data.storyboard_video_prompt
        if shot_data.storyboard_audio_prompt is not None:
            variant_shot.storyboard_audio_prompt = shot_data.storyboard_audio_prompt
        if shot_data.storyboard_dialogue is not None:
            variant_shot.storyboard_dialogue = shot_data.storyboard_dialogue
        if shot_data.scene_override is not None:
            variant_shot.scene_override = shot_data.scene_override
            # 🔒 用户手动保存场景描述后，锁定自动填充（不再跟随场景主体自动更新）
            variant_shot.scene_override_locked = True
        if shot_data.scene_override_locked is not None:
            variant_shot.scene_override_locked = bool(shot_data.scene_override_locked)
        if shot_data.sora_prompt is not None:
            variant_shot.sora_prompt = shot_data.sora_prompt
            # 打印保存的Sora提示词
            if variant_shot.id == shot.id:  # 只打印一次
                print("=" * 80)
                print(f"[保存Sora提示词] 镜头ID: {shot.id}, 镜号: {shot.shot_number}")
                print("-" * 80)
                print("完整的Sora提示词:")
                print(shot_data.sora_prompt)
                print("=" * 80)
        if shot_data.sora_prompt_status is not None:
            variant_shot.sora_prompt_status = str(shot_data.sora_prompt_status or "").strip() or "idle"
        if shot_data.selected_card_ids is not None:
            variant_shot.selected_card_ids = json.dumps(shot_data.selected_card_ids)
        if sound_card_ids_in_payload:
            variant_shot.selected_sound_card_ids = (
                json.dumps(normalized_sound_card_ids, ensure_ascii=False)
                if normalized_sound_card_ids is not None
                else None
            )
        if shot_data.aspect_ratio is not None:
            variant_shot.aspect_ratio = shot_data.aspect_ratio
        if shot_data.duration is not None:
            variant_shot.duration = shot_data.duration
        if shot_data.storyboard_video_model is not None:
            variant_shot.storyboard_video_model = _normalize_storyboard_video_model(
                shot_data.storyboard_video_model,
                default_model=getattr(episode, "storyboard_video_model", DEFAULT_STORYBOARD_VIDEO_MODEL)
            )
        if shot_data.storyboard_video_model_override_enabled is not None:
            variant_shot.storyboard_video_model_override_enabled = bool(
                shot_data.storyboard_video_model_override_enabled
            )
        if shot_data.duration_override_enabled is not None:
            variant_shot.duration_override_enabled = bool(shot_data.duration_override_enabled)
        if shot_data.provider is not None:
            variant_shot.provider = shot_data.provider
        if shot_data.storyboard_image_path is not None:
            variant_shot.storyboard_image_path = str(shot_data.storyboard_image_path or "").strip()
        if shot_data.storyboard_image_status is not None:
            variant_shot.storyboard_image_status = str(shot_data.storyboard_image_status or "").strip()
        if shot_data.storyboard_image_model is not None:
            variant_shot.storyboard_image_model = str(shot_data.storyboard_image_model or "").strip()
        if shot_data.first_frame_reference_image_url is not None:
            variant_shot.first_frame_reference_image_url = normalize_first_frame_candidate_url(
                shot_data.first_frame_reference_image_url
            )
        if shot_data.uploaded_scene_image_url is not None:
            variant_shot.uploaded_scene_image_url = str(shot_data.uploaded_scene_image_url or "").strip()
        if shot_data.use_uploaded_scene_image is not None:
            variant_shot.use_uploaded_scene_image = bool(shot_data.use_uploaded_scene_image)

        _backfill_storyboard_visual_references_from_family(variant_shot, db)
        _apply_episode_storyboard_video_settings_to_shot(variant_shot, episode)

    db.commit()
    db.refresh(shot)

    # ✅ 调试：打印返回的shot对象的sora_prompt
    print(f"[返回镜头数据] ID: {shot.id}, sora_prompt长度: {len(shot.sora_prompt or '')}")
    print(f"[返回内容预览] {(shot.sora_prompt or '')[:100]}")

    # 计算detail_images_status
    detail_images = db.query(models.ShotDetailImage).filter(
        models.ShotDetailImage.shot_id == shot.id
    ).all()

    if not detail_images:
        shot.detail_images_status = 'idle'
    else:
        has_completed = any(img.status == 'completed' for img in detail_images)
        has_processing = any(img.status == 'processing' for img in detail_images)
        has_pending = any(img.status == 'pending' for img in detail_images)
        all_failed = all(img.status == 'failed' for img in detail_images)

        if has_completed:
            # 只要有成功的，就算completed（前端可以预览，会显示部分成功）
            shot.detail_images_status = 'completed'
        elif has_processing or has_pending:
            shot.detail_images_status = 'processing'
        elif all_failed:
            shot.detail_images_status = 'failed'
        else:
            shot.detail_images_status = 'idle'

    shot.selected_scene_image_url = _resolve_selected_scene_reference_image_url(shot, db)
    return shot

@app.get("/api/shots/{shot_id}/extract-scene")
async def extract_scene_from_cards(
    shot_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """从镜头的选中主体卡片中提取场景描述"""
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 提取场景描述
    scene_description = extract_scene_description(shot, db)

    return {
        "scene_description": scene_description,
        "shot_id": shot_id
    }

@app.delete("/api/episodes/{episode_id}")
async def delete_episode(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除剧集（级联删除所有关联镜头）"""
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="剧集不存在")

    # 验证权限
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if not script:
        raise HTTPException(status_code=404, detail="关联剧本不存在")
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    episode_cleanup_stats = _clear_episode_dependencies([episode_id], db)

    print(
        "[剧集删除清理] "
        f"episode_id={episode_id} "
        f"simple_batches={episode_cleanup_stats['deleted_simple_storyboard_batches']} "
        f"managed_tasks={episode_cleanup_stats['deleted_managed_tasks']} "
        f"managed_sessions={episode_cleanup_stats['deleted_managed_sessions']} "
        f"voiceover_tts_tasks={episode_cleanup_stats['deleted_voiceover_tts_tasks']} "
        f"unlinked_libraries={episode_cleanup_stats['unlinked_libraries']}"
    )

    # 删除剧集（会级联删除所有关联的镜头和视频记录）
    db.delete(episode)
    db.commit()

    return {"message": "剧集删除成功", "episode_id": episode_id}

@app.delete("/api/shots/{shot_id}")
async def delete_shot(
    shot_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除镜头。删除主镜头时删除整组变体，删除变体时仅删除当前变体。"""
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    # 验证权限
    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    variant_index = int(getattr(shot, "variant_index", 0) or 0)
    if variant_index > 0:
        if _is_storyboard_shot_generation_active(shot, db):
            raise HTTPException(status_code=400, detail="当前变体镜头正在生成中，请等待完成")
        shot_ids_to_delete = [shot.id]
        deleted_scope = "variant"
    else:
        if _count_active_video_generations_for_shot_family(shot, db) > 0:
            raise HTTPException(status_code=400, detail="当前镜头或其变体正在生成中，请等待完成")
        shot_ids_to_delete = _get_storyboard_shot_family_ids(shot, db)
        deleted_scope = "family"

    deleted_count = _delete_storyboard_shots_by_ids(
        shot_ids_to_delete,
        db,
        log_context=(
            f"episode_id={shot.episode_id} "
            f"shot_number={shot.shot_number} "
            f"stable_id={str(getattr(shot, 'stable_id', '') or '').strip() or '<empty>'}"
        ),
        allow_zero=True
    )

    db.commit()

    return {
        "message": "镜头删除成功",
        "shot_id": shot_id,
        "deleted_count": deleted_count,
        "deleted_scope": deleted_scope,
    }

@app.post("/api/shots/{shot_id}/duplicate", response_model=ShotResponse)
async def duplicate_shot(
    shot_id: int,
    request: dict = {},  # 新增：接收请求体
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """复制镜头生成新的变体"""
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    if _count_active_video_generations_for_shot_family(shot, db) > 0:
        raise HTTPException(status_code=400, detail="当前镜头已有正在生成中的视频，请等待完成")

    # 准备复制镜头


    # 查询该镜头号的最大变体序号
    max_variant = db.query(func.max(models.StoryboardShot.variant_index)).filter(
        models.StoryboardShot.episode_id == shot.episode_id,
        models.StoryboardShot.shot_number == shot.shot_number,
    ).scalar()
    next_variant = (max_variant or 0) + 1


    new_shot = models.StoryboardShot(
        **build_duplicate_shot_payload(
            shot,
            next_variant=next_variant,
        )
    )
    db.add(new_shot)
    db.flush()
    _backfill_storyboard_visual_references_from_family(new_shot, db)
    db.commit()
    db.refresh(new_shot)

    # 新复制的镜头没有detail_images（detail_images不会被复制）
    new_shot.detail_images_status = 'idle'
    new_shot.selected_scene_image_url = _resolve_selected_scene_reference_image_url(new_shot, db)

    return new_shot


def _debug_parse_card_ids(raw_value) -> List[int]:
    """Best-effort parser for selected_card_ids debug logging."""
    if raw_value is None:
        return []
    try:
        parsed = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []

    resolved = []
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
        resolved.append(card_id)
    return resolved


def _debug_resolve_subject_names(
    db: Session,
    selected_ids: List[int],
    library_id: Optional[int] = None
) -> List[str]:
    """Resolve subject names in selected order for debug logging."""
    if not selected_ids:
        return []

    query = db.query(models.SubjectCard).filter(
        models.SubjectCard.id.in_(selected_ids)
    )
    if library_id is not None:
        query = query.filter(models.SubjectCard.library_id == library_id)

    cards = query.all()
    card_name_map = {card.id: card.name for card in cards if card and card.name}
    return [card_name_map[card_id] for card_id in selected_ids if card_id in card_name_map]


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


def _parse_storyboard_sound_card_ids(raw_value: Any) -> Optional[List[int]]:
    """Parse storyboard selected_sound_card_ids; None means auto/default mode."""
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        if not raw_value.strip():
            return None
        try:
            parsed = json.loads(raw_value)
        except Exception:
            return None
    else:
        parsed = raw_value

    if parsed is None:
        return None
    if not isinstance(parsed, list):
        return None

    normalized = []
    seen = set()
    for item in parsed:
        try:
            card_id = int(item)
        except Exception:
            continue
        if card_id <= 0 or card_id in seen:
            continue
        seen.add(card_id)
        normalized.append(card_id)
    return normalized


def _get_episode_story_library(episode_id: int, db: Session) -> Optional[models.StoryLibrary]:
    return db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == episode_id
    ).first()


def _normalize_storyboard_selected_sound_card_ids(
    raw_ids: Optional[List[int]],
    episode_id: int,
    db: Session
) -> Optional[List[int]]:
    if raw_ids is None:
        return None

    normalized = []
    seen = set()
    for item in raw_ids:
        try:
            card_id = int(item)
        except Exception:
            continue
        if card_id <= 0 or card_id in seen:
            continue
        seen.add(card_id)
        normalized.append(card_id)

    if not normalized:
        return []

    library = _get_episode_story_library(episode_id, db)
    if not library:
        raise HTTPException(status_code=400, detail="当前片段未创建主体库，无法保存声音选择")

    valid_rows = db.query(models.SubjectCard.id).filter(
        models.SubjectCard.id.in_(normalized),
        models.SubjectCard.library_id == library.id,
        models.SubjectCard.card_type == "声音"
    ).all()
    valid_ids = {row[0] for row in valid_rows}
    invalid_ids = [card_id for card_id in normalized if card_id not in valid_ids]
    if invalid_ids:
        raise HTTPException(status_code=400, detail=f"存在无效声音卡片ID: {invalid_ids}")
    return [card_id for card_id in normalized if card_id in valid_ids]


def _resolve_storyboard_selected_sound_cards(
    shot: models.StoryboardShot,
    db: Session
) -> List[models.SubjectCard]:
    library = _get_episode_story_library(shot.episode_id, db)
    if not library:
        return []

    sound_cards = db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library.id,
        models.SubjectCard.card_type == "声音"
    ).all()
    if not sound_cards:
        return []

    sound_card_map = {card.id: card for card in sound_cards}
    explicit_sound_ids = _parse_storyboard_sound_card_ids(getattr(shot, "selected_sound_card_ids", None))
    if explicit_sound_ids is not None:
        return [sound_card_map[card_id] for card_id in explicit_sound_ids if card_id in sound_card_map]

    selected_role_cards = []
    try:
        selected_ids = json.loads(getattr(shot, "selected_card_ids", "[]") or "[]")
    except Exception:
        selected_ids = []
    if selected_ids:
        selected_cards = _resolve_selected_cards(db, selected_ids, library.id)
        selected_role_cards = [card for card in selected_cards if getattr(card, "card_type", "") == "角色"]

    linked_sound_map = {}
    fallback_name_map = {}
    narrator_card = None
    for sound_card in sound_cards:
        if (sound_card.name or "").strip() == "旁白" and narrator_card is None:
            narrator_card = sound_card
        linked_id = getattr(sound_card, "linked_card_id", None)
        if linked_id and linked_id not in linked_sound_map:
            linked_sound_map[int(linked_id)] = sound_card
        name_key = (sound_card.name or "").strip()
        if name_key and name_key not in fallback_name_map:
            fallback_name_map[name_key] = sound_card

    resolved = []
    seen = set()
    for role_card in selected_role_cards:
        sound_card = linked_sound_map.get(role_card.id)
        if not sound_card:
            sound_card = fallback_name_map.get((role_card.name or "").strip())
        if sound_card and sound_card.id not in seen:
            resolved.append(sound_card)
            seen.add(sound_card.id)

    if narrator_card and narrator_card.id not in seen:
        resolved.append(narrator_card)

    return resolved


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


SORA_REFERENCE_PROMPT_INSTRUCTION = "请你参考这段提示词中的人物站位进行编写新的提示词："


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


def _queue_single_storyboard_prompt_generation(
    shot_id: int,
    user: models.User,
    db: Session,
    background_tasks: BackgroundTasks,
    prompt_key: str = "generate_video_prompts",
    duration_template_field: str = "video_prompt_rule",
    started_message: str = "Sora提示词生成任务已开始，请稍后刷新页面查看结果。",
    large_shot_template_id: Optional[int] = None,
    reference_shot_id: Optional[int] = None,
):
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    excerpt = (shot.script_excerpt or "").strip()
    if not excerpt:
        raise HTTPException(status_code=400, detail="请先填写原剧本段落")

    if prompt_key == "generate_large_shot_prompts":
        resolved_template = _resolve_large_shot_template(db, large_shot_template_id)
        if not resolved_template:
            raise HTTPException(status_code=404, detail="大镜头模板不存在")
        large_shot_template_id = resolved_template.id

    debug_selected_ids = _debug_parse_card_ids(shot.selected_card_ids)
    debug_subject_names = _debug_resolve_subject_names(db, debug_selected_ids)
    print(
        f"[SoraSubjectDebug][single_request] shot_id={shot.id} "
        f"episode_id={shot.episode_id} shot_number={shot.shot_number} "
        f"raw_selected_card_ids={shot.selected_card_ids!r} "
        f"parsed_selected_ids={debug_selected_ids} "
        f"resolved_subject_names={debug_subject_names} "
        f"large_shot_template_id={large_shot_template_id} "
        f"prompt_key={prompt_key} duration_template_field={duration_template_field} "
        f"reference_shot_id={reference_shot_id}"
    )

    shot.sora_prompt_status = 'generating'
    try:
        _submit_storyboard_prompt_task(
            db,
            shot=shot,
            episode=episode,
            script=script,
            prompt_key=prompt_key,
            duration_template_field=duration_template_field,
            large_shot_template_id=large_shot_template_id,
            reference_shot_id=reference_shot_id,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
        if shot:
            shot.sora_prompt_status = 'failed'
            db.commit()
        raise HTTPException(status_code=502, detail=f"提交文本任务失败: {str(exc)}")

    return {
        "message": started_message,
        "shot_id": shot_id
    }


class GenerateSoraPromptRequest(BaseModel):
    reference_shot_id: Optional[int] = None


@app.post("/api/shots/{shot_id}/generate-sora-prompt")
async def generate_sora_prompt(
    shot_id: int,
    background_tasks: BackgroundTasks,
    request: Optional[GenerateSoraPromptRequest] = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """生成Sora提示词（后台任务）"""
    return _queue_single_storyboard_prompt_generation(
        shot_id=shot_id,
        user=user,
        db=db,
        background_tasks=background_tasks,
        prompt_key="generate_video_prompts",
        duration_template_field="video_prompt_rule",
        started_message="Sora提示词生成任务已开始，请稍后刷新页面查看结果。",
        reference_shot_id=(request.reference_shot_id if request else None),
    )


class GenerateLargeShotPromptRequest(BaseModel):
    template_id: Optional[int] = None


@app.post("/api/shots/{shot_id}/generate-large-shot-prompt")
async def generate_large_shot_prompt(
    shot_id: int,
    background_tasks: BackgroundTasks,
    request: Optional[GenerateLargeShotPromptRequest] = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """生成大镜头提示词（后台任务）"""
    return _queue_single_storyboard_prompt_generation(
        shot_id=shot_id,
        user=user,
        db=db,
        background_tasks=background_tasks,
        prompt_key="generate_large_shot_prompts",
        duration_template_field="large_shot_prompt_rule",
        started_message="大镜头提示词生成任务已开始，请稍后刷新页面查看结果。"
        ,
        large_shot_template_id=(request.template_id if request else None),
    )


@app.post("/api/shots/{shot_id}/manual-sora-prompt", response_model=ShotResponse)
async def manual_set_sora_prompt(
    shot_id: int,
    request: ManualSoraPromptRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """手动设置Sora提示词，并将状态置为completed"""
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    prompt_text = (request.sora_prompt or "").strip()
    if not prompt_text:
        raise HTTPException(status_code=400, detail="提示词不能为空")

    all_variant_shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == shot.episode_id,
        models.StoryboardShot.shot_number == shot.shot_number,
        models.StoryboardShot.variant_index == shot.variant_index
    ).all()

    for variant_shot in all_variant_shots:
        variant_shot.sora_prompt = prompt_text
        variant_shot.storyboard_video_prompt = prompt_text
        variant_shot.sora_prompt_status = 'completed'

    db.commit()
    db.refresh(shot)

    shot.selected_scene_image_url = _resolve_selected_scene_reference_image_url(shot, db)
    return shot

@app.get("/api/shots/{shot_id}/full-sora-prompt")
async def get_full_sora_prompt(
    shot_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取完整的Sora提示词（用于复制）"""
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 构建完整提示词
    full_prompt = build_sora_prompt(shot, db)

    return {
        "full_prompt": full_prompt
    }

def _do_generate_single_sora_prompt(
    shot_id: int,
    user_id: int,
    prompt_key: str = "generate_video_prompts",
    duration_template_field: str = "video_prompt_rule",
    large_shot_template_id: Optional[int] = None,
):
    """后台任务：生成单个镜头提示词"""
    db = SessionLocal()
    try:
        shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
        if not shot:
            return

        episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
        script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
        if script.user_id != user_id:
            return

        effective_video_settings = _apply_episode_storyboard_video_settings_to_shot(shot, episode)

        excerpt = (shot.script_excerpt or "").strip()

        selected_ids = []
        try:
            selected_ids = json.loads(shot.selected_card_ids or "[]")
        except Exception:
            selected_ids = []

        selected_cards = _resolve_selected_cards(db, selected_ids)
        subject_names = [card.name for card in selected_cards if card and card.name]
        subject_text = _build_subject_text_for_ai(selected_cards)

        print(
            f"[SoraSubjectDebug][single_worker_prepare] shot_id={shot.id} "
            f"episode_id={shot.episode_id} shot_number={shot.shot_number} "
            f"raw_selected_card_ids={shot.selected_card_ids!r} "
            f"parsed_selected_ids={selected_ids} "
            f"resolved_subject_names={subject_names} "
            f"resolved_subject_text={subject_text} "
            f"excerpt_len={len(excerpt)} "
            f"large_shot_template_id={large_shot_template_id} "
            f"prompt_key={prompt_key} duration_template_field={duration_template_field}"
        )

        try:
            prompt_style = (script.sora_prompt_style or "").strip() or None
            scene_description = (shot.scene_override or "").strip()
            large_shot_template = None
            large_shot_template_content = ""
            large_shot_template_name = ""
            if prompt_key == "generate_large_shot_prompts":
                large_shot_template = _resolve_large_shot_template(db, large_shot_template_id)
                if not large_shot_template:
                    raise Exception("大镜头模板不存在")
                large_shot_template_id = large_shot_template.id
                large_shot_template_name = (large_shot_template.name or "").strip()
                large_shot_template_content = (large_shot_template.content or "").strip()

            # 调用AI生成
            result = generate_storyboard_prompts(
                excerpt,
                subject_names,
                effective_video_settings["duration"],
                prompt_style,
                shot_id=shot.id,
                prompt_key=prompt_key,
                subject_text_override=subject_text,
                scene_description=scene_description,
                duration_template_field=duration_template_field,
                large_shot_template_id=large_shot_template_id,
                large_shot_template_content=large_shot_template_content,
                large_shot_template_name=large_shot_template_name,
            )

            # 获取timeline数组并转换为表格格式
            timeline = result.get("timeline", [])

            # 保存原始timeline JSON数据
            shot.timeline_json = json.dumps(timeline, ensure_ascii=False)

            table_content = format_timeline_to_table(timeline)

            # 将表格存储到storyboard_video_prompt字段（用于前端显示）
            shot.storyboard_video_prompt = table_content
            shot.storyboard_audio_prompt = ""  # 不再使用此字段
            shot.storyboard_dialogue = ""  # 不再使用此字段

            # ✅ 生成时，自动提取场景描述并保存到 scene_override（仅在未锁定且为空时）
            if should_autofill_scene_override(
                current_scene_override=shot.scene_override,
                scene_override_locked=bool(getattr(shot, "scene_override_locked", False)),
            ):
                scene_desc = extract_scene_description(shot, db)
                if scene_desc:
                    shot.scene_override = scene_desc
                    print(f"[自动填充] scene_override: {scene_desc[:100]}..." if len(scene_desc) > 100 else f"[自动填充] scene_override: {scene_desc}")
            else:
                print(f"[跳过自动填充] scene_override已存在或已锁定，保持当前内容")

            # ✅ 将表格内容保存到 sora_prompt，让用户可以直接看到和编辑
            shot.sora_prompt = table_content
            shot.sora_prompt_status = 'completed'  # 设置状态为已完成

            # 打印生成的表格内容（用于调试）
            print("=" * 80)
            print(f"[生成Sora提示词] 镜头ID: {shot.id}, 镜号: {shot.shot_number}")
            print("-" * 80)
            print("分镜表格内容:")
            print(table_content)
            print("=" * 80)
            print(
                f"[SoraSubjectDebug][single_worker_done] shot_id={shot.id} "
                f"timeline_count={len(timeline)} subject_names={subject_names}"
            )

            db.commit()

        except Exception as e:
            print(f"[SoraSubjectDebug][single_worker_error] shot_id={shot.id} error={str(e)}")
            shot.sora_prompt_status = 'failed'  # 设置状态为失败
            db.commit()  # 确保状态保存

    except Exception as e:
        db.rollback()
    finally:
        db.close()

def _do_batch_generate_sora_prompts(
    episode_id: int,
    default_template: str,
    user_id: int,
    shot_ids: Optional[List[int]] = None,
    duration: Optional[int] = None
):
    """后台任务：批量生成Sora提示词（并发执行）"""
    db = SessionLocal()
    try:
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if not episode:
            print(f"批量生成失败：片段 {episode_id} 不存在")
            return

        script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
        if script.user_id != user_id:
            print(f"批量生成失败：用户 {user_id} 无权限")
            return

        # 标记为生成中
        episode.batch_generating_prompts = True
        db.commit()

        # 获取所有镜头
        query = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.episode_id == episode_id
        )

        # 如果指定了shot_ids，则只处理这些镜头
        if shot_ids:
            query = query.filter(models.StoryboardShot.id.in_(shot_ids))

        shots = query.order_by(models.StoryboardShot.shot_number, models.StoryboardShot.variant_index).all()

        if not shots:
            print(f"批量生成失败：片段 {episode_id} 没有镜头")
            episode.batch_generating_prompts = False
            db.commit()
            return

        library = db.query(models.StoryLibrary).filter(
            models.StoryLibrary.episode_id == episode.id
        ).first()

        prompt_style = (script.sora_prompt_style or "").strip() or None

        # 准备任务参数
        tasks = []
        for shot in shots:
            effective_video_settings = _apply_episode_storyboard_video_settings_to_shot(shot, episode)
            # 如果镜头没有模板，设置默认模板
            if not (shot.prompt_template or "").strip():
                shot.prompt_template = default_template

            # 获取镜头的选中角色
            selected_ids = []
            try:
                selected_ids = json.loads(shot.selected_card_ids or "[]")
            except Exception:
                selected_ids = []

            selected_cards = _resolve_selected_cards(
                db,
                selected_ids,
                library.id if library else None
            )
            subject_names = [card.name for card in selected_cards if card and card.name]
            subject_text = _build_subject_text_for_ai(selected_cards)

            print(
                f"[SoraSubjectDebug][batch_prepare] shot_id={shot.id} "
                f"episode_id={shot.episode_id} shot_number={shot.shot_number} "
                f"raw_selected_card_ids={shot.selected_card_ids!r} "
                f"parsed_selected_ids={selected_ids} "
                f"resolved_subject_names={subject_names} "
                f"resolved_subject_text={subject_text}"
            )

            # 获取剧本段落
            excerpt = (shot.script_excerpt or "").strip()
            scene_description = (shot.scene_override or "").strip()

            # 如果为空，跳过这个镜头
            if not excerpt:
                continue

            tasks.append({
                'shot_id': shot.id,
                'excerpt': excerpt,
                'subject_names': subject_names,
                'subject_text': subject_text,
                'scene_description': scene_description,
                'duration': effective_video_settings["duration"],
                'prompt_style': prompt_style
            })

        db.commit()  # 提交模板设置

        if not tasks:
            print(f"批量生成失败：没有有效的镜头数据")
            episode.batch_generating_prompts = False
            db.commit()
            return

        # 定义单个任务处理函数
        def process_single_shot(task):
            try:
                result = generate_storyboard_prompts(
                    script_excerpt=task['excerpt'],
                    subject_names=task['subject_names'],
                    duration=task['duration'],
                    prompt_style=task['prompt_style'],
                    shot_id=task['shot_id'],
                    subject_text_override=task['subject_text'],
                    scene_description=task['scene_description']
                )
                return {'shot_id': task['shot_id'], 'success': True, 'result': result}
            except Exception as e:
                return {'shot_id': task['shot_id'], 'success': False, 'error': str(e)}

        # 使用线程池并发执行（最多10个并发）
        from concurrent.futures import ThreadPoolExecutor, as_completed
        success_count = 0
        failed_count = 0

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(process_single_shot, task) for task in tasks]

            for future in as_completed(futures):
                try:
                    result = future.result()
                    shot_id = result['shot_id']

                    if result['success']:
                        # 保存结果到数据库
                        db_save = SessionLocal()
                        try:
                            shot = db_save.query(models.StoryboardShot).filter(
                                models.StoryboardShot.id == shot_id
                            ).first()

                            if shot:
                                ai_result = result['result']

                                # 获取timeline数组并转换为表格格式
                                timeline = ai_result.get("timeline", [])

                                # 保存原始timeline JSON数据
                                shot.timeline_json = json.dumps(timeline, ensure_ascii=False)

                                table_content = format_timeline_to_table(timeline)

                                # 将表格存储到storyboard_video_prompt字段（用于前端显示）
                                shot.storyboard_video_prompt = table_content
                                shot.storyboard_audio_prompt = ""  # 不再使用此字段
                                shot.storyboard_dialogue = ""  # 不再使用此字段

                                # ✅ 批量生成时，自动提取场景描述并保存到 scene_override（仅在未锁定且为空时）
                                if should_autofill_scene_override(
                                    current_scene_override=shot.scene_override,
                                    scene_override_locked=bool(getattr(shot, "scene_override_locked", False)),
                                ):
                                    scene_desc = extract_scene_description(shot, db_save)
                                    if scene_desc:
                                        shot.scene_override = scene_desc
                                        print(f"[自动填充] scene_override: {scene_desc[:100]}..." if len(scene_desc) > 100 else f"[自动填充] scene_override: {scene_desc}")
                                else:
                                    print(f"[跳过自动填充] scene_override已存在或已锁定，保持当前内容")

                                # ✅ 将表格内容保存到 sora_prompt，让用户可以直接看到和编辑
                                shot.sora_prompt = table_content
                                shot.sora_prompt_status = 'completed'  # 设置状态为已完成

                                # 打印生成的表格内容
                                print("=" * 80)
                                print(f"[批量生成Sora提示词] 镜头ID: {shot.id}, 镜号: {shot.shot_number}")
                                print("-" * 80)
                                print("分镜表格内容:")
                                print(table_content[:200] + "..." if len(table_content) > 200 else table_content)
                                print("=" * 80)

                                db_save.commit()
                                success_count += 1
                        finally:
                            db_save.close()
                    else:
                        # 失败：设置状态为 failed
                        error_msg = result.get('error', 'Unknown error')
                        print(f"[批量生成Sora提示词] 镜头ID {shot_id} 失败: {error_msg}")

                        db_save = SessionLocal()
                        try:
                            shot = db_save.query(models.StoryboardShot).filter(
                                models.StoryboardShot.id == shot_id
                            ).first()
                            if shot:
                                shot.sora_prompt_status = 'failed'
                                db_save.commit()
                        finally:
                            db_save.close()

                        failed_count += 1

                except Exception as e:
                    print(f"[批量生成Sora提示词] 处理结果时出错: {str(e)}")
                    failed_count += 1

        print(f"批量生成完成：成功 {success_count}，失败 {failed_count}")

        # 标记为完成
        episode.batch_generating_prompts = False
        db.commit()

    except Exception as e:
        try:
            episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
            if episode:
                episode.batch_generating_prompts = False
                db.commit()
        except:
            pass
    finally:
        db.close()


@app.post("/api/episodes/{episode_id}/batch-generate-sora-prompts")
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


@app.post("/api/episodes/{episode_id}/batch-generate-sora-videos")
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

# ==================== 托管视频生成API ====================

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

@app.post("/api/episodes/{episode_id}/start-managed-generation")
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

@app.post("/api/episodes/{episode_id}/stop-managed-generation")
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

@app.get("/api/episodes/{episode_id}/managed-session-status", response_model=ManagedSessionStatusResponse)
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

@app.post("/api/episodes/{episode_id}/refresh-videos")
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

@app.get("/api/managed-sessions/{session_id}/tasks")
def get_managed_tasks(
    session_id: int,
    status_filter: Optional[str] = None,  # all/pending/processing/completed/failed
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取托管会话的任务列表"""
    session = db.query(models.ManagedSession).filter(
        models.ManagedSession.id == session_id
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == session.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 查询任务
    query = db.query(models.ManagedTask).filter(
        models.ManagedTask.session_id == session_id
    )

    if status_filter and status_filter != "all":
        query = query.filter(models.ManagedTask.status == status_filter)

    tasks = query.order_by(models.ManagedTask.created_at.asc()).all()

    # 构造响应
    result = []
    for task in tasks:
        shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id == task.shot_id
        ).first() if task.shot_id > 0 else None

        # 查询原始镜头（variant_index=0）
        original_shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.stable_id == task.shot_stable_id,
            models.StoryboardShot.variant_index == 0
        ).first()

        result.append({
            "id": task.id,
            "session_id": task.session_id,
            "shot_id": task.shot_id,
            "shot_stable_id": task.shot_stable_id,
            "shot_number": shot.shot_number if shot else 0,
            "variant_index": shot.variant_index if shot else 0,
            "original_shot_number": original_shot.shot_number if original_shot else 0,
            "video_path": task.video_path,
            "status": task.status,
            "error_message": task.error_message,
            "task_id": task.task_id,
            "prompt_text": task.prompt_text or "",
            "created_at": task.created_at,
            "completed_at": task.completed_at
        })

    return result

@app.get("/api/shots/{shot_id}/videos", response_model=List[ShotVideoResponse])
async def get_shot_videos(
    shot_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取镜头生成的视频列表"""
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    videos = db.query(models.ShotVideo).filter(
        models.ShotVideo.shot_id == shot_id
    ).order_by(models.ShotVideo.created_at.desc()).all()

    needs_commit = False
    current_video_path = (shot.video_path or "").strip()
    if current_video_path and not current_video_path.startswith("error:"):
        if not any(v.video_path == current_video_path for v in videos):
            db.add(models.ShotVideo(shot_id=shot_id, video_path=current_video_path))
            needs_commit = True
        if not (shot.thumbnail_video_path or "").strip():
            shot.thumbnail_video_path = current_video_path
            needs_commit = True

    if needs_commit:
        db.commit()
        videos = db.query(models.ShotVideo).filter(
            models.ShotVideo.shot_id == shot_id
        ).order_by(models.ShotVideo.created_at.desc()).all()

    return videos

@app.put("/api/shots/{shot_id}/thumbnail")
async def update_shot_thumbnail(
    shot_id: int,
    request: ThumbnailUpdate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """设置镜头卡片缩略视频"""
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    video = db.query(models.ShotVideo).filter(
        models.ShotVideo.id == request.video_id,
        models.ShotVideo.shot_id == shot_id
    ).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")

    shot.thumbnail_video_path = video.video_path
    db.commit()

    return {"message": "缩略图已更新", "thumbnail_video_path": shot.thumbnail_video_path}

# ==================== 分镜表导入API ====================

@app.post("/api/episodes/{episode_id}/import-storyboard")
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

@app.get("/api/episodes/{episode_id}/export-storyboard")
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


# ==================== 拼图生成API ====================

def _generate_collage_task(shot_id: int, include_scenes: bool = False, card_ids_hash: str = None):
    """后台任务：生成拼图"""
    db = SessionLocal()
    try:
        # Get shot's aspect_ratio
        shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
        shot_aspect_ratio = (shot.aspect_ratio or "16:9") if shot else "16:9"

        # 生成拼图
        collage_url = generate_collage_image(shot_id, db, include_scenes, aspect_ratio=shot_aspect_ratio)

        # 保存到数据库，记录主体ID组合哈希值
        new_collage = models.ShotCollage(
            shot_id=shot_id,
            collage_path=collage_url,
            is_selected=False,
            card_ids_hash=card_ids_hash
        )
        db.add(new_collage)
        db.commit()
        print(f"[拼图生成] 镜头 {shot_id} 拼图生成成功: {collage_url}, card_ids_hash: {card_ids_hash}")
    except Exception as e:
        print(f"[拼图生成] 镜头 {shot_id} 拼图生成失败: {str(e)}")
    finally:
        db.close()


def _generate_collage_and_video(shot_id: int, full_prompt: str, include_scenes: bool = False):
    """后台任务：生成拼图然后生成视频"""
    db = SessionLocal()
    try:
        shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
        if not shot:
            print(f"[生成拼图+视频] 镜头 {shot_id} 不存在")
            return

        owner_username = ""
        try:
            episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
            if episode:
                _apply_episode_storyboard_video_settings_to_shot(shot, episode)
            script = db.query(models.Script).filter(models.Script.id == episode.script_id).first() if episode else None
            owner = db.query(models.User).filter(models.User.id == script.user_id).first() if script else None
            owner_username = (owner.username or "").strip() if owner else ""
        except Exception:
            owner_username = ""

        # 步骤1：生成拼图
        print(f"[生成拼图+视频] 开始生成拼图... (include_scenes={include_scenes})")
        try:
            collage_url = generate_collage_image(shot_id, db, include_scenes, aspect_ratio=shot.aspect_ratio or "16:9")

            # 创建拼图记录并设置为选中
            new_collage = models.ShotCollage(
                shot_id=shot_id,
                collage_path=collage_url,
                is_selected=True
            )
            db.add(new_collage)
            db.commit()
            db.refresh(new_collage)
            print(f"[生成拼图+视频] 拼图生成成功: {collage_url}")
        except Exception as e:
            print(f"[生成拼图+视频] 拼图生成失败: {str(e)}")
            shot.video_status = 'failed'
            shot.video_path = f"error:拼图生成失败: {str(e)}"
            db.commit()
            return

        # 步骤2：调用统一 Video API 生成视频
        print(f"[生成拼图+视频] 开始生成视频...")
        try:
            model_name = _resolve_storyboard_video_model_by_provider(
                shot.provider,
                default_model=getattr(shot, "storyboard_video_model", None) or getattr(episode, "storyboard_video_model", None) or DEFAULT_STORYBOARD_VIDEO_MODEL
            )
            request_data = _build_unified_storyboard_video_task_payload(
                shot=shot,
                db=db,
                username=owner_username,
                model_name=model_name,
                provider=shot.provider or _resolve_storyboard_video_provider(model_name),
                full_prompt=full_prompt,
                aspect_ratio=shot.aspect_ratio,
                duration=shot.duration,
                first_frame_image_url=new_collage.collage_path,
                resolution_name=getattr(episode, "storyboard_video_resolution_name", None) if episode else None,
                appoint_account=getattr(episode, "storyboard_video_appoint_account", "") if episode else "",
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
                print(f"[生成拼图+视频] {error_msg}")
                save_ai_debug(
                    'video_generate',
                    request_data,
                    {'error': error_msg, 'status_code': submit_response.status_code},
                    shot_id=shot_id
                )
                shot.video_status = 'failed'
                shot.video_path = f"error:{error_msg}"
                db.commit()
                return

            submit_result = submit_response.json()
            task_id = submit_result.get('task_id')

            if not task_id:
                error_msg = f"视频返回异常: {submit_result.get('message', '未知错误')}"
                print(f"[生成拼图+视频] {error_msg}")
                save_ai_debug(
                    'video_generate',
                    request_data,
                    {'error': error_msg, 'response': submit_result},
                    shot_id=shot_id
                )
                shot.video_status = 'failed'
                shot.video_path = f"error:{error_msg}"
                db.commit()
                return

            save_ai_debug(
                'video_generate',
                request_data,
                {'task_id': task_id, 'response': submit_result},
                shot_id=shot_id
            )

            shot.task_id = task_id
            shot.video_status = 'processing'
            shot.video_submitted_at = datetime.utcnow()  # ✅ 重置提交时间
            _record_storyboard_video_charge(
                db,
                shot=shot,
                task_id=task_id,
                stage="video_generate",
                detail_payload={
                    "source": "collage_generate",
                    "provider": request_data.get("provider"),
                    "model": request_data.get("model"),
                },
            )
            db.commit()
            print(f"[生成拼图+视频] 视频生成任务已提交: task_id={task_id}")

        except Exception as e:
            print(f"[生成拼图+视频] 视频生成失败: {str(e)}")
            save_ai_debug(
                'video_generate',
                request_data if 'request_data' in locals() else {},
                {'error': str(e)},
                shot_id=shot_id
            )
            shot.video_status = 'failed'
            shot.video_path = f"error:{str(e)}"
            db.commit()

    except Exception as e:
        print(f"[生成拼图+视频] 整体流程失败: {str(e)}")
    finally:
        db.close()


# ==================== 视频生成API ====================

ACTIVE_VIDEO_GENERATION_STATUSES = ("submitting", "preparing", "processing")
ACTIVE_MANAGED_TASK_STATUSES = ("pending", "processing")
MAX_ACTIVE_VIDEO_GENERATIONS_PER_SHOT = 1


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


def _is_storyboard_shot_generation_active(
    shot: models.StoryboardShot,
    db: Session
) -> bool:
    if str(getattr(shot, "video_status", "") or "").strip().lower() in ACTIVE_VIDEO_GENERATION_STATUSES:
        return True

    shot_id = int(getattr(shot, "id", 0) or 0)
    if shot_id <= 0:
        return False

    active_task = db.query(models.ManagedTask.id).filter(
        models.ManagedTask.shot_id == shot_id,
        models.ManagedTask.status.in_(ACTIVE_MANAGED_TASK_STATUSES),
    ).first()
    return active_task is not None


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

@app.post("/api/shots/{shot_id}/generate-video")
async def generate_video(
    shot_id: int,
    request: GenerateVideoRequest = GenerateVideoRequest(),
    background_tasks: BackgroundTasks = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    if _backfill_storyboard_visual_references_from_family(shot, db):
        db.commit()
        db.refresh(shot)

    _ensure_storyboard_video_generation_slots_available([shot], db)

    # 为本次“点击生成视频”固定一个任务分组 key，后续调试事件归到同一条 dashboard 任务
    video_debug_folder = save_ai_debug(
        'video_generate',
        {
            "shot_id": shot_id,
            "episode_id": shot.episode_id,
            "first_frame_reference_image_url": normalize_first_frame_candidate_url(
                getattr(shot, "first_frame_reference_image_url", "")
            ),
            "requested_at": datetime.utcnow().isoformat()
        },
        output_data={"status": "request_received"},
        shot_id=shot_id
    )

    episode_settings = _apply_episode_storyboard_video_settings_to_shot(shot, episode)
    db.commit()
    db.refresh(shot)

    # ✅ 总是调用 build_sora_prompt() 进行完整拼接（视频风格 + 场景 + 表格）
    full_prompt = build_sora_prompt(shot, db)

    if not full_prompt:
        raise HTTPException(status_code=400, detail="缺少Sora提示词")

    selected_first_frame_image_url = _resolve_selected_first_frame_reference_image_url(shot, db)

    # ✅ 不要覆盖 shot.sora_prompt，保留用户编辑的内容

    # 打印提交给Sora API的完整提示词
    print("=" * 80)
    print(f"[生成视频] 镜头ID: {shot.id}, 镜号: {shot.shot_number}")
    print(f"[生成视频] 首帧参考图: {selected_first_frame_image_url or '未选择'}")
    print("-" * 80)
    print("提交给Sora API的完整提示词:")
    print(full_prompt)
    print("=" * 80)

    try:
        model_name = _resolve_storyboard_video_model_by_provider(
            shot.provider,
            default_model=getattr(shot, "storyboard_video_model", None) or episode_settings["model"]
        )
        request_data = _build_unified_storyboard_video_task_payload(
            shot=shot,
            db=db,
            username=user.username,
            model_name=model_name,
            provider=shot.provider or _resolve_storyboard_video_provider(model_name),
            full_prompt=full_prompt,
            aspect_ratio=shot.aspect_ratio,
            duration=shot.duration,
            first_frame_image_url=selected_first_frame_image_url,
            resolution_name=episode_settings.get("resolution_name", ""),
            appoint_account=_normalize_storyboard_video_appoint_account(
                request.appoint_account,
                default_value=episode_settings.get("appoint_account", "")
            ),
        )
        submit_timeout = 60 if _is_moti_storyboard_video_model(model_name) else 30

        def call_video_api():
            return requests.post(
                get_video_task_create_url(),
                headers=get_video_api_headers(),
                json=request_data,
                timeout=submit_timeout
            )

        loop = asyncio.get_event_loop()
        submit_response = await loop.run_in_executor(executor, call_video_api)

        if submit_response.status_code != 200:
            error_msg = f"视频请求失败: {submit_response.status_code}"
            save_ai_debug(
                'video_generate',
                request_data,
                {'error': error_msg, 'status_code': submit_response.status_code},
                shot_id=shot_id,
                task_folder=video_debug_folder
            )
            raise Exception(error_msg)

        submit_result = submit_response.json()

        task_id = submit_result.get('task_id')
        if not task_id:
            error_msg = f"视频返回异常: {submit_result.get('message', '未知错误')}"
            save_ai_debug(
                'video_generate',
                request_data,
                {'error': error_msg, 'response': submit_result},
                shot_id=shot_id,
                task_folder=video_debug_folder
            )
            raise Exception(error_msg)

        save_ai_debug(
            'video_generate',
            request_data,
            {'task_id': task_id, 'response': submit_result},
            shot_id=shot_id,
            task_folder=video_debug_folder
        )

        shot.task_id = task_id
        shot.video_status = 'processing'
        shot.video_submitted_at = datetime.utcnow()  # ✅ 重置提交时间，避免超时检查误判
        _record_storyboard_video_charge(
            db,
            shot=shot,
            task_id=task_id,
            stage="video_generate",
            detail_payload={
                "source": "single_generate_with_image",
                "provider": request_data.get("provider"),
                "model": request_data.get("model"),
            },
        )
        db.commit()
        db.refresh(shot)

        return {
            "task_id": task_id,
            "status": "processing"
        }

    except Exception as e:
        if 'request_data' in locals():
            save_ai_debug(
                'video_generate',
                request_data,
                {'exception': str(e)},
                shot_id=shot_id,
                task_folder=video_debug_folder
            )
        status_code = 400 if str(e) in SEEDANCE_AUDIO_VALIDATION_ERRORS else 500
        raise HTTPException(status_code=status_code, detail=str(e))
@app.get("/api/shots/{shot_id}/video-status")
def check_shot_video_status(
    shot_id: int,
    db: Session = Depends(get_db)
):
    """检查视频生成状态"""
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    return {
        "status": shot.video_status,  # idle/processing/completed/failed
        "video_path": shot.video_path,
        "task_id": shot.task_id
    }


@app.get("/api/tasks/{task_id}/status")
async def query_task_status(
    task_id: str,
    user: models.User = Depends(get_current_user)
):
    """根据task_id查询Sora任务状态（返回服务商原始响应）"""
    from video_service import check_video_status, is_transient_video_status_error

    try:
        # return_raw=True 表示返回服务商的原始JSON响应
        status_info = check_video_status(task_id, return_raw=True)
        return status_info
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


def _normalize_video_task_ids(task_ids: List[str]) -> List[str]:
    normalized_task_ids = []
    seen_task_ids = set()
    for task_id in task_ids or []:
        normalized_task_id = str(task_id or "").strip()
        if normalized_task_id and normalized_task_id not in seen_task_ids:
            normalized_task_ids.append(normalized_task_id)
            seen_task_ids.add(normalized_task_id)
    return normalized_task_ids


def _get_user_cancelable_video_task_ids(
    task_ids: List[str],
    user: models.User,
    db: Session
) -> set:
    if not task_ids or not user:
        return set()

    active_shot_statuses = ["submitting", "preparing", "processing"]
    active_managed_task_statuses = ["pending", "processing"]

    owned_task_ids = {
        task_id
        for (task_id,) in db.query(models.StoryboardShot.task_id).join(
            models.Episode,
            models.StoryboardShot.episode_id == models.Episode.id
        ).join(
            models.Script,
            models.Episode.script_id == models.Script.id
        ).filter(
            models.Script.user_id == user.id,
            models.StoryboardShot.task_id.in_(task_ids),
            models.StoryboardShot.video_status.in_(active_shot_statuses),
        ).all()
        if task_id
    }

    owned_task_ids.update({
        task_id
        for (task_id,) in db.query(models.ManagedTask.task_id).join(
            models.ManagedSession,
            models.ManagedTask.session_id == models.ManagedSession.id
        ).join(
            models.Episode,
            models.ManagedSession.episode_id == models.Episode.id
        ).join(
            models.Script,
            models.Episode.script_id == models.Script.id
        ).filter(
            models.Script.user_id == user.id,
            models.ManagedTask.task_id.in_(task_ids),
            models.ManagedTask.status.in_(active_managed_task_statuses),
            models.ManagedSession.status.in_(ACTIVE_MANAGED_SESSION_STATUSES),
        ).all()
        if task_id
    })

    return owned_task_ids


@app.post("/api/video/tasks/cancel")
async def cancel_video_tasks(
    request: CancelVideoTasksRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """代理取消上游视频生成任务。"""
    task_ids = _normalize_video_task_ids(request.task_ids)
    if not task_ids:
        raise HTTPException(status_code=400, detail="缺少任务ID")

    owned_task_ids = _get_user_cancelable_video_task_ids(task_ids, user, db)
    unauthorized_task_ids = [
        task_id for task_id in task_ids
        if task_id not in owned_task_ids
    ]
    if unauthorized_task_ids:
        raise HTTPException(status_code=403, detail="无权取消任务")

    try:
        loop = asyncio.get_event_loop()
        cancel_result = await loop.run_in_executor(
            executor,
            _cancel_upstream_video_tasks,
            task_ids
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"取消任务失败: {str(e)}")

    if not cancel_result.get("ok", False):
        response_payload = cancel_result.get("response") or {}
        detail = response_payload.get("detail") if isinstance(response_payload, dict) else None
        raise HTTPException(status_code=502, detail=detail or "取消任务失败")

    return cancel_result



def _cancel_upstream_video_tasks(task_ids: List[str]) -> dict:
    normalized_task_ids = [
        str(task_id or "").strip()
        for task_id in (task_ids or [])
        if str(task_id or "").strip()
    ]
    if not normalized_task_ids:
        return {
            "requested_count": 0,
            "status_code": None,
            "ok": True,
            "response": {}
        }

    response = requests.post(
        get_video_tasks_cancel_url(),
        headers=get_video_api_headers(),
        json={"task_ids": normalized_task_ids},
        timeout=30
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"raw_text": response.text}

    return {
        "requested_count": len(normalized_task_ids),
        "status_code": response.status_code,
        "ok": response.status_code == 200,
        "response": payload
    }


def cancel_invalid_seedance_video_tasks(db: Session) -> dict:
    """取消当前库里已在跑、但不满足 Seedance 音频规则的任务，并标记本地失败。"""
    episode_cache: Dict[int, Any] = {}

    def get_episode(episode_id: int):
        if episode_id not in episode_cache:
            episode_cache[episode_id] = db.query(models.Episode).filter(
                models.Episode.id == episode_id
            ).first()
        return episode_cache[episode_id]

    invalid_shots = []
    processing_shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.task_id != "",
        models.StoryboardShot.video_status.in_(["submitting", "preparing", "processing"])
    ).all()

    for shot in processing_shots:
        episode = get_episode(shot.episode_id)
        default_model = getattr(episode, "storyboard_video_model", None) if episode else None
        model_name = _resolve_storyboard_video_model_by_provider(
            getattr(shot, "provider", None),
            default_model=getattr(shot, "storyboard_video_model", None) or default_model or "sora-2"
        )
        if not _is_moti_storyboard_video_model(model_name):
            continue
        assets = _collect_moti_v2_reference_assets(shot, db)
        reason = _get_seedance_audio_validation_error(
            assets["audio_items"],
            float(assets["total_audio_duration_seconds"] or 0.0)
        )
        if not reason:
            continue
        invalid_shots.append({
            "shot_id": shot.id,
            "task_id": shot.task_id,
            "reason": reason
        })

    invalid_managed_tasks = []
    processing_managed_tasks = db.query(models.ManagedTask).join(
        models.ManagedSession,
        models.ManagedTask.session_id == models.ManagedSession.id
    ).filter(
        models.ManagedTask.status == "processing",
        models.ManagedTask.task_id != "",
        models.ManagedSession.status.in_(ACTIVE_MANAGED_SESSION_STATUSES),
        models.ManagedSession.provider == "moti"
    ).all()

    for task in processing_managed_tasks:
        original_shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.stable_id == task.shot_stable_id,
            models.StoryboardShot.variant_index == 0
        ).first()
        if not original_shot:
            continue
        assets = _collect_moti_v2_reference_assets(original_shot, db)
        reason = _get_seedance_audio_validation_error(
            assets["audio_items"],
            float(assets["total_audio_duration_seconds"] or 0.0)
        )
        if not reason:
            continue
        invalid_managed_tasks.append({
            "managed_task_id": task.id,
            "task_id": task.task_id,
            "session_id": task.session_id,
            "reason": reason
        })

    cancel_target_ids = list({
        item["task_id"]
        for item in invalid_shots + invalid_managed_tasks
        if item.get("task_id")
    })
    cancel_result = _cancel_upstream_video_tasks(cancel_target_ids)

    if invalid_shots:
        shot_map = {
            shot.id: shot
            for shot in db.query(models.StoryboardShot).filter(
                models.StoryboardShot.id.in_([item["shot_id"] for item in invalid_shots])
            ).all()
        }
        for item in invalid_shots:
            shot = shot_map.get(item["shot_id"])
            if not shot:
                continue
            shot.video_status = "failed"
            shot.video_error_message = item["reason"]
            shot.video_path = f"error:{item['reason']}"
            shot.thumbnail_video_path = ""

    touched_session_ids = set()
    if invalid_managed_tasks:
        managed_task_map = {
            task.id: task
            for task in db.query(models.ManagedTask).filter(
                models.ManagedTask.id.in_([item["managed_task_id"] for item in invalid_managed_tasks])
            ).all()
        }
        for item in invalid_managed_tasks:
            task = managed_task_map.get(item["managed_task_id"])
            if not task:
                continue
            task.status = "failed"
            task.error_message = item["reason"]
            task.completed_at = datetime.utcnow()
            touched_session_ids.add(task.session_id)

    if invalid_shots or invalid_managed_tasks:
        db.commit()

    if touched_session_ids:
        try:
            from managed_generation_service import ManagedGenerationPoller
            sync_poller = ManagedGenerationPoller()
            for session_id in touched_session_ids:
                sync_poller._update_session_progress(session_id, db)
            db.commit()
        except Exception as e:
            _rollback_quietly(db)
            print(f"[seedance] 刷新托管进度失败: {str(e)}")

    return {
        "invalid_shot_count": len(invalid_shots),
        "invalid_managed_task_count": len(invalid_managed_tasks),
        "cancel_result": cancel_result,
        "invalid_shots": invalid_shots,
        "invalid_managed_tasks": invalid_managed_tasks
    }


def _looks_like_video_status_query_failure(message: str) -> bool:
    normalized = str(message or "").strip()
    if not normalized:
        return False
    return (
        normalized.startswith("请求失败: 5")
        or normalized.startswith("查询异常:")
        or normalized.startswith("状态查询失败: HTTP 5")
    )


def repair_managed_tasks_failed_by_query_errors(db: Session) -> dict:
    """修复因状态查询 5xx/超时被误判失败的托管任务。"""
    from video_service import check_video_status, is_transient_video_status_error
    from managed_generation_service import ManagedGenerationPoller

    candidate_tasks = db.query(models.ManagedTask).join(
        models.ManagedSession,
        models.ManagedTask.session_id == models.ManagedSession.id
    ).filter(
        models.ManagedTask.status == "failed",
        models.ManagedTask.task_id != "",
        models.ManagedSession.status.in_(ACTIVE_MANAGED_SESSION_STATUSES)
    ).order_by(models.ManagedTask.id.asc()).all()

    candidate_tasks = [
        task for task in candidate_tasks
        if _looks_like_video_status_query_failure(task.error_message)
    ]

    revived_tasks = []
    superseded_tasks = []
    confirmed_failed_tasks = []
    skipped_tasks = []
    cancel_task_ids = set()
    touched_session_ids = set()

    for task in candidate_tasks:
        status_info = check_video_status(task.task_id)
        if is_transient_video_status_error(status_info):
            skipped_tasks.append({
                "managed_task_id": task.id,
                "task_id": task.task_id,
                "reason": status_info.get("error_message", "")
            })
            continue

        upstream_status = str(status_info.get("status") or "").strip().lower()
        siblings = db.query(models.ManagedTask).filter(
            models.ManagedTask.session_id == task.session_id,
            models.ManagedTask.shot_stable_id == task.shot_stable_id
        ).order_by(models.ManagedTask.id.asc()).all()

        other_tasks = [item for item in siblings if item.id != task.id]
        other_processing = [
            item for item in other_tasks
            if item.status == "processing" and str(item.task_id or "").strip()
        ]
        other_completed = [item for item in other_tasks if item.status == "completed"]

        if upstream_status in {"submitted", "pending", "processing"}:
            if other_processing or other_completed:
                cancel_task_ids.add(task.task_id)
                takeover_task = (other_processing or other_completed)[0]
                note = f"状态查询异常后已由任务#{takeover_task.id}接管，已取消旧上游任务"
                base_error = str(task.error_message or "").strip()
                task.error_message = note if not base_error else f"{base_error}；{note}"
                superseded_tasks.append({
                    "managed_task_id": task.id,
                    "task_id": task.task_id,
                    "upstream_status": upstream_status,
                    "takeover_task_id": takeover_task.id
                })
                touched_session_ids.add(task.session_id)
                continue

            task.status = "processing"
            task.error_message = ""
            task.completed_at = None
            revived_tasks.append({
                "managed_task_id": task.id,
                "task_id": task.task_id,
                "upstream_status": upstream_status
            })
            touched_session_ids.add(task.session_id)
            continue

        if upstream_status == "completed":
            if other_completed:
                confirmed_failed_tasks.append({
                    "managed_task_id": task.id,
                    "task_id": task.task_id,
                    "upstream_status": upstream_status,
                    "reason": "同镜头已有完成结果，保留现状"
                })
                continue

            task.status = "processing"
            task.error_message = ""
            task.completed_at = None
            revived_tasks.append({
                "managed_task_id": task.id,
                "task_id": task.task_id,
                "upstream_status": upstream_status
            })
            touched_session_ids.add(task.session_id)
            continue

        confirmed_failed_tasks.append({
            "managed_task_id": task.id,
            "task_id": task.task_id,
            "upstream_status": upstream_status,
            "reason": str(status_info.get("error_message") or "")
        })

    cancel_result = _cancel_upstream_video_tasks(list(cancel_task_ids))

    if revived_tasks or superseded_tasks:
        db.commit()

    if touched_session_ids:
        try:
            sync_poller = ManagedGenerationPoller()
            for session_id in touched_session_ids:
                sync_poller._update_session_progress(session_id, db)
            db.commit()
        except Exception as e:
            _rollback_quietly(db)
            print(f"[managed-repair] 刷新托管进度失败: {str(e)}")

    return {
        "candidate_count": len(candidate_tasks),
        "revived_count": len(revived_tasks),
        "superseded_count": len(superseded_tasks),
        "confirmed_failed_count": len(confirmed_failed_tasks),
        "skipped_count": len(skipped_tasks),
        "cancel_result": cancel_result,
        "revived_tasks": revived_tasks,
        "superseded_tasks": superseded_tasks,
        "confirmed_failed_tasks": confirmed_failed_tasks,
        "skipped_tasks": skipped_tasks
    }


@app.get("/api/shots/{shot_id}/export")
async def export_shot_video(
    shot_id: int,
    db: Session = Depends(get_db)
):
    """导出单个镜头的视频"""
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    if shot.video_status != 'completed':
        raise HTTPException(status_code=400, detail=f"视频未完成生成，当前状态: {shot.video_status}")

    if not shot.video_path:
        raise HTTPException(status_code=404, detail="视频路径不存在")

    # video_path现在保存的是CDN URL，直接返回
    return {
        "video_url": shot.video_path,
        "shot_number": shot.shot_number
    }

# ==================== 分镜图生成API ====================

class GenerateStoryboardImageRequest(BaseModel):
    requirement: str
    style: str
    provider: Optional[str] = None
    model: str = "banana-pro"
    size: str = "9:16"
    resolution: str = "2K"


class GenerateDetailImagesRequest(BaseModel):
    provider: Optional[str] = None
    size: str = "9:16"
    resolution: str = "2K"
    model: Optional[str] = None
    selected_sub_shot_index: Optional[int] = None
    selected_sub_shot_text: Optional[str] = None


class SetDetailImageCoverRequest(BaseModel):
    image_url: str


class SetFirstFrameReferenceRequest(BaseModel):
    image_url: str = ""


class SetShotSceneImageSelectionRequest(BaseModel):
    use_uploaded_scene_image: bool = False


_DETAIL_IMAGES_MODEL_CONFIG = {
    "seedream-4.0": {
        "actual_model": "seedream-4.0",
        "provider": "jimeng"
    },
    "seedream-4.1": {
        "actual_model": "seedream-4.1",
        "provider": "jimeng"
    },
    "seedream-4.5": {
        "actual_model": "seedream-4.5",
        "provider": "jimeng"
    },
    "seedream-4.6": {
        "actual_model": "seedream-4.6",
        "provider": "jimeng"
    },
    "nano-banana-2": {
        "actual_model": "nano-banana-2",
        "provider": "momo"
    },
    "nano-banana-pro": {
        "actual_model": "nano-banana-pro",
        "provider": "momo"
    },
    "gpt-image-2": {
        "actual_model": "gpt-image-2",
        "provider": "momo"
    },
    "jimeng-4.0": {
        "actual_model": "图片 4.0",
        "provider": "jimeng"
    },
    "jimeng-4.1": {
        "actual_model": "图片 4.1",
        "provider": "jimeng"
    },
    "jimeng-4.5": {
        "actual_model": "图片 4.5",
        "provider": "jimeng"
    },
    "jimeng-4.6": {
        "actual_model": "图片 4.6",
        "provider": "jimeng"
    },
    "banana2": {
        "actual_model": "banana2",
        "provider": "momo"
    },
    "banana2-moti": {
        "actual_model": "banana2-moti",
        "provider": "momo"
    },
    "banana-pro": {
        "actual_model": "banana-pro",
        "provider": "momo"
    }
}


def _normalize_detail_images_provider(
    value: Optional[str],
    default_provider: str = ""
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
    default_provider: str = ""
) -> str:
    return _normalize_detail_images_provider(
        getattr(episode, "detail_images_provider", None) if episode is not None else None,
        default_provider=default_provider
    )


def _normalize_detail_images_model(
    value: Optional[str],
    default_model: str = "seedream-4.0"
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


def _resolve_detail_images_actual_model(model: Optional[str]) -> str:
    normalized = _normalize_detail_images_model(model, default_model="seedream-4.0")
    try:
        route = image_platform_client.resolve_image_route(normalized)
        return str(route.get("model") or normalized)
    except Exception:
        legacy = _DETAIL_IMAGES_MODEL_CONFIG.get(normalized)
        return str((legacy or {}).get("actual_model") or normalized)


def _build_image_generation_debug_meta(
    model_key: Optional[str],
    provider: Optional[str] = None,
    actual_model: Optional[str] = None,
    has_reference_images: bool = False
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
            has_reference_images=has_reference_images
        ),
        "status_api_url_template": get_image_status_api_url(
            task_id="{task_id}",
            model_name=normalized_model,
            provider=resolved_provider
        ),
    }


def _build_image_generation_request_payload(
    *,
    provider: str,
    actual_model: str,
    prompt_text: str,
    ratio: str,
    reference_images: Optional[List[str]] = None,
    name: Optional[str] = None,
    resolution: Optional[str] = None,
    cw: Optional[int] = None
) -> dict:
    normalized_provider = str(provider or "").strip().lower()
    normalized_reference_images = [
        str(url or "").strip()
        for url in (reference_images or [])
        if str(url or "").strip()
    ]
    payload = {
        "model": actual_model,
        "prompt": prompt_text,
        "username": "story_creator",
        "provider": normalized_provider,
        "action": "image2image" if normalized_reference_images else "text2image",
        "ratio": ratio,
        "reference_images": normalized_reference_images,
        "extra": {
            "n": 1,
            "name": name,
            "cw": _normalize_storyboard2_image_cw(cw, default_value=50),
        },
    }
    if resolution and normalized_provider != "jimeng":
        payload["resolution"] = resolution
    return payload


def _submit_single_image_generation_task(
    *,
    prompt_text: str,
    model_name: str,
    provider: Optional[str] = None,
    size: str = "9:16",
    resolution: Optional[str] = None,
    reference_images: Optional[List[str]] = None,
    name: Optional[str] = None,
) -> dict:
    normalized_provider = str(provider or "").strip().lower()
    normalized_reference_images = reference_images if reference_images else None
    submit_api_url = get_image_submit_api_url(
        model_name=model_name,
        provider=normalized_provider,
        has_reference_images=bool(normalized_reference_images)
    )

    task_id = submit_image_generation(
        prompt_text,
        model_name,
        size,
        resolution,
        1,
        normalized_reference_images,
        normalized_provider or None,
    )

    return {
        "task_id": task_id,
        "submit_api_url": submit_api_url,
        "status_api_url": get_image_status_api_url(
            task_id=task_id,
            model_name=model_name,
            provider=normalized_provider
        ),
        "provider": normalized_provider,
        "model_name": model_name,
    }


def _generate_single_image_with_polling(
    prompt_text: str,
    model_name: str,
    provider: Optional[str] = None,
    size: str = "9:16",
    resolution: Optional[str] = None,
    reference_images: Optional[List[str]] = None,
    timeout: int = 600,
    poll_interval_seconds: int = 5,
) -> dict:
    """提交并轮询图片任务，返回单图结果。"""
    normalized_provider = str(provider or "").strip().lower()
    submit_result = _submit_single_image_generation_task(
        prompt_text=prompt_text,
        model_name=model_name,
        provider=normalized_provider,
        size=size,
        resolution=resolution,
        reference_images=reference_images,
        name=f"single_image_{uuid.uuid4().hex[:8]}",
    )
    task_id = submit_result["task_id"]
    submit_api_url = submit_result["submit_api_url"]
    status_api_url = submit_result["status_api_url"]
    start_time = time.time()
    transient_query_error_count = 0
    while time.time() - start_time < timeout:
        status_result = get_image_task_status(task_id, model_name, normalized_provider)
        if is_transient_image_status_error(status_result):
            transient_query_error_count += 1
            if transient_query_error_count >= 10:
                return {
                    "success": False,
                    "error": f"连续查询异常 10 次：{status_result.get('error_message') or '状态查询失败'}",
                    "task_id": task_id,
                    "submit_api_url": submit_api_url,
                    "status_api_url": status_api_url,
                }
            time.sleep(max(1, int(poll_interval_seconds)))
            continue
        transient_query_error_count = 0
        status = status_result.get("status")
        if status == "completed":
            images = status_result.get("images") or []
            if not images:
                return {
                    "success": False,
                    "error": "生成任务已完成，但未返回图片",
                    "task_id": task_id,
                    "submit_api_url": submit_api_url,
                    "status_api_url": status_api_url
                }
            return {
                "success": True,
                "images": images[:1],
                "task_id": task_id,
                "submit_api_url": submit_api_url,
                "status_api_url": status_api_url
            }
        if status == "failed":
            return {
                "success": False,
                "error": status_result.get("error") or status_result.get("error_message") or "生成失败",
                "task_id": task_id,
                "submit_api_url": submit_api_url,
                "status_api_url": status_api_url
            }
        time.sleep(max(1, int(poll_interval_seconds)))

    return {
        "success": False,
        "error": f"生成超时（超过{timeout}秒）",
        "task_id": task_id,
        "submit_api_url": submit_api_url,
        "status_api_url": status_api_url
    }


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


def _normalize_storyboard_video_model(value: Optional[str], default_model: str = DEFAULT_STORYBOARD_VIDEO_MODEL) -> str:
    raw = (value or "").strip()
    if raw in _STORYBOARD_VIDEO_MODEL_CONFIG:
        return raw
    fallback = (default_model or "").strip()
    if fallback in _STORYBOARD_VIDEO_MODEL_CONFIG:
        return fallback
    return DEFAULT_STORYBOARD_VIDEO_MODEL


def _map_api_model_by_duration(model: str, duration: Optional[int]) -> str:
    """
    根据时长映射模型名称
    - sora-2 + 25秒 → sora-2-pro
    - 其他情况 → 保持原样
    """
    if (model or "").strip().lower() == "sora-2" and duration == 25:
        return "sora-2-pro"
    return model


def _get_seedance_audio_validation_error(audio_items: List[dict], total_audio_duration_seconds: float) -> str:
    audio_count = len(audio_items or [])
    if audio_count > SEEDANCE_AUDIO_MAX_COUNT:
        return SEEDANCE_AUDIO_COUNT_ERROR
    if total_audio_duration_seconds >= SEEDANCE_AUDIO_MAX_TOTAL_SECONDS:
        return SEEDANCE_AUDIO_DURATION_ERROR
    return ""


def _collect_moti_v2_reference_assets(shot, db, first_frame_image_url: str = "") -> dict:
    """
    收集 Seedance 2.0 系列模型所需的参考素材。
    """
    if shot is None or db is None:
        normalized_first_frame = str(first_frame_image_url or "").strip()
        return {
            "image_prefix_parts": ["首帧参考图"] if normalized_first_frame else [],
            "image_urls": [normalized_first_frame] if normalized_first_frame else [],
            "selected_scene_image_url": "",
            "audio_prefix_parts": [],
            "audio_items": [],
            "total_audio_duration_seconds": 0.0,
        }

    def is_role_subject_card_type(card_type: str) -> bool:
        card_type_text = str(card_type or "").strip().lower()
        if not card_type_text:
            return False
        if card_type_text == "role":
            return True
        if "角色" in card_type_text:
            return True
        if "瑙掕壊" in card_type_text:
            return True
        return False

    def collect_reference_items(cards):
        items = []
        for card in cards:
            ref_image = db.query(models.GeneratedImage).filter(
                models.GeneratedImage.card_id == card.id,
                models.GeneratedImage.is_reference == True,
                models.GeneratedImage.status == "completed"
            ).first()
            if ref_image:
                image_url = ref_image.image_path
            else:
                card_img = db.query(models.CardImage).filter(
                    models.CardImage.card_id == card.id
                ).order_by(models.CardImage.order.asc(), models.CardImage.id.asc()).first()
                image_url = card_img.image_path if card_img else None

            if image_url:
                items.append((card.name, image_url))
        return items

    selected_ids = []
    try:
        selected_ids = json.loads(shot.selected_card_ids or "[]")
    except Exception:
        pass

    # 取角色卡/道具卡，保持 selected_card_ids 中的顺序。
    # 最终组装时顺序固定为：首帧 > 场景 > 道具 > 角色。
    role_cards = []
    prop_cards = []
    if selected_ids:
        cards_by_id = {
            c.id: c for c in db.query(models.SubjectCard).filter(
                models.SubjectCard.id.in_(selected_ids)
            ).all()
        }
        for cid in selected_ids:
            card = cards_by_id.get(cid)
            if not card:
                continue
            if _is_prop_subject_card_type(card.card_type):
                prop_cards.append(card)
            elif is_role_subject_card_type(card.card_type):
                role_cards.append(card)

    selected_scene_image_url = _resolve_selected_scene_reference_image_url(shot, db)
    image_meta = build_seedance_reference_images(
        first_frame_image_url=first_frame_image_url,
        scene_image_url=selected_scene_image_url,
        prop_reference_items=collect_reference_items(prop_cards),
        role_reference_items=collect_reference_items(role_cards),
    )

    # 构建音频前缀 + audio content items
    audio_prefix_parts = []
    audio_items = []
    audio_index = 1
    total_audio_duration_seconds = 0.0

    role_card_map = {card.id: card for card in role_cards if card}
    selected_sound_cards = _resolve_storyboard_selected_sound_cards(shot, db)
    for sound_card in selected_sound_cards:
        ref_audio = db.query(models.SubjectCardAudio).filter(
            models.SubjectCardAudio.card_id == sound_card.id,
            models.SubjectCardAudio.is_reference == True
        ).first()
        if not ref_audio or not ref_audio.audio_path:
            continue

        duration_seconds = _ensure_audio_duration_seconds_cached(ref_audio, db)
        sound_name = (sound_card.name or "").strip()
        linked_role = role_card_map.get(getattr(sound_card, "linked_card_id", None))
        if not linked_role and getattr(sound_card, "linked_card_id", None):
            linked_role = db.query(models.SubjectCard).filter(
                models.SubjectCard.id == sound_card.linked_card_id
            ).first()
        label = "旁白" if sound_name == "旁白" else ((linked_role.name or "").strip() if linked_role else sound_name)
        if not label:
            label = sound_name or f"声音{audio_index}"

        audio_prefix_parts.append(f"{label}[音频{audio_index}]")
        audio_items.append({
            "url": ref_audio.audio_path,
            "label": label,
            "duration_seconds": duration_seconds
        })
        total_audio_duration_seconds += duration_seconds
        audio_index += 1

    return {
        "image_prefix_parts": image_meta["image_prefix_parts"],
        "image_urls": image_meta["image_urls"],
        "selected_scene_image_url": selected_scene_image_url,
        "audio_prefix_parts": audio_prefix_parts,
        "audio_items": audio_items,
        "total_audio_duration_seconds": total_audio_duration_seconds,
    }


def _build_moti_v2_content(shot, db, full_prompt: str, first_frame_image_url: str = "") -> list:
    """
    为 Seedance 2.0 系列模型构建 v2 content 数组。
    顺序：text(前缀+提示词) → 首帧参考图/场景图/角色参考图 → 声音卡片音频
    """
    assets = _collect_moti_v2_reference_assets(
        shot,
        db,
        first_frame_image_url=first_frame_image_url,
    )
    audio_items = assets["audio_items"]
    validation_error = _get_seedance_audio_validation_error(
        audio_items,
        float(assets["total_audio_duration_seconds"] or 0.0)
    )
    if validation_error:
        raise ValueError(validation_error)

    # 组装 text（moti v2 接口不支持换行，统一压平为空格）
    clean_prompt = build_seedance_prompt(
        prompt=full_prompt,
    )
    text = build_seedance_content_text(
        prompt=clean_prompt,
        image_prefix_parts=assets["image_prefix_parts"],
        audio_prefix_parts=assets["audio_prefix_parts"],
    )

    # 组装 content 数组
    content = [{"type": "text", "text": text}]
    for url in assets["image_urls"]:
        content.append({
            "type": "image_url",
            "image_url": {"url": url},
            "role": "reference_image"
        })
    for audio_item in audio_items:
        content.append({
            "type": "audio_url",
            "audio_url": {"url": audio_item["url"]}
        })

    return content


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


def _build_storyboard_video_text_and_images_content(full_prompt: str, image_urls: List[str]) -> list:
    text = str(full_prompt or "").strip()
    content = [{"type": "text", "text": text}]
    for url in image_urls or []:
        normalized_url = str(url or "").strip()
        if not normalized_url:
            continue
        content.append({
            "type": "image_url",
            "image_url": {"url": normalized_url},
            "role": "reference_image"
        })
    return content


def _build_grok_video_content(shot, db, full_prompt: str, first_frame_image_url: str = "") -> list:
    assets = _collect_moti_v2_reference_assets(
        shot,
        db,
        first_frame_image_url=first_frame_image_url,
    )
    return _build_storyboard_video_text_and_images_content(full_prompt, assets["image_urls"])


def _build_unified_storyboard_video_task_payload(
    *,
    shot,
    db,
    username: str,
    model_name: str,
    provider: str,
    full_prompt: str,
    aspect_ratio: str,
    duration: int,
    first_frame_image_url: str = "",
    resolution_name: Optional[str] = None,
    appoint_account: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_provider = str(provider or "").strip().lower()
    normalized_model = _normalize_storyboard_video_model(model_name, default_model=DEFAULT_STORYBOARD_VIDEO_MODEL)
    if normalized_provider == "yijia-grok":
        normalized_provider = "yijia"
        normalized_model = "grok"

    payload: Dict[str, Any] = {
        "username": str(username or "").strip(),
        "provider": normalized_provider,
        "model": normalized_model,
        "ratio": _normalize_storyboard_video_aspect_ratio(
            aspect_ratio,
            model=normalized_model,
            default_ratio=_STORYBOARD_VIDEO_MODEL_CONFIG[normalized_model]["default_ratio"],
        ),
        "duration": _normalize_storyboard_video_duration(
            duration,
            model=normalized_model,
            default_duration=_STORYBOARD_VIDEO_MODEL_CONFIG[normalized_model]["default_duration"],
        ),
    }

    if normalized_provider == "moti":
        payload.update({
            "content": _build_moti_v2_content(
                shot,
                db,
                full_prompt,
                first_frame_image_url=first_frame_image_url,
            ),
            "typography": "全能参考",
            "watermark": False,
        })
        normalized_appoint_account = _normalize_storyboard_video_appoint_account(appoint_account)
        if normalized_appoint_account:
            payload["extra"] = {
                "appoint_accounts": [normalized_appoint_account]
            }
        return payload

    if normalized_model == "grok":
        payload.update({
            "content": _build_grok_video_content(
                shot,
                db,
                full_prompt,
                first_frame_image_url=first_frame_image_url,
            ),
            "resolution_name": _normalize_storyboard_video_resolution_name(
                resolution_name,
                model=normalized_model,
                default_resolution=_STORYBOARD_VIDEO_MODEL_CONFIG[normalized_model].get("default_resolution", ""),
            ),
        })
        return payload

    payload.update({
        "prompt": str(full_prompt or "").strip(),
        "aspect_ratio": payload["ratio"],
    })
    if first_frame_image_url:
        payload["image_url"] = str(first_frame_image_url).strip()
    return payload


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


def _is_moti_storyboard_video_model(model: Optional[str]) -> bool:
    return _normalize_storyboard_video_model(
        model,
        default_model=DEFAULT_STORYBOARD_VIDEO_MODEL
    ) in MOTI_STORYBOARD_VIDEO_MODELS


def _resolve_storyboard_video_model_by_provider(provider: Optional[str], default_model: str = DEFAULT_STORYBOARD_VIDEO_MODEL) -> str:
    raw = (provider or "").strip().lower()
    if raw in {"yijia-grok", "yijia"}:
        normalized_default = _normalize_storyboard_video_model(default_model, default_model=DEFAULT_STORYBOARD_VIDEO_MODEL)
        if normalized_default in {"sora-2", "grok"}:
            return normalized_default
        return "grok"
    if raw == "moti":
        normalized_default = _normalize_storyboard_video_model(default_model, default_model=DEFAULT_STORYBOARD_VIDEO_MODEL)
        if _is_moti_storyboard_video_model(normalized_default):
            return normalized_default
        return DEFAULT_STORYBOARD_VIDEO_MODEL
    return _normalize_storyboard_video_model(default_model, default_model=DEFAULT_STORYBOARD_VIDEO_MODEL)


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

@app.post("/api/shots/{shot_id}/generate-storyboard-image")
async def generate_storyboard_image(
    shot_id: int,
    request: GenerateStoryboardImageRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """为镜头生成分镜图"""
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    # 检查是否有sora_prompt
    if not shot.sora_prompt or shot.sora_prompt.strip() == "":
        raise HTTPException(status_code=400, detail="请先生成SORA提示词")

    requested_image_model = _normalize_detail_images_model(
        str(request.model or "").strip() or "banana-pro",
        default_model="banana-pro",
    )
    requested_image_provider = str(request.provider or "").strip().lower() or None

    # 检查是否已有completed的分镜图，如果有则创建变体
    if shot.storyboard_image_status == 'completed' and shot.storyboard_image_path:
        # 创建变体：复制当前shot，variant_index+1
        max_variant = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.episode_id == shot.episode_id,
            models.StoryboardShot.shot_number == shot.shot_number
        ).order_by(models.StoryboardShot.variant_index.desc()).first()

        new_variant_index = (max_variant.variant_index if max_variant else 0) + 1

        new_shot = models.StoryboardShot(
            **build_storyboard_image_variant_payload(
                shot,
                next_variant=new_variant_index,
            )
        )
        db.add(new_shot)
        db.flush()
        _backfill_storyboard_visual_references_from_family(new_shot, db)
        db.commit()
        db.refresh(new_shot)

        shot = new_shot  # 使用新创建的变体
        print(f"[分镜图生成] 创建变体镜头: {shot.shot_number}_{shot.variant_index}")

    # 获取镜头关联的主体作为参考图
    selected_card_ids = _debug_parse_card_ids(getattr(shot, "selected_card_ids", "[]"))
    if len(selected_card_ids) > 5:
        raise HTTPException(
            status_code=400,
            detail=f"参考主体数量超过限制，最多支持5个主体，当前选择了{len(selected_card_ids)}个"
        )
    reference_images = _collect_storyboard_subject_reference_urls(shot, db)
    for image_url in reference_images:
        print(f"[分镜图生成] 添加主体参考图: {image_url}")

    # 拼接最终prompt
    # 顺序：绘图要求 + 绘画风格 + 场景描述 + SORA提示词
    prompt_parts = [request.requirement, request.style]
    image_ratio = _resolve_storyboard_sora_image_ratio(episode, request.size)

    # 添加场景描述（如果有）
    if shot.scene_override and shot.scene_override.strip():
        prompt_parts.append(shot.scene_override.strip())

    prompt_parts.append(shot.sora_prompt)

    final_prompt = " ".join(
        str(part or "").replace("\r", " ").replace("\n", " ").strip()
        for part in prompt_parts
        if str(part or "").strip()
    )

    print("=" * 80)
    print(f"[分镜图生成] 镜头ID: {shot.id}, 镜号: {shot.shot_number}")
    print(f"[分镜图生成] 模型: {requested_image_model}, 尺寸: {image_ratio}, 分辨率: {request.resolution}")
    print(f"[分镜图生成] 参考图数量: {len(reference_images)}")
    print(f"[分镜图生成] 是否有场景描述: {'是' if shot.scene_override and shot.scene_override.strip() else '否'}")
    print("-" * 80)
    print("最终拼接的Prompt:")
    print(final_prompt)
    print("=" * 80)

    # 提交生成任务
    try:
        loop = asyncio.get_event_loop()
        task_id = await loop.run_in_executor(
            executor,
            lambda: submit_image_generation(
                final_prompt,
                requested_image_model,
                image_ratio,
                request.resolution,
                1,
                reference_images if reference_images else None,
                requested_image_provider,
            )
        )

        shot.storyboard_image_task_id = task_id
        shot.storyboard_image_model = requested_image_model
        shot.storyboard_image_status = 'processing'
        _record_storyboard_image_charge(
            db,
            shot=shot,
            model_name=requested_image_model,
            provider=_build_image_generation_debug_meta(
                requested_image_model,
                provider=requested_image_provider,
                has_reference_images=bool(reference_images),
            )["provider"],
            resolution=request.resolution,
            task_id=task_id,
            detail_payload={
                "size": image_ratio,
                "resolution": request.resolution,
            },
        )
        db.commit()
        db.refresh(shot)

        return {
            "task_id": task_id,
            "status": "processing",
            "shot_id": shot.id
        }

    except Exception as e:
        print(f"[分镜图生成] 失败: {str(e)}")
        shot.storyboard_image_model = requested_image_model
        shot.storyboard_image_status = 'failed'
        shot.storyboard_image_path = f"error:{str(e)}"
        db.commit()
        raise HTTPException(status_code=500, detail=f"分镜图生成失败: {str(e)}")

# ==================== 镜头细化图片生成API ====================

@app.post("/api/shots/{shot_id}/generate-detail-images")
async def generate_detail_images(
    shot_id: int,
    request: Optional[GenerateDetailImagesRequest] = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """为镜头生成细化图片（解析timeline_json并并发调用CC生成）"""
    print(f"\n[细化图片生成] ========== 开始处理镜头ID: {shot_id} ==========")

    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        print(f"[细化图片生成] 错误: 镜头不存在")
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        print(f"[细化图片生成] 错误: 无权限")
        raise HTTPException(status_code=403, detail="无权限")

    image_ratio = _resolve_storyboard_sora_image_ratio(
        episode,
        request.size if request else None,
    )
    episode_default_detail_model = _normalize_detail_images_model(
        getattr(episode, "detail_images_model", None),
        default_model="seedream-4.0"
    )
    detail_images_model = _normalize_detail_images_model(
        request.model if request else None,
        default_model=episode_default_detail_model
    )
    episode_default_detail_provider = _resolve_episode_detail_images_provider(episode)
    detail_images_provider = _normalize_detail_images_provider(
        request.provider if request else None,
        default_provider=episode_default_detail_provider
    ) or None
    detail_image_resolution = str(request.resolution or "2K").strip() if request else "2K"
    print(f"[细化图片生成] 使用尺寸比例: {image_ratio}")
    print(f"[细化图片生成] 使用模型: {detail_images_model}, 分辨率: {detail_image_resolution}")
    include_scene_references = True
    image_cw = _normalize_storyboard2_image_cw(
        getattr(episode, "storyboard2_image_cw", None),
        default_value=50
    )
    scene_description = (shot.scene_override or "").strip()
    selected_sub_shot_index = None
    if request and request.selected_sub_shot_index is not None:
        try:
            selected_sub_shot_index = int(request.selected_sub_shot_index)
        except Exception:
            raise HTTPException(status_code=400, detail="镜头选择参数无效")
    print("[细化图片生成] 镜头图参考图策略: 携带当前镜头全部主体参考图（角色/场景/道具）")

    # 检查是否有timeline_json
    print(f"[细化图片生成] 检查timeline_json: 长度={len(shot.timeline_json) if shot.timeline_json else 0}")
    if not shot.timeline_json or shot.timeline_json.strip() == "":
        print(f"[细化图片生成] 错误: timeline_json为空，请先生成Sora提示词")
        raise HTTPException(status_code=400, detail="请先生成Sora提示词")

    # 解析timeline_json
    try:
        timeline_data = json.loads(shot.timeline_json)
        if isinstance(timeline_data, dict):
            timeline = timeline_data.get("timeline") or []
        elif isinstance(timeline_data, list):
            timeline = timeline_data
        else:
            timeline = []

        print(f"[细化图片生成] timeline解析成功，原始包含{len(timeline)}个子镜头")
        if not timeline:
            print(f"[细化图片生成] 错误: timeline数据为空")
            raise HTTPException(status_code=400, detail="timeline数据为空")

        # 默认使用第一个分镜，可通过 selected_sub_shot_index 选择其他分镜
        target_sub_shot_index = selected_sub_shot_index or 1
        if target_sub_shot_index < 1 or target_sub_shot_index > len(timeline):
            raise HTTPException(
                status_code=400,
                detail=f"镜头选择超出范围，可选 1~{len(timeline)}"
            )

        target_item = timeline[target_sub_shot_index - 1]
        if isinstance(target_item, dict):
            selected_item = dict(target_item)
        else:
            selected_item = {
                "time": "",
                "visual": str(target_item or ""),
                "audio": ""
            }
        selected_sub_shot_text = str(request.selected_sub_shot_text or "").strip() if request else ""
        if selected_sub_shot_text:
            selected_item["visual"] = selected_sub_shot_text
        selected_item["__source_sub_shot_index"] = target_sub_shot_index
        timeline = [selected_item]
        print(
            f"[细化图片生成] 已按规则裁剪为指定子镜头: index={target_sub_shot_index}, 当前数量: {len(timeline)}"
        )
    except json.JSONDecodeError as e:
        print(f"[细化图片生成] 错误: timeline JSON解析失败 - {str(e)}")
        print(f"[细化图片生成] timeline内容: {shot.timeline_json[:200]}...")
        raise HTTPException(status_code=400, detail="timeline数据格式错误")

    # 确保镜头有stable_id
    if not shot.stable_id:
        shot.stable_id = str(uuid.uuid4())
        db.commit()
        print(f"[细化图片生成] 为镜头生成stable_id: {shot.stable_id}")

    # 不再创建变体镜头；始终在当前镜头内累积镜头图历史。
    print(f"[细化图片生成] 使用当前镜头写入镜头图: shot_id={shot.id}, 镜号={shot.shot_number}, 变体={shot.variant_index}")

    # 保存镜头图文案覆盖（独立于右侧Sora提示词）
    try:
        selected_index_for_override = 1
        if timeline and isinstance(timeline[0], dict):
            selected_index_for_override = int(timeline[0].get("__source_sub_shot_index") or 1)
        selected_visual_text = ""
        if timeline and isinstance(timeline[0], dict):
            selected_visual_text = str(timeline[0].get("visual", "") or "").strip()

        if selected_visual_text:
            raw_overrides = shot.detail_image_prompt_overrides or "{}"
            try:
                prompt_overrides = json.loads(raw_overrides) if isinstance(raw_overrides, str) else (raw_overrides or {})
            except Exception:
                prompt_overrides = {}
            if not isinstance(prompt_overrides, dict):
                prompt_overrides = {}
            prompt_overrides[str(selected_index_for_override)] = selected_visual_text
            shot.detail_image_prompt_overrides = json.dumps(prompt_overrides, ensure_ascii=False)
            db.commit()
    except Exception as save_override_error:
        print(f"[细化图片生成] 保存 detail_image_prompt_overrides 失败: {str(save_override_error)}")

    # 获取参考图URLs
    reference_urls = _collect_storyboard_subject_reference_urls(shot, db)
    for ref_url in reference_urls:
        print(f"[细化图片生成] 添加主体参考图: {ref_url}")

    print(f"[细化图片生成] 参考图数量: {len(reference_urls)}")

    # 创建/更新 pending 状态的 detail_images 记录（按子镜头序号复用，保留历史图片列表）
    print(f"[细化图片生成] 开始创建或更新{len(timeline)}个子镜头记录...")
    for idx, item in enumerate(timeline, start=1):
        source_sub_shot_index = item.get("__source_sub_shot_index") if isinstance(item, dict) else None
        try:
            detail_sub_shot_index = int(source_sub_shot_index) if source_sub_shot_index is not None else int(idx)
        except Exception:
            detail_sub_shot_index = int(idx)
        detail_img = db.query(models.ShotDetailImage).filter(
            models.ShotDetailImage.shot_id == shot.id,
            models.ShotDetailImage.sub_shot_index == detail_sub_shot_index
        ).order_by(models.ShotDetailImage.id.desc()).first()

        if detail_img:
            detail_img.time_range = item.get("time", "")
            detail_img.visual_text = item.get("visual", "")
            detail_img.audio_text = item.get("audio", "")
            detail_img.status = "pending"
            detail_img.error_message = ""
            detail_img.task_id = ""
            detail_img.provider = ""
            detail_img.model_name = ""
            detail_img.submit_api_url = ""
            detail_img.status_api_url = ""
            detail_img.query_error_count = 0
            detail_img.last_query_error = ""
            detail_img.submitted_at = None
            detail_img.last_query_at = None
        else:
            detail_img = models.ShotDetailImage(
                shot_id=shot.id,
                sub_shot_index=detail_sub_shot_index,
                time_range=item.get("time", ""),
                visual_text=item.get("visual", ""),
                audio_text=item.get("audio", ""),
                status="pending",
                task_id="",
                provider="",
                model_name="",
                submit_api_url="",
                status_api_url="",
                query_error_count=0,
                last_query_error="",
                submitted_at=None,
                last_query_at=None,
            )
            db.add(detail_img)
        print(f"[细化图片生成]   - 子镜头{detail_sub_shot_index}: {item.get('time', '')} - {item.get('visual', '')[:50]}...")

    db.commit()
    print(f"[细化图片生成] 所有子镜头记录已创建并提交")

    from datetime import datetime
    debug_dir = f"detail_images_shot_{shot.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    detail_images_debug_meta = _build_image_generation_debug_meta(
        detail_images_model,
        provider=detail_images_provider,
        has_reference_images=bool(reference_urls)
    )

    # 保存输入信息
    debug_info = {
        "shot_id": shot.id,
        "shot_number": shot.shot_number,
        "stable_id": shot.stable_id,
        "aspect_ratio": shot.aspect_ratio,
        "detail_images_model": detail_images_model,
        "detail_images_provider": detail_images_debug_meta["provider"],
        "detail_images_actual_model": detail_images_debug_meta["actual_model"],
        "detail_images_api_url": detail_images_debug_meta["submit_api_url"],
        "detail_images_status_api_url_template": detail_images_debug_meta["status_api_url_template"],
        "include_scene_references": include_scene_references,
        "image_cw": image_cw,
        "image_ratio": image_ratio,
        "resolution": detail_image_resolution,
        "scene_description": scene_description,
        "selected_sub_shot_index": timeline[0].get("__source_sub_shot_index") if timeline and isinstance(timeline[0], dict) else 1,
        "timeline": timeline,
        "reference_urls": reference_urls,
        "sub_shot_count": len(timeline)
    }

    _save_detail_images_debug(debug_dir, "input.json", debug_info, shot_id=shot.id)

    # 启动后台任务
    from threading import Thread
    thread = Thread(
        target=_process_detail_images_generation,
        args=(
            shot.id,
            shot.stable_id,
            timeline,
            reference_urls,
            image_ratio,
            detail_image_resolution,
            detail_images_model,
            detail_images_debug_meta["provider"],
            image_cw,
            scene_description,
            debug_dir
        )
    )
    thread.daemon = True
    thread.start()

    print(f"[细化图片生成] 后台任务已启动")
    print(f"[细化图片生成] ========== 处理完成 ==========\n")

    return {
        "message": "细化图片生成已启动",
        "shot_id": shot.id,
        "sub_shot_count": len(timeline),
        "selected_sub_shot_index": timeline[0].get("__source_sub_shot_index") if timeline and isinstance(timeline[0], dict) else 1,
        "model": detail_images_model
    }


def _process_detail_images_generation(
    shot_id,
    stable_id,
    timeline,
    reference_urls,
    image_ratio,
    detail_image_resolution,
    detail_images_model,
    detail_images_provider,
    image_cw,
    scene_description,
    debug_dir
):
    """后台任务：并发生成子镜头图片（即梦并发在接口层统一限流）。"""
    from threading import Thread
    import json

    print(f"\n[细化图片后台任务] ========== 开始处理镜头{shot_id} ==========")

    normalized_image_cw = _normalize_storyboard2_image_cw(image_cw, default_value=50)
    db = SessionLocal()
    try:
        # 更新所有pending状态为processing
        updated_count = db.query(models.ShotDetailImage).filter(
            models.ShotDetailImage.shot_id == shot_id,
            models.ShotDetailImage.status == "pending"
        ).update({"status": "processing"})
        db.commit()
        print(f"[细化图片后台任务] 已将{updated_count}个子镜头状态更新为processing")

        normalized_detail_model = _normalize_detail_images_model(
            detail_images_model,
            default_model="seedream-4.0"
        )
        detail_meta = _build_image_generation_debug_meta(
            normalized_detail_model,
            provider=detail_images_provider,
        )
        detail_provider = detail_meta["provider"]
        detail_actual_model = detail_meta["actual_model"]
        print(
            f"[细化图片后台任务] 模型配置: model={normalized_detail_model}, "
            f"provider={detail_provider}, actual_model={detail_actual_model}"
        )

        # 准备所有任务数据
        tasks_data = []
        for idx, item in enumerate(timeline, start=1):
            source_sub_shot_index = item.get("__source_sub_shot_index") if isinstance(item, dict) else None
            try:
                detail_sub_shot_index = int(source_sub_shot_index) if source_sub_shot_index is not None else int(idx)
            except Exception:
                detail_sub_shot_index = int(idx)
            visual = str(item.get("visual", "") or "")
            scene_text = str(scene_description or "").strip()
            prompt_parts = [scene_text, visual]
            prompt_text = " ".join(
                str(part or "").replace("\r", " ").replace("\n", " ").strip()
                for part in prompt_parts
                if str(part or "").strip()
            ).strip()
            if not prompt_text:
                prompt_text = visual
            sub_stable_id = f"{stable_id}_sub{detail_sub_shot_index}"
            request_name = f"shot_{shot_id}_sub{detail_sub_shot_index}"
            request_payload = _build_image_generation_request_payload(
                provider=detail_provider,
                actual_model=detail_actual_model,
                prompt_text=prompt_text,
                ratio=image_ratio,
                reference_images=reference_urls,
                name=request_name,
                resolution=detail_image_resolution,
                cw=normalized_image_cw
            )

            # 保存输入内容到debug
            input_info = {
                "sub_shot_index": detail_sub_shot_index,
                "stable_id": sub_stable_id,
                "provider": detail_provider,
                "requested_model": normalized_detail_model,
                "actual_model": detail_actual_model,
                "api_url": get_image_submit_api_url(
                    model_name=normalized_detail_model,
                    provider=detail_provider,
                    has_reference_images=bool(reference_urls)
                ),
                "status_api_url_template": get_image_status_api_url(
                    task_id="{task_id}",
                    model_name=normalized_detail_model,
                    provider=detail_provider
                ),
                "scene_description": scene_text,
                "visual_text": visual,
                "prompt_text": prompt_text,
                "ratio": image_ratio,
                "resolution": detail_image_resolution,
                "reference_urls": reference_urls,
                "request_payload": request_payload
            }
            _save_detail_images_debug(
                debug_dir,
                f"sub_shot_{detail_sub_shot_index}_input.json",
                input_info,
                shot_id=shot_id,
            )

            tasks_data.append((detail_sub_shot_index, sub_stable_id, prompt_text, visual, request_payload))

        print(f"[细化图片后台任务] 已准备{len(tasks_data)}个任务，开始并发执行（即梦在接口层统一限流）...")

        # 定义单个子镜头的处理函数
        def process_single_sub_shot(detail_sub_shot_index, sub_stable_id, prompt_text, visual_text, request_payload):
            try:
                print(f"\n[细化图片后台任务] ========== 子镜头 {detail_sub_shot_index}/{len(tasks_data)} 开始生成 ==========")
                print(f"[细化图片后台任务] Prompt: {prompt_text[:120]}...")
                print(
                    f"[细化图片后台任务] 比例: {image_ratio}, 参考图数量: {len(reference_urls) if reference_urls else 0}, "
                    f"provider={detail_provider}"
                )

                submit_result = _submit_single_image_generation_task(
                    prompt_text=prompt_text,
                    model_name=detail_actual_model,
                    provider=detail_provider,
                    size=image_ratio,
                    resolution=detail_image_resolution,
                    reference_images=reference_urls if reference_urls else None,
                    name=f"shot_{shot_id}_sub{detail_sub_shot_index}",
                )
                print(
                    f"[细化图片后台任务] ✓ 子镜头{detail_sub_shot_index}提交成功: "
                    f"task_id={submit_result.get('task_id')}"
                )

                # 创建独立的数据库session
                db_local = SessionLocal()
                try:
                    # 查找数据库记录
                    detail_img = db_local.query(models.ShotDetailImage).filter(
                        models.ShotDetailImage.shot_id == shot_id,
                        models.ShotDetailImage.sub_shot_index == detail_sub_shot_index
                    ).order_by(models.ShotDetailImage.id.desc()).first()

                    if not detail_img:
                        print(f"[细化图片后台任务] 警告：未找到子镜头{detail_sub_shot_index}的数据库记录")
                        return
                    shot_record = db_local.query(models.StoryboardShot).filter(
                        models.StoryboardShot.id == shot_id
                    ).first()
                    if not shot_record:
                        print(f"[细化图片后台任务] 警告：未找到镜头{shot_id}的数据库记录")
                        return

                    detail_img.optimized_prompt = prompt_text
                    detail_img.status = "processing"
                    detail_img.error_message = ""
                    detail_img.task_id = str(submit_result.get("task_id") or "").strip()
                    detail_img.provider = detail_provider
                    detail_img.model_name = normalized_detail_model
                    detail_img.submit_api_url = str(submit_result.get("submit_api_url") or "").strip()
                    detail_img.status_api_url = str(submit_result.get("status_api_url") or "").strip()
                    detail_img.query_error_count = 0
                    detail_img.last_query_error = ""
                    detail_img.submitted_at = datetime.utcnow()
                    detail_img.last_query_at = None
                    _record_detail_image_charge(
                        db_local,
                        detail_img=detail_img,
                        shot=shot_record,
                        model_name=normalized_detail_model,
                        provider=detail_provider,
                        resolution=detail_image_resolution,
                        task_id=detail_img.task_id,
                        detail_payload={
                            "sub_shot_index": detail_sub_shot_index,
                            "size": image_ratio,
                            "resolution": detail_image_resolution,
                        },
                    )
                    db_local.commit()

                    submit_debug_data = {
                        "sub_shot_index": detail_sub_shot_index,
                        "stable_id": sub_stable_id,
                        "provider": detail_provider,
                        "requested_model": normalized_detail_model,
                        "actual_model": detail_actual_model,
                        "api_url": detail_img.submit_api_url,
                        "status_api_url": detail_img.status_api_url,
                        "task_id": detail_img.task_id,
                        "visual_text": visual_text,
                        "prompt_text": prompt_text,
                        "request_payload": request_payload,
                    }
                    _save_detail_images_debug(
                        debug_dir,
                        f"sub_shot_{detail_sub_shot_index}_submit_result.json",
                        submit_debug_data,
                        shot_id=shot_id,
                    )
                    print(
                        f"[细化图片后台任务] ✓ 子镜头{detail_sub_shot_index}已交由后台轮询: "
                        f"task_id={detail_img.task_id}"
                    )

                finally:
                    db_local.close()

            except Exception as e:
                error_msg = str(e)
                print(f"[细化图片后台任务] ✗ 子镜头{detail_sub_shot_index}异常: {error_msg}")
                import traceback
                traceback.print_exc()

                # 保存错误到debug
                error_data = {
                    "sub_shot_index": detail_sub_shot_index,
                    "stable_id": sub_stable_id,
                    "provider": detail_provider,
                    "requested_model": normalized_detail_model,
                    "actual_model": detail_actual_model,
                    "api_url": get_image_submit_api_url(
                        model_name=normalized_detail_model,
                        provider=detail_provider,
                        has_reference_images=bool(reference_urls)
                    ),
                    "status_api_url_template": get_image_status_api_url(
                        task_id="{task_id}",
                        model_name=normalized_detail_model,
                        provider=detail_provider
                    ),
                    "prompt_text": prompt_text,
                    "request_payload": request_payload,
                    "error": error_msg,
                    "traceback": traceback.format_exc(),
                }
                _save_detail_images_debug(
                    debug_dir,
                    f"sub_shot_{detail_sub_shot_index}_error.json",
                    error_data,
                    shot_id=shot_id,
                )

                # 更新数据库
                db_local = SessionLocal()
                try:
                    detail_img = db_local.query(models.ShotDetailImage).filter(
                        models.ShotDetailImage.shot_id == shot_id,
                        models.ShotDetailImage.sub_shot_index == detail_sub_shot_index
                    ).order_by(models.ShotDetailImage.id.desc()).first()
                    if detail_img:
                        existing_images = []
                        try:
                            existing_images = json.loads(detail_img.images_json or "[]")
                        except Exception:
                            existing_images = []
                        has_existing_images = (
                            isinstance(existing_images, list)
                            and any(isinstance(url, str) and url.strip() for url in existing_images)
                        )
                        detail_img.error_message = error_msg
                        detail_img.task_id = ""
                        detail_img.provider = detail_provider
                        detail_img.model_name = normalized_detail_model
                        detail_img.submit_api_url = get_image_submit_api_url(
                            model_name=normalized_detail_model,
                            provider=detail_provider,
                            has_reference_images=bool(reference_urls)
                        )
                        detail_img.status_api_url = ""
                        detail_img.query_error_count = 0
                        detail_img.last_query_error = ""
                        detail_img.last_query_at = None
                        detail_img.status = 'completed' if has_existing_images else 'failed'
                        db_local.commit()
                finally:
                    db_local.close()

        # 启动所有线程
        threads = []
        for detail_sub_shot_index, sub_stable_id, prompt_text, visual_text, request_payload in tasks_data:
            thread = Thread(
                target=process_single_sub_shot,
                args=(detail_sub_shot_index, sub_stable_id, prompt_text, visual_text, request_payload)
            )
            thread.daemon = True
            thread.start()
            threads.append(thread)

        # 等待所有线程完成
        for thread in threads:
            thread.join()

        print(f"\n[细化图片后台任务] ========== 镜头{shot_id}所有子镜头处理完成 ==========")

    except Exception as e:
        print(f"[细化图片生成] 后台任务失败: {str(e)}")
        import traceback
        traceback.print_exc()

        # 标记所有processing状态为failed
        db.query(models.ShotDetailImage).filter(
            models.ShotDetailImage.shot_id == shot_id,
            models.ShotDetailImage.status == "processing",
            or_(
                models.ShotDetailImage.task_id == "",
                models.ShotDetailImage.task_id.is_(None)
            )
        ).update({
            "status": "failed",
            "error_message": str(e)
        })
        db.commit()
    finally:
        db.close()


@app.get("/api/shots/{shot_id}/detail-images")
async def get_shot_detail_images(
    shot_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取镜头的所有细化图片"""
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    detail_images = db.query(models.ShotDetailImage).filter(
        models.ShotDetailImage.shot_id == shot_id
    ).order_by(models.ShotDetailImage.sub_shot_index).all()

    return {
        "shot_id": shot_id,
        "cover_image_url": (shot.storyboard_image_path or "").strip(),
        "first_frame_reference_image_url": (getattr(shot, "first_frame_reference_image_url", "") or "").strip(),
        "uploaded_first_frame_reference_image_url": (getattr(shot, "uploaded_first_frame_reference_image_url", "") or "").strip(),
        "uploaded_scene_image_url": (getattr(shot, "uploaded_scene_image_url", "") or "").strip(),
        "use_uploaded_scene_image": bool(getattr(shot, "use_uploaded_scene_image", False)),
        "selected_scene_image_url": _resolve_selected_scene_reference_image_url(shot, db),
        "detail_images": [
            {
                "id": img.id,
                "sub_shot_index": img.sub_shot_index,
                "time_range": img.time_range,
                "visual_text": img.visual_text,
                "audio_text": img.audio_text,
                "optimized_prompt": img.optimized_prompt,
                "images": json.loads(img.images_json) if img.images_json else [],
                "status": img.status,
                "error_message": img.error_message
            }
            for img in detail_images
        ]
    }


@app.patch("/api/shots/{shot_id}/detail-images/cover")
async def set_shot_detail_image_cover(
    shot_id: int,
    request: SetDetailImageCoverRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """设置镜头细化图中的某张图片为封面镜头图。"""
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    target_url = str(request.image_url or "").strip()
    if not target_url:
        raise HTTPException(status_code=400, detail="图片地址不能为空")

    detail_images = db.query(models.ShotDetailImage).filter(
        models.ShotDetailImage.shot_id == shot_id
    ).all()
    allowed_urls = set()
    for img in detail_images:
        if not img.images_json:
            continue
        try:
            image_list = json.loads(img.images_json)
        except Exception:
            image_list = []
        if not isinstance(image_list, list):
            continue
        for url in image_list:
            if isinstance(url, str) and url.strip():
                allowed_urls.add(url.strip())

    if target_url not in allowed_urls:
        raise HTTPException(status_code=400, detail="该图片不属于当前镜头")

    shot.storyboard_image_path = target_url
    shot.storyboard_image_status = "completed"
    db.commit()

    return {
        "shot_id": shot.id,
        "cover_image_url": target_url,
        "message": "封面镜头图已更新"
    }


def _get_shot_detail_image_urls(shot_id: int, db: Session) -> List[str]:
    detail_images = db.query(models.ShotDetailImage).filter(
        models.ShotDetailImage.shot_id == shot_id
    ).all()
    detail_urls = []
    for detail_image in detail_images:
        if not detail_image.images_json:
            continue
        try:
            image_list = json.loads(detail_image.images_json)
        except Exception:
            image_list = []
        if not isinstance(image_list, list):
            continue
        for url in image_list:
            image_url = normalize_first_frame_candidate_url(url)
            if image_url:
                detail_urls.append(image_url)
    return detail_urls


def _get_shot_first_frame_candidate_urls(shot: models.StoryboardShot, db: Session) -> List[str]:
    return collect_first_frame_candidate_urls(
        storyboard_image_url=getattr(shot, "storyboard_image_path", ""),
        detail_image_urls=_get_shot_detail_image_urls(shot.id, db),
        uploaded_first_frame_image_url=getattr(shot, "uploaded_first_frame_reference_image_url", ""),
    )


def _get_subject_card_reference_image_url(
    card_id: int,
    db: Session,
    *,
    allow_uploaded_fallback: bool = True,
) -> str:
    reference_image = db.query(models.GeneratedImage).filter(
        models.GeneratedImage.card_id == card_id,
        models.GeneratedImage.is_reference == True,
        models.GeneratedImage.status == "completed",
    ).order_by(
        models.GeneratedImage.created_at.desc(),
        models.GeneratedImage.id.desc(),
    ).first()
    if reference_image and str(reference_image.image_path or "").strip():
        return str(reference_image.image_path).strip()

    if not allow_uploaded_fallback:
        return ""

    uploaded_image = db.query(models.CardImage).filter(
        models.CardImage.card_id == card_id
    ).order_by(
        models.CardImage.order.desc(),
        models.CardImage.id.desc(),
    ).first()
    if uploaded_image and str(uploaded_image.image_path or "").strip():
        return str(uploaded_image.image_path).strip()
    return ""


def _collect_storyboard_subject_reference_urls(
    shot: models.StoryboardShot,
    db: Session,
    *,
    allow_uploaded_fallback: bool = True,
) -> List[str]:
    selected_ids = _debug_parse_card_ids(getattr(shot, "selected_card_ids", "[]"))
    if not selected_ids:
        return []

    selected_cards = _resolve_selected_cards(db, selected_ids)
    reference_urls: List[str] = []
    seen_urls = set()
    for card in selected_cards:
        if not card:
            continue
        image_url = _get_subject_card_reference_image_url(
            card.id,
            db,
            allow_uploaded_fallback=allow_uploaded_fallback,
        )
        normalized_url = str(image_url or "").strip()
        if not normalized_url or normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        reference_urls.append(normalized_url)
    return reference_urls


def _resolve_storyboard_sora_image_ratio(
    episode: Optional[models.Episode],
    requested_size: Optional[str] = None,
) -> str:
    if episode:
        video_ratio = _normalize_jimeng_ratio(
            getattr(episode, "storyboard_video_aspect_ratio", None),
            default_ratio=getattr(episode, "shot_image_size", None) or "9:16",
        )
        return video_ratio
    return _normalize_jimeng_ratio(requested_size, default_ratio="9:16")


def _get_selected_scene_card_image_url(
    shot: models.StoryboardShot,
    db: Session,
) -> str:
    selected_ids = _debug_parse_card_ids(getattr(shot, "selected_card_ids", "[]"))
    selected_cards = _resolve_selected_cards(db, selected_ids)
    for card in selected_cards:
        if not card or card.card_type != "场景":
            continue
        image_url = _get_subject_card_reference_image_url(
            card.id,
            db,
            allow_uploaded_fallback=False,
        )
        if image_url:
            return image_url
    return ""


def _resolve_selected_scene_reference_image_url(
    shot: models.StoryboardShot,
    db: Session,
) -> str:
    return resolve_scene_reference_image_url(
        selected_scene_card_image_url=_get_selected_scene_card_image_url(shot, db),
        uploaded_scene_image_url=getattr(shot, "uploaded_scene_image_url", ""),
        use_uploaded_scene_image=bool(getattr(shot, "use_uploaded_scene_image", False)),
    )


def _backfill_storyboard_visual_references_from_family(
    shot: models.StoryboardShot,
    db: Session,
) -> bool:
    family_shots = db.query(models.StoryboardShot).filter(
        models.StoryboardShot.episode_id == shot.episode_id,
        models.StoryboardShot.shot_number == shot.shot_number,
    ).order_by(
        models.StoryboardShot.variant_index.asc(),
        models.StoryboardShot.id.asc(),
    ).all()

    source_shot = choose_storyboard_reference_source(shot, family_shots)
    if not source_shot:
        return False

    changed = False

    source_storyboard_image_path = str(getattr(source_shot, "storyboard_image_path", "") or "").strip()
    if not str(getattr(shot, "storyboard_image_path", "") or "").strip() and source_storyboard_image_path:
        shot.storyboard_image_path = source_storyboard_image_path
        changed = True

    source_storyboard_image_status = str(getattr(source_shot, "storyboard_image_status", "") or "").strip()
    if (
        str(getattr(shot, "storyboard_image_status", "") or "").strip() in {"", "idle"}
        and source_storyboard_image_status
    ):
        shot.storyboard_image_status = source_storyboard_image_status
        changed = True

    source_storyboard_image_model = str(getattr(source_shot, "storyboard_image_model", "") or "").strip()
    if not str(getattr(shot, "storyboard_image_model", "") or "").strip() and source_storyboard_image_model:
        shot.storyboard_image_model = source_storyboard_image_model
        changed = True

    source_first_frame = normalize_first_frame_candidate_url(
        getattr(source_shot, "first_frame_reference_image_url", "")
    )
    if not normalize_first_frame_candidate_url(getattr(shot, "first_frame_reference_image_url", "")) and source_first_frame:
        shot.first_frame_reference_image_url = source_first_frame
        changed = True

    source_uploaded_scene = str(getattr(source_shot, "uploaded_scene_image_url", "") or "").strip()
    if not str(getattr(shot, "uploaded_scene_image_url", "") or "").strip() and source_uploaded_scene:
        shot.uploaded_scene_image_url = source_uploaded_scene
        changed = True

    if (
        not bool(getattr(shot, "use_uploaded_scene_image", False))
        and bool(getattr(source_shot, "use_uploaded_scene_image", False))
        and str(getattr(shot, "uploaded_scene_image_url", "") or "").strip()
    ):
        shot.use_uploaded_scene_image = True
        changed = True

    return changed


def _resolve_selected_first_frame_reference_image_url(
    shot: models.StoryboardShot,
    db: Session
) -> str:
    target_url = normalize_first_frame_candidate_url(
        getattr(shot, "first_frame_reference_image_url", "")
    )
    if not target_url:
        return ""
    if target_url in _get_shot_first_frame_candidate_urls(shot, db):
        return target_url
    if _backfill_storyboard_visual_references_from_family(shot, db):
        db.flush()
        if target_url in _get_shot_first_frame_candidate_urls(shot, db):
            return target_url
    return ""


@app.patch("/api/shots/{shot_id}/first-frame-reference")
async def set_shot_first_frame_reference(
    shot_id: int,
    request: SetFirstFrameReferenceRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    target_url = normalize_first_frame_candidate_url(request.image_url)
    if target_url:
        detail_urls = _get_shot_detail_image_urls(shot_id, db)
        if not is_allowed_first_frame_candidate_url(
            target_url=target_url,
            storyboard_image_url=getattr(shot, "storyboard_image_path", ""),
            detail_image_urls=detail_urls,
            uploaded_first_frame_image_url=getattr(shot, "uploaded_first_frame_reference_image_url", ""),
        ):
            raise HTTPException(status_code=400, detail="该图片不属于当前镜头")

    shot.first_frame_reference_image_url = target_url
    db.commit()

    return {
        "shot_id": shot.id,
        "first_frame_reference_image_url": target_url,
        "message": "首帧参考图已更新" if target_url else "已取消首帧参考图",
        "candidate_urls": _get_shot_first_frame_candidate_urls(shot, db),
    }


@app.post("/api/shots/{shot_id}/first-frame-reference-image")
async def upload_shot_first_frame_reference_image(
    shot_id: int,
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    try:
        loop = asyncio.get_event_loop()
        cdn_url = await loop.run_in_executor(
            executor,
            save_and_upload_to_cdn,
            file
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传首帧参考图失败: {str(e)}")

    shot.uploaded_first_frame_reference_image_url = str(cdn_url or "").strip()
    db.commit()

    return {
        "shot_id": shot.id,
        "uploaded_first_frame_reference_image_url": shot.uploaded_first_frame_reference_image_url,
        "first_frame_reference_image_url": str(getattr(shot, "first_frame_reference_image_url", "") or "").strip(),
        "candidate_urls": _get_shot_first_frame_candidate_urls(shot, db),
        "message": "首帧参考图已上传",
    }


@app.post("/api/shots/{shot_id}/scene-image")
async def upload_shot_scene_image(
    shot_id: int,
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    try:
        loop = asyncio.get_event_loop()
        cdn_url = await loop.run_in_executor(
            executor,
            save_and_upload_to_cdn,
            file
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传场景图片失败: {str(e)}")

    shot.uploaded_scene_image_url = cdn_url
    shot.use_uploaded_scene_image = False
    db.commit()

    return {
        "shot_id": shot.id,
        "uploaded_scene_image_url": cdn_url,
        "use_uploaded_scene_image": False,
        "selected_scene_image_url": _resolve_selected_scene_reference_image_url(shot, db),
        "message": "场景图片已上传"
    }


@app.patch("/api/shots/{shot_id}/scene-image-selection")
async def set_shot_scene_image_selection(
    shot_id: int,
    request: SetShotSceneImageSelectionRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    if request.use_uploaded_scene_image and not str(getattr(shot, "uploaded_scene_image_url", "") or "").strip():
        raise HTTPException(status_code=400, detail="当前镜头没有已上传的场景图片")

    shot.use_uploaded_scene_image = bool(request.use_uploaded_scene_image)
    db.commit()

    return {
        "shot_id": shot.id,
        "uploaded_scene_image_url": (getattr(shot, "uploaded_scene_image_url", "") or "").strip(),
        "use_uploaded_scene_image": bool(getattr(shot, "use_uploaded_scene_image", False)),
        "selected_scene_image_url": _resolve_selected_scene_reference_image_url(shot, db),
        "message": "已切换到镜头场景图片" if shot.use_uploaded_scene_image else "已切换到场景卡图片"
    }

# ==================== 视频导出API ====================

@app.get("/api/episodes/{episode_id}/export-all")
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

# 重新处理视频（下载并上传到CDN）
@app.post("/api/shots/{shot_id}/reprocess-video")
async def reprocess_shot_video(
    shot_id: int,
    db: Session = Depends(get_db)
):
    """重新处理视频：下载Sora视频并上传到自己的CDN（适用于已完成但还是Sora URL的视频）"""
    from video_service import download_and_upload_video

    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    if shot.video_status != 'completed':
        raise HTTPException(status_code=400, detail=f"视频未完成生成，当前状态: {shot.video_status}")

    if not shot.video_path:
        raise HTTPException(status_code=404, detail="视频路径不存在")

    # 如果已经是自己CDN的URL（包含moapp.net.cn），不需要重新处理
    if 'moapp.net.cn' in shot.video_path:
        return {"message": "视频已经在自己的CDN", "video_url": shot.video_path}

    # 重新处理：下载并上传到自己的CDN
    try:
        cdn_url = download_and_upload_video(shot.video_path, shot_id)
        previous_video_path = shot.video_path
        previous_thumbnail = shot.thumbnail_video_path
        shot.video_path = cdn_url
        if not previous_thumbnail or previous_thumbnail == previous_video_path:
            shot.thumbnail_video_path = cdn_url

        new_video = models.ShotVideo(
            shot_id=shot.id,
            video_path=cdn_url
        )
        db.add(new_video)
        db.commit()

        return {"message": "视频处理成功", "video_url": cdn_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")


# ==================== 故事板2 API ====================

class Storyboard2GenerateImagesRequest(BaseModel):
    requirement: str = ""
    style: str = ""
    provider: Optional[str] = None
    model: Optional[str] = None
    size: str = "9:16"
    resolution: str = "2K"
    timeout_seconds: int = 420


class Storyboard2SetCurrentImageRequest(BaseModel):
    current_image_id: Optional[int] = None


class Storyboard2BatchGenerateSoraPromptsRequest(BaseModel):
    default_template: str = "2d漫画风格（细）"
    shot_ids: Optional[List[int]] = None


class Storyboard2UpdateShotRequest(BaseModel):
    excerpt: str = ""
    selected_card_ids: Optional[List[int]] = None


class Storyboard2UpdateSubShotRequest(BaseModel):
    sora_prompt: Optional[str] = None
    scene_override: Optional[str] = None
    selected_card_ids: Optional[List[int]] = None


class Storyboard2GenerateVideoRequest(BaseModel):
    model: str = "grok"
    duration: Optional[int] = None
    aspect_ratio: Optional[str] = None
    resolution_name: Optional[str] = None


def _verify_episode_permission(episode_id: int, user: models.User, db: Session) -> models.Episode:
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if not script or script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    return episode


def _parse_storyboard2_timeline(timeline_json_value):
    if not timeline_json_value:
        return []

    try:
        timeline_data = json.loads(timeline_json_value) if isinstance(timeline_json_value, str) else timeline_json_value
    except Exception:
        return []

    if isinstance(timeline_data, list):
        return timeline_data

    if isinstance(timeline_data, dict):
        nested = timeline_data.get("timeline")
        if isinstance(nested, list):
            return nested

    return []


def _storyboard2_fallback_timeline(duration_seconds: int):
    total = max(6, int(duration_seconds or 10))
    segment_count = 2 if total <= 9 else (3 if total <= 14 else 4)
    segment_length = total / segment_count

    timeline = []
    for idx in range(segment_count):
        start = int(round(idx * segment_length))
        if idx == segment_count - 1:
            end = total
        else:
            end = max(start + 1, int(round((idx + 1) * segment_length)))

        timeline.append({
            "time": f"{start}s-{end}s",
            "visual": f"分镜{idx + 1}画面描述",
            "audio": ""
        })

    return timeline


def _resolve_storyboard2_time_range(item: dict, index: int, total_count: int) -> str:
    if not isinstance(item, dict):
        return f"分镜 {index}/{total_count}"

    raw_time = item.get("time") or item.get("time_range") or ""
    if isinstance(raw_time, str) and raw_time.strip():
        return raw_time.strip()

    start = item.get("start_time", item.get("start", item.get("begin")))
    end = item.get("end_time", item.get("end"))
    duration = item.get("duration")

    if start is not None and end is not None:
        return f"{start}s-{end}s"
    if start is not None and duration is not None:
        try:
            end_value = float(start) + float(duration)
            if end_value.is_integer():
                end_value = int(end_value)
            return f"{start}s-{end_value}s"
        except Exception:
            pass

    return f"分镜 {index}/{total_count}"


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


def _extract_storyboard2_timeline_subject_names(timeline_item: dict) -> List[str]:
    """Extract subject names from timeline item fields."""
    if not isinstance(timeline_item, dict):
        return []

    raw_candidates = [
        timeline_item.get("subjects"),
        timeline_item.get("subject_names"),
        timeline_item.get("subject_list"),
        timeline_item.get("subject"),
        timeline_item.get("main_subject"),
    ]

    names: List[str] = []

    def append_name(value):
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                append_name(item)
            return
        if isinstance(value, dict):
            for key in ("name", "subject", "label"):
                if value.get(key):
                    append_name(value.get(key))
                    return
            return

        text = str(value).strip()
        if not text:
            return

        parts = re.split(r"[\n,，、;；|/]+", text)
        for part in parts:
            token = str(part or "").strip()
            if not token:
                continue
            token = re.sub(r"^(角色|场景|道具|主体|人物)\s*[:：]\s*", "", token)
            token = re.sub(r"^(男主\d*|女主\d*|主角\d*)\s*[:：]\s*", "", token)
            token = token.strip("[]【】")
            if not token:
                continue
            names.append(token)

    for candidate in raw_candidates:
        append_name(candidate)

    resolved = []
    seen = set()
    for name in names:
        clean = str(name or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        resolved.append(clean)
    return resolved


def _normalize_storyboard2_subject_token(text_value) -> str:
    text = str(text_value or "").strip()
    if not text:
        return ""

    text = re.sub(r"^(角色|场景|道具|主体|人物)\s*[:：]\s*", "", text)
    text = re.sub(r"^(男主\d*|女主\d*|主角\d*)\s*[:：]\s*", "", text)
    text = text.strip("[]【】")
    text = re.sub(r"[（(][^（）()]{1,20}[)）]\s*$", "", text).strip()
    text = re.sub(r"\s+", "", text)
    return text.lower()


def _resolve_storyboard2_subject_ids_from_names(
    subject_names: List[str],
    library_cards: List[models.SubjectCard]
) -> List[int]:
    """Resolve timeline subject names to SubjectCard ids."""
    if not subject_names or not library_cards:
        return []

    exact_map: Dict[str, List[int]] = {}
    fuzzy_entries: List[Tuple[int, str]] = []

    for card in library_cards:
        if not card:
            continue
        card_id = int(card.id)
        card_tokens = [
            _normalize_storyboard2_subject_token(card.name),
            _normalize_storyboard2_subject_token(card.alias),
        ]
        for token in card_tokens:
            if not token:
                continue
            exact_map.setdefault(token, []).append(card_id)
            fuzzy_entries.append((card_id, token))

    resolved_ids: List[int] = []
    seen_ids = set()
    for raw_name in subject_names:
        normalized_name = _normalize_storyboard2_subject_token(raw_name)
        if not normalized_name:
            continue

        matched_ids = exact_map.get(normalized_name, [])
        if not matched_ids:
            fuzzy_hits = []
            for card_id, token in fuzzy_entries:
                if not token:
                    continue
                if len(normalized_name) >= 2 and (normalized_name in token or token in normalized_name):
                    fuzzy_hits.append(card_id)
            matched_ids = fuzzy_hits

        for card_id in matched_ids:
            if card_id in seen_ids:
                continue
            seen_ids.add(card_id)
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


def _mark_storyboard2_image_task_active(sub_shot_id: int):
    try:
        task_id = int(sub_shot_id)
    except Exception:
        return
    with storyboard2_active_image_tasks_lock:
        storyboard2_active_image_tasks.add(task_id)


def _mark_storyboard2_image_task_inactive(sub_shot_id: int):
    try:
        task_id = int(sub_shot_id)
    except Exception:
        return
    with storyboard2_active_image_tasks_lock:
        storyboard2_active_image_tasks.discard(task_id)


def _is_storyboard2_image_task_active(sub_shot_id: int) -> bool:
    try:
        task_id = int(sub_shot_id)
    except Exception:
        return False
    with storyboard2_active_image_tasks_lock:
        return task_id in storyboard2_active_image_tasks


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


def _get_storyboard2_sub_shot_with_permission(sub_shot_id: int, user: models.User, db: Session):
    sub_shot = db.query(models.Storyboard2SubShot).filter(
        models.Storyboard2SubShot.id == sub_shot_id
    ).first()
    if not sub_shot:
        raise HTTPException(status_code=404, detail="分镜不存在")

    storyboard2_shot = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.id == sub_shot.storyboard2_shot_id
    ).first()
    if not storyboard2_shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    _verify_episode_permission(storyboard2_shot.episode_id, user, db)
    return sub_shot, storyboard2_shot


def _get_storyboard2_shot_with_permission(storyboard2_shot_id: int, user: models.User, db: Session):
    storyboard2_shot = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.id == storyboard2_shot_id
    ).first()
    if not storyboard2_shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    _verify_episode_permission(storyboard2_shot.episode_id, user, db)
    return storyboard2_shot


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


def _is_prop_subject_card_type(card_type: str) -> bool:
    card_type_text = str(card_type or "").strip().lower()
    if not card_type_text:
        return False
    if card_type_text == "prop":
        return True
    if "道具" in card_type_text:
        return True
    if "閬撳叿" in card_type_text:
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


def _collect_storyboard2_reference_images(
    storyboard2_shot: models.Storyboard2Shot,
    db: Session,
    sub_shot: Optional[models.Storyboard2SubShot] = None,
    include_scene_references: bool = False
):
    selected_card_ids = _parse_storyboard2_card_ids(getattr(sub_shot, "selected_card_ids", "[]"))
    if not selected_card_ids:
        selected_card_ids = _resolve_storyboard2_selected_card_ids(storyboard2_shot, db)
    if not selected_card_ids:
        return []

    filtered_card_ids = list(selected_card_ids)
    if not include_scene_references and filtered_card_ids:
        selected_cards = db.query(models.SubjectCard.id, models.SubjectCard.card_type).filter(
            models.SubjectCard.id.in_(filtered_card_ids)
        ).all()
        scene_card_ids = {
            int(card_id) for card_id, card_type in selected_cards
            if _is_scene_subject_card_type(card_type)
        }
        safe_filtered_ids = []
        for card_id in filtered_card_ids:
            try:
                card_id_int = int(card_id)
            except Exception:
                continue
            if card_id_int not in scene_card_ids:
                safe_filtered_ids.append(card_id_int)
        filtered_card_ids = safe_filtered_ids
        if not filtered_card_ids:
            return []

    reference_images = []
    seen_urls = set()
    for card_id in filtered_card_ids:
        # 优先使用主体“参考图”（与故事板(sora)一致）
        ref_image = db.query(models.GeneratedImage).filter(
            models.GeneratedImage.card_id == card_id,
            models.GeneratedImage.is_reference == True,
            models.GeneratedImage.status == "completed"
        ).order_by(
            models.GeneratedImage.created_at.desc(),
            models.GeneratedImage.id.desc()
        ).first()
        if ref_image and ref_image.image_path:
            if ref_image.image_path not in seen_urls:
                seen_urls.add(ref_image.image_path)
                reference_images.append(ref_image.image_path)
            continue

        # 兜底使用主体上传图（确保主体上传后也能参与参考）
        uploaded_image = db.query(models.CardImage).filter(
            models.CardImage.card_id == card_id
        ).order_by(
            models.CardImage.order.desc(),
            models.CardImage.created_at.desc(),
            models.CardImage.id.desc()
        ).first()
        if uploaded_image and uploaded_image.image_path:
            if uploaded_image.image_path not in seen_urls:
                seen_urls.add(uploaded_image.image_path)
                reference_images.append(uploaded_image.image_path)

    return reference_images


def _get_optional_prompt_config_content(key: str, fallback: str = "") -> str:
    """读取可选提示词配置，读取失败时回退到默认值。"""
    try:
        content = get_prompt_by_key(key)
        content_text = str(content or "").strip()
        if content_text:
            return content_text
    except Exception:
        pass
    return str(fallback or "").strip()


def _save_storyboard2_image_debug(debug_dir: Optional[str], filename: str, payload: dict):
    """保存故事板2镜头图调试文件，不影响主流程。"""
    if not debug_dir:
        return
    try:
        os.makedirs(debug_dir, exist_ok=True)
        file_path = os.path.join(debug_dir, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        log_file_task_event(
            task_folder=os.path.basename(debug_dir),
            file_name=filename,
            payload=payload,
            task_type="storyboard2_image",
            stage="storyboard2_image",
            episode_id=int(payload.get("episode_id")) if isinstance(payload, dict) and payload.get("episode_id") else None,
        )
    except Exception as e:
        print(f"[故事板2镜头图调试] 保存 {filename} 失败: {str(e)}")


def _save_storyboard2_video_debug(debug_dir: Optional[str], filename: str, payload: dict):
    """保存故事板2视频调试信息。"""
    if not debug_dir:
        return
    try:
        os.makedirs(debug_dir, exist_ok=True)
        file_path = os.path.join(debug_dir, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        log_file_task_event(
            task_folder=os.path.basename(debug_dir),
            file_name=filename,
            payload=payload,
            task_type="storyboard2_video",
            stage="storyboard2_video",
            episode_id=int(payload.get("episode_id")) if isinstance(payload, dict) and payload.get("episode_id") else None,
        )
    except Exception as e:
        print(f"[故事板2视频调试] 保存 {filename} 失败: {str(e)}")


def _save_detail_images_debug(debug_dir: Optional[str], filename: str, payload: dict, shot_id: Optional[int] = None):
    """记录细化图片调试信息到看板数据库。"""
    if not debug_dir:
        return
    try:
        log_file_task_event(
            task_folder=os.path.basename(debug_dir),
            file_name=filename,
            payload=payload,
            task_type="detail_images",
            stage="detail_images",
            shot_id=shot_id,
        )
    except Exception as e:
        print(f"[细化图片调试] 记录 {filename} 失败: {str(e)}")


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


def _poll_storyboard2_sub_shot_video_status(
    sub_shot_video_id: int,
    task_id: str,
    debug_dir: Optional[str] = None
):
    """后台轮询故事板2视频任务并落库。"""
    from video_service import check_video_status, is_transient_video_status_error

    polling_history = []
    try:
        while True:
            # 仅在读写数据库时短暂持有会话，避免轮询线程长期占用连接池。
            db = SessionLocal()
            try:
                video_record = db.query(models.Storyboard2SubShotVideo).filter(
                    models.Storyboard2SubShotVideo.id == sub_shot_video_id
                ).first()
                if not video_record:
                    return
                if bool(getattr(video_record, "is_deleted", False)):
                    return

            finally:
                db.close()

            status_info = check_video_status(task_id)
            if is_transient_video_status_error(status_info):
                print(f"[poll] video_id={sub_shot_video_id} task_id={task_id} 上游暂时错误，5秒后重试: {status_info.get('error_message','')}")
                polling_history.append({
                    "polled_at": datetime.now().isoformat(),
                    "status": "query_failed",
                    "progress": 0,
                    "video_url": "",
                    "cdn_uploaded": False,
                    "error_message": str(status_info.get("error_message") or "")
                })
                time.sleep(5)
                continue
            status = _normalize_storyboard2_video_status(
                status_info.get("status"),
                default_value="processing"
            )
            progress_raw = status_info.get("progress")
            error_message = str(status_info.get("error_message") or "").strip()
            video_url = str(status_info.get("video_url") or "").strip()
            cdn_uploaded = bool(status_info.get("cdn_uploaded", False))

            try:
                progress = int(progress_raw) if progress_raw is not None else 0
            except Exception:
                progress = 0

            polling_history.append({
                "polled_at": datetime.now().isoformat(),
                "status": status,
                "progress": progress,
                "video_url": video_url,
                "cdn_uploaded": cdn_uploaded,
                "error_message": error_message
            })
            print(f"[poll] video_id={sub_shot_video_id} task_id={task_id} status={status} progress={progress} video_url={video_url[:60] if video_url else ''}")
            try:
                video_record = db.query(models.Storyboard2SubShotVideo).filter(
                    models.Storyboard2SubShotVideo.id == sub_shot_video_id
                ).first()
                if not video_record:
                    return
                if bool(getattr(video_record, "is_deleted", False)):
                    return

                if _is_storyboard2_video_processing(status):
                    video_record.status = status if status in {"pending", "processing"} else "processing"
                    video_record.progress = max(0, min(progress, 99))
                    video_record.error_message = ""
                    db.commit()
                    should_sleep = True
                elif status == "completed":
                    if not video_url:
                        video_record.status = "failed"
                        video_record.error_message = "任务完成但未返回视频地址"
                        billing_service.reverse_charge_entry(
                            db,
                            billing_key=f"video:storyboard2:{video_record.sub_shot_id}:task:{task_id}",
                            reason="completed_without_video_url",
                        )
                    else:
                        final_video_url = video_url
                        final_thumbnail_url = video_url
                        final_cdn_uploaded = cdn_uploaded

                        if not final_cdn_uploaded:
                            processed_video_url, processed_thumbnail_url, processed_cdn_uploaded, _process_meta = _process_storyboard2_video_cover_and_cdn(
                                video_record=video_record,
                                db=db,
                                upstream_video_url=video_url,
                                task_id=task_id,
                                debug_dir=debug_dir
                            )
                            final_video_url = processed_video_url or final_video_url
                            final_thumbnail_url = processed_thumbnail_url or final_thumbnail_url
                            final_cdn_uploaded = bool(processed_cdn_uploaded)

                        video_record.status = "completed"
                        video_record.video_url = final_video_url
                        if final_thumbnail_url:
                            video_record.thumbnail_url = final_thumbnail_url
                        video_record.progress = 100
                        video_record.error_message = ""
                        video_record.cdn_uploaded = final_cdn_uploaded
                        billing_service.finalize_charge_entry(
                            db,
                            billing_key=f"video:storyboard2:{video_record.sub_shot_id}:task:{task_id}",
                        )
                    db.commit()
                    _save_storyboard2_video_debug(debug_dir, "output.json", {
                        "sub_shot_video_id": sub_shot_video_id,
                        "task_id": task_id,
                        "status": video_record.status,
                        "video_url": video_record.video_url,
                        "thumbnail_url": video_record.thumbnail_url,
                        "cdn_uploaded": video_record.cdn_uploaded,
                        "finished_at": datetime.now().isoformat()
                    })
                    _save_storyboard2_video_debug(debug_dir, "polling_history.json", polling_history)
                    return
                elif status in {"failed", "cancelled"}:
                    video_record.status = "failed"
                    video_record.error_message = error_message or f"任务状态: {status}"
                    billing_service.reverse_charge_entry(
                        db,
                        billing_key=f"video:storyboard2:{video_record.sub_shot_id}:task:{task_id}",
                        reason=f"provider_{status}",
                    )
                    db.commit()
                    _save_storyboard2_video_debug(debug_dir, "error.json", {
                        "sub_shot_video_id": sub_shot_video_id,
                        "task_id": task_id,
                        "status": status,
                        "error_message": video_record.error_message,
                        "failed_at": datetime.now().isoformat()
                    })
                    _save_storyboard2_video_debug(debug_dir, "polling_history.json", polling_history)
                    return
                else:
                    video_record.status = "processing"
                    video_record.progress = max(0, min(progress, 99))
                    db.commit()
                    should_sleep = True
            finally:
                db.close()

            if should_sleep:
                time.sleep(5)
    except Exception as e:
        try:
            db = SessionLocal()
            try:
                db.rollback()
                failed_record = db.query(models.Storyboard2SubShotVideo).filter(
                    models.Storyboard2SubShotVideo.id == sub_shot_video_id
                ).first()
                if failed_record:
                    failed_record.status = "failed"
                    failed_record.error_message = str(e)
                    db.commit()
            finally:
                db.close()
        except Exception:
            pass
        _save_storyboard2_video_debug(debug_dir, "exception.json", {
            "sub_shot_video_id": sub_shot_video_id,
            "task_id": task_id,
            "error": str(e),
            "failed_at": datetime.now().isoformat()
        })
        _save_storyboard2_video_debug(debug_dir, "polling_history.json", polling_history)


def _recover_storyboard2_video_polling():
    """服务重启后，恢复所有处于处理中但无轮询线程的 Storyboard2SubShotVideo 任务。"""
    from threading import Thread
    print("[recover] 开始扫描需要恢复的 storyboard2 视频任务...")
    db = SessionLocal()
    try:
        processing_records = db.query(models.Storyboard2SubShotVideo).filter(
            models.Storyboard2SubShotVideo.is_deleted == False,
            models.Storyboard2SubShotVideo.task_id != "",
            models.Storyboard2SubShotVideo.status.in_(["submitted", "pending", "processing"])
        ).all()
        recovered = [(r.id, r.task_id) for r in processing_records]
    finally:
        db.close()

    print(f"[recover] 扫描完成，找到 {len(recovered)} 条需要恢复的任务")

    for record_id, task_id in recovered:
        print(f"[recover] 恢复轮询: video_id={record_id} task_id={task_id}")
        t = Thread(
            target=_poll_storyboard2_sub_shot_video_status,
            args=(record_id, task_id)
        )
        t.daemon = True
        t.start()

    if recovered:
        print(f"[recover] 已启动 {len(recovered)} 个恢复轮询线程: ids={[r[0] for r in recovered]}")
    else:
        print("[recover] 无需恢复，没有处理中的任务")


def _collect_storyboard2_subject_names(
    storyboard2_shot: Optional[models.Storyboard2Shot],
    library: Optional[models.StoryLibrary],
    db: Session
) -> List[str]:
    if not library:
        return []

    cards = db.query(models.SubjectCard).filter(
        models.SubjectCard.library_id == library.id
    ).filter(
        models.SubjectCard.card_type.in_(ALLOWED_CARD_TYPES)
    ).order_by(
        models.SubjectCard.id.asc()
    ).all()

    role_names = []
    scene_names = []
    prop_names = []
    for card in cards:
        name = (card.name or "").strip()
        if not name:
            continue
        if card.card_type == "角色":
            role_names.append(name)
        elif _is_scene_subject_card_type(card.card_type):
            scene_names.append(name)
        elif _is_prop_subject_card_type(card.card_type):
            prop_names.append(name)
    return role_names + scene_names + prop_names


def _apply_storyboard2_timeline_prompts(
    storyboard2_shot: models.Storyboard2Shot,
    timeline: List[dict],
    db: Session
):
    existing_sub_shots = sorted(
        list(storyboard2_shot.sub_shots or []),
        key=lambda x: (x.sub_shot_index, x.id)
    )

    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.episode_id == storyboard2_shot.episode_id
    ).first()
    library_cards = []
    if library:
        library_cards = db.query(models.SubjectCard).filter(
            models.SubjectCard.library_id == library.id,
            models.SubjectCard.card_type.in_(ALLOWED_CARD_TYPES)
        ).all()

    fallback_selected_ids = _resolve_storyboard2_selected_card_ids(storyboard2_shot, db)

    if not timeline:
        for sub in existing_sub_shots:
            if not (sub.sora_prompt or "").strip():
                sub.sora_prompt = (sub.visual_text or "").strip()
        return

    for idx, item in enumerate(timeline, start=1):
        timeline_item = item if isinstance(item, dict) else {}
        time_range = _resolve_storyboard2_time_range(timeline_item, idx, len(timeline))
        visual_text = str(
            timeline_item.get("visual")
            or timeline_item.get("visual_text")
            or timeline_item.get("description")
            or ""
        ).strip()
        audio_text = str(
            timeline_item.get("audio")
            or timeline_item.get("audio_text")
            or ""
        ).strip()
        timeline_subject_names = _extract_storyboard2_timeline_subject_names(timeline_item)
        selected_card_ids = _resolve_storyboard2_subject_ids_from_names(timeline_subject_names, library_cards)
        if not selected_card_ids and fallback_selected_ids:
            selected_card_ids = list(fallback_selected_ids)
        auto_scene_override = _extract_scene_description_from_card_ids(selected_card_ids, db)

        if not visual_text:
            visual_text = f"分镜{idx}画面描述"

        if idx <= len(existing_sub_shots):
            sub = existing_sub_shots[idx - 1]
            sub.sub_shot_index = idx
            sub.time_range = time_range
            sub.visual_text = visual_text
            sub.audio_text = audio_text
            sub.sora_prompt = visual_text
            sub.selected_card_ids = json.dumps(selected_card_ids, ensure_ascii=False)
            if not bool(getattr(sub, "scene_override_locked", False)) and not (sub.scene_override or "").strip():
                sub.scene_override = auto_scene_override
            continue

        db.add(models.Storyboard2SubShot(
            storyboard2_shot_id=storyboard2_shot.id,
            sub_shot_index=idx,
            time_range=time_range,
            visual_text=visual_text,
            audio_text=audio_text,
            sora_prompt=visual_text,
            selected_card_ids=json.dumps(selected_card_ids, ensure_ascii=False),
            scene_override=auto_scene_override,
            scene_override_locked=False
        ))

    if len(existing_sub_shots) > len(timeline):
        for sub in existing_sub_shots[len(timeline):]:
            if not (sub.sora_prompt or "").strip():
                sub.sora_prompt = (sub.visual_text or "").strip()


def _do_batch_generate_storyboard2_sora_prompts(
    episode_id: int,
    default_template: str,
    user_id: int,
    shot_ids: Optional[List[int]] = None
):
    """后台任务：批量生成故事板2的Sora提示词并写入分镜行"""
    db = SessionLocal()
    try:
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if not episode:
            print(f"[故事板2批量Sora] 片段不存在: {episode_id}")
            return

        script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
        if not script or script.user_id != user_id:
            print(f"[故事板2批量Sora] 无权限，用户ID: {user_id}, 片段ID: {episode_id}")
            return

        # 故事板2批量提示词状态独立于故事板（sora）
        episode.batch_generating_storyboard2_prompts = True
        db.commit()

        query = db.query(models.Storyboard2Shot).filter(
            models.Storyboard2Shot.episode_id == episode_id
        )
        if shot_ids:
            query = query.filter(models.Storyboard2Shot.id.in_(shot_ids))

        storyboard2_shots = query.order_by(
            models.Storyboard2Shot.display_order.asc(),
            models.Storyboard2Shot.shot_number.asc(),
            models.Storyboard2Shot.id.asc()
        ).all()

        if not storyboard2_shots:
            print(f"[故事板2批量Sora] 没有可处理镜头，episode_id={episode_id}")
            episode.batch_generating_storyboard2_prompts = False
            db.commit()
            return

        library = db.query(models.StoryLibrary).filter(
            models.StoryLibrary.episode_id == episode_id
        ).first()
        # 故事板2使用独立提示词模板，不继承故事板(Sora)的自定义规则
        prompt_style = None
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
        all_subject_names = [card.name for card in all_subject_cards if card and (card.name or "").strip()]
        all_subject_text = _build_storyboard2_subject_text(all_subject_cards)

        tasks = []
        for storyboard2_shot in storyboard2_shots:
            excerpt = (storyboard2_shot.excerpt or "").strip()
            if not excerpt:
                continue

            source_shot = None
            if storyboard2_shot.source_shot_id:
                source_shot = db.query(models.StoryboardShot).filter(
                    models.StoryboardShot.id == storyboard2_shot.source_shot_id
                ).first()

            duration = int(source_shot.duration or 10) if source_shot else 10
            if duration not in (10, 15):
                duration = 10 if duration < 13 else 15

            subject_names = all_subject_names
            subject_text = all_subject_text
            print(
                f"[SoraSubjectDebug][storyboard2_batch_prepare] storyboard2_shot_id={storyboard2_shot.id} "
                f"source_shot_id={storyboard2_shot.source_shot_id} shot_number={storyboard2_shot.shot_number} "
                f"source_mode=all_library_subjects "
                f"subject_count={len(subject_names)} "
                f"resolved_subject_text={subject_text}"
            )

            tasks.append({
                "storyboard2_shot_id": storyboard2_shot.id,
                "source_shot_id": source_shot.id if source_shot else None,
                "excerpt": excerpt,
                "subject_names": subject_names,
                "subject_text": subject_text,
                "duration": duration,
                "prompt_style": prompt_style
            })

        if not tasks:
            print(f"[故事板2批量Sora] 没有有效镜头数据，episode_id={episode_id}")
            episode.batch_generating_storyboard2_prompts = False
            db.commit()
            return

        def process_single_storyboard2_shot(task):
            try:
                ai_result = generate_storyboard_prompts(
                    script_excerpt=task["excerpt"],
                    subject_names=task["subject_names"],
                    duration=task["duration"],
                    prompt_style=task["prompt_style"],
                    shot_id=task["source_shot_id"],
                    subject_text_override=task["subject_text"],
                    prompt_key=STORYBOARD2_VIDEO_PROMPT_KEY
                )
                timeline = ai_result.get("timeline", []) if isinstance(ai_result, dict) else []
                return {
                    "storyboard2_shot_id": task["storyboard2_shot_id"],
                    "duration": task["duration"],
                    "timeline": timeline,
                    "success": True
                }
            except Exception as e:
                return {
                    "storyboard2_shot_id": task["storyboard2_shot_id"],
                    "error": str(e),
                    "success": False
                }

        from concurrent.futures import ThreadPoolExecutor, as_completed
        success_count = 0
        failed_count = 0

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(process_single_storyboard2_shot, task) for task in tasks]
            for future in as_completed(futures):
                result = future.result()
                storyboard2_shot_id = result["storyboard2_shot_id"]

                db_save = SessionLocal()
                try:
                    target_shot = db_save.query(models.Storyboard2Shot).filter(
                        models.Storyboard2Shot.id == storyboard2_shot_id
                    ).first()
                    if not target_shot:
                        failed_count += 1
                        continue

                    if result["success"]:
                        timeline = result.get("timeline", [])
                        if not timeline:
                            timeline = _storyboard2_fallback_timeline(result.get("duration", 10))
                        _apply_storyboard2_timeline_prompts(target_shot, timeline, db_save)
                        db_save.commit()
                        success_count += 1
                        print(f"[故事板2批量Sora] 镜头ID {storyboard2_shot_id} 处理完成")
                    else:
                        db_save.rollback()
                        failed_count += 1
                        print(
                            f"[故事板2批量Sora] 镜头ID {storyboard2_shot_id} 失败: "
                            f"{result.get('error', 'Unknown error')}"
                        )
                except Exception as e:
                    db_save.rollback()
                    failed_count += 1
                    print(f"[故事板2批量Sora] 保存镜头ID {storyboard2_shot_id} 结果失败: {str(e)}")
                finally:
                    db_save.close()

        print(f"[故事板2批量Sora] 完成：成功 {success_count}，失败 {failed_count}")
        episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
        if episode:
            episode.batch_generating_storyboard2_prompts = False
            db.commit()
    except Exception as e:
        print(f"[故事板2批量Sora] 后台任务异常: {str(e)}")
        try:
            episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
            if episode:
                episode.batch_generating_storyboard2_prompts = False
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@app.get("/api/episodes/{episode_id}/storyboard2")
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


@app.patch("/api/storyboard2/shots/{storyboard2_shot_id}")
async def update_storyboard2_shot(
    storyboard2_shot_id: int,
    request: Storyboard2UpdateShotRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新故事板2镜头信息（原文描述/主体选择）"""
    storyboard2_shot = _get_storyboard2_shot_with_permission(storyboard2_shot_id, user, db)
    storyboard2_shot.excerpt = (request.excerpt or "").strip()

    if request.selected_card_ids is not None:
        normalized_card_ids = []
        seen_card_ids = set()
        for card_id in request.selected_card_ids:
            if not isinstance(card_id, int) or card_id <= 0 or card_id in seen_card_ids:
                continue
            seen_card_ids.add(card_id)
            normalized_card_ids.append(card_id)

        if normalized_card_ids:
            library = db.query(models.StoryLibrary).filter(
                models.StoryLibrary.episode_id == storyboard2_shot.episode_id
            ).first()
            if not library:
                raise HTTPException(status_code=400, detail="当前片段未创建主体库，无法保存主体选择")

            valid_cards = db.query(models.SubjectCard.id).filter(
                models.SubjectCard.id.in_(normalized_card_ids),
                models.SubjectCard.library_id == library.id,
                models.SubjectCard.card_type.in_(ALLOWED_CARD_TYPES)
            ).all()
            valid_card_ids = {item[0] for item in valid_cards}
            invalid_ids = [card_id for card_id in normalized_card_ids if card_id not in valid_card_ids]
            if invalid_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"存在无效主体ID: {invalid_ids}"
                )

        storyboard2_shot.selected_card_ids = json.dumps(normalized_card_ids, ensure_ascii=False)

    db.commit()

    return {
        "message": "镜头描述已更新",
        "shot_id": storyboard2_shot.id,
        "excerpt": storyboard2_shot.excerpt,
        "selected_card_ids": _parse_storyboard2_card_ids(storyboard2_shot.selected_card_ids)
    }


@app.patch("/api/storyboard2/subshots/{sub_shot_id}")
async def update_storyboard2_sub_shot(
    sub_shot_id: int,
    request: Storyboard2UpdateSubShotRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新故事板2分镜内容（支持编辑分镜描述并自动保存）。"""
    sub_shot, storyboard2_shot = _get_storyboard2_sub_shot_with_permission(sub_shot_id, user, db)

    if request.sora_prompt is not None:
        sub_shot.sora_prompt = (request.sora_prompt or "").strip()

    if request.scene_override is not None:
        sub_shot.scene_override = (request.scene_override or "").strip()
        sub_shot.scene_override_locked = True

    if request.selected_card_ids is not None:
        normalized_card_ids = []
        seen_card_ids = set()
        for card_id in request.selected_card_ids:
            if not isinstance(card_id, int) or card_id <= 0 or card_id in seen_card_ids:
                continue
            seen_card_ids.add(card_id)
            normalized_card_ids.append(card_id)

        if normalized_card_ids:
            library = db.query(models.StoryLibrary).filter(
                models.StoryLibrary.episode_id == storyboard2_shot.episode_id
            ).first()
            if not library:
                raise HTTPException(status_code=400, detail="当前片段未创建主体库，无法保存主体选择")

            valid_cards = db.query(models.SubjectCard.id).filter(
                models.SubjectCard.id.in_(normalized_card_ids),
                models.SubjectCard.library_id == library.id,
                models.SubjectCard.card_type.in_(ALLOWED_CARD_TYPES)
            ).all()
            valid_card_ids = {item[0] for item in valid_cards}
            invalid_ids = [card_id for card_id in normalized_card_ids if card_id not in valid_card_ids]
            if invalid_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"存在无效主体ID: {invalid_ids}"
                )

        sub_shot.selected_card_ids = json.dumps(normalized_card_ids, ensure_ascii=False)
        if not bool(getattr(sub_shot, "scene_override_locked", False)) and not (sub_shot.scene_override or "").strip():
            auto_scene_override = _extract_scene_description_from_card_ids(normalized_card_ids, db)
            if auto_scene_override:
                sub_shot.scene_override = auto_scene_override

    db.commit()

    return {
        "message": "分镜描述已更新",
        "sub_shot_id": sub_shot.id,
        "sora_prompt": sub_shot.sora_prompt or "",
        "scene_override": sub_shot.scene_override or "",
        "scene_override_locked": bool(getattr(sub_shot, "scene_override_locked", False)),
        "selected_card_ids": _parse_storyboard2_card_ids(getattr(sub_shot, "selected_card_ids", "[]"))
    }


@app.post("/api/episodes/{episode_id}/storyboard2/batch-generate-sora-prompts")
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


def _process_storyboard2_sub_shot_image_generation(
    sub_shot_id: int,
    prompt_text: str,
    model_name: str,
    provider: Optional[str],
    size: str,
    resolution: str,
    timeout_seconds: int,
    image_cw: int = 50,
    reference_images: Optional[List[str]] = None,
    debug_dir: Optional[str] = None
):
    """后台线程：生成故事板2分镜候选图并落库"""
    _mark_storyboard2_image_task_active(sub_shot_id)
    db_local = SessionLocal()
    normalized_image_cw = _normalize_storyboard2_image_cw(image_cw, default_value=50)
    task_id = None
    polling_history = []
    saved_images = []
    last_task_result = None
    try:
        sub_shot = db_local.query(models.Storyboard2SubShot).filter(
            models.Storyboard2SubShot.id == sub_shot_id
        ).first()
        if not sub_shot:
            return
        storyboard2_shot = db_local.query(models.Storyboard2Shot).filter(
            models.Storyboard2Shot.id == sub_shot.storyboard2_shot_id
        ).first()
        if not storyboard2_shot:
            return

        sub_shot.image_generate_status = "processing"
        sub_shot.image_generate_progress = "1/4"
        sub_shot.image_generate_error = ""
        db_local.commit()

        _save_storyboard2_image_debug(debug_dir, "worker_start.json", {
            "sub_shot_id": sub_shot_id,
            "storyboard2_shot_id": sub_shot.storyboard2_shot_id,
            "prompt_text": prompt_text,
            "provider": provider,
            "model": model_name,
            "size": size,
            "resolution": resolution,
            "timeout_seconds": timeout_seconds,
            "reference_images": reference_images or [],
            "reference_image_count": len(reference_images or []),
            "started_at": datetime.now().isoformat()
        })

        _save_storyboard2_image_debug(debug_dir, "submit_result.json", {
            "provider": provider,
            "model": model_name,
            "submitted_at": datetime.now().isoformat(),
            "requested_image_count": 4
        })

        api_result = jimeng_generate_image_with_polling(
            prompt_text=prompt_text,
            ratio=size,
            cref=reference_images if reference_images else None,
            name=f"storyboard2_subshot_{sub_shot.id}",
            timeout=timeout_seconds,
            cw=normalized_image_cw,
            model=model_name,
            provider=provider,
        )
        last_task_result = api_result
        task_id = str(api_result.get("task_id") or "").strip()

        if task_id:
            _record_storyboard2_image_charge(
                db_local,
                sub_shot=sub_shot,
                storyboard2_shot=storyboard2_shot,
                task_id=task_id,
                model_name=model_name,
                resolution=resolution,
                quantity=4,
                detail_payload={
                    "size": size,
                    "resolution": resolution,
                    "requested_image_count": 4,
                },
            )

        remote_images = api_result.get("images") or []
        polling_history.append({
            "timestamp": datetime.now().isoformat(),
            "status": "completed" if api_result.get("success") else "failed",
            "image_count": len(remote_images),
            "error": api_result.get("error")
        })

        if not api_result.get("success"):
            _save_storyboard2_image_debug(debug_dir, "task_result_failed.json", api_result)
            raise Exception(api_result.get("error") or "镜头图生成失败")

        if not remote_images:
            raise Exception("生成任务已完成，但未返回图片")

        _save_storyboard2_image_debug(debug_dir, "task_result_completed.json", api_result)

        total_count = min(4, len(remote_images))
        new_images = []
        for idx, remote_url in enumerate(remote_images[:4], start=1):
            cdn_url = download_and_upload_image(remote_url, sub_shot.id)
            new_img = models.Storyboard2SubShotImage(
                sub_shot_id=sub_shot.id,
                image_url=cdn_url,
                size=size
            )
            db_local.add(new_img)
            db_local.flush()
            new_images.append(new_img)
            saved_images.append({
                "index": idx,
                "remote_url": remote_url,
                "cdn_url": cdn_url,
                "image_id": new_img.id
            })

            sub_shot.image_generate_progress = f"{idx}/{total_count}"
            db_local.commit()

        if not new_images:
            raise Exception("未成功保存生成图片")

        # 仅在当前图为空时，第一次生成自动将首图设为当前图
        if sub_shot.current_image_id is None:
            sub_shot.current_image_id = new_images[0].id

        sub_shot.image_generate_status = "idle"
        sub_shot.image_generate_progress = ""
        sub_shot.image_generate_error = ""
        if task_id:
            billing_service.record_image_task_cost_for_storyboard2_sub_shot(
                db_local,
                sub_shot_id=int(sub_shot.id),
                stage="storyboard2_image_generate",
                provider=str(api_result.get("provider") or provider or ""),
                model_name=str(api_result.get("model") or model_name or ""),
                resolution=str(api_result.get("resolution") or resolution or ""),
                cost_rmb=api_result.get("cost"),
                external_task_id=str(task_id or ""),
                billing_key=f"image:storyboard2:{sub_shot.id}:task:{task_id}:cost",
                operation_key=f"image:storyboard2:{storyboard2_shot.id}:sub{sub_shot.id}",
                detail_payload={
                    "size": size,
                    "resolution": resolution,
                    "requested_image_count": 4,
                    "remote_image_count": len(remote_images),
                    "saved_image_count": len(saved_images),
                },
            )
        db_local.commit()

        _save_storyboard2_image_debug(debug_dir, "output.json", {
            "provider": provider,
            "task_id": task_id,
            "status": "completed",
            "remote_image_count": len(remote_images),
            "saved_image_count": len(saved_images),
            "saved_images": saved_images,
            "current_image_id": sub_shot.current_image_id,
            "finished_at": datetime.now().isoformat()
        })
        _save_storyboard2_image_debug(debug_dir, "polling_history.json", polling_history)
        return
    except Exception as e:
        _save_storyboard2_image_debug(debug_dir, "error.json", {
            "task_id": task_id,
            "error": str(e),
            "last_task_result": last_task_result,
            "saved_images": saved_images,
            "failed_at": datetime.now().isoformat()
        })
        _save_storyboard2_image_debug(debug_dir, "polling_history.json", polling_history)
        try:
            db_local.rollback()
            failed_sub_shot = db_local.query(models.Storyboard2SubShot).filter(
                models.Storyboard2SubShot.id == sub_shot_id
            ).first()
            if failed_sub_shot:
                failed_sub_shot.image_generate_status = "failed"
                failed_sub_shot.image_generate_progress = ""
                failed_sub_shot.image_generate_error = str(e)
                if task_id:
                    billing_service.reverse_charge_entry(
                        db_local,
                        billing_key=f"image:storyboard2:{failed_sub_shot.id}:task:{task_id}",
                        reason="provider_failed",
                    )
                db_local.commit()
        except Exception:
            pass
    finally:
        db_local.close()
        _mark_storyboard2_image_task_inactive(sub_shot_id)


@app.post("/api/storyboard2/subshots/{sub_shot_id}/generate-images")
async def generate_storyboard2_sub_shot_images(
    sub_shot_id: int,
    request: Storyboard2GenerateImagesRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """为故事板2某个分镜追加4张可选图片（异步后台生成）"""
    sub_shot, storyboard2_shot = _get_storyboard2_sub_shot_with_permission(sub_shot_id, user, db)
    debug_dir = None

    if (sub_shot.image_generate_status or "").strip() == "processing":
        if _is_storyboard2_image_task_active(sub_shot.id):
            return {
                "message": "当前分镜正在生成中",
                "sub_shot_id": sub_shot.id,
                "status": "processing",
                "progress": sub_shot.image_generate_progress or "1/4"
            }
        # 历史遗留的processing（例如重启后线程丢失），先回收为failed，再允许重新提交。
        sub_shot.image_generate_status = "failed"
        sub_shot.image_generate_progress = ""
        if not (sub_shot.image_generate_error or "").strip():
            sub_shot.image_generate_error = "检测到历史任务中断，请重新生成"
        db.commit()

    episode = db.query(models.Episode).filter(models.Episode.id == storyboard2_shot.episode_id).first()
    episode_default_image_model = getattr(episode, "detail_images_model", None) if episode else "seedream-4.0"
    requested_image_model = _normalize_detail_images_model(
        request.model,
        default_model=episode_default_image_model,
    )
    requested_image_provider = _normalize_detail_images_provider(
        request.provider,
        default_provider=_resolve_episode_detail_images_provider(episode)
    ) or None
    image_debug_meta = _build_image_generation_debug_meta(
        requested_image_model,
        provider=requested_image_provider,
    )
    actual_model = image_debug_meta["actual_model"]

    image_prompt_prefix = _get_optional_prompt_config_content(
        STORYBOARD2_IMAGE_PROMPT_KEY,
        STORYBOARD2_IMAGE_PROMPT_DEFAULT
    )

    prompt_parts = []
    if image_prompt_prefix:
        prompt_parts.append(image_prompt_prefix)
    if request.requirement and request.requirement.strip():
        prompt_parts.append(request.requirement.strip())
    if request.style and request.style.strip():
        prompt_parts.append(request.style.strip())
    scene_override_text = _resolve_storyboard2_scene_override_text(
        sub_shot=sub_shot,
        storyboard2_shot=storyboard2_shot,
        db=db
    )
    if scene_override_text:
        prompt_parts.append(scene_override_text)
    visual_prompt = (sub_shot.sora_prompt or "").strip() or (sub_shot.visual_text or "").strip()
    if visual_prompt:
        prompt_parts.append(visual_prompt)

    final_prompt = " ".join(
        str(part or "").replace("\r", " ").replace("\n", " ").strip()
        for part in prompt_parts
        if str(part or "").strip()
    ).strip()
    if not final_prompt:
        raise HTTPException(status_code=400, detail="缺少可用于生成图片的提示词")

    include_scene_references = bool(getattr(episode, "storyboard2_include_scene_references", False)) if episode else False
    image_cw = _normalize_storyboard2_image_cw(
        getattr(episode, "storyboard2_image_cw", None),
        default_value=50
    ) if episode else 50
    reference_images = _collect_storyboard2_reference_images(
        storyboard2_shot,
        db,
        sub_shot=sub_shot,
        include_scene_references=include_scene_references
    )
    timeout_seconds = max(60, min(int(request.timeout_seconds or 420), 1800))
    default_image_ratio = _normalize_jimeng_ratio(getattr(episode, "shot_image_size", None), default_ratio="9:16")
    selected_size = _normalize_jimeng_ratio(request.size, default_ratio=default_image_ratio)

    try:
        debug_folder = (
            f"storyboard2_subshot_{sub_shot.id}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        debug_dir = os.path.abspath(os.path.join("ai_debug", debug_folder))
        existing_candidate_count = db.query(models.Storyboard2SubShotImage).filter(
            models.Storyboard2SubShotImage.sub_shot_id == sub_shot.id
        ).count()

        _save_storyboard2_image_debug(debug_dir, "input.json", {
            "sub_shot_id": sub_shot.id,
            "storyboard2_shot_id": storyboard2_shot.id,
            "episode_id": storyboard2_shot.episode_id,
            "source_shot_id": storyboard2_shot.source_shot_id,
            "sub_shot_index": sub_shot.sub_shot_index,
            "time_range": sub_shot.time_range,
            "visual_text": sub_shot.visual_text,
            "sora_prompt": sub_shot.sora_prompt,
            "scene_override": scene_override_text,
            "sub_shot_selected_card_ids": _parse_storyboard2_card_ids(getattr(sub_shot, "selected_card_ids", "[]")),
            "shot_excerpt": storyboard2_shot.excerpt,
            "image_prompt_prefix": image_prompt_prefix,
            "provider": image_debug_meta["provider"],
            "model": requested_image_model,
            "actual_model": actual_model,
            "size": selected_size,
            "final_prompt": final_prompt,
            "reference_images": reference_images,
            "reference_image_count": len(reference_images),
            "image_cw": image_cw,
            "include_scene_references": include_scene_references,
            "existing_candidate_count": existing_candidate_count,
            "requested_at": datetime.now().isoformat()
        })
        print(f"[故事板2镜头图调试] 已创建调试目录: {debug_dir}")
    except Exception as debug_error:
        debug_dir = None
        print(f"[故事板2镜头图调试] 创建调试目录失败: {str(debug_error)}")

    sub_shot.image_generate_status = "processing"
    sub_shot.image_generate_progress = "1/4"
    sub_shot.image_generate_error = ""
    db.commit()
    _mark_storyboard2_image_task_active(sub_shot.id)

    from threading import Thread
    thread = Thread(
        target=_process_storyboard2_sub_shot_image_generation,
        args=(
            sub_shot.id,
            final_prompt,
            actual_model,
            image_debug_meta["provider"],
            selected_size,
            request.resolution,
            timeout_seconds,
            image_cw,
            reference_images,
            debug_dir
        )
    )
    thread.daemon = True
    try:
        thread.start()
    except Exception as e:
        _mark_storyboard2_image_task_inactive(sub_shot.id)
        sub_shot.image_generate_status = "failed"
        sub_shot.image_generate_progress = ""
        sub_shot.image_generate_error = f"任务启动失败: {str(e)}"
        db.commit()
        raise HTTPException(status_code=500, detail=f"镜头图任务启动失败: {str(e)}")

    return {
        "message": "镜头图生成任务已启动",
        "sub_shot_id": sub_shot.id,
        "status": "processing",
        "progress": "1/4",
        "debug_dir": debug_dir
    }


@app.post("/api/storyboard2/subshots/{sub_shot_id}/generate-video")
async def generate_storyboard2_sub_shot_video(
    sub_shot_id: int,
    request: Storyboard2GenerateVideoRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """为故事板2某个分镜提交视频生成任务（返回task_id并后台轮询落库）。"""
    sub_shot, storyboard2_shot = _get_storyboard2_sub_shot_with_permission(sub_shot_id, user, db)

    processing_video = db.query(models.Storyboard2SubShotVideo).filter(
        models.Storyboard2SubShotVideo.sub_shot_id == sub_shot.id,
        models.Storyboard2SubShotVideo.is_deleted == False,
        models.Storyboard2SubShotVideo.status.in_(["submitted", "pending", "processing"])
    ).order_by(
        models.Storyboard2SubShotVideo.id.desc()
    ).first()
    if processing_video:
        return {
            "message": "当前分镜已有视频任务进行中",
            "sub_shot_id": sub_shot.id,
            "video_id": processing_video.id,
            "task_id": processing_video.task_id,
            "status": "processing",
            "progress": int(processing_video.progress or 0)
        }

    episode = db.query(models.Episode).filter(models.Episode.id == storyboard2_shot.episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    current_image = None
    if sub_shot.current_image_id:
        current_image = db.query(models.Storyboard2SubShotImage).filter(
            models.Storyboard2SubShotImage.id == sub_shot.current_image_id
        ).first()
    if not current_image:
        current_image = db.query(models.Storyboard2SubShotImage).filter(
            models.Storyboard2SubShotImage.sub_shot_id == sub_shot.id
        ).order_by(
            models.Storyboard2SubShotImage.id.asc()
        ).first()
    if not current_image or not (current_image.image_url or "").strip():
        raise HTTPException(status_code=400, detail="请先生成并设置当前图片，再生成视频")

    default_ratio = _normalize_jimeng_ratio(getattr(episode, "shot_image_size", None), default_ratio="9:16")
    selected_ratio = _normalize_jimeng_ratio(request.aspect_ratio, default_ratio=default_ratio)
    default_duration = _normalize_storyboard2_video_duration(
        getattr(episode, "storyboard2_video_duration", None),
        default_value=6
    )
    selected_duration = _normalize_storyboard2_video_duration(request.duration, default_value=default_duration)
    selected_resolution_name = _normalize_storyboard_video_resolution_name(
        request.resolution_name,
        model="grok",
        default_resolution=getattr(episode, "storyboard_video_resolution_name", None) or "720p"
    )

    requested_model = (request.model or "").strip() or "grok"
    actual_model = "grok"

    # 从 GlobalSettings 读取 Grok 准则
    grok_rule = ""
    try:
        grok_setting = db.query(models.GlobalSettings).filter(
            models.GlobalSettings.key == "grok_rule"
        ).first()
        grok_rule = grok_setting.value if grok_setting and grok_setting.value else GROK_RULE_DEFAULT
    except Exception:
        grok_rule = GROK_RULE_DEFAULT

    prompt_parts = []
    if grok_rule:
        prompt_parts.append(grok_rule)
    if storyboard2_shot.excerpt and storyboard2_shot.excerpt.strip():
        prompt_parts.append(storyboard2_shot.excerpt.strip())

    visual_prompt = (sub_shot.sora_prompt or "").strip() or (sub_shot.visual_text or "").strip()
    if visual_prompt:
        prompt_parts.append(visual_prompt)

    final_prompt = "\n".join(prompt_parts).strip()
    if not final_prompt:
        raise HTTPException(status_code=400, detail="缺少可用于生成视频的提示词")

    debug_dir = None
    try:
        request_payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
        debug_folder = (
            f"storyboard2_subshot_video_{sub_shot.id}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        debug_dir = os.path.abspath(os.path.join("ai_debug", debug_folder))
        _save_storyboard2_video_debug(debug_dir, "input.json", {
            "sub_shot_id": sub_shot.id,
            "storyboard2_shot_id": storyboard2_shot.id,
            "episode_id": storyboard2_shot.episode_id,
            "source_shot_id": storyboard2_shot.source_shot_id,
            "sub_shot_index": sub_shot.sub_shot_index,
            "time_range": sub_shot.time_range,
            "visual_text": sub_shot.visual_text,
            "sora_prompt": sub_shot.sora_prompt,
            "shot_excerpt": storyboard2_shot.excerpt,
            "grok_rule": grok_rule,
            "request": request_payload,
            "requested_model": requested_model,
            "actual_model": actual_model,
            "duration": selected_duration,
            "aspect_ratio": selected_ratio,
            "resolution_name": selected_resolution_name,
            "image_url": current_image.image_url,
            "final_prompt": final_prompt,
            "requested_at": datetime.now().isoformat()
        })
    except Exception as debug_error:
        debug_dir = None
        print(f"[故事板2视频调试] 创建调试目录失败: {str(debug_error)}")

    request_data = {
        "username": user.username,
        "provider": "yijia",
        "model": actual_model,
        "content": _build_storyboard_video_text_and_images_content(final_prompt, [current_image.image_url]),
        "ratio": selected_ratio,
        "duration": selected_duration,
        "resolution_name": selected_resolution_name,
    }

    def call_storyboard2_video_api():
        return requests.post(
            get_video_task_create_url(),
            headers=get_video_api_headers(),
            json=request_data,
            timeout=60
        )

    try:
        loop = asyncio.get_event_loop()
        submit_response = await loop.run_in_executor(executor, call_storyboard2_video_api)
    except Exception as e:
        _save_storyboard2_video_debug(debug_dir, "submit_exception.json", {
            "error": str(e),
            "request_data": request_data
        })
        raise HTTPException(status_code=500, detail=f"视频任务提交失败: {str(e)}")

    response_json = {}
    try:
        response_json = submit_response.json()
    except Exception:
        response_json = {"raw_text": submit_response.text}

    if submit_response.status_code != 200:
        _save_storyboard2_video_debug(debug_dir, "submit_error.json", {
            "status_code": submit_response.status_code,
            "request_data": request_data,
            "response": response_json
        })
        raise HTTPException(
            status_code=500,
            detail=f"视频任务提交失败: HTTP {submit_response.status_code}"
        )

    task_id = str(response_json.get("task_id") or "").strip()
    if not task_id:
        _save_storyboard2_video_debug(debug_dir, "submit_error.json", {
            "status_code": submit_response.status_code,
            "request_data": request_data,
            "response": response_json
        })
        raise HTTPException(status_code=500, detail="视频任务提交失败: 未返回task_id")

    raw_status = str(response_json.get("status") or "pending").strip().lower()
    initial_status = _normalize_storyboard2_video_status(raw_status, default_value="pending")

    progress_value = response_json.get("progress", 0)
    try:
        progress_int = max(0, min(int(progress_value), 100))
    except Exception:
        progress_int = 0

    sub_shot_video = models.Storyboard2SubShotVideo(
        sub_shot_id=sub_shot.id,
        task_id=task_id,
        model_name=actual_model,
        duration=selected_duration,
        aspect_ratio=selected_ratio,
        status=initial_status,
        progress=progress_int,
        error_message=""
    )
    db.add(sub_shot_video)
    _record_storyboard2_video_charge(
        db,
        sub_shot=sub_shot,
        storyboard2_shot=storyboard2_shot,
        task_id=task_id,
        model_name=actual_model,
        duration=selected_duration,
        detail_payload={
            "aspect_ratio": selected_ratio,
            "resolution_name": selected_resolution_name,
            "initial_status": initial_status,
            "video_id_pending": True,
        },
    )
    db.commit()
    db.refresh(sub_shot_video)

    if initial_status == "completed":
        billing_service.finalize_charge_entry(
            db,
            billing_key=f"video:storyboard2:{sub_shot.id}:task:{task_id}",
        )
        db.commit()
    elif initial_status == "failed":
        billing_service.reverse_charge_entry(
            db,
            billing_key=f"video:storyboard2:{sub_shot.id}:task:{task_id}",
            reason="submit_failed",
        )
        db.commit()

    _save_storyboard2_video_debug(debug_dir, "submit_result.json", {
        "sub_shot_video_id": sub_shot_video.id,
        "task_id": task_id,
        "request_data": request_data,
        "response": response_json,
        "submitted_at": datetime.now().isoformat()
    })

    if _is_storyboard2_video_processing(initial_status):
        from threading import Thread
        polling_thread = Thread(
            target=_poll_storyboard2_sub_shot_video_status,
            args=(sub_shot_video.id, task_id, debug_dir, 3600)
        )
        polling_thread.daemon = True
        polling_thread.start()
    elif initial_status == "completed":
        upstream_video_url = str(response_json.get("video_url") or "").strip()
        upstream_cdn_uploaded = bool(response_json.get("cdn_uploaded", False))
        final_video_url = upstream_video_url
        final_thumbnail_url = upstream_video_url
        final_cdn_uploaded = upstream_cdn_uploaded

        if upstream_video_url and not upstream_cdn_uploaded:
            processed_video_url, processed_thumbnail_url, processed_cdn_uploaded, _process_meta = _process_storyboard2_video_cover_and_cdn(
                video_record=sub_shot_video,
                db=db,
                upstream_video_url=upstream_video_url,
                task_id=task_id,
                debug_dir=debug_dir
            )
            final_video_url = processed_video_url or final_video_url
            final_thumbnail_url = processed_thumbnail_url or final_thumbnail_url
            final_cdn_uploaded = bool(processed_cdn_uploaded)

        sub_shot_video.video_url = final_video_url
        if final_thumbnail_url:
            sub_shot_video.thumbnail_url = final_thumbnail_url
        sub_shot_video.progress = 100
        sub_shot_video.cdn_uploaded = final_cdn_uploaded
        db.commit()

    return {
        "message": "视频生成任务已启动",
        "sub_shot_id": sub_shot.id,
        "video_id": sub_shot_video.id,
        "task_id": task_id,
        "status": "processing" if _is_storyboard2_video_processing(sub_shot_video.status) else sub_shot_video.status,
        "progress": int(sub_shot_video.progress or 0),
        "debug_dir": debug_dir
    }


@app.delete("/api/storyboard2/videos/{video_id}")
async def delete_storyboard2_video(
    video_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """软删除故事板2分镜视频记录。"""
    video_record = db.query(models.Storyboard2SubShotVideo).filter(
        models.Storyboard2SubShotVideo.id == video_id
    ).first()
    if not video_record:
        raise HTTPException(status_code=404, detail="视频不存在")

    owner_sub_shot = db.query(models.Storyboard2SubShot).filter(
        models.Storyboard2SubShot.id == video_record.sub_shot_id
    ).first()
    if not owner_sub_shot:
        raise HTTPException(status_code=404, detail="视频所属分镜不存在")

    owner_storyboard2_shot = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.id == owner_sub_shot.storyboard2_shot_id
    ).first()
    if not owner_storyboard2_shot:
        raise HTTPException(status_code=404, detail="视频所属镜头不存在")

    _verify_episode_permission(owner_storyboard2_shot.episode_id, user, db)

    if bool(getattr(video_record, "is_deleted", False)):
        return {
            "message": "视频已删除",
            "video_id": video_id
        }

    video_record.is_deleted = True
    video_record.deleted_at = datetime.utcnow()
    db.commit()

    return {
        "message": "视频删除成功",
        "video_id": video_id
    }


@app.patch("/api/storyboard2/subshots/{sub_shot_id}/current-image")
async def set_storyboard2_current_image(
    sub_shot_id: int,
    request: Storyboard2SetCurrentImageRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """设置故事板2分镜当前图（支持跨分镜引用候选图）"""
    sub_shot, storyboard2_shot = _get_storyboard2_sub_shot_with_permission(sub_shot_id, user, db)

    if request.current_image_id is not None:
        target_image = db.query(models.Storyboard2SubShotImage).filter(
            models.Storyboard2SubShotImage.id == request.current_image_id
        ).first()
        if not target_image:
            raise HTTPException(status_code=404, detail="图片不存在")

        image_owner_sub_shot = db.query(models.Storyboard2SubShot).filter(
            models.Storyboard2SubShot.id == target_image.sub_shot_id
        ).first()
        if not image_owner_sub_shot:
            raise HTTPException(status_code=404, detail="图片所属分镜不存在")

        image_owner_storyboard2_shot = db.query(models.Storyboard2Shot).filter(
            models.Storyboard2Shot.id == image_owner_sub_shot.storyboard2_shot_id
        ).first()
        if not image_owner_storyboard2_shot or image_owner_storyboard2_shot.episode_id != storyboard2_shot.episode_id:
            raise HTTPException(status_code=400, detail="仅支持设置为同一片段内的图片")

    sub_shot.current_image_id = request.current_image_id
    db.commit()

    return {
        "message": "当前图设置成功",
        "sub_shot_id": sub_shot.id,
        "current_image_id": sub_shot.current_image_id
    }


@app.delete("/api/storyboard2/images/{image_id}")
async def delete_storyboard2_image(
    image_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除故事板2候选图（受删除规则约束）"""
    image_record = db.query(models.Storyboard2SubShotImage).filter(
        models.Storyboard2SubShotImage.id == image_id
    ).first()
    if not image_record:
        raise HTTPException(status_code=404, detail="图片不存在")

    owner_sub_shot = db.query(models.Storyboard2SubShot).filter(
        models.Storyboard2SubShot.id == image_record.sub_shot_id
    ).first()
    if not owner_sub_shot:
        raise HTTPException(status_code=404, detail="图片所属分镜不存在")

    owner_storyboard2_shot = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.id == owner_sub_shot.storyboard2_shot_id
    ).first()
    if not owner_storyboard2_shot:
        raise HTTPException(status_code=404, detail="图片所属镜头不存在")

    _verify_episode_permission(owner_storyboard2_shot.episode_id, user, db)

    owner_candidate_count = db.query(models.Storyboard2SubShotImage).filter(
        models.Storyboard2SubShotImage.sub_shot_id == owner_sub_shot.id
    ).count()

    if owner_candidate_count <= 1:
        raise HTTPException(status_code=400, detail="当前分镜仅剩1张可选图，无法删除")

    if owner_sub_shot.current_image_id == image_id:
        raise HTTPException(status_code=400, detail="当前图不允许删除")

    referenced_sub_shots = db.query(models.Storyboard2SubShot).join(
        models.Storyboard2Shot,
        models.Storyboard2SubShot.storyboard2_shot_id == models.Storyboard2Shot.id
    ).filter(
        models.Storyboard2Shot.episode_id == owner_storyboard2_shot.episode_id,
        models.Storyboard2SubShot.current_image_id == image_id
    ).all()

    for sub_shot in referenced_sub_shots:
        sub_shot.current_image_id = None

    db.delete(image_record)
    db.commit()

    return {
        "message": "删除成功",
        "image_id": image_id,
        "cleared_current_count": len(referenced_sub_shots)
    }


# ==================== 爆款库 API ====================

class HitDramaCreate(BaseModel):
    drama_name: str
    view_count: str = ""
    opening_15_sentences: str = ""
    first_episode_script: str = ""
    online_time: str = ""

class HitDramaUpdate(BaseModel):
    drama_name: Optional[str] = None
    view_count: Optional[str] = None
    opening_15_sentences: Optional[str] = None
    first_episode_script: Optional[str] = None
    online_time: Optional[str] = None

class HitDramaResponse(BaseModel):
    id: int
    drama_name: str
    view_count: str
    opening_15_sentences: str
    first_episode_script: str
    online_time: str
    video_filename: Optional[str]
    created_by: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class HitDramaHistoryResponse(BaseModel):
    id: int
    drama_id: int
    action_type: str
    field_name: Optional[str]
    old_value: Optional[str]
    new_value: Optional[str]
    edited_by: str
    edited_at: datetime
    drama_name: Optional[str] = None

    class Config:
        from_attributes = True


@app.get("/api/hit-dramas", response_model=List[HitDramaResponse])
def get_hit_dramas(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取所有爆款库记录（不包括已删除）"""
    dramas = db.query(models.HitDrama).filter(
        models.HitDrama.is_deleted == False
    ).order_by(
        case(
            (or_(models.HitDrama.online_time.is_(None), models.HitDrama.online_time == ""), 1),
            else_=0
        ),
        models.HitDrama.online_time.desc(),
        models.HitDrama.created_at.desc(),
        models.HitDrama.id.desc()
    ).all()
    return dramas


@app.post("/api/hit-dramas", response_model=HitDramaResponse)
def create_hit_drama(
    drama: HitDramaCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """新增爆款库记录"""
    try:
        normalized_data = normalize_hit_drama_payload(drama.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    new_drama = models.HitDrama(
        drama_name=normalized_data["drama_name"],
        view_count=normalized_data["view_count"],
        opening_15_sentences=normalized_data["opening_15_sentences"],
        first_episode_script=normalized_data["first_episode_script"],
        online_time=normalized_data["online_time"],
        created_by=user.username
    )
    db.add(new_drama)
    db.commit()
    db.refresh(new_drama)

    # 记录创建历史
    history = models.HitDramaEditHistory(
        drama_id=new_drama.id,
        action_type="create",
        field_name=None,
        old_value=None,
        new_value=f"创建记录：{normalized_data['drama_name']}",
        edited_by=user.username
    )
    db.add(history)
    db.commit()

    return new_drama


@app.put("/api/hit-dramas/{drama_id}", response_model=HitDramaResponse)
def update_hit_drama(
    drama_id: int,
    drama_update: HitDramaUpdate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """更新爆款库记录"""
    drama = db.query(models.HitDrama).filter(
        models.HitDrama.id == drama_id,
        models.HitDrama.is_deleted == False
    ).first()

    if not drama:
        raise HTTPException(status_code=404, detail="记录不存在")

    # 记录变化的字段
    changes = []
    field_mapping = {
        "drama_name": "剧名",
        "view_count": "播放量",
        "opening_15_sentences": "开头15句",
        "first_episode_script": "第一集文案",
        "online_time": "上线时间"
    }

    try:
        normalized_update = normalize_hit_drama_payload(drama_update.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    for field, value in normalized_update.items():
        if value is not None:
            old_value = getattr(drama, field)
            if old_value != value:
                # 记录历史
                history = models.HitDramaEditHistory(
                    drama_id=drama_id,
                    action_type="update",
                    field_name=field_mapping.get(field, field),
                    old_value=str(old_value) if old_value else "",
                    new_value=str(value),
                    edited_by=user.username
                )
                db.add(history)
                changes.append(field)

                # 更新字段
                setattr(drama, field, value)

    if changes:
        drama.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(drama)

    return drama


@app.delete("/api/hit-dramas/{drama_id}")
def delete_hit_drama(
    drama_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除爆款库记录（软删除）"""
    drama = db.query(models.HitDrama).filter(
        models.HitDrama.id == drama_id,
        models.HitDrama.is_deleted == False
    ).first()

    if not drama:
        raise HTTPException(status_code=404, detail="记录不存在")

    # 软删除
    drama.is_deleted = True
    drama.updated_at = datetime.utcnow()

    # 记录删除历史
    history = models.HitDramaEditHistory(
        drama_id=drama_id,
        action_type="delete",
        field_name=None,
        old_value=f"剧名：{drama.drama_name}",
        new_value="已删除",
        edited_by=user.username
    )
    db.add(history)
    db.commit()

    return {"message": "删除成功", "drama_id": drama_id}


@app.get("/api/hit-dramas/history", response_model=List[HitDramaHistoryResponse])
def get_hit_drama_history(
    user_filter: Optional[str] = None,
    drama_name_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取编辑历史（支持筛选）"""
    query = db.query(models.HitDramaEditHistory).join(
        models.HitDrama,
        models.HitDramaEditHistory.drama_id == models.HitDrama.id
    )

    # 应用筛选条件
    if user_filter:
        query = query.filter(models.HitDramaEditHistory.edited_by.contains(user_filter))

    if drama_name_filter:
        query = query.filter(models.HitDrama.drama_name.contains(drama_name_filter))

    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
            query = query.filter(models.HitDramaEditHistory.edited_at >= start_dt)
        except ValueError:
            pass

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date)
            query = query.filter(models.HitDramaEditHistory.edited_at <= end_dt)
        except ValueError:
            pass

    histories = query.order_by(models.HitDramaEditHistory.edited_at.desc()).all()

    # 添加剧名信息
    result = []
    for history in histories:
        drama = db.query(models.HitDrama).filter(models.HitDrama.id == history.drama_id).first()
        history_dict = {
            "id": history.id,
            "drama_id": history.drama_id,
            "action_type": history.action_type,
            "field_name": history.field_name,
            "old_value": history.old_value,
            "new_value": history.new_value,
            "edited_by": history.edited_by,
            "edited_at": history.edited_at,
            "drama_name": drama.drama_name if drama else None
        }
        result.append(HitDramaHistoryResponse(**history_dict))

    return result


@app.post("/api/hit-dramas/upload-video")
async def upload_hit_drama_video(
    drama_id: int = Form(...),
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """上传爆款库视频"""
    drama = db.query(models.HitDrama).filter(
        models.HitDrama.id == drama_id,
        models.HitDrama.is_deleted == False
    ).first()

    if not drama:
        raise HTTPException(status_code=404, detail="记录不存在")

    # 创建上传目录
    upload_dir = os.path.join("uploads", "hit_drama_videos")
    os.makedirs(upload_dir, exist_ok=True)

    # 生成文件名
    timestamp = int(time.time() * 1000)
    file_ext = os.path.splitext(file.filename)[1]
    filename = f"{timestamp}_{file.filename}"
    file_path = os.path.join(upload_dir, filename)

    # 保存文件
    with open(file_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)

    # 更新数据库
    old_filename = drama.video_filename
    drama.video_filename = filename
    drama.updated_at = datetime.utcnow()

    # 记录历史
    history = models.HitDramaEditHistory(
        drama_id=drama_id,
        action_type="update",
        field_name="视频",
        old_value=old_filename if old_filename else "无",
        new_value=filename,
        edited_by=user.username
    )
    db.add(history)
    db.commit()

    return {"message": "上传成功", "filename": filename}


@app.post("/api/hit-dramas/import-excel")
async def import_hit_drama_excel(
    file: UploadFile = File(...),
    import_mode: str = Form("append"),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """导入Excel数据"""
    try:
        import pandas as pd
        from io import BytesIO

        # 读取Excel文件
        content = await file.read()
        df = pd.read_excel(BytesIO(content))

        # 验证列名
        required_columns = ["剧名", "播放量", "开头15句", "第一集文案", "上线时间"]
        if not all(col in df.columns for col in required_columns):
            raise HTTPException(status_code=400, detail="Excel格式不正确，缺少必要的列")

        normalized_import_mode = str(import_mode or "append").strip().lower()
        if normalized_import_mode not in {"append", "overwrite"}:
            raise HTTPException(status_code=400, detail="导入模式不正确")

        # 过滤空行
        df_clean = df.dropna(how='all')

        rows_to_import = []
        for row_index, row in df_clean.iterrows():
            # 跳过所有字段都为空的行
            if pd.isna(row["剧名"]) or str(row["剧名"]).strip() == "":
                continue

            try:
                rows_to_import.append(normalize_hit_drama_payload({
                    "drama_name": str(row["剧名"]),
                    "view_count": str(row["播放量"]) if not pd.isna(row["播放量"]) else "",
                    "opening_15_sentences": str(row["开头15句"]) if not pd.isna(row["开头15句"]) else "",
                    "first_episode_script": str(row["第一集文案"]) if not pd.isna(row["第一集文案"]) else "",
                    "online_time": str(row["上线时间"]) if not pd.isna(row["上线时间"]) else "",
                }))
            except ValueError as exc:
                try:
                    excel_row_number = int(row_index) + 2
                except (TypeError, ValueError):
                    excel_row_number = "未知"
                raise HTTPException(status_code=400, detail=f"第 {excel_row_number} 行：{exc}")

        if normalized_import_mode == "overwrite":
            db.query(models.HitDramaEditHistory).delete(synchronize_session=False)
            db.query(models.HitDrama).delete(synchronize_session=False)

        imported_count = 0
        for row_data in rows_to_import:
            new_drama = models.HitDrama(
                drama_name=row_data["drama_name"],
                view_count=row_data["view_count"],
                opening_15_sentences=row_data["opening_15_sentences"],
                first_episode_script=row_data["first_episode_script"],
                online_time=row_data["online_time"],
                created_by=user.username
            )
            db.add(new_drama)
            imported_count += 1

        db.commit()

        action_label = "覆盖导入" if normalized_import_mode == "overwrite" else "追加导入"
        return {
            "message": f"{action_label}成功，共导入 {imported_count} 条记录",
            "count": imported_count,
            "import_mode": normalized_import_mode
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"导入失败：{str(e)}")


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "10001"))
    workers = max(1, int(os.getenv("WEB_CONCURRENCY", "1")))

    # 多 worker Web 模式下默认关闭本进程内 poller，避免每个 worker 各自启动一套后台线程。
    if workers > 1 and os.getenv("ENABLE_BACKGROUND_POLLER") is None:
        os.environ["ENABLE_BACKGROUND_POLLER"] = "0"

    if workers > 1:
        uvicorn.run("main:app", host=host, port=port, workers=workers)
    else:
        uvicorn.run(app, host=host, port=port)
