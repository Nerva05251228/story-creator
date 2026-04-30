from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from database import get_db
import models

security = HTTPBearer()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    验证token并返回当前用户
    """
    token = credentials.credentials
    user = db.query(models.User).filter(models.User.token == token).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token"
        )

    return user

def verify_library_owner(library_id: int, user: models.User, db: Session):
    """
    验证角色库所有权
    """
    library = db.query(models.StoryLibrary).filter(
        models.StoryLibrary.id == library_id
    ).first()

    if not library:
        raise HTTPException(status_code=404, detail="Story library not found")

    if library.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this library"
        )

    return library
