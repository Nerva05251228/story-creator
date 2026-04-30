"""
添加"生成主体绘画提示词"配置到prompt_configs表
执行方式：python migrations/add_generate_subject_ai_prompt.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """添加新的提示词配置"""
    with engine.connect() as conn:
        try:
            # 检查是否已存在
            result = conn.execute(text("""
                SELECT COUNT(*) FROM prompt_configs WHERE key = 'generate_subject_ai_prompt'
            """))
            count = result.fetchone()[0]

            if count > 0:
                print("✓ generate_subject_ai_prompt 配置已存在，跳过")
                return

            # 添加新的提示词配置
            conn.execute(text("""
                INSERT INTO prompt_configs (key, name, description, content, is_active, created_at, updated_at)
                VALUES (
                    'generate_subject_ai_prompt',
                    '生成主体绘画提示词',
                    '为单个主体生成AI绘画提示词与别名',
                    '你是一位专业的AI绘画提示词工程师。请为指定主体生成详细的绘���提示词。

【主体信息】
- 名称：{subject_name}
- 类型：{subject_type}

【分镜表上下文】
{storyboard_context}

【任务】
根据分镜表中该主体出现的场景和描述，生成详细的绘画提示词与别名。
- 角色 ai_prompt：年龄 + 性别 + 表情 + 眼睛 + 发型 + 配饰 + 衣服 + 细节
- 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征
- alias 为简短描述（10-20字）
- ai_prompt需要是纯中文的

【输出格式】
请严格按照以下JSON格式输出：
{{
  "ai_prompt": "详细的绘画提示词（纯中文）",
  "alias": "简短描述（10-20字）"
}}

**重要提示**：
- 角色 ai_prompt：年龄 + 性别 + 表情 + 眼睛 + 发型 + 配饰 + 衣服 + 细节
- 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征
- ai_prompt必须是纯中文
- alias为10-20字的简短描述
- 直接输出JSON对象，不要用markdown代码块包裹',
                    1,
                    datetime('now'),
                    datetime('now')
                )
            """))
            print("✓ 添加 generate_subject_ai_prompt 配置成功")

        except Exception as e:
            print(f"添加配置失败: {e}")
            raise

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
