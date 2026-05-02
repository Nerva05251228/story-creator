import secrets
from datetime import datetime, timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

import models
from api.schemas.admin_users import CreateUserRequest
from api.services.admin_auth import _verify_admin_panel_password
from api.services.auth import HIDDEN_USERS, hash_password
from api.services.episode_cleanup import clear_episode_dependencies
from database import get_db


router = APIRouter()


def _get_today_video_counts_by_user(db: Session) -> Dict[int, int]:
    shanghai_offset = timedelta(hours=8)
    now_utc = datetime.utcnow()
    now_shanghai = now_utc + shanghai_offset
    start_of_day = (
        now_shanghai.replace(hour=0, minute=0, second=0, microsecond=0)
        - shanghai_offset
    )
    end_of_day = start_of_day + timedelta(days=1)
    counts: Dict[int, int] = {}

    shot_video_rows = db.query(
        models.Script.user_id,
        func.count(models.ShotVideo.id),
    ).join(
        models.Episode,
        models.Episode.script_id == models.Script.id,
    ).join(
        models.StoryboardShot,
        models.StoryboardShot.episode_id == models.Episode.id,
    ).join(
        models.ShotVideo,
        models.ShotVideo.shot_id == models.StoryboardShot.id,
    ).filter(
        models.ShotVideo.created_at >= start_of_day,
        models.ShotVideo.created_at < end_of_day,
    ).group_by(
        models.Script.user_id,
    ).all()

    for user_id, total_count in shot_video_rows:
        numeric_user_id = int(user_id or 0)
        counts[numeric_user_id] = counts.get(numeric_user_id, 0) + int(total_count or 0)

    storyboard2_rows = db.query(
        models.Script.user_id,
        func.count(models.Storyboard2SubShotVideo.id),
    ).join(
        models.Episode,
        models.Episode.script_id == models.Script.id,
    ).join(
        models.Storyboard2Shot,
        models.Storyboard2Shot.episode_id == models.Episode.id,
    ).join(
        models.Storyboard2SubShot,
        models.Storyboard2SubShot.storyboard2_shot_id == models.Storyboard2Shot.id,
    ).join(
        models.Storyboard2SubShotVideo,
        models.Storyboard2SubShotVideo.sub_shot_id == models.Storyboard2SubShot.id,
    ).filter(
        models.Storyboard2SubShotVideo.created_at >= start_of_day,
        models.Storyboard2SubShotVideo.created_at < end_of_day,
        models.Storyboard2SubShotVideo.status == "completed",
        models.Storyboard2SubShotVideo.video_url != "",
        models.Storyboard2SubShotVideo.is_deleted == False,
    ).group_by(
        models.Script.user_id,
    ).all()

    for user_id, total_count in storyboard2_rows:
        numeric_user_id = int(user_id or 0)
        counts[numeric_user_id] = counts.get(numeric_user_id, 0) + int(total_count or 0)

    return counts


@router.get("/api/admin/users")
async def get_all_users_admin(
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """获取所有用户（管理用，隐藏保留账号）"""
    _verify_admin_panel_password(x_admin_password)
    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    today_video_counts = _get_today_video_counts_by_user(db)
    return [{
        "id": user.id,
        "username": user.username,
        "password": user.password_plain,
        "created_at": user.created_at,
        "today_video_count": int(today_video_counts.get(int(user.id or 0), 0)),
    } for user in users if user.username not in HIDDEN_USERS]


@router.post("/api/admin/users")
async def create_user_admin(
    request: CreateUserRequest,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """创建新用户（管理用）"""
    _verify_admin_panel_password(x_admin_password)

    existing_user = db.query(models.User).filter(
        models.User.username == request.username
    ).first()

    if existing_user:
        raise HTTPException(status_code=400, detail="用户名已存在")

    token = secrets.token_urlsafe(32)
    new_user = models.User(
        username=request.username,
        token=token,
        password_hash=hash_password("123456"),
        password_plain="123456",
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "id": new_user.id,
        "username": new_user.username,
        "password": new_user.password_plain,
        "created_at": new_user.created_at,
    }


@router.delete("/api/admin/users/{user_id}")
async def delete_user_admin(
    user_id: int,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """删除用户（管理用，不允许删除保留账号）"""
    _verify_admin_panel_password(x_admin_password)
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if user.username in HIDDEN_USERS:
        raise HTTPException(status_code=403, detail="无法删除此用户")

    episode_ids = [
        episode_id
        for episode_id, in db.query(models.Episode.id)
        .join(models.Script, models.Episode.script_id == models.Script.id)
        .filter(models.Script.user_id == user_id)
        .all()
    ]
    from api.routers.episodes import _delete_episode_storyboard_shots

    for episode_id in episode_ids:
        _delete_episode_storyboard_shots(episode_id, db)
    episode_cleanup_stats = clear_episode_dependencies(episode_ids, db)

    print(
        "[用户删除清理] "
        f"user_id={user_id} username={user.username} "
        f"episodes={len(episode_ids)} "
        f"managed_tasks={episode_cleanup_stats['deleted_managed_tasks']} "
        f"managed_sessions={episode_cleanup_stats['deleted_managed_sessions']} "
        f"voiceover_tts_tasks={episode_cleanup_stats['deleted_voiceover_tts_tasks']} "
        f"unlinked_libraries={episode_cleanup_stats['unlinked_libraries']}"
    )

    db.delete(user)
    db.commit()

    return {"message": "用户删除成功"}


@router.post("/api/admin/users/{user_id}/reset-password")
async def reset_user_password_admin(
    user_id: int,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    """重置用户密码为 123456（管理用，不允许重置保留账号）"""
    _verify_admin_panel_password(x_admin_password)
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if user.username in HIDDEN_USERS:
        raise HTTPException(status_code=403, detail="无法操作此用户")

    user.password_hash = hash_password("123456")
    user.password_plain = "123456"
    db.commit()
    return {"message": "密码已重置为 123456"}


@router.post("/api/admin/users/{user_id}/impersonate")
async def impersonate_user_admin(
    user_id: int,
    x_admin_password: Optional[str] = Header(None, alias="X-Admin-Password"),
    db: Session = Depends(get_db),
):
    _verify_admin_panel_password(x_admin_password)
    user = db.query(models.User).filter(models.User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if user.username in HIDDEN_USERS:
        raise HTTPException(status_code=403, detail="该用户不允许免密登录")

    return {
        "id": user.id,
        "username": user.username,
        "token": user.token,
        "created_at": user.created_at,
    }
