"""
添加 episodes.storyboard_error 字段

用于存储分镜表生成过程中的错误信息
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """添加 storyboard_error 字段"""
    try:
        with engine.begin() as conn:
            # 检查字段是否已存在
            result = conn.execute(text("PRAGMA table_info(episodes)"))
            columns = {row[1] for row in result.fetchall()}

            if "storyboard_error" not in columns:
                conn.execute(
                    text("ALTER TABLE episodes ADD COLUMN storyboard_error TEXT DEFAULT ''")
                )
                print("OK: Added episodes.storyboard_error field")
            else:
                print("OK: episodes.storyboard_error field already exists")

    except Exception as e:
        print(f"ERROR: Migration failed: {str(e)}")
        raise

if __name__ == "__main__":
    upgrade()
    print("Migration completed!")
