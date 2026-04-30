from datetime import datetime

from pydantic import BaseModel


class UserResponse(BaseModel):
    id: int
    username: str
    created_at: datetime

    class Config:
        from_attributes = True


class StoryLibraryCreate(BaseModel):
    name: str
    description: str = ""


class StoryLibraryResponse(BaseModel):
    id: int
    user_id: int
    name: str
    description: str
    created_at: datetime
    owner: UserResponse

    class Config:
        from_attributes = True
