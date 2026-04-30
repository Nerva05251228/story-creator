"""
创建爆款库相关表
"""
from database import engine, Base
from models import HitDrama, HitDramaEditHistory

def migrate():
    print("Creating hit_dramas and hit_drama_edit_history tables...")
    Base.metadata.create_all(bind=engine, tables=[
        HitDrama.__table__,
        HitDramaEditHistory.__table__
    ])
    print("Tables created successfully!")

if __name__ == "__main__":
    migrate()
