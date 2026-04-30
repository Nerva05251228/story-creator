from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import models
from database import get_db


router = APIRouter(prefix="/api/public")


@router.get("/users")
async def get_all_users(db: Session = Depends(get_db)):
    """Return users available to the public story-library browser."""
    users = db.query(models.User).all()

    result = []
    for user in users:
        library_count = db.query(models.StoryLibrary).filter(
            models.StoryLibrary.user_id == user.id
        ).count()

        total_cards = db.query(models.SubjectCard).join(models.StoryLibrary).filter(
            models.StoryLibrary.user_id == user.id
        ).count()

        result.append({
            "id": user.id,
            "username": user.username,
            "library_count": library_count,
            "total_cards": total_cards,
        })

    return result
