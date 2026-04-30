"""
添加scene_override字段到storyboard_shots表
用于存储用户可编辑的场景描述（独立于主体卡片的ai_prompt）

执行方式：python migrations/add_scene_override_to_shots.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """添加scene_override字段"""
    with engine.connect() as conn:
        try:
            # 添加 scene_override 字段（TEXT类型，默认空字符串）
            conn.execute(text("""
                ALTER TABLE storyboard_shots
                ADD COLUMN scene_override TEXT DEFAULT ''
            """))
            print("✓ 添加 scene_override 字段成功")

            # 更新现有记录的默认值
            conn.execute(text("""
                UPDATE storyboard_shots
                SET scene_override = ''
                WHERE scene_override IS NULL
            """))
            print("✓ 更新现有记录的scene_override为空字符串")

        except Exception as e:
            print(f"scene_override 字段可能已存在: {e}")

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
