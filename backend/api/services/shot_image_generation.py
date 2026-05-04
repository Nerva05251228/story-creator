import asyncio
import os
import json
import time
import traceback
import uuid
from datetime import datetime
from threading import Thread
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

import billing_service
import image_platform_client
import models
from api.schemas.shots import GenerateDetailImagesRequest, SetDetailImageCoverRequest
from api.services import billing_charges, storyboard_defaults, storyboard_reference_assets
from dashboard_service import log_file_task_event
from database import SessionLocal, get_db
from image_generation_service import (
    get_image_status_api_url,
    get_image_submit_api_url,
    submit_image_generation,
)
from auth import get_current_user


_DETAIL_IMAGES_MODEL_CONFIG = {
    "seedream-4.0": {
        "actual_model": "seedream-4.0",
        "provider": "jimeng",
    },
    "seedream-4.1": {
        "actual_model": "seedream-4.1",
        "provider": "jimeng",
    },
    "seedream-4.5": {
        "actual_model": "seedream-4.5",
        "provider": "jimeng",
    },
    "seedream-4.6": {
        "actual_model": "seedream-4.6",
        "provider": "jimeng",
    },
    "nano-banana-2": {
        "actual_model": "nano-banana-2",
        "provider": "momo",
    },
    "nano-banana-pro": {
        "actual_model": "nano-banana-pro",
        "provider": "momo",
    },
    "gpt-image-2": {
        "actual_model": "gpt-image-2",
        "provider": "momo",
    },
    "jimeng-4.0": {
        "actual_model": "图片 4.0",
        "provider": "jimeng",
    },
    "jimeng-4.1": {
        "actual_model": "图片 4.1",
        "provider": "jimeng",
    },
    "jimeng-4.5": {
        "actual_model": "图片 4.5",
        "provider": "jimeng",
    },
    "jimeng-4.6": {
        "actual_model": "图片 4.6",
        "provider": "jimeng",
    },
    "banana2": {
        "actual_model": "banana2",
        "provider": "momo",
    },
    "banana2-moti": {
        "actual_model": "banana2-moti",
        "provider": "momo",
    },
    "banana-pro": {
        "actual_model": "banana-pro",
        "provider": "momo",
    },
}


_normalize_detail_images_provider = storyboard_defaults.normalize_detail_images_provider
_resolve_episode_detail_images_provider = storyboard_defaults.resolve_episode_detail_images_provider
_normalize_detail_images_model = storyboard_defaults.normalize_detail_images_model
_normalize_storyboard2_image_cw = storyboard_defaults.normalize_storyboard2_image_cw

_record_detail_image_charge = billing_charges.record_detail_image_charge
_resolve_selected_scene_reference_image_url = storyboard_reference_assets.resolve_selected_scene_reference_image_url
_collect_storyboard_subject_reference_urls = storyboard_reference_assets.collect_storyboard_subject_reference_urls


def _normalize_jimeng_ratio(value: Optional[str], default_ratio: str = "9:16") -> str:
    allowed_ratios = {"21:9", "16:9", "3:2", "4:3", "1:1", "3:4", "2:3", "9:16"}
    legacy_map = {
        "1:2": "9:16",
        "2:1": "16:9",
    }
    raw = (value or "").strip()
    normalized = legacy_map.get(raw, raw)
    if normalized in allowed_ratios:
        return normalized
    fallback = legacy_map.get((default_ratio or "").strip(), (default_ratio or "").strip())
    return fallback if fallback in allowed_ratios else "9:16"


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


def _build_image_generation_request_payload(
    *,
    provider: str,
    actual_model: str,
    prompt_text: str,
    ratio: str,
    reference_images: Optional[List[str]] = None,
    name: Optional[str] = None,
    resolution: Optional[str] = None,
    cw: Optional[int] = None,
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
        has_reference_images=bool(normalized_reference_images),
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
            provider=normalized_provider,
        ),
        "provider": normalized_provider,
        "model_name": model_name,
    }


def _save_detail_images_debug(debug_dir: Optional[str], filename: str, payload: dict, shot_id: Optional[int] = None):
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


def _get_detail_image_allowed_urls(shot_id: int, db: Session) -> set[str]:
    detail_images = db.query(models.ShotDetailImage).filter(
        models.ShotDetailImage.shot_id == shot_id
    ).all()
    allowed_urls: set[str] = set()
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
    return allowed_urls


