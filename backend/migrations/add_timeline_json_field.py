"""
为StoryboardShot表添加timeline_json字段
"""
import sqlite3


def run_migration():
    db_path = "story_creator.db"

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 添加 timeline_json 字段（存储AI返回的原始timeline JSON数据）
        try:
            cursor.execute(
                "ALTER TABLE storyboard_shots ADD COLUMN timeline_json TEXT DEFAULT ''"
            )
            print("[OK] Added timeline_json column to storyboard_shots table")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print("[SKIP] timeline_json column already exists")
            else:
                raise

        conn.commit()
        print("[SUCCESS] Migration completed successfully")

    except Exception as e:
        print(f"[ERROR] Migration failed: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
