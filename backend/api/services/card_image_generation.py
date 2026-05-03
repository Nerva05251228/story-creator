import asyncio
import json
import uuid
from datetime import datetime
from typing import Any, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

import image_platform_client
import models
from ai_service import get_prompt_by_key
from api.schemas.card_media import ImageGenerationRequest
from api.services import billing_charges
from dashboard_service import log_debug_task_event
from image_generation_service import (
    get_image_status_api_url,
    get_image_submit_api_url,
    normalize_image_model_key,
    submit_image_generation,
)


CHARACTER_THREE_VIEW_PROMPT_KEY = "character_three_view_image_prompt"
CHARACTER_THREE_VIEW_PROMPT_DEFAULT = (
    "生人物三视图，生成全身三视图以及一张面部特写(最左边占满三分之一的位置是超大的面部特写，"
    "右边三分之二放正视图、侧视图、后视图，（正视图、侧视图、后视图并排）纯白背景"
)

_IMAGE_MODEL_CONFIG = {
    "seedream-4.0": {"actual_model": "seedream-4.0", "provider": "jimeng"},
    "seedream-4.1": {"actual_model": "seedream-4.1", "provider": "jimeng"},
    "seedream-4.5": {"actual_model": "seedream-4.5", "provider": "jimeng"},
    "seedream-4.6": {"actual_model": "seedream-4.6", "provider": "jimeng"},
    "nano-banana-2": {"actual_model": "nano-banana-2", "provider": "momo"},
    "nano-banana-pro": {"actual_model": "nano-banana-pro", "provider": "momo"},
    "gpt-image-2": {"actual_model": "gpt-image-2", "provider": "momo"},
}


def _card_type_matches(card_type: str, *values: str) -> bool:
    normalized = str(card_type or "").strip()
    return any(value and value in normalized for value in values)


def save_ai_debug(
    stage: str,
    input_data: dict,
    output_data: Optional[dict] = None,
    raw_response: Optional[dict] = None,
    episode_id: Optional[int] = None,
    shot_id: Optional[int] = None,
    batch_id: Optional[str] = None,
    task_folder: Optional[str] = None,
    attempt: Optional[int] = None,
):
    try:
        if not episode_id and not shot_id:
            return None

        if not task_folder:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            task_folder = f"episode_{episode_id}_{timestamp}" if episode_id else f"shot_{shot_id}_{timestamp}"

        file_prefix = stage
        if batch_id:
            file_prefix += f"_batch{batch_id}"
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
        except Exception as exc:
            print(f"[dashboard] save_ai_debug sync failed: {str(exc)}")
        return task_folder
    except Exception as exc:
        print(f"[dashboard] save_ai_debug failed: {str(exc)}")
        return None


def _get_optional_prompt_config_content(key: str, fallback: str = "") -> str:
    try:
        content_text = str(get_prompt_by_key(key) or "").strip()
        if content_text:
            return content_text
    except Exception:
        pass
    return str(fallback or "").strip()


def _build_card_image_prompt(
    card: models.SubjectCard,
    style_template: str,
    generation_mode: str,
) -> str:
    normalized_mode = str(generation_mode or "default").strip().lower()

    if normalized_mode == "three_view":
        if not _card_type_matches(card.card_type, "角色", "瑙掕壊", "role"):
            raise HTTPException(status_code=400, detail="只有角色卡片支持生成三视图")
        return _get_optional_prompt_config_content(
            CHARACTER_THREE_VIEW_PROMPT_KEY,
            CHARACTER_THREE_VIEW_PROMPT_DEFAULT,
        )

    if _card_type_matches(card.card_type, "角色", "瑙掕壊", "role"):
        final_prompt = "生成一张角色站立的图片，全身，正面角度，纯白色背景,带简单阴影。\n"
        if style_template:
            final_prompt += f"生成图片的风格是：{style_template}\n"
        final_prompt += card.ai_prompt
        return final_prompt

    final_prompt = ""
    if style_template:
        final_prompt += f"生成图片的风格是：{style_template}\n"
    if _card_type_matches(card.card_type, "场景", "鍦烘櫙", "scene"):
        final_prompt += f"生成图片中场景的是：{card.ai_prompt}"
    else:
        final_prompt += card.ai_prompt
    return final_prompt


