"""
将所有镜头的视频时长默认值更新为15秒
执行方式：python migrations/update_shot_duration_to_15.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """将所有 duration=10 的镜头更新为 duration=15"""
    with engine.connect() as conn:
        try:
            # 查询有多少镜头需要更新
            result = conn.execute(text("""
                SELECT COUNT(*) as count FROM storyboard_shots WHERE duration = 10
            """))
            count = result.fetchone()[0]
            print(f"[INFO] Found {count} shots with duration=10")

            if count == 0:
                print("[INFO] No shots to update")
                conn.commit()
                return

            # 更新所有 duration=10 的镜头为 15
            conn.execute(text("""
                UPDATE storyboard_shots
                SET duration = 15
                WHERE duration = 10
            """))

            conn.commit()
            print(f"[OK] Successfully updated {count} shots to duration=15")

        except Exception as e:
            print(f"[ERROR] Failed to update: {e}")
            conn.rollback()
            raise

        print("\n[DONE] Database migration completed!")

if __name__ == "__main__":
    print("Starting database migration: Update shot duration to 15 seconds...")
    upgrade()
