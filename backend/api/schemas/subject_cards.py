from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


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
