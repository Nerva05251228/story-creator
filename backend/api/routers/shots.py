import importlib
import inspect
import sys
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from sqlalchemy.orm import Session

import models
from api.schemas.shots import (
    GenerateDetailImagesRequest,
    GenerateLargeShotPromptRequest,
    GenerateSoraPromptRequest,
    GenerateStoryboardImageRequest,
    GenerateVideoRequest,
    ManualSoraPromptRequest,
    ShotCreate,
    ShotResponse,
    ShotUpdate,
    ShotVideoResponse,
    SetDetailImageCoverRequest,
    SetFirstFrameReferenceRequest,
    SetShotSceneImageSelectionRequest,
    ThumbnailUpdate,
    VideoStatusInfoResponse,
)
from api.services import shot_image_generation
from api.services import shot_reference_workflow
from auth import get_current_user
from database import get_db


router = APIRouter()


def _legacy_main():
    return sys.modules.get("main") or importlib.import_module("main")


async def _call_legacy(function_name: str, **kwargs):
    result = getattr(_legacy_main(), function_name)(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


@router.post("/api/episodes/{episode_id}/shots", response_model=ShotResponse)
async def create_shot(
    episode_id: int,
    shot: ShotCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "create_shot",
        episode_id=episode_id,
        shot=shot,
        user=user,
        db=db,
    )


@router.get("/api/episodes/{episode_id}/shots", response_model=List[ShotResponse])
async def get_episode_shots(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "get_episode_shots",
        episode_id=episode_id,
        user=user,
        db=db,
    )


@router.get("/api/shots/{shot_id}/video-status-info", response_model=VideoStatusInfoResponse)
async def get_shot_video_status_info(
    shot_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "get_shot_video_status_info",
        shot_id=shot_id,
        user=user,
        db=db,
    )


@router.put("/api/shots/{shot_id}", response_model=ShotResponse)
async def update_shot(
    shot_id: int,
    shot_data: ShotUpdate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "update_shot",
        shot_id=shot_id,
        shot_data=shot_data,
        user=user,
        db=db,
    )


@router.get("/api/shots/{shot_id}/extract-scene")
async def extract_scene_from_cards(
    shot_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "extract_scene_from_cards",
        shot_id=shot_id,
        user=user,
        db=db,
    )


@router.delete("/api/shots/{shot_id}")
async def delete_shot(
    shot_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "delete_shot",
        shot_id=shot_id,
        user=user,
        db=db,
    )


@router.post("/api/shots/{shot_id}/duplicate", response_model=ShotResponse)
async def duplicate_shot(
    shot_id: int,
    request: dict = {},
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "duplicate_shot",
        shot_id=shot_id,
        request=request,
        user=user,
        db=db,
    )


@router.post("/api/shots/{shot_id}/generate-sora-prompt")
async def generate_sora_prompt(
    shot_id: int,
    background_tasks: BackgroundTasks,
    request: Optional[GenerateSoraPromptRequest] = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "generate_sora_prompt",
        shot_id=shot_id,
        background_tasks=background_tasks,
        request=request,
        user=user,
        db=db,
    )


@router.post("/api/shots/{shot_id}/generate-large-shot-prompt")
async def generate_large_shot_prompt(
    shot_id: int,
    background_tasks: BackgroundTasks,
    request: Optional[GenerateLargeShotPromptRequest] = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "generate_large_shot_prompt",
        shot_id=shot_id,
        background_tasks=background_tasks,
        request=request,
        user=user,
        db=db,
    )


@router.post("/api/shots/{shot_id}/manual-sora-prompt", response_model=ShotResponse)
async def manual_set_sora_prompt(
    shot_id: int,
    request: ManualSoraPromptRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "manual_set_sora_prompt",
        shot_id=shot_id,
        request=request,
        user=user,
        db=db,
    )


@router.get("/api/shots/{shot_id}/full-sora-prompt")
async def get_full_sora_prompt(
    shot_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "get_full_sora_prompt",
        shot_id=shot_id,
        user=user,
        db=db,
    )


@router.get("/api/shots/{shot_id}/videos", response_model=List[ShotVideoResponse])
async def get_shot_videos(
    shot_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "get_shot_videos",
        shot_id=shot_id,
        user=user,
        db=db,
    )


@router.put("/api/shots/{shot_id}/thumbnail")
async def update_shot_thumbnail(
    shot_id: int,
    request: ThumbnailUpdate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "update_shot_thumbnail",
        shot_id=shot_id,
        request=request,
        user=user,
        db=db,
    )


@router.post("/api/shots/{shot_id}/generate-video")
async def generate_video(
    shot_id: int,
    request: GenerateVideoRequest = GenerateVideoRequest(),
    background_tasks: BackgroundTasks = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "generate_video",
        shot_id=shot_id,
        request=request,
        background_tasks=background_tasks,
        user=user,
        db=db,
    )


@router.get("/api/shots/{shot_id}/video-status")
async def check_shot_video_status(
    shot_id: int,
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "check_shot_video_status",
        shot_id=shot_id,
        db=db,
    )


@router.get("/api/shots/{shot_id}/export")
async def export_shot_video(
    shot_id: int,
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "export_shot_video",
        shot_id=shot_id,
        db=db,
    )


@router.post("/api/shots/{shot_id}/generate-storyboard-image")
async def generate_storyboard_image(
    shot_id: int,
    request: GenerateStoryboardImageRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await shot_reference_workflow.generate_storyboard_image(
        shot_id=shot_id,
        request=request,
        user=user,
        db=db,
    )


@router.post("/api/shots/{shot_id}/generate-detail-images")
async def generate_detail_images(
    shot_id: int,
    request: Optional[GenerateDetailImagesRequest] = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await shot_image_generation.generate_detail_images(
        shot_id=shot_id,
        request=request,
        user=user,
        db=db,
    )


@router.get("/api/shots/{shot_id}/detail-images")
async def get_shot_detail_images(
    shot_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await shot_image_generation.get_shot_detail_images(
        shot_id=shot_id,
        user=user,
        db=db,
    )


@router.patch("/api/shots/{shot_id}/detail-images/cover")
async def set_shot_detail_image_cover(
    shot_id: int,
    request: SetDetailImageCoverRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await shot_image_generation.set_shot_detail_image_cover(
        shot_id=shot_id,
        request=request,
        user=user,
        db=db,
    )


@router.post("/api/shots/{shot_id}/first-frame-reference-image")
async def upload_shot_first_frame_reference_image(
    shot_id: int,
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await shot_reference_workflow.upload_shot_first_frame_reference_image(
        shot_id=shot_id,
        file=file,
        user=user,
        db=db,
    )


@router.post("/api/shots/{shot_id}/scene-image")
async def upload_shot_scene_image(
    shot_id: int,
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await shot_reference_workflow.upload_shot_scene_image(
        shot_id=shot_id,
        file=file,
        user=user,
        db=db,
    )


@router.patch("/api/shots/{shot_id}/first-frame-reference")
async def set_shot_first_frame_reference(
    shot_id: int,
    request: SetFirstFrameReferenceRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await shot_reference_workflow.set_shot_first_frame_reference(
        shot_id=shot_id,
        request=request,
        user=user,
        db=db,
    )


@router.patch("/api/shots/{shot_id}/scene-image-selection")
async def set_shot_scene_image_selection(
    shot_id: int,
    request: SetShotSceneImageSelectionRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await shot_reference_workflow.set_shot_scene_image_selection(
        shot_id=shot_id,
        request=request,
        user=user,
        db=db,
    )


@router.post("/api/shots/{shot_id}/reprocess-video")
async def reprocess_shot_video(
    shot_id: int,
    db: Session = Depends(get_db),
):
    return await _call_legacy(
        "reprocess_shot_video",
        shot_id=shot_id,
        db=db,
    )
