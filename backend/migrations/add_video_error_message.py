"""
添加video_error_message字段到storyboard_shots表
执行方式：python migrations/add_video_error_message.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """添加新字段"""
    with engine.connect() as conn:
        try:
            # 添加 video_error_message 字段（TEXT类型）
            conn.execute(text("""
                ALTER TABLE storyboard_shots
                ADD COLUMN video_error_message TEXT DEFAULT ''
            """))
            print("[OK] 添加 video_error_message 字段成功")
        except Exception as e:
            print(f"video_error_message 字段可能已存在: {e}")

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
