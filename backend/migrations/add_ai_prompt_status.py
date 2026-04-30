"""
添加ai_prompt_status字段到subject_cards表
执行方式：python migrations/add_ai_prompt_status.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """添加ai_prompt_status字段"""
    with engine.connect() as conn:
        try:
            # 检查字段是否已存在
            result = conn.execute(text("PRAGMA table_info(subject_cards)"))
            columns = {row[1] for row in result.fetchall()}

            if 'ai_prompt_status' in columns:
                print("✓ ai_prompt_status 字段已存在，跳过")
                return

            # 添加字段
            conn.execute(text("""
                ALTER TABLE subject_cards
                ADD COLUMN ai_prompt_status TEXT DEFAULT NULL
            """))
            print("✓ 添加 ai_prompt_status 字段成功")

        except Exception as e:
            print(f"添加字段失败: {e}")
            raise

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
