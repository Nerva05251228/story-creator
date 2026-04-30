"""
更新所有镜头的provider为yijia
执行方式：python migrations/update_provider_to_yijia.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """更新provider字段"""
    with engine.connect() as conn:
        try:
            # 更新所有记录的provider为yijia
            result = conn.execute(text("""
                UPDATE storyboard_shots
                SET provider = 'yijia'
                WHERE provider != 'yijia' OR provider IS NULL
            """))
            updated_count = result.rowcount
            print(f"✓ 已更新 {updated_count} 条记录的provider为yijia")

        except Exception as e:
            print(f"更新失败: {e}")
            raise

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
