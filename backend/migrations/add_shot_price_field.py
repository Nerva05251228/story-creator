"""
为StoryboardShot表添加price字段
"""
import sqlite3


def run_migration():
    db_path = "story_creator.db"

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 添加 price 字段（单位：分，如80表示0.8元）
        try:
            cursor.execute(
                "ALTER TABLE storyboard_shots ADD COLUMN price INTEGER DEFAULT 0"
            )
            print("[OK] Added price column to storyboard_shots table")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print("[SKIP] price column already exists")
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
