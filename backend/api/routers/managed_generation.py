from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import models
from api.schemas.episodes import ManagedSessionStatusResponse
from api.services import managed_generation
from auth import get_current_user
from database import get_db


router = APIRouter()


@router.post("/api/episodes/{episode_id}/stop-managed-generation")
async def stop_managed_generation(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return managed_generation.stop_managed_generation(episode_id, user, db)


@router.get("/api/managed-sessions/{session_id}/tasks")
def get_managed_tasks(
    session_id: int,
    status_filter: Optional[str] = None,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return managed_generation.get_managed_tasks(session_id, status_filter, user, db)


@router.get("/api/episodes/{episode_id}/managed-session-status", response_model=ManagedSessionStatusResponse)
def get_managed_session_status(
    episode_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return managed_generation.get_managed_session_status(episode_id, user, db)
