"""
为Episode表添加opening相关字段
"""
import sqlite3


def run_migration():
    db_path = "story_creator.db"

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 添加 opening_content 字段
        try:
            cursor.execute(
                "ALTER TABLE episodes ADD COLUMN opening_content TEXT DEFAULT ''"
            )
            print("[OK] Added opening_content column to episodes table")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print("[SKIP] opening_content column already exists")
            else:
                raise

        # 添加 opening_generating 字段
        try:
            cursor.execute(
                "ALTER TABLE episodes ADD COLUMN opening_generating BOOLEAN DEFAULT 0"
            )
            print("[OK] Added opening_generating column to episodes table")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print("[SKIP] opening_generating column already exists")
            else:
                raise

        # 添加 opening_error 字段
        try:
            cursor.execute(
                "ALTER TABLE episodes ADD COLUMN opening_error TEXT DEFAULT ''"
            )
            print("[OK] Added opening_error column to episodes table")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print("[SKIP] opening_error column already exists")
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
