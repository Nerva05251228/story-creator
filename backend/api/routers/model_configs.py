from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from api.schemas.model_configs import UpdateModelConfigRequest
from api.services.admin_auth import _verify_admin_panel_password
import api.services.model_configs as model_configs_service
from database import get_db


router = APIRouter()


@router.get("/api/admin/model-configs")
async def get_model_configs(
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """返回模型选择页需要的缓存模型与功能分配。"""
    _verify_admin_panel_password(x_admin_password)
    return model_configs_service.get_model_configs_payload(db)


@router.post("/api/admin/model-configs/sync-models")
async def sync_model_cache(
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    _verify_admin_panel_password(x_admin_password)
    sync_result = model_configs_service.sync_models_from_upstream(db)
    db.commit()
    cache_payload = model_configs_service.get_cached_models_payload(db)
    return {
        "message": "模型缓存已同步",
        "count": int(sync_result.get("count") or 0),
        "last_synced_at": cache_payload.get("last_synced_at"),
        "models": cache_payload.get("models", []),
    }


@router.put("/api/admin/model-config/{function_key}")
async def update_model_config(
    function_key: str,
    request: UpdateModelConfigRequest,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """更新某功能的 model 分配。"""
    _verify_admin_panel_password(x_admin_password)
    model_configs_service._ensure_function_model_configs(db)
    row = db.query(model_configs_service.models.FunctionModelConfig).filter(
        model_configs_service.models.FunctionModelConfig.function_key == function_key
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="功能配置不存在")

    explicit_model_id = str(request.model_id or "").strip() or model_configs_service.DEFAULT_TEXT_MODEL_ID
    resolved = model_configs_service.resolve_ai_model_option(
        model_configs_service.RELAY_PROVIDER_KEY,
        explicit_model_id,
        db=db,
    )

    row.provider_key = model_configs_service.RELAY_PROVIDER_KEY
    row.model_key = resolved["model_id"]
    row.model_id = resolved["model_id"]
    db.commit()
    db.refresh(row)
    return model_configs_service._serialize_function_model_config(row, db)
