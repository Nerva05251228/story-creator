"""
添加分镜图相关字段到storyboard_shots表
执行方式：python migrations/add_storyboard_image_fields.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """添加分镜图相关字段"""
    with engine.connect() as conn:
        try:
            # 添加 storyboard_image_path 字段
            conn.execute(text("""
                ALTER TABLE storyboard_shots
                ADD COLUMN storyboard_image_path TEXT DEFAULT ''
            """))
            print("✓ 添加 storyboard_image_path 字段成功")
        except Exception as e:
            print(f"storyboard_image_path 字段可能已存在: {e}")

        try:
            # 添加 storyboard_image_status 字段
            conn.execute(text("""
                ALTER TABLE storyboard_shots
                ADD COLUMN storyboard_image_status TEXT DEFAULT 'idle'
            """))
            print("✓ 添加 storyboard_image_status 字段成功")
        except Exception as e:
            print(f"storyboard_image_status 字段可能已存在: {e}")

        try:
            # 添加 storyboard_image_task_id 字段
            conn.execute(text("""
                ALTER TABLE storyboard_shots
                ADD COLUMN storyboard_image_task_id TEXT DEFAULT ''
            """))
            print("✓ 添加 storyboard_image_task_id 字段成功")
        except Exception as e:
            print(f"storyboard_image_task_id 字段可能已存在: {e}")

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
