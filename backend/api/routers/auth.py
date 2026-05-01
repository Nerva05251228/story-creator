from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import models
from api.schemas.auth import ChangePasswordRequest, LoginRequest, PasswordVerifyRequest
from api.services.auth import change_user_password, login_user, verify_nerva_password_value, verify_user
from auth import get_current_user
from database import get_db


router = APIRouter()


@router.post("/api/auth/login")
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """通过用户名 + 密码登录"""
    return login_user(request.username, request.password, db)


@router.post("/api/auth/verify")
async def verify_token(user: models.User = Depends(get_current_user)):
    """验证token是否有效"""
    return verify_user(user)


@router.post("/api/auth/change-password")
async def change_password(request: ChangePasswordRequest, db: Session = Depends(get_db)):
    """修改密码（需要验证原密码）"""
    return change_user_password(
        request.username,
        request.old_password,
        request.new_password,
        db,
    )


@router.post("/api/auth/verify-nerva-password")
async def verify_nerva_password(request: PasswordVerifyRequest):
    """验证nerva用户密码"""
    return verify_nerva_password_value(request.password)
