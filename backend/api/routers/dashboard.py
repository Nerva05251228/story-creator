import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

import models
from api.schemas.dashboard import DashboardBulkDeleteRequest
from api.services.admin_auth import _verify_admin_panel_password
from dashboard_query_service import (
    is_dashboard_task_query_supported,
    query_dashboard_task,
)
from dashboard_service import (
    DASHBOARD_STATUS_LABELS,
    DASHBOARD_TASK_TYPE_LABELS,
    summarize_dashboard_batch_events,
)
from database import get_db


router = APIRouter()


def _parse_dashboard_date(date_text: Optional[str], *, end_exclusive: bool = False) -> Optional[datetime]:
    text_value = str(date_text or "").strip()
    if not text_value:
        return None
    try:
        parsed = datetime.strptime(text_value, "%Y-%m-%d")
        if end_exclusive:
            return parsed + timedelta(days=1)
        return parsed
    except ValueError:
        raise HTTPException(status_code=400, detail=f"日期格式错误: {text_value}，请使用 YYYY-MM-DD")


def _apply_dashboard_query_filters(
    query,
    *,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    creator_username: Optional[str] = None,
    keyword: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    if status:
        query = query.filter(models.DashboardTaskLog.status == str(status).strip())
    if task_type:
        query = query.filter(models.DashboardTaskLog.task_type == str(task_type).strip())
    if creator_username:
        query = query.filter(models.DashboardTaskLog.creator_username.ilike(f"%{str(creator_username).strip()}%"))
    if keyword:
        like = f"%{str(keyword).strip()}%"
        query = query.filter(or_(
            models.DashboardTaskLog.title.ilike(like),
            models.DashboardTaskLog.task_key.ilike(like),
            models.DashboardTaskLog.script_name.ilike(like),
            models.DashboardTaskLog.episode_name.ilike(like),
            models.DashboardTaskLog.creator_username.ilike(like),
            models.DashboardTaskLog.error_message.ilike(like),
            models.DashboardTaskLog.result_summary.ilike(like),
            models.DashboardTaskLog.api_url.ilike(like),
        ))

    start_dt = _parse_dashboard_date(date_from, end_exclusive=False)
    end_dt = _parse_dashboard_date(date_to, end_exclusive=True)
    if start_dt:
        query = query.filter(models.DashboardTaskLog.created_at >= start_dt)
    if end_dt:
        query = query.filter(models.DashboardTaskLog.created_at < end_dt)
    return query


def _safe_parse_dashboard_json(payload_text: Any, default_value: Any):
    if not payload_text:
        return default_value
    if isinstance(payload_text, (dict, list)):
        return payload_text
    try:
        return json.loads(payload_text)
    except Exception:
        return payload_text


SIMPLE_STORYBOARD_TIMEOUT_SECONDS = 3600
DASHBOARD_SIMPLE_STORYBOARD_TIMEOUT_ERROR = "简单分镜任务超时（超过 1 小时），已自动标记为失败。"


def _mark_dashboard_simple_storyboard_timeout_if_needed(row: Optional[models.DashboardTaskLog], db: Session) -> bool:
    if not row:
        return False
    if str(getattr(row, "task_type", "") or "").strip() != "simple_storyboard":
        return False
    if str(getattr(row, "status", "") or "").strip() != "submitting":
        return False
    created_at = getattr(row, "created_at", None)
    if not created_at:
        return False
    if (datetime.utcnow() - created_at).total_seconds() < SIMPLE_STORYBOARD_TIMEOUT_SECONDS:
        return False
    row.status = "failed"
    if not str(getattr(row, "error_message", "") or "").strip():
        row.error_message = DASHBOARD_SIMPLE_STORYBOARD_TIMEOUT_ERROR
    if not str(getattr(row, "result_summary", "") or "").strip():
        row.result_summary = DASHBOARD_SIMPLE_STORYBOARD_TIMEOUT_ERROR
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return True


def _build_dashboard_batch_summary(events: Any, fallback_status: str) -> Dict[str, Any]:
    summary = summarize_dashboard_batch_events(events, fallback_status=fallback_status)
    if not summary.get("has_batches"):
        return summary

    items = summary.get("items") or []
    summary_lines: List[str] = []
    counts = summary.get("counts") or {}
    header_parts = [f"Batch {int(counts.get('total') or 0)}"]
    for status_key in ("completed", "processing", "submitting", "failed", "cancelled"):
        count = int(counts.get(status_key) or 0)
        if count <= 0:
            continue
        header_parts.append(f"{DASHBOARD_STATUS_LABELS.get(status_key, status_key)} {count}")
    summary_lines.append(" | ".join(header_parts))

    for item in items:
        status = str(item.get("latest_status") or "")
        batch_line_parts = [f"Batch {item.get('batch_id')}", DASHBOARD_STATUS_LABELS.get(status, status)]
        latest_attempt = item.get("latest_attempt")
        if latest_attempt is not None:
            batch_line_parts.append(f"尝试 {latest_attempt}")
        shots_count = item.get("shots_count")
        if shots_count is not None and status == "completed":
            batch_line_parts.append(f"{shots_count} 镜头")
        last_error = str(item.get("last_error") or "").strip()
        line = " | ".join(part for part in batch_line_parts if str(part).strip())
        if last_error and status != "completed":
            line = f"{line} | {last_error}"
        summary_lines.append(line)

    summary["summary_text"] = "\n".join(summary_lines)
    return summary


def _serialize_dashboard_task(row: models.DashboardTaskLog, include_payloads: bool = False) -> dict:
    parsed_events = _safe_parse_dashboard_json(row.events_json, [])
    batch_summary = _build_dashboard_batch_summary(parsed_events, row.status)
    resolved_status = batch_summary.get("overall_status") if batch_summary.get("has_batches") else row.status
    data = {
        "id": row.id,
        "task_key": row.task_key,
        "task_folder": row.task_folder,
        "source_type": row.source_type,
        "source_record_type": row.source_record_type,
        "source_record_id": row.source_record_id,
        "task_type": row.task_type,
        "task_type_label": DASHBOARD_TASK_TYPE_LABELS.get(row.task_type, row.task_type or "任务"),
        "stage": row.stage,
        "title": row.title,
        "status": resolved_status,
        "status_label": DASHBOARD_STATUS_LABELS.get(resolved_status, resolved_status),
        "stored_status": row.status,
        "stored_status_label": DASHBOARD_STATUS_LABELS.get(row.status, row.status),
        "creator_user_id": row.creator_user_id,
        "creator_username": row.creator_username,
        "script_id": row.script_id,
        "script_name": row.script_name,
        "episode_id": row.episode_id,
        "episode_name": row.episode_name,
        "shot_id": row.shot_id,
        "shot_number": row.shot_number,
        "batch_id": row.batch_id,
        "provider": row.provider,
        "model_name": row.model_name,
        "api_url": row.api_url,
        "status_api_url": row.status_api_url,
        "external_task_id": row.external_task_id,
        "query_supported": is_dashboard_task_query_supported(row),
        "error_message": row.error_message,
        "result_summary": row.result_summary,
        "batch_summary": batch_summary if batch_summary.get("has_batches") else None,
        "batch_summary_text": batch_summary.get("summary_text", ""),
        "latest_filename": row.latest_filename,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }
    if include_payloads:
        data.update({
            "input_payload": _safe_parse_dashboard_json(row.input_payload, {}),
            "output_payload": _safe_parse_dashboard_json(row.output_payload, {}),
            "raw_response_payload": _safe_parse_dashboard_json(row.raw_response_payload, {}),
            "result_payload": _safe_parse_dashboard_json(row.result_payload, {}),
            "latest_event_payload": _safe_parse_dashboard_json(row.latest_event_payload, {}),
            "events": parsed_events,
        })
    return data


@router.get("/api/dashboard/tasks")
async def list_dashboard_tasks(
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    creator_username: Optional[str] = None,
    keyword: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    size: int = 100,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    _verify_admin_panel_password(x_admin_password)
    normalized_page = max(1, int(page or 1))
    normalized_size = max(1, min(int(size or 100), 500))

    filtered_query = _apply_dashboard_query_filters(
        db.query(models.DashboardTaskLog),
        status=status,
        task_type=task_type,
        creator_username=creator_username,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
    )

    stale_rows = filtered_query.filter(
        models.DashboardTaskLog.task_type == "simple_storyboard",
        models.DashboardTaskLog.status == "submitting",
    ).all()
    for stale_row in stale_rows:
        _mark_dashboard_simple_storyboard_timeout_if_needed(stale_row, db)

    filtered_query = _apply_dashboard_query_filters(
        db.query(models.DashboardTaskLog),
        status=status,
        task_type=task_type,
        creator_username=creator_username,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
    )

    total = filtered_query.count()
    rows = filtered_query.order_by(
        models.DashboardTaskLog.created_at.desc(),
        models.DashboardTaskLog.id.desc(),
    ).offset((normalized_page - 1) * normalized_size).limit(normalized_size).all()

    status_rows = db.query(models.DashboardTaskLog.status).distinct().all()
    task_type_rows = db.query(models.DashboardTaskLog.task_type).distinct().all()

    return {
        "items": [_serialize_dashboard_task(row) for row in rows],
        "total": int(total or 0),
        "page": normalized_page,
        "size": normalized_size,
        "status_options": sorted({str(item[0] or "").strip() for item in status_rows if str(item[0] or "").strip()}),
        "task_type_options": sorted({str(item[0] or "").strip() for item in task_type_rows if str(item[0] or "").strip()}),
        "status_labels": DASHBOARD_STATUS_LABELS,
        "task_type_labels": DASHBOARD_TASK_TYPE_LABELS,
    }


@router.get("/api/dashboard/tasks/{task_id}")
async def get_dashboard_task_detail(
    task_id: int,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    _verify_admin_panel_password(x_admin_password)
    row = db.query(models.DashboardTaskLog).filter(models.DashboardTaskLog.id == task_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    _mark_dashboard_simple_storyboard_timeout_if_needed(row, db)
    return _serialize_dashboard_task(row, include_payloads=True)


@router.post("/api/dashboard/tasks/{task_id}/query-status")
async def query_dashboard_task_status(
    task_id: int,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    _verify_admin_panel_password(x_admin_password)
    row = db.query(models.DashboardTaskLog).filter(models.DashboardTaskLog.id == task_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        return query_dashboard_task(row)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/api/dashboard/tasks/{task_id}")
async def delete_dashboard_task(
    task_id: int,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    _verify_admin_panel_password(x_admin_password)
    row = db.query(models.DashboardTaskLog).filter(models.DashboardTaskLog.id == task_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    db.delete(row)
    db.commit()
    return {"message": "任务记录已删除", "deleted_count": 1}


@router.post("/api/dashboard/tasks/bulk-delete")
async def bulk_delete_dashboard_tasks(
    request: DashboardBulkDeleteRequest,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    _verify_admin_panel_password(x_admin_password)
    query = db.query(models.DashboardTaskLog)

    normalized_ids = [int(item) for item in (request.ids or []) if str(item).strip()]
    if normalized_ids:
        query = query.filter(models.DashboardTaskLog.id.in_(normalized_ids))
    else:
        query = _apply_dashboard_query_filters(
            query,
            status=request.status,
            task_type=request.task_type,
            creator_username=request.creator_username,
            keyword=request.keyword,
            date_from=request.date_from,
            date_to=request.date_to,
        )
        has_filters = any([
            request.status,
            request.task_type,
            request.creator_username,
            request.keyword,
            request.date_from,
            request.date_to,
        ])
        if not has_filters and not request.delete_all:
            raise HTTPException(status_code=400, detail="未指定删除条件")

    deleted_count = query.delete(synchronize_session=False)
    db.commit()
    return {"message": "任务记录已删除", "deleted_count": int(deleted_count or 0)}
