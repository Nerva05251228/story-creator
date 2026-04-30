"""
为 managed_sessions 表添加 provider 字段
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
            # 检查列是否已存在
            result = conn.execute(text("PRAGMA table_info(managed_sessions)"))
            columns = [row[1] for row in result]

            if "provider" not in columns:
                # 添加 provider 列，默认值为 yijia
                conn.execute(text(
                    "ALTER TABLE managed_sessions ADD COLUMN provider VARCHAR DEFAULT 'yijia'"
                ))
                conn.commit()
                print("[OK] Added provider column to managed_sessions table")
            else:
                print("[SKIP] provider column already exists")

        except Exception as e:
            print(f"[ERROR] Migration failed: {str(e)}")
            conn.rollback()
            raise

if __name__ == "__main__":
    migrate()
