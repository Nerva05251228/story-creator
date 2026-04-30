"""
将主体库从剧本级别迁移到剧集级别
- 将 story_libraries 表的 script_id 改为 episode_id
- 为每个 episode 创建独立的主体库（复制原剧本的主体库）
执行方式：python migrations/migrate_library_to_episode_level.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """将主体库迁移到剧集级别"""
    with engine.connect() as conn:
        try:
            print("开始迁移主体库...")

            # 1. 创建新的 story_libraries 表（episode_id 替代 script_id）
            print("\n步骤 1: 创建新表 story_libraries_new")
            conn.execute(text("""
                CREATE TABLE story_libraries_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    episode_id INTEGER,
                    name VARCHAR NOT NULL,
                    description VARCHAR DEFAULT '',
                    created_at DATETIME,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (episode_id) REFERENCES episodes(id)
                )
            """))
            print("✓ 新表创建成功")

            # 2. 查询所有剧本和对应的主体库
            print("\n步骤 2: 查询现有数据")
            scripts_with_libs = conn.execute(text("""
                SELECT s.id as script_id, s.user_id, sl.id as library_id, sl.name, sl.description, sl.created_at
                FROM scripts s
                LEFT JOIN story_libraries sl ON sl.script_id = s.id
                ORDER BY s.id
            """)).fetchall()

            print(f"✓ 找到 {len(scripts_with_libs)} 个剧本")

            # 3. 为每个剧本的每个episode创建独立的主体库
            print("\n步骤 3: 为每个episode创建主体库")
            new_library_count = 0
            card_copy_count = 0

            for script_row in scripts_with_libs:
                script_id = script_row[0]
                user_id = script_row[1]
                old_library_id = script_row[2]
                library_name = script_row[3] if script_row[3] else f"主体库"
                library_desc = script_row[4] if script_row[4] else ""
                created_at = script_row[5]

                # 查询该剧本下的所有episode
                episodes = conn.execute(text("""
                    SELECT id, name FROM episodes WHERE script_id = :script_id
                """), {"script_id": script_id}).fetchall()

                if not episodes:
                    print(f"  剧本 {script_id}: 无episode，跳过")
                    continue

                print(f"  剧本 {script_id}: 找到 {len(episodes)} 个episode")

                for episode_row in episodes:
                    episode_id = episode_row[0]
                    episode_name = episode_row[1]

                    # 为每个episode创建新的主体库
                    new_library_name = f"{library_name} - {episode_name}" if library_name else f"{episode_name} - 主体库"

                    conn.execute(text("""
                        INSERT INTO story_libraries_new (user_id, episode_id, name, description, created_at)
                        VALUES (:user_id, :episode_id, :name, :description, :created_at)
                    """), {
                        "user_id": user_id,
                        "episode_id": episode_id,
                        "name": new_library_name,
                        "description": library_desc,
                        "created_at": created_at
                    })
                    new_library_count += 1

                    # 获取新创建的library_id
                    new_library_id = conn.execute(text("SELECT last_insert_rowid()")).fetchone()[0]

                    # 如果原剧本有主体库，复制主体卡片
                    if old_library_id:
                        # 查询原主体库的所有主体卡片
                        old_cards = conn.execute(text("""
                            SELECT name, alias, card_type, ai_prompt, style_template_id, created_at
                            FROM subject_cards
                            WHERE library_id = :library_id
                        """), {"library_id": old_library_id}).fetchall()

                        for card_row in old_cards:
                            # 创建新的主体卡片
                            conn.execute(text("""
                                INSERT INTO subject_cards (library_id, name, alias, card_type, ai_prompt, style_template_id, created_at)
                                VALUES (:library_id, :name, :alias, :card_type, :ai_prompt, :style_template_id, :created_at)
                            """), {
                                "library_id": new_library_id,
                                "name": card_row[0],
                                "alias": card_row[1],
                                "card_type": card_row[2],
                                "ai_prompt": card_row[3],
                                "style_template_id": card_row[4],
                                "created_at": card_row[5]
                            })

                            new_card_id = conn.execute(text("SELECT last_insert_rowid()")).fetchone()[0]

                            # 获取原卡片ID（用于复制图片关联）
                            original_card_id = conn.execute(text("""
                                SELECT id FROM subject_cards
                                WHERE library_id = :library_id
                                AND name = :name
                                AND card_type = :card_type
                                ORDER BY id ASC
                                LIMIT 1
                            """), {
                                "library_id": old_library_id,
                                "name": card_row[0],
                                "card_type": card_row[2]
                            }).fetchone()

                            if original_card_id:
                                original_card_id = original_card_id[0]

                                # 复制 card_images
                                card_images = conn.execute(text("""
                                    SELECT image_path, "order", created_at
                                    FROM card_images
                                    WHERE card_id = :card_id
                                """), {"card_id": original_card_id}).fetchall()

                                for img_row in card_images:
                                    conn.execute(text("""
                                        INSERT INTO card_images (card_id, image_path, "order", created_at)
                                        VALUES (:card_id, :image_path, :order, :created_at)
                                    """), {
                                        "card_id": new_card_id,
                                        "image_path": img_row[0],
                                        "order": img_row[1],
                                        "created_at": img_row[2]
                                    })

                                # 复制 generated_images
                                gen_images = conn.execute(text("""
                                    SELECT image_path, model_name, is_reference, task_id, status, created_at
                                    FROM generated_images
                                    WHERE card_id = :card_id
                                """), {"card_id": original_card_id}).fetchall()

                                for gen_img_row in gen_images:
                                    conn.execute(text("""
                                        INSERT INTO generated_images (card_id, image_path, model_name, is_reference, task_id, status, created_at)
                                        VALUES (:card_id, :image_path, :model_name, :is_reference, :task_id, :status, :created_at)
                                    """), {
                                        "card_id": new_card_id,
                                        "image_path": gen_img_row[0],
                                        "model_name": gen_img_row[1],
                                        "is_reference": gen_img_row[2],
                                        "task_id": gen_img_row[3],
                                        "status": gen_img_row[4],
                                        "created_at": gen_img_row[5]
                                    })

                            card_copy_count += 1

                    print(f"    ✓ Episode {episode_id} ({episode_name}): 创建主体库 {new_library_id}")

            print(f"✓ 共创建 {new_library_count} 个新主体库，复制 {card_copy_count} 个主体卡片")

            # 4. 删除旧表
            print("\n步骤 4: 删除旧表 story_libraries")
            conn.execute(text("DROP TABLE story_libraries"))
            print("✓ 旧表已删除")

            # 5. 重命名新表
            print("\n步骤 5: 重命名新表")
            conn.execute(text("ALTER TABLE story_libraries_new RENAME TO story_libraries"))
            print("✓ 表重命名成功")

            # 6. 创建索引
            print("\n步骤 6: 创建索引")
            conn.execute(text("CREATE INDEX ix_story_libraries_id ON story_libraries (id)"))
            conn.execute(text("CREATE INDEX ix_story_libraries_user_id ON story_libraries (user_id)"))
            conn.execute(text("CREATE INDEX ix_story_libraries_episode_id ON story_libraries (episode_id)"))
            print("✓ 索引创建成功")

            # 提交事务
            conn.commit()
            print("\n" + "="*60)
            print("数据库迁移完成！")
            print(f"主体库已从剧本级别迁移到剧集级别")
            print(f"共创建 {new_library_count} 个独立的剧集主体库")
            print("="*60)

        except Exception as e:
            print(f"\n❌ 迁移失败: {e}")
            conn.rollback()
            raise

if __name__ == "__main__":
    print("="*60)
    print("主体库迁移工具 - 从剧本级别迁移到剧集级别")
    print("="*60)
    upgrade()
