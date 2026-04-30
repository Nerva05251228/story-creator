from fastapi import APIRouter

import image_platform_client


router = APIRouter()


@router.get("/api/image-generation/models")
async def get_image_models():
    return {"models": image_platform_client.get_image_model_catalog_public()}
