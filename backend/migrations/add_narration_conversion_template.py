"""
为全局配置表添加 narration_conversion_template（文本转解说剧提示词）
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from database import DATABASE_URL

def migrate():
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        try:
            # 默认提示词内容
            default_template = """1 读取文本文件并理解
 2 把故事改写成解说故事的形式，改写过程如下：
    （1）找到故事的第一主角
    （2）把故事用主角自述的方式讲出来，以第一人称视角讲述
    （3）保留少量精彩的对话即可
    （4）保留一些场景描述
    （5）文字风格要幽默"""

            # 检查是否已存在 narration_conversion_template 配置
            result = conn.execute(text(
                "SELECT COUNT(*) FROM global_settings WHERE key = 'narration_conversion_template'"
            ))
            count = result.scalar()

            if count == 0:
                # 插入默认配置
                conn.execute(text(
                    "INSERT INTO global_settings (key, value, created_at, updated_at) "
                    "VALUES (:key, :value, datetime('now'), datetime('now'))"
                ), {"key": "narration_conversion_template", "value": default_template})
                conn.commit()
                print("[OK] Added narration_conversion_template to global_settings")
            else:
                print("[SKIP] narration_conversion_template already exists in global_settings")

        except Exception as e:
            print(f"[ERROR] Migration failed: {str(e)}")
            conn.rollback()
            raise

if __name__ == "__main__":
    migrate()
