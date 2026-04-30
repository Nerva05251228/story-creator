"""
创建shot_detail_images表（镜头细化图片表）
"""
import sqlite3


def run_migration():
    db_path = "story_creator.db"

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 创建 shot_detail_images 表
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shot_detail_images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shot_id INTEGER NOT NULL,
                    sub_shot_index INTEGER NOT NULL,
                    time_range TEXT DEFAULT '',
                    visual_text TEXT DEFAULT '',
                    audio_text TEXT DEFAULT '',
                    optimized_prompt TEXT DEFAULT '',
                    images_json TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'idle',
                    error_message TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (shot_id) REFERENCES storyboard_shots (id) ON DELETE CASCADE
                )
            """)
            print("[OK] Created shot_detail_images table")
        except sqlite3.OperationalError as e:
            if "already exists" in str(e).lower():
                print("[SKIP] shot_detail_images table already exists")
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
