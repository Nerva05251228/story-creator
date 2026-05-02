import json
import os
import subprocess
import tempfile
from typing import Any, List, Optional
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

import models
from ai_config import get_ai_config
from ai_service import get_prompt_by_key
from api.schemas.subject_cards import (
    SubjectCardCreate,
    SubjectCardResponse,
    SubjectCardUpdate,
)
from auth import get_current_user, verify_library_owner
from database import get_db
from text_relay_service import submit_and_persist_text_task


router = APIRouter()

ALLOWED_CARD_TYPES = ("角色", "场景", "道具")
ALL_SUBJECT_CARD_TYPES = ("角色", "场景", "道具", "声音")
SOUND_CARD_TYPE = "声音"


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


@router.post("/api/libraries/{library_id}/cards", response_model=SubjectCardResponse)
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


@router.get("/api/libraries/{library_id}/cards", response_model=List[SubjectCardResponse])
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


@router.put("/api/cards/{card_id}", response_model=SubjectCardResponse)
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


@router.delete("/api/cards/{card_id}")
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


@router.get("/api/cards/{card_id}")
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


@router.put("/api/cards/{card_id}/prompt")
async def update_card_prompt(
    card_id: int,
    prompt_data: dict,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """鏇存柊鍗＄墖鐨凙I prompt"""
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="卡片不存在")

    verify_library_owner(card.library_id, user, db)

    card.ai_prompt = prompt_data.get("prompt", "")
    db.commit()

    return {"message": "更新成功", "ai_prompt": card.ai_prompt}


@router.post("/api/cards/{card_id}/generate-ai-prompt")
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


@router.post("/api/libraries/{library_id}/batch-generate-prompts")
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
