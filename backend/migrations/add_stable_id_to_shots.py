"""
添加 stable_id 字段到 storyboard_shots 表
执行方式：python migrations/add_stable_id_to_shots.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine
import uuid

def upgrade():
    """添加新字段并初始化数据"""
    with engine.connect() as conn:
        try:
            # 1. 添加 stable_id 字段（VARCHAR(36)类型，可为空）
            conn.execute(text("""
                ALTER TABLE storyboard_shots
                ADD COLUMN stable_id VARCHAR(36)
            """))
            print("✓ 添加 stable_id 字段成功")
        except Exception as e:
            print(f"stable_id 字段可能已存在: {e}")
            return

        try:
            # 2. 为现有记录生成 UUID
            result = conn.execute(text("SELECT id FROM storyboard_shots"))
            existing_ids = [row[0] for row in result]

            print(f"正在为 {len(existing_ids)} 条记录生成 UUID...")

            for shot_id in existing_ids:
                new_uuid = str(uuid.uuid4())
                conn.execute(
                    text("UPDATE storyboard_shots SET stable_id = :uuid WHERE id = :id"),
                    {"uuid": new_uuid, "id": shot_id}
                )

            print(f"✓ 已为 {len(existing_ids)} 条记录生成 stable_id")

        except Exception as e:
            print(f"初始化 stable_id 失败: {e}")
            conn.rollback()
            return

        try:
            # 3. 创建索引
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_storyboard_shots_stable_id
                ON storyboard_shots(stable_id)
            """))
            print("✓ 创建索引成功")
        except Exception as e:
            print(f"创建索引失败: {e}")

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
