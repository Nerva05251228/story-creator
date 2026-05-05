from fastapi import HTTPException
from sqlalchemy.orm import Session

import models


def verify_episode_permission(episode_id: int, user: models.User, db: Session) -> models.Episode:
    episode = db.query(models.Episode).filter(models.Episode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="片段不存在")

    script = db.query(models.Script).filter(models.Script.id == episode.script_id).first()
    if not script or script.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权限")

    return episode


def get_storyboard2_sub_shot_with_permission(sub_shot_id: int, user: models.User, db: Session):
    sub_shot = db.query(models.Storyboard2SubShot).filter(
        models.Storyboard2SubShot.id == sub_shot_id
    ).first()
    if not sub_shot:
        raise HTTPException(status_code=404, detail="分镜不存在")

    storyboard2_shot = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.id == sub_shot.storyboard2_shot_id
    ).first()
    if not storyboard2_shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    verify_episode_permission(storyboard2_shot.episode_id, user, db)
    return sub_shot, storyboard2_shot


def get_storyboard2_shot_with_permission(storyboard2_shot_id: int, user: models.User, db: Session):
    storyboard2_shot = db.query(models.Storyboard2Shot).filter(
        models.Storyboard2Shot.id == storyboard2_shot_id
    ).first()
    if not storyboard2_shot:
        raise HTTPException(status_code=404, detail="镜头不存在")

    verify_episode_permission(storyboard2_shot.episode_id, user, db)
    return storyboard2_shot
