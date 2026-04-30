"""
添加CDN上传相关字段到storyboard_shots表
执行方式：python migrations/add_cdn_uploaded_fields.py
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
            # 添加 cdn_uploaded 字段（BOOLEAN类型）
            conn.execute(text("""
                ALTER TABLE storyboard_shots
                ADD COLUMN cdn_uploaded BOOLEAN DEFAULT FALSE
            """))
            print("✓ 添加 cdn_uploaded 字段成功")
        except Exception as e:
            print(f"cdn_uploaded 字段可能已存在: {e}")

        try:
            # 添加 video_submitted_at 字段（DATETIME类型）
            conn.execute(text("""
                ALTER TABLE storyboard_shots
                ADD COLUMN video_submitted_at DATETIME
            """))
            print("✓ 添加 video_submitted_at 字段成功")
        except Exception as e:
            print(f"video_submitted_at 字段可能已存在: {e}")

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
