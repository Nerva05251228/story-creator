import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from typing import List

from fastapi import Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

import models
from api.schemas.shots import (
    GenerateStoryboardImageRequest,
    SetFirstFrameReferenceRequest,
    SetShotSceneImageSelectionRequest,
)
from api.services import billing_charges, shot_image_generation, storyboard_reference_assets
from api.services.card_media import save_and_upload_to_cdn
from auth import get_current_user
from database import get_db
from image_generation_service import submit_image_generation
from storyboard_variant import build_storyboard_image_variant_payload, choose_storyboard_reference_source
from storyboard_video_reference import (
    collect_first_frame_candidate_urls,
    is_allowed_first_frame_candidate_url,
    normalize_first_frame_candidate_url,
)


executor = ThreadPoolExecutor(max_workers=10)

_debug_parse_card_ids = storyboard_reference_assets.parse_card_ids
_collect_storyboard_subject_reference_urls = storyboard_reference_assets.collect_storyboard_subject_reference_urls

_normalize_detail_images_model = shot_image_generation._normalize_detail_images_model
_resolve_storyboard_sora_image_ratio = shot_image_generation._resolve_storyboard_sora_image_ratio
_build_image_generation_debug_meta = shot_image_generation._build_image_generation_debug_meta

_record_storyboard_image_charge = billing_charges.record_storyboard_image_charge


def _load_shot_context(shot_id: int, user: models.User, db: Session):
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == shot_id).first()
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    episode = db.query(models.Episode).filter(models.Episode.id == shot.episode_id).first()
    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")
    return shot, episode, script


def _get_shot_detail_image_urls(shot_id: int, db: Session) -> List[str]:
    detail_images = db.query(models.ShotDetailImage).filter(
        models.ShotDetailImage.shot_id == shot_id
    ).all()
    detail_urls: List[str] = []
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
    db: Session,
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


async def generate_storyboard_image(
    shot_id: int,
    request: GenerateStoryboardImageRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """为镜头生成分镜图"""
    shot, episode, _script = _load_shot_context(shot_id, user, db)

    if not shot.sora_prompt or shot.sora_prompt.strip() == "":
        raise HTTPException(status_code=400, detail="请先生成SORA提示词")

    requested_image_model = _normalize_detail_images_model(
        str(request.model or "").strip() or "banana-pro",
        default_model="banana-pro",
    )
    requested_image_provider = str(request.provider or "").strip().lower() or None

    if shot.storyboard_image_status == "completed" and shot.storyboard_image_path:
        max_variant = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.episode_id == shot.episode_id,
            models.StoryboardShot.shot_number == shot.shot_number,
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

        shot = new_shot
        print(f"[分镜图生成] 创建变体镜头: {shot.shot_number}_{shot.variant_index}")

    selected_card_ids = _debug_parse_card_ids(getattr(shot, "selected_card_ids", "[]"))
    if len(selected_card_ids) > 5:
        raise HTTPException(
            status_code=400,
            detail=f"参考主体数量超过限制，最多支持5个主体，当前选择了{len(selected_card_ids)}个",
        )
    reference_images = _collect_storyboard_subject_reference_urls(shot, db)
    for image_url in reference_images:
        print(f"[分镜图生成] 添加主体参考图: {image_url}")

    prompt_parts = [request.requirement, request.style]
    image_ratio = _resolve_storyboard_sora_image_ratio(episode, request.size)

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
            ),
        )

        shot.storyboard_image_task_id = task_id
        shot.storyboard_image_model = requested_image_model
        shot.storyboard_image_status = "processing"
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
            "shot_id": shot.id,
        }

    except Exception as e:
        print(f"[分镜图生成] 失败: {str(e)}")
        shot.storyboard_image_model = requested_image_model
        shot.storyboard_image_status = "failed"
        shot.storyboard_image_path = f"error:{str(e)}"
        db.commit()
        raise HTTPException(status_code=500, detail=f"分镜图生成失败: {str(e)}")


async def set_shot_first_frame_reference(
    shot_id: int,
    request: SetFirstFrameReferenceRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shot, _episode, _script = _load_shot_context(shot_id, user, db)

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


async def upload_shot_first_frame_reference_image(
    shot_id: int,
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shot, _episode, _script = _load_shot_context(shot_id, user, db)

    try:
        loop = asyncio.get_event_loop()
        cdn_url = await loop.run_in_executor(
            executor,
            save_and_upload_to_cdn,
            file,
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


async def upload_shot_scene_image(
    shot_id: int,
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shot, _episode, _script = _load_shot_context(shot_id, user, db)

    try:
        loop = asyncio.get_event_loop()
        cdn_url = await loop.run_in_executor(
            executor,
            save_and_upload_to_cdn,
            file,
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
        "selected_scene_image_url": storyboard_reference_assets.resolve_selected_scene_reference_image_url(shot, db),
        "message": "场景图片已上传",
    }


async def set_shot_scene_image_selection(
    shot_id: int,
    request: SetShotSceneImageSelectionRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shot, _episode, _script = _load_shot_context(shot_id, user, db)

    if request.use_uploaded_scene_image and not str(getattr(shot, "uploaded_scene_image_url", "") or "").strip():
        raise HTTPException(status_code=400, detail="当前镜头没有已上传的场景图片")

    shot.use_uploaded_scene_image = bool(request.use_uploaded_scene_image)
    db.commit()

    return {
        "shot_id": shot.id,
        "uploaded_scene_image_url": (getattr(shot, "uploaded_scene_image_url", "") or "").strip(),
        "use_uploaded_scene_image": bool(getattr(shot, "use_uploaded_scene_image", False)),
        "selected_scene_image_url": storyboard_reference_assets.resolve_selected_scene_reference_image_url(shot, db),
        "message": "已切换到镜头场景图片" if shot.use_uploaded_scene_image else "已切换到场景卡图片",
    }
