"""
将 sora_rule 从 users 表迁移到 global_settings 表
执行方式：python migrations/migrate_sora_rule_to_global.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine
from datetime import datetime

def upgrade():
    """迁移 sora_rule 到全局配置"""
    with engine.connect() as conn:
        # 1. 创建 global_settings 表
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS global_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    value TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
            print("✓ 创建 global_settings 表成功")
        except Exception as e:
            print(f"global_settings 表可能已存在: {e}")

        # 2. 从 user_id=1 读取 sora_rule
        try:
            result = conn.execute(text("""
                SELECT sora_rule FROM users WHERE id = 1
            """))
            row = result.fetchone()

            if row and row[0]:
                sora_rule_value = row[0]
                print(f"✓ 读取到 user_id=1 的 sora_rule: {sora_rule_value}")
            else:
                sora_rule_value = "准则：不要出现字幕"
                print(f"⚠ user_id=1 没有 sora_rule，使用默认值")

        except Exception as e:
            print(f"读取 sora_rule 失败，使用默认值: {e}")
            sora_rule_value = "准则：不要出现字幕"

        # 3. 插入或更新 global_settings 表
        try:
            # 先检查是否已存在
            result = conn.execute(text("""
                SELECT id FROM global_settings WHERE key = 'sora_rule'
            """))
            exists = result.fetchone()

            if exists:
                # 更新现有记录
                conn.execute(text("""
                    UPDATE global_settings
                    SET value = :value, updated_at = :updated_at
                    WHERE key = 'sora_rule'
                """), {"value": sora_rule_value, "updated_at": datetime.utcnow()})
                print(f"✓ 更新 global_settings 中的 sora_rule")
            else:
                # 插入新记录
                conn.execute(text("""
                    INSERT INTO global_settings (key, value, created_at, updated_at)
                    VALUES ('sora_rule', :value, :created_at, :updated_at)
                """), {
                    "value": sora_rule_value,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                })
                print(f"✓ 插入 global_settings 中的 sora_rule")

        except Exception as e:
            print(f"插入/更新 global_settings 失败: {e}")

        conn.commit()
        print("\n数据库迁移完成！")
        print(f"全局 sora_rule 已设置为: {sora_rule_value}")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
