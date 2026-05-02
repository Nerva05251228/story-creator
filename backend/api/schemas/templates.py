from datetime import datetime
from typing import Optional

from pydantic import BaseModel


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
