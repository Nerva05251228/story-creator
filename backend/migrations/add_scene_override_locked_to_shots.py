"""
添加scene_override_locked字段到storyboard_shots表
用于标记场景描述是否锁定（不再自动填充）

执行方式：python migrations/add_scene_override_locked_to_shots.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """添加scene_override_locked字段"""
    with engine.connect() as conn:
        try:
            # 添加 scene_override_locked 字段（BOOLEAN类型，默认False）
            conn.execute(text("""
                ALTER TABLE storyboard_shots
                ADD COLUMN scene_override_locked BOOLEAN DEFAULT 0 NOT NULL
            """))
            print("[OK] 添加 scene_override_locked 字段成功")

            # 更新现有记录的默认值为False(0)
            conn.execute(text("""
                UPDATE storyboard_shots
                SET scene_override_locked = 0
                WHERE scene_override_locked IS NULL
            """))
            print("[OK] 更新现有记录的scene_override_locked为False")

        except Exception as e:
            print(f"scene_override_locked 字段可能已存在: {e}")

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
