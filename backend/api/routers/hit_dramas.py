import os
import re
import time
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import case, or_
from sqlalchemy.orm import Session

import models
from api.schemas.hit_dramas import (
    HitDramaCreate,
    HitDramaHistoryResponse,
    HitDramaResponse,
    HitDramaUpdate,
)
from auth import get_current_user
from database import get_db


router = APIRouter()

HIT_DRAMA_ONLINE_TIME_PATTERN = re.compile(r"^(?P<year>\d{4})[./-](?P<month>\d{1,2})[./-](?P<day>\d{1,2})$")


def normalize_hit_drama_online_time(value: Any) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return ""

    match = HIT_DRAMA_ONLINE_TIME_PATTERN.fullmatch(raw_value)
    if not match:
        raise ValueError("上线时间格式应为 YYYY.MM.DD")

    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))

    try:
        datetime(year, month, day)
    except ValueError as exc:
        raise ValueError("上线时间不是有效日期") from exc

    return f"{year:04d}.{month:02d}.{day:02d}"


def normalize_hit_drama_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    normalized_payload: Dict[str, str] = {}
    if "drama_name" in payload:
        normalized_payload["drama_name"] = str(payload.get("drama_name") or "").strip()
    if "view_count" in payload:
        normalized_payload["view_count"] = str(payload.get("view_count") or "").strip()
    if "opening_15_sentences" in payload:
        normalized_payload["opening_15_sentences"] = str(payload.get("opening_15_sentences") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if "first_episode_script" in payload:
        normalized_payload["first_episode_script"] = str(payload.get("first_episode_script") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if "online_time" in payload:
        normalized_payload["online_time"] = normalize_hit_drama_online_time(payload.get("online_time"))
    return normalized_payload


@router.get("/api/hit-dramas", response_model=List[HitDramaResponse])
def get_hit_dramas(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取所有爆款库记录（不包括已删除）"""
    dramas = db.query(models.HitDrama).filter(
        models.HitDrama.is_deleted == False
    ).order_by(
        case(
            (or_(models.HitDrama.online_time.is_(None), models.HitDrama.online_time == ""), 1),
            else_=0,
        ),
        models.HitDrama.online_time.desc(),
        models.HitDrama.created_at.desc(),
        models.HitDrama.id.desc(),
    ).all()
    return dramas


@router.post("/api/hit-dramas", response_model=HitDramaResponse)
def create_hit_drama(
    drama: HitDramaCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """新增爆款库记录"""
    try:
        normalized_data = normalize_hit_drama_payload(drama.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    new_drama = models.HitDrama(
        drama_name=normalized_data["drama_name"],
        view_count=normalized_data["view_count"],
        opening_15_sentences=normalized_data["opening_15_sentences"],
        first_episode_script=normalized_data["first_episode_script"],
        online_time=normalized_data["online_time"],
        created_by=user.username,
    )
    db.add(new_drama)
    db.commit()
    db.refresh(new_drama)

    history = models.HitDramaEditHistory(
        drama_id=new_drama.id,
        action_type="create",
        field_name=None,
        old_value=None,
        new_value=f"创建记录：{normalized_data['drama_name']}",
        edited_by=user.username,
    )
    db.add(history)
    db.commit()

    return new_drama


@router.put("/api/hit-dramas/{drama_id}", response_model=HitDramaResponse)
def update_hit_drama(
    drama_id: int,
    drama_update: HitDramaUpdate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新爆款库记录"""
    drama = db.query(models.HitDrama).filter(
        models.HitDrama.id == drama_id,
        models.HitDrama.is_deleted == False,
    ).first()

    if not drama:
        raise HTTPException(status_code=404, detail="记录不存在")

    changes = []
    field_mapping = {
        "drama_name": "剧名",
        "view_count": "播放量",
        "opening_15_sentences": "开头15句",
        "first_episode_script": "第一集文案",
        "online_time": "上线时间",
    }

    try:
        normalized_update = normalize_hit_drama_payload(drama_update.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    for field, value in normalized_update.items():
        if value is not None:
            old_value = getattr(drama, field)
            if old_value != value:
                history = models.HitDramaEditHistory(
                    drama_id=drama_id,
                    action_type="update",
                    field_name=field_mapping.get(field, field),
                    old_value=str(old_value) if old_value else "",
                    new_value=str(value),
                    edited_by=user.username,
                )
                db.add(history)
                changes.append(field)

                setattr(drama, field, value)

    if changes:
        drama.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(drama)

    return drama


@router.delete("/api/hit-dramas/{drama_id}")
def delete_hit_drama(
    drama_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除爆款库记录（软删除）"""
    drama = db.query(models.HitDrama).filter(
        models.HitDrama.id == drama_id,
        models.HitDrama.is_deleted == False,
    ).first()

    if not drama:
        raise HTTPException(status_code=404, detail="记录不存在")

    drama.is_deleted = True
    drama.updated_at = datetime.utcnow()

    history = models.HitDramaEditHistory(
        drama_id=drama_id,
        action_type="delete",
        field_name=None,
        old_value=f"剧名：{drama.drama_name}",
        new_value="已删除",
        edited_by=user.username,
    )
    db.add(history)
    db.commit()

    return {"message": "删除成功", "drama_id": drama_id}


@router.get("/api/hit-dramas/history", response_model=List[HitDramaHistoryResponse])
def get_hit_drama_history(
    user_filter: Optional[str] = None,
    drama_name_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取编辑历史（支持筛选）"""
    query = db.query(models.HitDramaEditHistory).join(
        models.HitDrama,
        models.HitDramaEditHistory.drama_id == models.HitDrama.id,
    )

    if user_filter:
        query = query.filter(models.HitDramaEditHistory.edited_by.contains(user_filter))

    if drama_name_filter:
        query = query.filter(models.HitDrama.drama_name.contains(drama_name_filter))

    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
            query = query.filter(models.HitDramaEditHistory.edited_at >= start_dt)
        except ValueError:
            pass

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date)
            query = query.filter(models.HitDramaEditHistory.edited_at <= end_dt)
        except ValueError:
            pass

    histories = query.order_by(models.HitDramaEditHistory.edited_at.desc()).all()

    result = []
    for history in histories:
        drama = db.query(models.HitDrama).filter(models.HitDrama.id == history.drama_id).first()
        history_dict = {
            "id": history.id,
            "drama_id": history.drama_id,
            "action_type": history.action_type,
            "field_name": history.field_name,
            "old_value": history.old_value,
            "new_value": history.new_value,
            "edited_by": history.edited_by,
            "edited_at": history.edited_at,
            "drama_name": drama.drama_name if drama else None,
        }
        result.append(HitDramaHistoryResponse(**history_dict))

    return result


@router.post("/api/hit-dramas/upload-video")
async def upload_hit_drama_video(
    drama_id: int = Form(...),
    file: UploadFile = File(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传爆款库视频"""
    drama = db.query(models.HitDrama).filter(
        models.HitDrama.id == drama_id,
        models.HitDrama.is_deleted == False,
    ).first()

    if not drama:
        raise HTTPException(status_code=404, detail="记录不存在")

    upload_dir = os.path.join("uploads", "hit_drama_videos")
    os.makedirs(upload_dir, exist_ok=True)

    timestamp = int(time.time() * 1000)
    filename = f"{timestamp}_{file.filename}"
    file_path = os.path.join(upload_dir, filename)

    with open(file_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)

    old_filename = drama.video_filename
    drama.video_filename = filename
    drama.updated_at = datetime.utcnow()

    history = models.HitDramaEditHistory(
        drama_id=drama_id,
        action_type="update",
        field_name="视频",
        old_value=old_filename if old_filename else "无",
        new_value=filename,
        edited_by=user.username,
    )
    db.add(history)
    db.commit()

    return {"message": "上传成功", "filename": filename}


@router.post("/api/hit-dramas/import-excel")
async def import_hit_drama_excel(
    file: UploadFile = File(...),
    import_mode: str = Form("append"),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """导入Excel数据"""
    try:
        import pandas as pd

        content = await file.read()
        df = pd.read_excel(BytesIO(content))

        required_columns = ["剧名", "播放量", "开头15句", "第一集文案", "上线时间"]
        if not all(col in df.columns for col in required_columns):
            raise HTTPException(status_code=400, detail="Excel格式不正确，缺少必要的列")

        normalized_import_mode = str(import_mode or "append").strip().lower()
        if normalized_import_mode not in {"append", "overwrite"}:
            raise HTTPException(status_code=400, detail="导入模式不正确")

        df_clean = df.dropna(how="all")

        rows_to_import = []
        for row_index, row in df_clean.iterrows():
            if pd.isna(row["剧名"]) or str(row["剧名"]).strip() == "":
                continue

            try:
                rows_to_import.append(normalize_hit_drama_payload({
                    "drama_name": str(row["剧名"]),
                    "view_count": str(row["播放量"]) if not pd.isna(row["播放量"]) else "",
                    "opening_15_sentences": str(row["开头15句"]) if not pd.isna(row["开头15句"]) else "",
                    "first_episode_script": str(row["第一集文案"]) if not pd.isna(row["第一集文案"]) else "",
                    "online_time": str(row["上线时间"]) if not pd.isna(row["上线时间"]) else "",
                }))
            except ValueError as exc:
                try:
                    excel_row_number = int(row_index) + 2
                except (TypeError, ValueError):
                    excel_row_number = "未知"
                raise HTTPException(status_code=400, detail=f"第 {excel_row_number} 行：{exc}")

        if normalized_import_mode == "overwrite":
            db.query(models.HitDramaEditHistory).delete(synchronize_session=False)
            db.query(models.HitDrama).delete(synchronize_session=False)

        imported_count = 0
        for row_data in rows_to_import:
            new_drama = models.HitDrama(
                drama_name=row_data["drama_name"],
                view_count=row_data["view_count"],
                opening_15_sentences=row_data["opening_15_sentences"],
                first_episode_script=row_data["first_episode_script"],
                online_time=row_data["online_time"],
                created_by=user.username,
            )
            db.add(new_drama)
            imported_count += 1

        db.commit()

        action_label = "覆盖导入" if normalized_import_mode == "overwrite" else "追加导入"
        return {
            "message": f"{action_label}成功，共导入 {imported_count} 条记录",
            "count": imported_count,
            "import_mode": normalized_import_mode,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"导入失败：{str(e)}")
