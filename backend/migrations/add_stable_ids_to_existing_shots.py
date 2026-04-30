"""
为已存在的镜头生成stable_id
"""
import sqlite3
import uuid


def run_migration():
    db_path = "story_creator.db"

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 查找所有没有stable_id的镜头
        cursor.execute("""
            SELECT id FROM storyboard_shots
            WHERE stable_id IS NULL OR stable_id = ''
        """)

        shots_without_stable_id = cursor.fetchall()

        if not shots_without_stable_id:
            print("[OK] 所有镜头都已有stable_id")
            return

        print(f"[INFO] 找到 {len(shots_without_stable_id)} 个没有stable_id的镜头")

        # 为每个镜头生成stable_id
        for (shot_id,) in shots_without_stable_id:
            new_stable_id = str(uuid.uuid4())
            cursor.execute("""
                UPDATE storyboard_shots
                SET stable_id = ?
                WHERE id = ?
            """, (new_stable_id, shot_id))
            print(f"[OK] 镜头ID {shot_id} 已生成stable_id: {new_stable_id}")

        conn.commit()
        print(f"[SUCCESS] 已为 {len(shots_without_stable_id)} 个镜头生成stable_id")

    except Exception as e:
        print(f"[ERROR] 迁移失败: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_migration()
