"""
删除数据库中不再使用的旧 Grok 提示词配置

原因：Grok 准则统一改为使用 GlobalSettings 表中的 grok_rule 配置
需要删除 PromptConfig 表中的两个旧配置：
  - key: "grok_rule"
  - key: "storyboard2_grok_rule"

执行方式：python migrations/cleanup_unused_grok_configs.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
import models

def cleanup_unused_grok_configs():
    db = SessionLocal()
    try:
        # 要删除的旧配置 keys
        unused_keys = ["grok_rule", "storyboard2_grok_rule"]

        for key in unused_keys:
            # 查找该 key 的配置
            configs = db.query(models.PromptConfig).filter(
                models.PromptConfig.key == key
            ).all()

            if not configs:
                print(f"[INFO] 未找到配置: {key}，无需删除")
                continue

            count = len(configs)
            print(f"找到 {count} 条配置: {key}")

            # 删除
            for config in configs:
                print(f"  - 删除 ID={config.id}, name='{config.name}'")
                db.delete(config)

        db.commit()
        print("\n[OK] 已成功删除所有不再使用的 Grok 配置")
        print("[INFO] 现在 Grok 准则统一使用：管理 > 视频生成准则 > Grok生成视频准则")

    except Exception as e:
        print(f"[ERROR] 删除失败: {str(e)}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    print("=" * 80)
    print("清理不再使用的旧 Grok 提示词配置")
    print("=" * 80)
    print()

    confirm = input("确认要删除这些配置吗？(y/n): ")
    if confirm.lower() == 'y':
        cleanup_unused_grok_configs()
    else:
        print("已取消")
