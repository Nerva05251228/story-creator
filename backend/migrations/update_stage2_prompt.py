"""
更新阶段2 prompt模板 - 添加name_mappings支持

问题：之前的阶段2 prompt没有要求AI输出name_mappings，导致：
- "小丸子"无法映射到"李馨儿"
- "废太子"无法映射到"萧景珩"
- 使用字符串包含关系的启发式方法不可靠

解决方案：
- 修改prompt要求AI输出name_mappings字段
- 后端使用AI提供的映射表来更新分镜表中的主体名称
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
import models

NEW_STAGE2_PROMPT = """你是一位专业的AI绘画提示词工程师。以下是用户输入的分镜表：

  【分镜表】（共{total_shots}个镜头）
  {full_storyboard_json}

  【任务】
  1. 主体类型只有两类：角色 / 场景。
  2. 智能识别重复和相似的主体：
     - 如果不同名称明显指向同一角色（昵称、称号、全名等），只保留一个规范名
     - 如果不同名称指向相似场景，可以合并为一个
  3. 生成主体名称映射表（name_mappings）：
     - 将所有被合并的原始名称映射到规范名称
     - 映射表格式为JSON对象，键为原始名称，值为规范名称
  4. 为每个主体生成绘画提示词与别名。
     - 角色 ai_prompt：年龄 + 性别 + 表情 + 眼睛 + 发型 + 配饰 + 衣服 + 细节
     - 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征
     - alias 为简短描述（10-20字）

  【输出格式】
  请严格按照以下JSON格式输出：
  {{
    "subjects": [
      {{
        "name": "规范主体名称",
        "type": "角色 或 场景",
        "ai_prompt": "详细的绘画提示词",
        "alias": "简短描述（10-20字）"
      }}
    ],
    "name_mappings": {{
      "原始名称1": "规范名称1",
      "原始名称2": "规范名称1"
    }}
  }}

  **重要提示**：
  - subjects 数组中只包含去重后的规范主体
  - name_mappings 必须包含所有被合并的原始名称到规范名称的映射
  - 如果某个主体没有被合并，则不需要在 name_mappings 中出现
  - 角色 ai_prompt：年龄 + 性别 + 表情 + 眼睛 + 发型 + 配饰 + 衣服 + 细节
  - 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征
  - 直接输出JSON对象，不要用markdown代码块包裹"""

def update_prompt():
    db = SessionLocal()
    try:
        # 查找阶段2的prompt配置
        config = db.query(models.PromptConfig).filter(
            models.PromptConfig.key == "stage2_refine_shot"
        ).first()

        if not config:
            print("[ERROR] 未找到 stage2_refine_shot 配置")
            return

        print(f"当前配置内容（前200字）：")
        print(config.content[:200])
        print("...")

        # 更新内容
        config.content = NEW_STAGE2_PROMPT
        db.commit()

        print("\n[SUCCESS] 已更新 stage2_refine_shot prompt")
        print("\n新配置内容（前200字）：")
        print(config.content[:200])
        print("...")

    except Exception as e:
        print(f"\n[ERROR] 更新失败: {str(e)}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    print("=" * 80)
    print("更新阶段2 prompt模板 - 添加name_mappings支持")
    print("=" * 80)

    confirm = input("确认要更新阶段2 prompt吗？(y/n): ")
    if confirm.lower() == 'y':
        update_prompt()
    else:
        print("已取消")
