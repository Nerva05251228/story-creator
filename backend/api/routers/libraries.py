import os
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
from api.schemas.story_library import StoryLibraryCreate, StoryLibraryResponse
from auth import get_current_user, verify_library_owner
from database import get_db


router = APIRouter()


@router.post("/api/libraries", response_model=StoryLibraryResponse)
async def create_library(
    library: StoryLibraryCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    new_library = models.StoryLibrary(
        user_id=user.id,
        name=library.name,
        description=library.description,
    )
    db.add(new_library)
    db.commit()
    db.refresh(new_library)
    return new_library


@router.get("/api/libraries/my", response_model=List[StoryLibraryResponse])
async def get_my_libraries(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    libraries = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.user_id == user.id
    ).order_by(models.StoryLibrary.created_at.desc()).all()
    return libraries


@router.get("/api/libraries/{library_id}", response_model=StoryLibraryResponse)
async def get_library(
    library_id: int,
    db: Session = Depends(get_db),
):
    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.id == library_id
    ).first()

    if not library:
        raise HTTPException(status_code=404, detail="Library not found")

    return library


@router.put("/api/libraries/{library_id}", response_model=StoryLibraryResponse)
async def update_library(
    library_id: int,
    library_data: StoryLibraryCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    library = verify_library_owner(library_id, user, db)

    library.name = library_data.name
    library.description = library_data.description

    db.commit()
    db.refresh(library)
    return library


@router.delete("/api/libraries/{library_id}")
async def delete_library(
    library_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    library = verify_library_owner(library_id, user, db)

    for card in library.subject_cards:
        for image in card.images:
            if os.path.exists(image.image_path):
                os.remove(image.image_path)

    db.delete(library)
    db.commit()
    return {"message": "Library deleted successfully"}
