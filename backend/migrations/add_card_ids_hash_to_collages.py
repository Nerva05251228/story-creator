"""
添加card_ids_hash字段到shot_collages表
用于存储拼图对应的主体ID组合，实现拼图复用

执行方式：python migrations/add_card_ids_hash_to_collages.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """添加card_ids_hash字段"""
    with engine.connect() as conn:
        try:
            # 添加 card_ids_hash 字段（TEXT类型，可为空）
            conn.execute(text("""
                ALTER TABLE shot_collages
                ADD COLUMN card_ids_hash TEXT
            """))
            print("✓ 添加 card_ids_hash 字段成功")

        except Exception as e:
            print(f"card_ids_hash 字段可能已存在: {e}")

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
