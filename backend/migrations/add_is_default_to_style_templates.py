"""
添加 is_default 字段到 style_templates 表
执行方式：python migrations/add_is_default_to_style_templates.py
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
            # 添加 is_default 字段（BOOLEAN类型，默认为False）
            conn.execute(text("""
                ALTER TABLE style_templates
                ADD COLUMN is_default BOOLEAN DEFAULT FALSE
            """))
            print("[OK] Added is_default field successfully")
        except Exception as e:
            print(f"[INFO] is_default field may already exist: {e}")

        conn.commit()
        print("\n[DONE] Database migration completed!")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
