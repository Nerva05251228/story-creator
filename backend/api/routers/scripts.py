import json
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import billing_service
import image_platform_client
import models
from api.routers.episodes import (
    _normalize_storyboard_video_appoint_account,
    _normalize_storyboard_video_aspect_ratio,
    _normalize_storyboard_video_duration,
    _normalize_storyboard_video_model,
    _normalize_storyboard_video_resolution_name,
)
from api.schemas.episodes import DEFAULT_STORYBOARD_VIDEO_MODEL
from api.schemas.scripts import (
    CopyScriptRequest,
    ScriptCreate,
    ScriptResponse,
    ScriptUpdate,
)
from api.services.card_media import _safe_audio_duration_seconds
from api.services.episode_cleanup import clear_episode_dependencies
from auth import get_current_user
from database import get_db
from image_generation_service import normalize_image_model_key


router = APIRouter()


_DETAIL_IMAGES_MODEL_CONFIG = {
    "seedream-4.0": {"actual_model": "seedream-4.0", "provider": "jimeng"},
    "seedream-4.1": {"actual_model": "seedream-4.1", "provider": "jimeng"},
    "seedream-4.5": {"actual_model": "seedream-4.5", "provider": "jimeng"},
    "seedream-4.6": {"actual_model": "seedream-4.6", "provider": "jimeng"},
    "nano-banana-2": {"actual_model": "nano-banana-2", "provider": "momo"},
    "nano-banana-pro": {"actual_model": "nano-banana-pro", "provider": "momo"},
    "gpt-image-2": {"actual_model": "gpt-image-2", "provider": "momo"},
    "jimeng-4.0": {"actual_model": "jimeng-4.0", "provider": "jimeng"},
    "jimeng-4.1": {"actual_model": "jimeng-4.1", "provider": "jimeng"},
    "jimeng-4.5": {"actual_model": "jimeng-4.5", "provider": "jimeng"},
    "jimeng-4.6": {"actual_model": "jimeng-4.6", "provider": "jimeng"},
    "banana2": {"actual_model": "banana2", "provider": "momo"},
    "banana2-moti": {"actual_model": "banana2-moti", "provider": "momo"},
    "banana-pro": {"actual_model": "banana-pro", "provider": "momo"},
}


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


@router.post("/api/scripts", response_model=ScriptResponse)
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

@router.get("/api/scripts/my", response_model=List[ScriptResponse])
async def get_my_scripts(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取我的剧本列表"""
    scripts = db.query(models.Script).filter(
        models.Script.user_id == user.id
    ).order_by(models.Script.created_at.desc()).all()

    return scripts

@router.get("/api/scripts/{script_id}", response_model=ScriptResponse)
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

@router.put("/api/scripts/{script_id}", response_model=ScriptResponse)
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

@router.delete("/api/scripts/{script_id}")
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
    episode_cleanup_stats = clear_episode_dependencies(episode_ids, db)

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
@router.post("/api/scripts/{script_id}/copy")
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
