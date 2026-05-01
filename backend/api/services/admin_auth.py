from typing import Optional

from fastapi import HTTPException

from env_config import get_env, is_placeholder_env_value


def _get_private_password_env(name: str) -> str:
    value = (get_env(name, "") or "").strip()
    if is_placeholder_env_value(value):
        return ""
    return value


ADMIN_PANEL_PASSWORD = _get_private_password_env("ADMIN_PANEL_PASSWORD")


def _verify_admin_panel_password(x_admin_password: Optional[str]) -> None:
    admin_panel_password = (ADMIN_PANEL_PASSWORD or "").strip()
    if (
        not admin_panel_password
        or (x_admin_password or "").strip() != admin_panel_password
    ):
        raise HTTPException(status_code=403, detail="管理员密码错误")
