"""
为Script表添加narration_template字段
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
            # 添加剧本级别的解说剧转换提示词模板字段
            conn.execute(text(
                "ALTER TABLE scripts ADD COLUMN narration_template TEXT DEFAULT ''"
            ))
            print("[OK] Added narration_template column to scripts table")

            conn.commit()
            print("[OK] Migration completed successfully")

        except Exception as e:
            if "duplicate column name" in str(e).lower():
                print("[SKIP] Column already exists")
            else:
                print(f"[ERROR] Migration failed: {str(e)}")
                conn.rollback()
                raise

if __name__ == "__main__":
    migrate()
