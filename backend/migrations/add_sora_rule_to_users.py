"""
添加sora_rule字段到users表
执行方式：python migrations/add_sora_rule_to_users.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """添加sora_rule字段"""
    with engine.connect() as conn:
        try:
            # 添加 sora_rule 字段（TEXT类型，默认值"准则：不要出现字幕"）
            conn.execute(text("""
                ALTER TABLE users
                ADD COLUMN sora_rule TEXT DEFAULT '准则：不要出现字幕'
            """))
            print("✓ 添加 sora_rule 字段成功")

            # 更新所有现有记录的sora_rule为默认值
            conn.execute(text("""
                UPDATE users
                SET sora_rule = '准则：不要出现字幕'
                WHERE sora_rule IS NULL OR sora_rule = ''
            """))
            print("✓ 更新现有记录的sora_rule为默认值")

        except Exception as e:
            print(f"sora_rule 字段可能已存在: {e}")

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
