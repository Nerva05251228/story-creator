import json
import sys
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
from api.schemas.settings import (
    PromptConfigResponse,
    PromptConfigUpdate,
    ShotDurationTemplateResponse,
)
from database import get_db
from simple_storyboard_rules import get_default_rule_config, normalize_rule_config


router = APIRouter()

DEFAULT_SORA_RULE = "准则：不要出现字幕"
GROK_RULE_DEFAULT = "严格按照提示词生视频，不要出现其他人物"
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


def _default_storyboard_video_prompt_template() -> str:
    return (
        "视频风格:逐帧动画，2d手绘动漫风格，强调帧间的手绘/精细绘制属性，而非3D渲染/CG动画的光滑感。"
        "画面整体呈现传统2D动画的逐帧绘制特征，包括但不限于：帧间微妙的线条变化、色彩的手工涂抹感、阴影的平面化处理。"
        "角色动作流畅但保留手绘的自然波动，背景元素展现水彩或厚涂等传统绘画技法的质感。"
        "整体视觉效果追求温暖、有机的手工艺术感，避免数字化的过度精确与机械感。"
    )


def _default_narration_conversion_template() -> str:
    return """1 读取文本文件并理解
 2 把故事改写成解说故事的形式，改写过程如下：
    （1）找到故事的第一个主角
    （2）把故事用主角自述的方式讲出来，以第一人称视角讲述
    （3）保留少量精彩的对话即可
    （4）保留一些场景描述
    （5）文字风格要幽默"""


def _default_opening_generation_template() -> str:
    return "我想把这个片段做成一个短视频，需要一个精彩吸引人的开头，请你帮我写一个开头"


def _upsert_global_setting(db: Session, key: str, value: str) -> models.GlobalSettings:
    setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == key).first()
    if setting:
        setting.value = value
        setting.updated_at = datetime.utcnow()
    else:
        setting = models.GlobalSettings(key=key, value=value)
        db.add(setting)
    return setting


def _get_global_setting_value(db: Session, key: str, default_value: str, require_non_blank: bool = False) -> str:
    try:
        setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == key).first()
        if setting and (not require_non_blank or str(setting.value or "").strip()):
            return setting.value
    except Exception:
        return default_value
    return default_value


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


def _get_default_prompt_content_map() -> dict:
    main_module = sys.modules.get("main")
    default_prompts = getattr(main_module, "DEFAULT_PROMPTS", None)
    if default_prompts is None:
        import main as main_module  # noqa: PLC0415
        default_prompts = getattr(main_module, "DEFAULT_PROMPTS", [])
    return {prompt["key"]: prompt["content"] for prompt in default_prompts}


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


@router.get("/api/video-generation-rules")
async def get_video_generation_rules(db: Session = Depends(get_db)):
    try:
        sora_setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "sora_rule").first()
        grok_setting = db.query(models.GlobalSettings).filter(models.GlobalSettings.key == "grok_rule").first()
        return {
            "sora_rule": sora_setting.value if sora_setting else DEFAULT_SORA_RULE,
            "grok_rule": grok_setting.value if grok_setting else GROK_RULE_DEFAULT,
        }
    except Exception:
        return {
            "sora_rule": DEFAULT_SORA_RULE,
            "grok_rule": GROK_RULE_DEFAULT,
        }


@router.put("/api/video-generation-rules")
async def update_video_generation_rules(request: dict, db: Session = Depends(get_db)):
    sora_rule = request.get("sora_rule", DEFAULT_SORA_RULE)
    grok_rule = request.get("grok_rule", GROK_RULE_DEFAULT)
    try:
        _upsert_global_setting(db, "sora_rule", sora_rule)
        _upsert_global_setting(db, "grok_rule", grok_rule)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新失败: {str(exc)}")
    return {
        "message": "视频生成准则更新成功",
        "sora_rule": sora_rule,
        "grok_rule": grok_rule,
    }


@router.get("/api/sora-rule")
async def get_sora_rule(db: Session = Depends(get_db)):
    return {"sora_rule": _get_global_setting_value(db, "sora_rule", DEFAULT_SORA_RULE)}


@router.put("/api/sora-rule")
async def update_sora_rule(request: dict, db: Session = Depends(get_db)):
    sora_rule = request.get("sora_rule", DEFAULT_SORA_RULE)
    try:
        setting = _upsert_global_setting(db, "sora_rule", sora_rule)
        db.commit()
        db.refresh(setting)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新失败: {str(exc)}")
    return {"message": "Sora准则更新成功", "sora_rule": sora_rule}


@router.get("/api/users/{user_id}/sora-rule")
async def get_sora_rule_legacy(user_id: int, db: Session = Depends(get_db)):
    _ = user_id
    return await get_sora_rule(db)


@router.put("/api/users/{user_id}/sora-rule")
async def update_sora_rule_legacy(user_id: int, request: dict, db: Session = Depends(get_db)):
    _ = user_id
    return await update_sora_rule(request, db)


@router.get("/api/global-settings/prompt_template")
async def get_prompt_template(db: Session = Depends(get_db)):
    return {
        "value": _get_global_setting_value(
            db,
            "prompt_template",
            _default_storyboard_video_prompt_template(),
        )
    }


