"""
为全局配置表添加 prompt_template 默认值
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
            # 默认提示词模板内容
            default_prompt_template = "视频风格:逐帧动画，2d手绘动漫风格，强调帧间的手绘/精细绘制属性，而非3D渲染/CG动画的光滑感。画面整体呈现传统2D动画的逐帧绘制特征，包括但不限于：帧间微妙的线条变化、色彩的手工涂抹感、阴影的平面化处理。角色动作流畅但保留手绘的自然波动，背景元素展现水彩或厚涂等传统绘画技法的质感。整体视觉效果追求温暖、有机的手工艺术感，避免数字化的过度精确与机械感。"

            # 检查是否已存在 prompt_template 配置
            result = conn.execute(text(
                "SELECT COUNT(*) FROM global_settings WHERE key = 'prompt_template'"
            ))
            count = result.scalar()

            if count == 0:
                # 插入默认配置
                conn.execute(text(
                    "INSERT INTO global_settings (key, value, created_at, updated_at) "
                    "VALUES (:key, :value, datetime('now'), datetime('now'))"
                ), {"key": "prompt_template", "value": default_prompt_template})
                conn.commit()
                print("[OK] Added prompt_template to global_settings")
            else:
                print("[SKIP] prompt_template already exists in global_settings")

        except Exception as e:
            print(f"[ERROR] Migration failed: {str(e)}")
            conn.rollback()
            raise

if __name__ == "__main__":
    migrate()