async def generate_detail_images(
    shot_id: int,
    request: Optional[GenerateDetailImagesRequest] = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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
        default_model="seedream-4.0",
    )
    detail_images_model = _normalize_detail_images_model(
        request.model if request else None,
        default_model=episode_default_detail_model,
    )
    episode_default_detail_provider = _resolve_episode_detail_images_provider(episode)
    detail_images_provider = _normalize_detail_images_provider(
        request.provider if request else None,
        default_provider=episode_default_detail_provider,
    ) or None
    detail_image_resolution = str(request.resolution or "2K").strip() if request else "2K"
    print(f"[细化图片生成] 使用尺寸比例: {image_ratio}")
    print(f"[细化图片生成] 使用模型: {detail_images_model}, 分辨率: {detail_image_resolution}")
    include_scene_references = True
    image_cw = _normalize_storyboard2_image_cw(
        getattr(episode, "storyboard2_image_cw", None),
        default_value=50,
    )
    scene_description = (shot.scene_override or "").strip()
    selected_sub_shot_index = None
    if request and request.selected_sub_shot_index is not None:
        try:
            selected_sub_shot_index = int(request.selected_sub_shot_index)
        except Exception:
            raise HTTPException(status_code=400, detail="镜头选择参数无效")
    print("[细化图片生成] 镜头图参考图策略: 携带当前镜头全部主体参考图（角色/场景/道具）")

    print(f"[细化图片生成] 检查timeline_json: 长度={len(shot.timeline_json) if shot.timeline_json else 0}")
    if not shot.timeline_json or shot.timeline_json.strip() == "":
        print(f"[细化图片生成] 错误: timeline_json为空，请先生成Sora提示词")
        raise HTTPException(status_code=400, detail="请先生成Sora提示词")

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

        target_sub_shot_index = selected_sub_shot_index or 1
        if target_sub_shot_index < 1 or target_sub_shot_index > len(timeline):
            raise HTTPException(
                status_code=400,
                detail=f"镜头选择超出范围，可选1~{len(timeline)}",
            )

        target_item = timeline[target_sub_shot_index - 1]
        if isinstance(target_item, dict):
            selected_item = dict(target_item)
        else:
            selected_item = {
                "time": "",
                "visual": str(target_item or ""),
                "audio": "",
            }
        selected_sub_shot_text = str(request.selected_sub_shot_text or "").strip() if request else ""
        if selected_sub_shot_text:
            selected_item["visual"] = selected_sub_shot_text
        selected_item["__source_sub_shot_index"] = target_sub_shot_index
        timeline = [selected_item]
        print(
            f"[细化图片生成] 已按规则裁切为指定子镜头: index={target_sub_shot_index}, 当前数量: {len(timeline)}"
        )
    except json.JSONDecodeError as e:
        print(f"[细化图片生成] 错误: timeline JSON解析失败 - {str(e)}")
        print(f"[细化图片生成] timeline内容: {shot.timeline_json[:200]}...")
        raise HTTPException(status_code=400, detail="timeline数据格式错误")

    if not shot.stable_id:
        shot.stable_id = str(uuid.uuid4())
        db.commit()
        print(f"[细化图片生成] 为镜头生成stable_id: {shot.stable_id}")

    print(f"[细化图片生成] 使用当前镜头写入镜头图, shot_id={shot.id}, 镜号={shot.shot_number}, 变体={shot.variant_index}")

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

    reference_urls = _collect_storyboard_subject_reference_urls(shot, db)
    for ref_url in reference_urls:
        print(f"[细化图片生成] 添加主体参考图: {ref_url}")

    print(f"[细化图片生成] 参考图数量: {len(reference_urls)}")

    print(f"[细化图片生成] 开始创建或更新{len(timeline)}个子镜头记录...")
    for idx, item in enumerate(timeline, start=1):
        source_sub_shot_index = item.get("__source_sub_shot_index") if isinstance(item, dict) else None
        try:
            detail_sub_shot_index = int(source_sub_shot_index) if source_sub_shot_index is not None else int(idx)
        except Exception:
            detail_sub_shot_index = int(idx)
        detail_img = db.query(models.ShotDetailImage).filter(
            models.ShotDetailImage.shot_id == shot.id,
            models.ShotDetailImage.sub_shot_index == detail_sub_shot_index,
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

    from datetime import datetime as _datetime

    debug_dir = f"detail_images_shot_{shot.id}_{_datetime.now().strftime('%Y%m%d_%H%M%S')}"

    detail_images_debug_meta = _build_image_generation_debug_meta(
        detail_images_model,
        provider=detail_images_provider,
        has_reference_images=bool(reference_urls),
    )

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
        "detail_image_resolution": detail_image_resolution,
        "timeline": timeline,
        "reference_urls": reference_urls,
        "scene_description": scene_description,
    }
    _save_detail_images_debug(debug_dir, "input.json", debug_info, shot_id=shot.id)

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
            debug_dir,
        ),
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
        "model": detail_images_model,
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
    debug_dir,
):
    """后台任务：并发生成子镜头图片。"""
    print(f"\n[细化图片后台任务] ========== 开始处理镜头 {shot_id} ==========")

    normalized_image_cw = _normalize_storyboard2_image_cw(image_cw, default_value=50)
    db = SessionLocal()
    try:
        updated_count = db.query(models.ShotDetailImage).filter(
            models.ShotDetailImage.shot_id == shot_id,
            models.ShotDetailImage.status == "pending",
        ).update({"status": "processing"})
        db.commit()
        print(f"[细化图片后台任务] 已将{updated_count}个子镜头状态更新为processing")

        normalized_detail_model = _normalize_detail_images_model(
            detail_images_model,
            default_model="seedream-4.0",
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
                cw=normalized_image_cw,
            )

            input_info = {
                "sub_shot_index": detail_sub_shot_index,
                "stable_id": sub_stable_id,
                "provider": detail_provider,
                "requested_model": normalized_detail_model,
                "actual_model": detail_actual_model,
                "api_url": get_image_submit_api_url(
                    model_name=normalized_detail_model,
                    provider=detail_provider,
                    has_reference_images=bool(reference_urls),
                ),
                "status_api_url_template": get_image_status_api_url(
                    task_id="{task_id}",
                    model_name=normalized_detail_model,
                    provider=detail_provider,
                ),
                "scene_description": scene_text,
                "visual_text": visual,
                "prompt_text": prompt_text,
                "ratio": image_ratio,
                "resolution": detail_image_resolution,
                "reference_urls": reference_urls,
                "request_payload": request_payload,
            }
            _save_detail_images_debug(
                debug_dir,
                f"sub_shot_{detail_sub_shot_index}_input.json",
                input_info,
                shot_id=shot_id,
            )

            tasks_data.append((detail_sub_shot_index, sub_stable_id, prompt_text, visual, request_payload))

        print(f"[细化图片后台任务] 已准备{len(tasks_data)}个任务，开始并发执行（即梦在接口层统一限流）...")

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
                    f"[细化图片后台任务] 提交成功: 子镜头{detail_sub_shot_index} task_id={submit_result.get('task_id')}"
                )

                db_local = SessionLocal()
                try:
                    detail_img = db_local.query(models.ShotDetailImage).filter(
                        models.ShotDetailImage.shot_id == shot_id,
                        models.ShotDetailImage.sub_shot_index == detail_sub_shot_index,
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
                        f"[细化图片后台任务] 子镜头{detail_sub_shot_index}已交由后台轮询 task_id={detail_img.task_id}"
                    )

                finally:
                    db_local.close()

            except Exception as e:
                error_msg = str(e)
                print(f"[细化图片后台任务] 子镜头{detail_sub_shot_index}异常: {error_msg}")
                traceback.print_exc()

                error_data = {
                    "sub_shot_index": detail_sub_shot_index,
                    "stable_id": sub_stable_id,
                    "provider": detail_provider,
                    "requested_model": normalized_detail_model,
                    "actual_model": detail_actual_model,
                    "api_url": get_image_submit_api_url(
                        model_name=normalized_detail_model,
                        provider=detail_provider,
                        has_reference_images=bool(reference_urls),
                    ),
                    "status_api_url_template": get_image_status_api_url(
                        task_id="{task_id}",
                        model_name=normalized_detail_model,
                        provider=detail_provider,
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

                db_local = SessionLocal()
                try:
                    detail_img = db_local.query(models.ShotDetailImage).filter(
                        models.ShotDetailImage.shot_id == shot_id,
                        models.ShotDetailImage.sub_shot_index == detail_sub_shot_index,
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
                            has_reference_images=bool(reference_urls),
                        )
                        detail_img.status_api_url = ""
                        detail_img.query_error_count = 0
                        detail_img.last_query_error = ""
                        detail_img.last_query_at = None
                        detail_img.status = "completed" if has_existing_images else "failed"
                        db_local.commit()
                finally:
                    db_local.close()

        threads = []
        for detail_sub_shot_index, sub_stable_id, prompt_text, visual_text, request_payload in tasks_data:
            thread = Thread(
                target=process_single_sub_shot,
                args=(detail_sub_shot_index, sub_stable_id, prompt_text, visual_text, request_payload),
            )
            thread.daemon = True
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join()

        print(f"\n[细化图片后台任务] ========== 镜头{shot_id}所有子镜头处理完成 ==========")

    except Exception as e:
        print(f"[细化图片生成] 后台任务失败: {str(e)}")
        traceback.print_exc()

        db.query(models.ShotDetailImage).filter(
            models.ShotDetailImage.shot_id == shot_id,
            models.ShotDetailImage.status == "processing",
            or_(
                models.ShotDetailImage.task_id == "",
                models.ShotDetailImage.task_id.is_(None),
            ),
        ).update({
            "status": "failed",
            "error_message": str(e),
        })
        db.commit()
    finally:
        db.close()


async def get_shot_detail_images(
    shot_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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
                "error_message": img.error_message,
            }
            for img in detail_images
        ],
    }


async def set_shot_detail_image_cover(
    shot_id: int,
    request: SetDetailImageCoverRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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

    allowed_urls = _get_detail_image_allowed_urls(shot_id, db)
    if target_url not in allowed_urls:
        raise HTTPException(status_code=400, detail="该图片不属于当前镜头")

    shot.storyboard_image_path = target_url
    shot.storyboard_image_status = "completed"
    db.commit()

    return {
        "shot_id": shot.id,
        "cover_image_url": target_url,
        "message": "封面镜头图已更新",
    }
