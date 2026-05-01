from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class HitDramaCreate(BaseModel):
    drama_name: str
    view_count: str = ""
    opening_15_sentences: str = ""
    first_episode_script: str = ""
    online_time: str = ""


class HitDramaUpdate(BaseModel):
    drama_name: Optional[str] = None
    view_count: Optional[str] = None
    opening_15_sentences: Optional[str] = None
    first_episode_script: Optional[str] = None
    online_time: Optional[str] = None


class HitDramaResponse(BaseModel):
    id: int
    drama_name: str
    view_count: str
    opening_15_sentences: str
    first_episode_script: str
    online_time: str
    video_filename: Optional[str]
    created_by: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class HitDramaHistoryResponse(BaseModel):
    id: int
    drama_id: int
    action_type: str
    field_name: Optional[str]
    old_value: Optional[str]
    new_value: Optional[str]
    edited_by: str
    edited_at: datetime
    drama_name: Optional[str] = None

    class Config:
        from_attributes = True
