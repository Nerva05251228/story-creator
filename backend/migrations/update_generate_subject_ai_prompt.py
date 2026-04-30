"""
更新"生成主体绘画提示词"配置（修复花括号问题）
执行方式：python migrations/update_generate_subject_ai_prompt.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """更新提示词配置"""
    with engine.connect() as conn:
        try:
            # 更新配置内容
            conn.execute(text("""
                UPDATE prompt_configs
                SET content = '你是一位专业的AI绘画提示词工程师。请为指定主体生成详细的绘画提示词。

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
                    updated_at = datetime('now')
                WHERE key = 'generate_subject_ai_prompt'
            """))
            print("✓ 更新 generate_subject_ai_prompt 配置成功")

        except Exception as e:
            print(f"更新配置失败: {e}")
            raise

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始更新数据库配置...")
    upgrade()
