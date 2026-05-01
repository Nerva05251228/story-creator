from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    username: str
    old_password: str
    new_password: str


class PasswordVerifyRequest(BaseModel):
    password: str
