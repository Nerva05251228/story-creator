from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
from api.schemas.templates import (
    LargeShotTemplateCreate,
    LargeShotTemplateResponse,
    StoryboardRequirementTemplateCreate,
    StoryboardRequirementTemplateResponse,
    StoryboardStyleTemplateCreate,
    StoryboardStyleTemplateResponse,
    StyleTemplateCreate,
    StyleTemplateResponse,
    TemplateCreate,
    TemplateResponse,
    VideoStyleTemplateCreate,
    VideoStyleTemplateResponse,
)
from api.services.style_templates import (
    _build_prop_style_template_content,
    _build_scene_style_template_content,
)
from database import get_db


router = APIRouter()

STYLE_TEMPLATE_MIGRATION_PLACEHOLDER_PREFIX = "[迁移占位]已删除风格模板"


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


@router.get("/api/templates", response_model=List[TemplateResponse])
async def get_templates(db: Session = Depends(get_db)):
    templates = db.query(models.PromptTemplate).order_by(
        models.PromptTemplate.is_default.desc(),
        models.PromptTemplate.created_at.desc()
    ).all()
    return templates


@router.post("/api/templates", response_model=TemplateResponse)
async def create_template(
    template: TemplateCreate,
    db: Session = Depends(get_db)
):
    new_template = models.PromptTemplate(
        name=template.name,
        content=template.content,
        is_default=False
    )
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return new_template


@router.get("/api/style-templates", response_model=List[StyleTemplateResponse])
async def get_style_templates(db: Session = Depends(get_db)):
    templates = db.query(models.StyleTemplate).filter(
        _visible_style_template_name_filter()
    ).order_by(
        models.StyleTemplate.created_at.desc()
    ).all()
    return [_serialize_style_template(template) for template in templates]


@router.post("/api/style-templates", response_model=StyleTemplateResponse)
async def create_style_template(
    template: StyleTemplateCreate,
    db: Session = Depends(get_db)
):
    normalized_payload = _normalize_style_template_payload(template)
    new_template = models.StyleTemplate(**normalized_payload)
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return _serialize_style_template(new_template)


@router.put("/api/style-templates/{template_id}", response_model=StyleTemplateResponse)
async def update_style_template(
    template_id: int,
    template: StyleTemplateCreate,
    db: Session = Depends(get_db)
):
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


@router.delete("/api/style-templates/{template_id}")
async def delete_style_template(
    template_id: int,
    db: Session = Depends(get_db)
):
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


@router.post("/api/style-templates/{template_id}/set-default")
async def set_default_template(
    template_id: int,
    db: Session = Depends(get_db)
):
    db_template = db.query(models.StyleTemplate).filter(
        models.StyleTemplate.id == template_id
    ).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    db.query(models.StyleTemplate).update({"is_default": False})
    db_template.is_default = True
    db.commit()
    db.refresh(db_template)

    return {"message": "已设置为默认模板", "template_id": template_id}


@router.get("/api/style-templates/default", response_model=StyleTemplateResponse)
async def get_default_template(db: Session = Depends(get_db)):
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


@router.get("/api/video-style-templates", response_model=List[VideoStyleTemplateResponse])
async def get_video_style_templates(db: Session = Depends(get_db)):
    templates = db.query(models.VideoStyleTemplate).order_by(
        models.VideoStyleTemplate.is_default.desc(),
        models.VideoStyleTemplate.created_at.desc()
    ).all()
    return templates


@router.post("/api/video-style-templates", response_model=VideoStyleTemplateResponse)
async def create_video_style_template(
    template: VideoStyleTemplateCreate,
    db: Session = Depends(get_db)
):
    new_template = models.VideoStyleTemplate(
        name=template.name,
        sora_rule=template.sora_rule,
        style_prompt=template.style_prompt
    )
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return new_template


@router.put("/api/video-style-templates/{template_id}", response_model=VideoStyleTemplateResponse)
async def update_video_style_template(
    template_id: int,
    template: VideoStyleTemplateCreate,
    db: Session = Depends(get_db)
):
    db_template = db.query(models.VideoStyleTemplate).filter(
        models.VideoStyleTemplate.id == template_id
    ).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")
    db_template.name = template.name
    db_template.sora_rule = template.sora_rule
    db_template.style_prompt = template.style_prompt
    db.commit()
    db.refresh(db_template)
    return db_template


@router.delete("/api/video-style-templates/{template_id}")
async def delete_video_style_template(template_id: int, db: Session = Depends(get_db)):
    db_template = db.query(models.VideoStyleTemplate).filter(
        models.VideoStyleTemplate.id == template_id
    ).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")
    db.delete(db_template)
    db.commit()
    return {"message": "模板已删除"}


@router.post("/api/video-style-templates/{template_id}/set-default")
async def set_default_video_style_template(template_id: int, db: Session = Depends(get_db)):
    db_template = db.query(models.VideoStyleTemplate).filter(
        models.VideoStyleTemplate.id == template_id
    ).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")
    db.query(models.VideoStyleTemplate).update({"is_default": False})
    db_template.is_default = True
    db.commit()
    return {"message": "已设置为默认模板", "template_id": template_id}


