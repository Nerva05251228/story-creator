"""
添加provider字段到storyboard_shots表
执行方式：python migrations/add_provider_to_shots.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """添加provider字段"""
    with engine.connect() as conn:
        try:
            # 添加 provider 字段（TEXT类型，默认值apimart）
            conn.execute(text("""
                ALTER TABLE storyboard_shots
                ADD COLUMN provider TEXT DEFAULT 'apimart'
            """))
            print("✓ 添加 provider 字段成功")

            # 更新所有现有记录的provider为apimart
            conn.execute(text("""
                UPDATE storyboard_shots
                SET provider = 'apimart'
                WHERE provider IS NULL OR provider = ''
            """))
            print("✓ 更新现有记录的provider为apimart")

        except Exception as e:
            print(f"provider 字段可能已存在: {e}")

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
