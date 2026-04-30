"""
将 style_templates 表改为全局配置（移除 user_id）
执行方式：python migrations/migrate_style_templates_to_global.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """将 style_templates 改为全局配置"""
    with engine.connect() as conn:
        # SQLite 不支持直接删除列，需要重建表
        try:
            # 1. 创建新的临时表（没有 user_id）
            conn.execute(text("""
                CREATE TABLE style_templates_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR NOT NULL,
                    content TEXT NOT NULL,
                    is_default BOOLEAN DEFAULT FALSE,
                    created_at DATETIME
                )
            """))
            print("OK 创建新表 style_templates_new")

            # 2. 复制数据（去除 user_id）
            conn.execute(text("""
                INSERT INTO style_templates_new (id, name, content, is_default, created_at)
                SELECT id, name, content, is_default, created_at
                FROM style_templates
            """))
            print("OK 复制数据到新表")

            # 3. 删除旧表
            conn.execute(text("DROP TABLE style_templates"))
            print("OK 删除旧表 style_templates")

            # 4. 重命名新表
            conn.execute(text("ALTER TABLE style_templates_new RENAME TO style_templates"))
            print("OK 重命名新表为 style_templates")

            # 5. 重建索引
            conn.execute(text("CREATE INDEX ix_style_templates_id ON style_templates (id)"))
            print("OK 重建索引")

            conn.commit()
            print("\n数据库迁移完成！")
            print("绘图风格模板已改为全局配置（移除 user_id）")

        except Exception as e:
            print(f"迁移失败: {e}")
            conn.rollback()
            raise

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