@router.put("/api/global-settings/prompt_template")
async def update_prompt_template(request: dict, db: Session = Depends(get_db)):
    value = request.get("value", "")
    try:
        setting = _upsert_global_setting(db, "prompt_template", value)
        db.commit()
        db.refresh(setting)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新失败: {str(exc)}")
    return {"message": "提示词模板更新成功", "value": value}


@router.get("/api/global-settings/narration_conversion_template")
async def get_narration_conversion_template(db: Session = Depends(get_db)):
    return {
        "value": _get_global_setting_value(
            db,
            "narration_conversion_template",
            _default_narration_conversion_template(),
        )
    }


@router.put("/api/global-settings/narration_conversion_template")
async def update_narration_conversion_template(request: dict, db: Session = Depends(get_db)):
    value = request.get("value", "")
    try:
        setting = _upsert_global_setting(db, "narration_conversion_template", value)
        db.commit()
        db.refresh(setting)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新失败: {str(exc)}")
    return {"message": "文本转解说剧提示词模板更新成功", "value": value}


@router.get("/api/global-settings/opening_generation_template")
async def get_opening_generation_template(db: Session = Depends(get_db)):
    return {
        "value": _get_global_setting_value(
            db,
            "opening_generation_template",
            _default_opening_generation_template(),
            require_non_blank=True,
        )
    }


@router.put("/api/global-settings/opening_generation_template")
async def update_opening_generation_template(request: dict, db: Session = Depends(get_db)):
    value = request.get("value", "")
    try:
        setting = _upsert_global_setting(db, "opening_generation_template", value)
        db.commit()
        db.refresh(setting)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新失败: {str(exc)}")
    return {"message": "精彩开头生成提示词模板更新成功", "value": value}


@router.get("/api/prompt-configs", response_model=List[PromptConfigResponse])
async def get_prompt_configs(db: Session = Depends(get_db)):
    configs = db.query(models.PromptConfig).all()
    configs = sorted(configs, key=_prompt_config_sort_key)
    return [_serialize_prompt_config(config) for config in configs]


@router.get("/api/prompt-configs/{config_id}", response_model=PromptConfigResponse)
async def get_prompt_config(config_id: int, db: Session = Depends(get_db)):
    config = db.query(models.PromptConfig).filter(models.PromptConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    return _serialize_prompt_config(config)


@router.put("/api/prompt-configs/{config_id}", response_model=PromptConfigResponse)
async def update_prompt_config(
    config_id: int,
    update_data: PromptConfigUpdate,
    db: Session = Depends(get_db)
):
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


@router.post("/api/prompt-configs/{config_id}/reset")
async def reset_prompt_config(config_id: int, db: Session = Depends(get_db)):
    config = db.query(models.PromptConfig).filter(models.PromptConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    default_prompts_map = _get_default_prompt_content_map()
    if config.key not in default_prompts_map:
        raise HTTPException(status_code=400, detail="无法重置：未找到默认配置")

    config.content = default_prompts_map[config.key]
    config.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(config)
    return {"message": "已重置为默认值", "config": _serialize_prompt_config(config)}


@router.get("/api/shot-duration-templates", response_model=list[ShotDurationTemplateResponse])
async def get_shot_duration_templates(db: Session = Depends(get_db)):
    templates = db.query(models.ShotDurationTemplate).order_by(
        models.ShotDurationTemplate.duration.asc()
    ).all()
    return [_serialize_shot_duration_template(t) for t in templates]


@router.get("/api/shot-duration-templates/{duration}", response_model=ShotDurationTemplateResponse)
async def get_shot_duration_template(duration: int, db: Session = Depends(get_db)):
    template = db.query(models.ShotDurationTemplate).filter(
        models.ShotDurationTemplate.duration == duration
    ).first()
    if not template:
        raise HTTPException(status_code=404, detail="该时长的模板不存在")
    return _serialize_shot_duration_template(template)


@router.put("/api/shot-duration-templates/{duration}", response_model=ShotDurationTemplateResponse)
async def update_shot_duration_template(
    duration: int,
    update_data: dict,
    db: Session = Depends(get_db)
):
    template = db.query(models.ShotDurationTemplate).filter(
        models.ShotDurationTemplate.duration == duration
    ).first()
    if not template:
        raise HTTPException(status_code=404, detail="该时长的模板不存在")

    if "shot_count_min" in update_data:
        template.shot_count_min = update_data["shot_count_min"]
    if "shot_count_max" in update_data:
        template.shot_count_max = update_data["shot_count_max"]
    if "simple_storyboard_config" in update_data:
        template.simple_storyboard_config_json = json.dumps(
            _normalize_simple_storyboard_config_payload(
                update_data["simple_storyboard_config"],
                duration,
            ),
            ensure_ascii=False,
        )
    if "video_prompt_rule" in update_data:
        template.video_prompt_rule = update_data["video_prompt_rule"]
    if "large_shot_prompt_rule" in update_data:
        template.large_shot_prompt_rule = update_data["large_shot_prompt_rule"]

    db.commit()
    db.refresh(template)
    return _serialize_shot_duration_template(template)
