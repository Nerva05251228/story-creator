from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ShotCreate(BaseModel):
    shot_number: int
    prompt_template: str = ""
    storyboard_video_prompt: str = ""
    storyboard_audio_prompt: str = ""
    storyboard_dialogue: str = ""
    sora_prompt: str = ""
    selected_card_ids: List[int] = []
    selected_sound_card_ids: Optional[List[int]] = None
    aspect_ratio: str = "16:9"
    duration: int = 15


class ShotUpdate(BaseModel):
    prompt_template: Optional[str] = None
    script_excerpt: Optional[str] = None
    storyboard_video_prompt: Optional[str] = None
    storyboard_audio_prompt: Optional[str] = None
    storyboard_dialogue: Optional[str] = None
    scene_override: Optional[str] = None
    scene_override_locked: Optional[bool] = None
    sora_prompt: Optional[str] = None
    sora_prompt_status: Optional[str] = None
    selected_card_ids: Optional[List[int]] = None
    selected_sound_card_ids: Optional[List[int]] = None
    aspect_ratio: Optional[str] = None
    duration: Optional[int] = None
    storyboard_video_model: Optional[str] = None
    storyboard_video_model_override_enabled: Optional[bool] = None
    duration_override_enabled: Optional[bool] = None
    provider: Optional[str] = None
    storyboard_image_path: Optional[str] = None
    storyboard_image_status: Optional[str] = None
    storyboard_image_model: Optional[str] = None
    first_frame_reference_image_url: Optional[str] = None
    uploaded_scene_image_url: Optional[str] = None
    use_uploaded_scene_image: Optional[bool] = None


class ManualSoraPromptRequest(BaseModel):
    sora_prompt: str


class ShotResponse(BaseModel):
    id: int
    episode_id: int
    shot_number: int
    variant_index: int
    prompt_template: str
    script_excerpt: str
    storyboard_video_prompt: str
    storyboard_audio_prompt: str
    storyboard_dialogue: str
    scene_override: str
    scene_override_locked: bool = False
    sora_prompt: Optional[str]
    sora_prompt_status: str
    selected_card_ids: str
    selected_sound_card_ids: Optional[str] = None
    video_path: str
    thumbnail_video_path: str
    video_status: str
    task_id: str
    managed_task_id: str = ""
    aspect_ratio: str
    duration: int
    storyboard_video_model: str = ""
    storyboard_video_model_override_enabled: bool = False
    duration_override_enabled: bool = False
    provider: str
    storyboard_image_path: str
    storyboard_image_status: str
    storyboard_image_task_id: str
    first_frame_reference_image_url: str = ""
    uploaded_scene_image_url: str = ""
    use_uploaded_scene_image: bool = False
    selected_scene_image_url: str = ""
    timeline_json: Optional[str] = ""
    detail_image_prompt_overrides: Optional[str] = "{}"
    detail_images_status: str
    detail_images_progress: Optional[str] = None
    detail_images_preview_path: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ShotVideoResponse(BaseModel):
    id: int
    shot_id: int
    video_path: str
    created_at: datetime

    class Config:
        from_attributes = True


class GenerateVideoRequest(BaseModel):
    appoint_account: Optional[str] = None


class ThumbnailUpdate(BaseModel):
    video_id: int


class VideoStatusInfoResponse(BaseModel):
    shot_id: int
    task_id: str
    status: str
    progress: int = 0
    info: str = ""
    error_message: str = ""


class GenerateSoraPromptRequest(BaseModel):
    reference_shot_id: Optional[int] = None


class GenerateLargeShotPromptRequest(BaseModel):
    template_id: Optional[int] = None


class GenerateStoryboardImageRequest(BaseModel):
    requirement: str
    style: str
    provider: Optional[str] = None
    model: str = "banana-pro"
    size: str = "9:16"
    resolution: str = "2K"


class GenerateDetailImagesRequest(BaseModel):
    provider: Optional[str] = None
    size: str = "9:16"
    resolution: str = "2K"
    model: Optional[str] = None
    selected_sub_shot_index: Optional[int] = None
    selected_sub_shot_text: Optional[str] = None
