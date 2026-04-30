"""
修复时长配置模板中的花括号转义问题
将JSON示例中的单花括号改成双花括号

执行方式：python fix_template_braces.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal
import models

def fix_templates():
    """修复所有模板的花括号问题"""
    db = SessionLocal()
    try:
        templates = db.query(models.ShotDurationTemplate).all()

        for template in templates:
            # 修复simple_storyboard_rule
            original_simple = template.simple_storyboard_rule

            # 简单替换策略：在```json和```之间的内容中，将单花括号替换为双花括号
            # 但保留 {content} 占位符不变
            if '```json' in original_simple and '```' in original_simple:
                parts = original_simple.split('```json', 1)
                if len(parts) == 2:
                    before_json = parts[0]
                    json_and_after = parts[1].split('```', 1)
                    if len(json_and_after) == 2:
                        json_example = json_and_after[0]
                        after_json = json_and_after[1]

                        # 在JSON示例中替换花括号（保留{content}）
                        fixed_json = json_example.replace('{', '{{').replace('}', '}}')
                        # 恢复 {content} 占位符
                        fixed_json = fixed_json.replace('{{content}}', '{content}')

                        template.simple_storyboard_rule = before_json + '```json' + fixed_json + '```' + after_json
                        print(f"Fixed simple_storyboard_rule for duration {template.duration}s")

        db.commit()
        print("\nAll templates fixed successfully!")

    except Exception as e:
        print(f"Fix failed: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    print("Fixing template braces...\n")
    fix_templates()
