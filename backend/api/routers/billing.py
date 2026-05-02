from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

import billing_service
import models
from api.schemas.billing import BillingPriceRuleRequest
from api.services.admin_auth import _verify_admin_panel_password
from auth import get_current_user
from database import get_db


router = APIRouter()


def _parse_optional_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    normalized = raw_value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


@router.get("/api/billing/users")
async def get_billing_users(
    month: Optional[str] = Query(None),
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """返回所有有账单的用户汇总。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    return {
        "users": billing_service.get_billing_user_list(db, month=month),
    }


@router.get("/api/billing/episodes")
async def get_billing_episodes(
    group_by: str = Query("script"),
    user_id: Optional[int] = Query(None),
    script_id: Optional[int] = Query(None),
    month: Optional[str] = Query(None),
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """返回管理员视图下的剧集汇总。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    episodes = billing_service.get_billing_episode_list(
        db,
        user_id=user_id,
        script_id=script_id,
        month=month,
    )
    return {
        "group_by": str(group_by or "script"),
        "episodes": episodes,
    }


@router.get("/api/billing/scripts")
async def get_billing_scripts(
    group_by: str = Query("script"),
    user_id: Optional[int] = Query(None),
    month: Optional[str] = Query(None),
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """返回管理员视图下的剧本汇总。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    return {
        "group_by": str(group_by or "script"),
        "scripts": billing_service.get_billing_script_list(
            db,
            user_id=user_id,
            month=month,
        ),
    }


@router.get("/api/billing/scripts/{script_id}")
async def get_billing_script_detail(
    script_id: int,
    month: Optional[str] = Query(None),
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """返回管理员视图下某个剧本的计费详情。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    detail = billing_service.get_script_billing_detail(
        db,
        script_id=int(script_id),
        month=month,
    )
    if not detail:
        raise HTTPException(status_code=404, detail="计费剧本不存在")
    return detail


@router.get("/api/billing/episodes/{episode_id}")
async def get_billing_episode_detail(
    episode_id: int,
    month: Optional[str] = Query(None),
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """返回管理员视图下某个剧集的计费详情。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    detail = billing_service.get_episode_billing_detail(
        db,
        episode_id=int(episode_id),
        month=month,
    )
    if not detail:
        raise HTTPException(status_code=404, detail="计费剧集不存在")
    return detail


@router.get("/api/billing/reimbursement-export")
async def get_billing_reimbursement_export(
    group_by: str = Query("script"),
    month: Optional[str] = Query(None),
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """返回报销用途的月度汇总数据。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    normalized_group_by = "user" if str(group_by or "").strip().lower() == "user" else "script"
    return {
        "group_by": normalized_group_by,
        "title": "按用户月度报销汇总" if normalized_group_by == "user" else "按剧本月度报销汇总",
        "month": month,
        "rows": billing_service.get_billing_reimbursement_rows(
            db,
            group_by=normalized_group_by,
            month=month,
        ),
    }


@router.get("/api/billing/rules")
async def get_billing_rules(
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """返回计费价格规则。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    return {
        "rules": billing_service.get_price_rules(db),
    }


@router.post("/api/billing/rules")
async def create_billing_rule(
    request: BillingPriceRuleRequest,
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """新增计费价格规则。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    try:
        row = billing_service.create_price_rule(
            db,
            rule_name=request.rule_name,
            category=request.category,
            stage=request.stage,
            provider=request.provider,
            model_name=request.model_name,
            resolution=request.resolution,
            billing_mode=request.billing_mode,
            unit_price_rmb=request.unit_price_rmb,
            is_active=request.is_active,
            priority=request.priority,
            effective_from=_parse_optional_iso_datetime(request.effective_from),
            effective_to=_parse_optional_iso_datetime(request.effective_to),
        )
        db.commit()
        db.refresh(row)
        return billing_service.serialize_price_rule(row)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"创建计费规则失败: {str(e)}")


@router.put("/api/billing/rules/{rule_id}")
async def update_billing_rule(
    rule_id: int,
    request: BillingPriceRuleRequest,
    user: models.User = Depends(get_current_user),
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """更新计费价格规则。"""
    _ = user
    _verify_admin_panel_password(x_admin_password)
    try:
        row = billing_service.update_price_rule(
            db,
            rule_id=int(rule_id),
            rule_name=request.rule_name,
            category=request.category,
            stage=request.stage,
            provider=request.provider,
            model_name=request.model_name,
            resolution=request.resolution,
            billing_mode=request.billing_mode,
            unit_price_rmb=request.unit_price_rmb,
            is_active=request.is_active,
            priority=request.priority,
            effective_from=_parse_optional_iso_datetime(request.effective_from),
            effective_to=_parse_optional_iso_datetime(request.effective_to),
        )
        if not row:
            raise HTTPException(status_code=404, detail="计费规则不存在")
        db.commit()
        db.refresh(row)
        return billing_service.serialize_price_rule(row)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"更新计费规则失败: {str(e)}")
