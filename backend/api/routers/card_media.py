import asyncio
import os
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models
from api.schemas.card_media import ImageGenerationRequest
from api.schemas.subject_cards import GeneratedImageResponse, SubjectCardAudioResponse
from api.services import card_image_generation as card_image_generation_service
from api.services import card_media as card_media_service
from auth import get_current_user, verify_library_owner
from database import get_db


router = APIRouter()
SOUND_CARD_TYPE = card_media_service.SOUND_CARD_TYPE


class SetReferenceRequest(BaseModel):
    generated_image_ids: List[int]


@router.post("/api/cards/{card_id}/generate-image")
async def generate_image_for_card(
    card_id: int,
    request: ImageGenerationRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    verify_library_owner(card.library_id, user, db)

    return await card_image_generation_service.submit_card_image_generation(
        db,
        card=card,
        request=request,
    )


@router.post("/api/cards/{card_id}/images")
async def upload_image(
    card_id: int,
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    verify_library_owner(card.library_id, user, db)

    try:
        cdn_url = await asyncio.to_thread(card_media_service.save_and_upload_to_cdn, file)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传图片失败: {str(e)}")

    max_order = db.query(models.CardImage).filter(
        models.CardImage.card_id == card_id
    ).count()

    new_image = models.CardImage(
        card_id=card_id,
        image_path=cdn_url,
        order=max_order,
    )
    db.add(new_image)

    if card.card_type != "场景":
        db.query(models.GeneratedImage).filter(
            models.GeneratedImage.card_id == card_id
        ).update({"is_reference": False})

    new_generated = models.GeneratedImage(
        card_id=card_id,
        image_path=cdn_url,
        model_name="upload",
        is_reference=(card.card_type != "场景"),
        status="completed",
        task_id="",
    )
    db.add(new_generated)

    db.commit()
    db.refresh(new_image)

    return {
        "id": new_image.id,
        "card_id": new_image.card_id,
        "image_path": new_image.image_path,
        "order": new_image.order,
    }


@router.delete("/api/images/{image_id}")
async def delete_image(
    image_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    image = db.query(models.CardImage).filter(models.CardImage.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    card = db.query(models.SubjectCard).filter(
        models.SubjectCard.id == image.card_id
    ).first()
    verify_library_owner(card.library_id, user, db)

    gen_img = db.query(models.GeneratedImage).filter(
        models.GeneratedImage.card_id == image.card_id,
        models.GeneratedImage.image_path == image.image_path,
        models.GeneratedImage.model_name == "upload",
    ).first()

    if gen_img and gen_img.is_reference and card.card_type != "场景":
        other_completed_images = db.query(models.GeneratedImage).filter(
            models.GeneratedImage.card_id == image.card_id,
            models.GeneratedImage.id != gen_img.id,
            models.GeneratedImage.status == "completed",
        ).all()
        if len(other_completed_images) == 0:
            raise HTTPException(status_code=400, detail="不能删除最后一张主体素材图")
        other_completed_images[0].is_reference = True
        print(f"[删除上传参考图] 自动将图片 {other_completed_images[0].id} 设为新的参考图")

    if os.path.exists(image.image_path):
        os.remove(image.image_path)

    if gen_img:
        db.delete(gen_img)

    db.delete(image)
    db.commit()
    return {"message": "Image deleted successfully"}


@router.post("/api/cards/{card_id}/audios", response_model=SubjectCardAudioResponse)
async def upload_card_audio(
    card_id: int,
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    verify_library_owner(card.library_id, user, db)

    if card.card_type != SOUND_CARD_TYPE:
        raise HTTPException(status_code=400, detail="只有声音卡片支持上传音频")

    try:
        cdn_url, duration_seconds = await asyncio.to_thread(
            card_media_service.save_audio_and_upload_to_cdn,
            file,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传音频失败: {str(e)}")

    db.query(models.SubjectCardAudio).filter(
        models.SubjectCardAudio.card_id == card_id
    ).update({"is_reference": False})

    new_audio = models.SubjectCardAudio(
        card_id=card_id,
        audio_path=cdn_url,
        file_name=str(file.filename or "").strip(),
        duration_seconds=card_media_service._safe_audio_duration_seconds(duration_seconds),
        is_reference=True,
    )
    db.add(new_audio)
    db.commit()
    db.refresh(new_audio)
    return new_audio


@router.get("/api/cards/{card_id}/audios", response_model=List[SubjectCardAudioResponse])
async def get_card_audios(
    card_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    verify_library_owner(card.library_id, user, db)

    audios = db.query(models.SubjectCardAudio).filter(
        models.SubjectCardAudio.card_id == card_id
    ).order_by(
        models.SubjectCardAudio.created_at.desc(),
        models.SubjectCardAudio.id.desc(),
    ).all()
    if card_media_service._backfill_audio_duration_cache(audios, db):
        db.commit()
    return audios


@router.delete("/api/cards/{card_id}/audios/{audio_id}")
async def delete_card_audio(
    card_id: int,
    audio_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    verify_library_owner(card.library_id, user, db)

    if card.card_type != SOUND_CARD_TYPE:
        raise HTTPException(status_code=400, detail="只有声音卡片支持删除音频")

    audio = db.query(models.SubjectCardAudio).filter(
        models.SubjectCardAudio.id == audio_id,
        models.SubjectCardAudio.card_id == card_id,
    ).first()
    if not audio:
        raise HTTPException(status_code=404, detail="音频不存在")

    was_reference = bool(audio.is_reference)
    db.delete(audio)
    db.flush()

    if was_reference:
        fallback_audio = db.query(models.SubjectCardAudio).filter(
            models.SubjectCardAudio.card_id == card_id
        ).order_by(
            models.SubjectCardAudio.created_at.desc(),
            models.SubjectCardAudio.id.desc(),
        ).first()
        if fallback_audio:
            fallback_audio.is_reference = True

    db.commit()
    return {"message": "Audio deleted successfully"}


@router.get("/api/cards/{card_id}/generated-images", response_model=List[GeneratedImageResponse])
def get_card_generated_images(
    card_id: int,
    db: Session = Depends(get_db),
):
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    generated_images = db.query(models.GeneratedImage).filter(
        models.GeneratedImage.card_id == card_id
    ).order_by(models.GeneratedImage.created_at.desc()).all()

    return generated_images


@router.put("/api/cards/{card_id}/reference-images")
async def set_reference_images(
    card_id: int,
    request: SetReferenceRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    verify_library_owner(card.library_id, user, db)

    db.query(models.GeneratedImage).filter(
        models.GeneratedImage.card_id == card_id
    ).update({"is_reference": False})

    if request.generated_image_ids:
        db.query(models.GeneratedImage).filter(
            models.GeneratedImage.id.in_(request.generated_image_ids),
            models.GeneratedImage.card_id == card_id,
        ).update({"is_reference": True}, synchronize_session=False)

    db.commit()
    return {"message": "参考图已更新"}


@router.delete("/api/generated-images/{generated_image_id}")
async def delete_generated_image(
    generated_image_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    gen_img = db.query(models.GeneratedImage).filter(
        models.GeneratedImage.id == generated_image_id
    ).first()

    if not gen_img:
        raise HTTPException(status_code=404, detail="Generated image not found")

    card = db.query(models.SubjectCard).filter(
        models.SubjectCard.id == gen_img.card_id
    ).first()
    verify_library_owner(card.library_id, user, db)

    if gen_img.is_reference and card.card_type != "场景":
        other_completed_images = db.query(models.GeneratedImage).filter(
            models.GeneratedImage.card_id == gen_img.card_id,
            models.GeneratedImage.id != gen_img.id,
            models.GeneratedImage.status == "completed",
        ).all()
        if len(other_completed_images) == 0:
            raise HTTPException(status_code=400, detail="不能删除最后一张主体素材图")
        other_completed_images[0].is_reference = True
        print(f"[删除参考图] 自动将图片 {other_completed_images[0].id} 设为新的参考图")

    db.delete(gen_img)
    db.commit()
    return {"message": "Generated image deleted successfully"}
