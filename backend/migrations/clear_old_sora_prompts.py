"""
清理旧的 sora_prompt 数据

问题：之前的版本会自动将完整内容（视频风格+场景+表格）保存到 sora_prompt 字段
现在：sora_prompt 应该只在用户手动编辑时才保存，后端生成时只填充 storyboard_video_prompt

这个脚本会清空所有 sora_prompt 字段，让系统回退到只显示表格内容
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
import models

def clear_sora_prompts():
    db = SessionLocal()
    try:
        # 查询所有有 sora_prompt 内容的镜头
        shots_with_prompt = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.sora_prompt != "",
            models.StoryboardShot.sora_prompt.isnot(None)
        ).all()

        count = len(shots_with_prompt)
        print(f"找到 {count} 个镜头有 sora_prompt 内容")

        if count == 0:
            print("无需清理")
            return

        # 清空所有 sora_prompt
        for shot in shots_with_prompt:
            shot.sora_prompt = ""
            print(f"清空镜头 {shot.id} (镜号 {shot.shot_number}) 的 sora_prompt")

        db.commit()
        print(f"\n成功清空 {count} 个镜头的 sora_prompt 字段")
        print("现在所有镜头将显示纯表格内容（storyboard_video_prompt）")

    except Exception as e:
        print(f"清理失败: {str(e)}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    print("=" * 80)
    print("清理旧的 sora_prompt 数据")
    print("=" * 80)

    confirm = input("确认要清空所有 sora_prompt 字段吗？(y/n): ")
    if confirm.lower() == 'y':
        clear_sora_prompts()
    else:
        print("已取消")
