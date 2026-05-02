from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel


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
