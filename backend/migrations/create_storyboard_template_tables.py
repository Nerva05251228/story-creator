"""
创建分镜图模板表
执行方式：python migrations/create_storyboard_template_tables.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """创建分镜图模板表"""
    with engine.connect() as conn:
        try:
            # 创建分镜图绘图要求模板表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS storyboard_requirement_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    is_default BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            print("✓ 创建 storyboard_requirement_templates 表成功")
        except Exception as e:
            print(f"storyboard_requirement_templates 表可能已存在: {e}")

        try:
            # 创建分镜图绘画风格模板表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS storyboard_style_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    is_default BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            print("✓ 创建 storyboard_style_templates 表成功")
        except Exception as e:
            print(f"storyboard_style_templates 表可能已存在: {e}")

        # 插入默认模板
        try:
            # 检查是否已有默认模板
            result = conn.execute(text("SELECT COUNT(*) as cnt FROM storyboard_requirement_templates"))
            count = result.fetchone()[0]

            if count == 0:
                conn.execute(text("""
                    INSERT INTO storyboard_requirement_templates (name, content, is_default)
                    VALUES ('默认绘图要求', '生成一个1*3的分格分镜图（图片上不要有文字），彩色，每个分格都是一个16：9的横板图', TRUE)
                """))
                print("✓ 插入默认绘图要求模板")
        except Exception as e:
            print(f"插入默认绘图要求模板失败: {e}")

        try:
            # 检查是否已有默认模板
            result = conn.execute(text("SELECT COUNT(*) as cnt FROM storyboard_style_templates"))
            count = result.fetchone()[0]

            if count == 0:
                conn.execute(text("""
                    INSERT INTO storyboard_style_templates (name, content, is_default)
                    VALUES ('默认绘画风格', '2D漫画，赛璐璐风格，低饱和度，古风，不要任何文字。', TRUE)
                """))
                print("✓ 插入默认绘画风格模板")
        except Exception as e:
            print(f"插入默认绘画风格模板失败: {e}")

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
