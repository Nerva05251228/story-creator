"""
将所有旧镜头的provider更新为apimart（从yijia或NULL）
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from database import DATABASE_URL

def migrate():
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        try:
            # 更新所有provider为NULL或'yijia'的镜头为'apimart'
            result = conn.execute(text(
                "UPDATE storyboard_shots "
                "SET provider = 'apimart' "
                "WHERE provider IS NULL OR provider = 'yijia'"
            ))

            updated_count = result.rowcount
            conn.commit()

            print(f"[OK] Updated {updated_count} shots to use provider 'apimart'")

        except Exception as e:
            print(f"[ERROR] Migration failed: {str(e)}")
            conn.rollback()
            raise

if __name__ == "__main__":
    migrate()
