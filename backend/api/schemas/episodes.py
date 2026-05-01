from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


DEFAULT_STORYBOARD_VIDEO_MODEL = "Seedance 2.0 Fast"


class EpisodeCreate(BaseModel):
    name: str
    content: str = ""
    batch_size: Optional[int] = 500
    shot_image_size: Optional[str] = "9:16"
    detail_images_model: Optional[str] = "seedream-4.0"
    detail_images_provider: Optional[str] = ""
    storyboard2_duration: Optional[int] = 15
    storyboard2_video_duration: Optional[int] = 6
    storyboard2_image_cw: Optional[int] = 50
    storyboard2_include_scene_references: Optional[bool] = False
    storyboard_video_model: Optional[str] = DEFAULT_STORYBOARD_VIDEO_MODEL
    storyboard_video_aspect_ratio: Optional[str] = "16:9"
    storyboard_video_duration: Optional[int] = 15
    storyboard_video_resolution_name: Optional[str] = "720p"
    storyboard_video_appoint_account: Optional[str] = ""
    video_style_template_id: Optional[int] = None
    video_prompt_template: Optional[str] = ""


class EpisodeResponse(BaseModel):
    id: int
    script_id: int
    name: str
    content: str
    shot_image_size: str = "9:16"
    detail_images_model: str = "seedream-4.0"
    detail_images_provider: str = ""
    storyboard2_video_duration: int = 6
    storyboard2_image_cw: int = 50
    storyboard2_include_scene_references: bool = False
    storyboard_video_model: str = DEFAULT_STORYBOARD_VIDEO_MODEL
    storyboard_video_aspect_ratio: str = "16:9"
    storyboard_video_duration: int = 15
    storyboard_video_resolution_name: str = "720p"
    storyboard_video_appoint_account: str = ""
    video_style_template_id: Optional[int] = None
    video_prompt_template: str = ""
    batch_generating_prompts: bool = False
    batch_generating_storyboard2_prompts: bool = False
    narration_converting: bool = False
    narration_error: str = ""
    opening_content: str = ""
    opening_generating: bool = False
    opening_error: str = ""
    library_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class StoryboardAnalyzeResponse(BaseModel):
    message: str
    generating: bool


class CreateStoryboardRequest(BaseModel):
    shots: List[dict]


class SimpleStoryboardRequest(BaseModel):
    content: Optional[str] = None
    batch_size: Optional[int] = 500


class AnalyzeStoryboardRequest(BaseModel):
    shots: List[dict]


class BatchGenerateSoraPromptsRequest(BaseModel):
    default_template: str = "2d漫画风格（细）"
    shot_ids: Optional[List[int]] = None
    duration: Optional[int] = None


class BatchGenerateSoraVideosRequest(BaseModel):
    aspect_ratio: Optional[str] = None
    duration: Optional[int] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    appoint_account: Optional[str] = None
    shot_ids: Optional[List[int]] = None


class StartManagedGenerationRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    aspect_ratio: Optional[str] = None
    duration: Optional[int] = None
    shot_ids: Optional[List[int]] = None
    variant_count: int = 1


class ManagedTaskResponse(BaseModel):
    id: int
    session_id: int
    shot_id: int
    shot_stable_id: str
    shot_number: int
    variant_index: int
    video_path: str
    status: str
    error_message: str
    task_id: str
    prompt_text: str = ""
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class ManagedSessionStatusResponse(BaseModel):
    session_id: Optional[int]
    status: str
    total_shots: int
    completed_shots: int
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class Storyboard2BatchGenerateSoraPromptsRequest(BaseModel):
    default_template: str = "2d漫画风格（细）"
    shot_ids: Optional[List[int]] = None
