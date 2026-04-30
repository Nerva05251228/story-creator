"""
添加分镜表相关字段到episodes表
执行方式：python migrations/add_storyboard_fields.py
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
            # 添加 storyboard_data 字段（TEXT类型，存储JSON）
            conn.execute(text("""
                ALTER TABLE episodes
                ADD COLUMN storyboard_data TEXT DEFAULT ''
            """))
            print("✓ 添加 storyboard_data 字段成功")
        except Exception as e:
            print(f"storyboard_data 字段可能已存在: {e}")

        try:
            # 添加 storyboard_generating 字段（BOOLEAN类型）
            conn.execute(text("""
                ALTER TABLE episodes
                ADD COLUMN storyboard_generating BOOLEAN DEFAULT FALSE
            """))
            print("✓ 添加 storyboard_generating 字段成功")
        except Exception as e:
            print(f"storyboard_generating 字段可能已存在: {e}")

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