def _resolve_style_template_content_for_card_type(
    style_template_obj: Optional[models.StyleTemplate],
    card_type: str,
) -> str:
    if not style_template_obj:
        return ""

    normalized_card_type = str(card_type or "").strip()
    if _card_type_matches(normalized_card_type, "场景", "鍦烘櫙", "scene"):
        return str(
            getattr(style_template_obj, "scene_content", None)
            or getattr(style_template_obj, "content", "")
            or ""
        ).strip()
    if _card_type_matches(normalized_card_type, "道具", "閬撳叿", "prop"):
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
            models.GeneratedImage.status == "completed",
        ).first()
        if not reference_image or not str(reference_image.image_path or "").strip():
            raise HTTPException(status_code=400, detail="请先选择一张主体素材图，再生成三视图")
        return [reference_image.image_path]

    reference_urls: List[str] = []
    for image_id in selected_ids:
        ref_img = db.query(models.GeneratedImage).filter(
            models.GeneratedImage.id == image_id,
            models.GeneratedImage.is_reference == True,
        ).first()
        if ref_img and ref_img.status == "completed":
            reference_urls.append(ref_img.image_path)
    return reference_urls


def _build_image_generation_debug_meta(
    model_key: Optional[str],
    provider: Optional[str] = None,
    actual_model: Optional[str] = None,
    has_reference_images: bool = False,
) -> dict:
    normalized_model = normalize_image_model_key(model_key or "seedream-4.0")
    try:
        route = image_platform_client.resolve_image_route(normalized_model, provider=provider)
    except Exception:
        route = {}
    fallback = _IMAGE_MODEL_CONFIG.get(normalized_model) or {}
    resolved_provider = str(provider or route.get("provider") or fallback.get("provider") or "").strip().lower()
    resolved_actual_model = str(actual_model or route.get("model") or fallback.get("actual_model") or normalized_model).strip()
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
) -> dict:
    normalized_reference_images = [
        str(url or "").strip()
        for url in (reference_images or [])
        if str(url or "").strip()
    ]
    normalized_provider = str(provider or "").strip().lower()
    payload = {
        "model": actual_model,
        "prompt": prompt_text,
        "username": "story_creator",
        "provider": normalized_provider,
        "action": "image2image" if normalized_reference_images else "text2image",
        "ratio": ratio,
        "reference_images": normalized_reference_images,
        "extra": {"n": 1, "name": name, "cw": 50},
    }
    if resolution and normalized_provider != "jimeng":
        payload["resolution"] = resolution
    return payload


_record_card_image_charge = billing_charges.record_card_image_charge


async def submit_card_image_generation(
    db: Session,
    *,
    card: models.SubjectCard,
    request: ImageGenerationRequest,
) -> dict:
    card_id = int(card.id)
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
            "requested_at": datetime.utcnow().isoformat(),
        },
        output_data={"status": "request_received"},
        shot_id=card_id,
    )

    style_template = ""
    style_source = "无"
    if card.style_template_id:
        style_template_obj = db.query(models.StyleTemplate).filter(
            models.StyleTemplate.id == card.style_template_id,
        ).first()
        if style_template_obj:
            style_template = _resolve_style_template_content_for_card_type(style_template_obj, card.card_type)
            style_source = f"卡片风格模板 (ID: {card.style_template_id}, 名称: {style_template_obj.name})"

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
        has_reference_images=bool(reference_urls),
    )

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
                "reference_image_ids": request.reference_image_ids or [],
            },
            "request_payload": request_payload,
            "reference_urls": reference_urls,
            "final_prompt": final_prompt,
        }
        loop = asyncio.get_event_loop()
        task_id = await loop.run_in_executor(
            None,
            lambda: submit_image_generation(
                final_prompt,
                requested_model,
                request.size,
                request.resolution,
                request.n,
                reference_urls if reference_urls else None,
                request.provider,
            ),
        )

        new_generated_image = models.GeneratedImage(
            card_id=card_id,
            image_path="",
            model_name=requested_model,
            is_reference=False,
            task_id=task_id,
            status="processing",
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

        card.is_generating_images = True
        card.generating_count = int(getattr(card, "generating_count", 0) or 0) + int(request.n or 1)

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
                    provider=image_debug_meta["provider"],
                ),
            },
            shot_id=card_id,
            task_folder=image_debug_folder,
        )

        return {
            "message": "图片生成任务已提交",
            "generated_image_id": new_generated_image.id,
            "task_id": task_id,
        }
    except Exception as exc:
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
                    "reference_image_ids": request.reference_image_ids or [],
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
                "final_prompt": final_prompt,
            },
            {
                "error": str(exc),
                "provider": image_debug_meta["provider"],
                "actual_model": image_debug_meta["actual_model"],
                "api_url": image_debug_meta["submit_api_url"],
            },
            shot_id=card_id,
            task_folder=image_debug_folder,
        )
        raise HTTPException(status_code=500, detail=f"提交任务失败: {str(exc)}")