@router.get("/api/large-shot-templates", response_model=List[LargeShotTemplateResponse])
async def get_large_shot_templates(db: Session = Depends(get_db)):
    templates = db.query(models.LargeShotTemplate).order_by(
        models.LargeShotTemplate.is_default.desc(),
        models.LargeShotTemplate.created_at.asc(),
        models.LargeShotTemplate.id.asc(),
    ).all()
    return templates


@router.post("/api/large-shot-templates", response_model=LargeShotTemplateResponse)
async def create_large_shot_template(
    template: LargeShotTemplateCreate,
    db: Session = Depends(get_db)
):
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


@router.put("/api/large-shot-templates/{template_id}", response_model=LargeShotTemplateResponse)
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


@router.delete("/api/large-shot-templates/{template_id}")
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


@router.post("/api/large-shot-templates/{template_id}/set-default")
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


@router.get(
    "/api/storyboard-templates/requirements",
    response_model=List[StoryboardRequirementTemplateResponse],
)
async def get_storyboard_requirement_templates(db: Session = Depends(get_db)):
    templates = db.query(models.StoryboardRequirementTemplate).order_by(
        models.StoryboardRequirementTemplate.created_at.desc()
    ).all()
    return templates


@router.post(
    "/api/storyboard-templates/requirements",
    response_model=StoryboardRequirementTemplateResponse,
)
async def create_storyboard_requirement_template(
    template: StoryboardRequirementTemplateCreate,
    db: Session = Depends(get_db)
):
    new_template = models.StoryboardRequirementTemplate(
        name=template.name,
        content=template.content
    )
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return new_template


@router.put(
    "/api/storyboard-templates/requirements/{template_id}",
    response_model=StoryboardRequirementTemplateResponse,
)
async def update_storyboard_requirement_template(
    template_id: int,
    template: StoryboardRequirementTemplateCreate,
    db: Session = Depends(get_db)
):
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


@router.delete("/api/storyboard-templates/requirements/{template_id}")
async def delete_storyboard_requirement_template(
    template_id: int,
    db: Session = Depends(get_db)
):
    db_template = db.query(models.StoryboardRequirementTemplate).filter(
        models.StoryboardRequirementTemplate.id == template_id
    ).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    db.delete(db_template)
    db.commit()
    return {"message": "模板已删除"}


@router.post("/api/storyboard-templates/requirements/{template_id}/set-default")
async def set_default_requirement_template(
    template_id: int,
    db: Session = Depends(get_db)
):
    db_template = db.query(models.StoryboardRequirementTemplate).filter(
        models.StoryboardRequirementTemplate.id == template_id
    ).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    db.query(models.StoryboardRequirementTemplate).update({"is_default": False})
    db_template.is_default = True
    db.commit()
    db.refresh(db_template)

    return {"message": "已设置为默认模板", "template_id": template_id}


@router.get(
    "/api/storyboard-templates/styles",
    response_model=List[StoryboardStyleTemplateResponse],
)
async def get_storyboard_style_templates(db: Session = Depends(get_db)):
    templates = db.query(models.StoryboardStyleTemplate).order_by(
        models.StoryboardStyleTemplate.created_at.desc()
    ).all()
    return templates


@router.post(
    "/api/storyboard-templates/styles",
    response_model=StoryboardStyleTemplateResponse,
)
async def create_storyboard_style_template(
    template: StoryboardStyleTemplateCreate,
    db: Session = Depends(get_db)
):
    new_template = models.StoryboardStyleTemplate(
        name=template.name,
        content=template.content
    )
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return new_template


@router.put(
    "/api/storyboard-templates/styles/{template_id}",
    response_model=StoryboardStyleTemplateResponse,
)
async def update_storyboard_style_template(
    template_id: int,
    template: StoryboardStyleTemplateCreate,
    db: Session = Depends(get_db)
):
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


@router.delete("/api/storyboard-templates/styles/{template_id}")
async def delete_storyboard_style_template(
    template_id: int,
    db: Session = Depends(get_db)
):
    db_template = db.query(models.StoryboardStyleTemplate).filter(
        models.StoryboardStyleTemplate.id == template_id
    ).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    db.delete(db_template)
    db.commit()
    return {"message": "模板已删除"}


@router.post("/api/storyboard-templates/styles/{template_id}/set-default")
async def set_default_style_template(
    template_id: int,
    db: Session = Depends(get_db)
):
    db_template = db.query(models.StoryboardStyleTemplate).filter(
        models.StoryboardStyleTemplate.id == template_id
    ).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="模板不存在")

    db.query(models.StoryboardStyleTemplate).update({"is_default": False})
    db_template.is_default = True
    db.commit()
    db.refresh(db_template)

    return {"message": "已设置为默认模板", "template_id": template_id}
