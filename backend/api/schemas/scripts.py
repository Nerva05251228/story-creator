from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ScriptCreate(BaseModel):
    name: str
    video_prompt_template: Optional[str] = ""
    style_template: Optional[str] = ""


class ScriptUpdate(BaseModel):
    name: Optional[str] = None
    sora_prompt_style: Optional[str] = None
    video_prompt_template: Optional[str] = None
    style_template: Optional[str] = None
    narration_template: Optional[str] = None


class ScriptResponse(BaseModel):
    id: int
    user_id: int
    name: str
    sora_prompt_style: str = ""
    video_prompt_template: str = ""
    style_template: str = ""
    narration_template: str = ""
    created_at: datetime

    class Config:
        from_attributes = True


class CopyScriptRequest(BaseModel):
    user_ids: List[int]
