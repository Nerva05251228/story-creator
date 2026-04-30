"""
创建托管视频生成相关表
执行方式：python migrations/create_managed_generation_tables.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """创建托管视频生成相关表"""
    with engine.connect() as conn:
        try:
            # 创建托管会话表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS managed_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'running',
                    total_shots INTEGER DEFAULT 0,
                    completed_shots INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (episode_id) REFERENCES episodes(id)
                )
            """))
            print("✓ 创建 managed_sessions 表成功")
        except Exception as e:
            print(f"managed_sessions 表可能已存在: {e}")

        try:
            # 创建托管任务表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS managed_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    shot_id INTEGER NOT NULL,
                    shot_stable_id TEXT NOT NULL,
                    video_path TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    error_message TEXT DEFAULT '',
                    task_id TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES managed_sessions(id),
                    FOREIGN KEY (shot_id) REFERENCES storyboard_shots(id)
                )
            """))
            print("✓ 创建 managed_tasks 表成功")
        except Exception as e:
            print(f"managed_tasks 表可能已存在: {e}")

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
