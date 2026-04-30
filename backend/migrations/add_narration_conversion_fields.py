"""
为Episode表添加文本转解说剧相关字段
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
            # 添加转换状态字段
            conn.execute(text(
                "ALTER TABLE episodes ADD COLUMN narration_converting INTEGER DEFAULT 0"
            ))
            print("[OK] Added narration_converting column")

            # 添加错误信息字段
            conn.execute(text(
                "ALTER TABLE episodes ADD COLUMN narration_error TEXT DEFAULT ''"
            ))
            print("[OK] Added narration_error column")

            conn.commit()
            print("[OK] Migration completed successfully")

        except Exception as e:
            if "duplicate column name" in str(e).lower():
                print("[SKIP] Columns already exist")
            else:
                print(f"[ERROR] Migration failed: {str(e)}")
                conn.rollback()
                raise

if __name__ == "__main__":
    migrate()
