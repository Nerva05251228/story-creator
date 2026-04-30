"""
添加简单分镜和详细分镜功能
1. 添加两个新的提示词配置：simple_storyboard_shot_division 和 detailed_storyboard_content_analysis
2. 为episodes表添加简单分镜相关字段
3. 将原有storyboard字段改名为detailed_storyboard

执行方式：python migrations/add_simple_detailed_storyboard.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def upgrade():
    """添加新功能"""
    with engine.connect() as conn:
        # 1. 添加简单分镜提示词
        try:
            result = conn.execute(text("""
                SELECT COUNT(*) FROM prompt_configs WHERE key = 'simple_storyboard_shot_division'
            """))
            count = result.fetchone()[0]

            if count == 0:
                conn.execute(text("""
                    INSERT INTO prompt_configs (key, name, description, content, is_active, created_at, updated_at)
                    VALUES (
                        'simple_storyboard_shot_division',
                        '简单分镜：镜头划分',
                        '将剧本文本按分段划分为多个镜头，仅输出镜号和原文片段',
                        '你是一位专业的影视分镜师。我将给你一段剧本内容（可能包含多个分段），请将其拆分为多个镜头。

【剧本内容】
{content}

【分镜拆分要求】
每个镜头必须输出以下内容：
- 镜号
- 原剧本段落

【分镜规则（强约束）】
1. 每个分镜包含 3–4 个短句。
2. 这些短句最好都是在同一个场景下发生的。
3. 如果剧本中已经明确给了分段标记（如"分段1："、"分段2："），则按照提供的分段进行划分。

严禁以下情况：
1. 一个短句单独成为一个镜头。

【长句例外规则】
如果一句话或一段台词超过 30 个字，
允许该句话独立成为一个分镜。
但仍然必须整体作为一个镜头，不得再拆分为多个单句镜头。

【核心总原则（非常重要）】
- 所有镜头的 original_text 拼接起来应该等于输入的完整剧本
- original_text 必须完整保留原文，不要修改、总结或省略
- 确保镜头之间连贯流畅，不遗漏任何原文内容
- 镜头编号从1开始连续递增

【输出格式】
请严格按照以下JSON格式输出：
```json
{
  "shots": [
    {
      "shot_number": 1,
      "original_text": "该镜头对应的原剧本段落（完整原文）"
    },
    {
      "shot_number": 2,
      "original_text": "该镜头对应的原剧本段落（完整原文）"
    }
  ]
}
```

**注意**：
- 只输出镜号和原文片段，不要输出主体、对白等其他信息
- 直接输出JSON对象，不要用markdown代码块包裹

请开始分析。',
                        1,
                        datetime('now'),
                        datetime('now')
                    )
                """))
                print("✓ 添加 simple_storyboard_shot_division 配置成功")
            else:
                print("✓ simple_storyboard_shot_division 配置已存在，跳过")

        except Exception as e:
            print(f"添加 simple_storyboard_shot_division 配置失败: {e}")
            raise

        # 2. 添加详细分镜提示词
        try:
            result = conn.execute(text("""
                SELECT COUNT(*) FROM prompt_configs WHERE key = 'detailed_storyboard_content_analysis'
            """))
            count = result.fetchone()[0]

            if count == 0:
                conn.execute(text("""
                    INSERT INTO prompt_configs (key, name, description, content, is_active, created_at, updated_at)
                    VALUES (
                        'detailed_storyboard_content_analysis',
                        '详细分镜：内容分析',
                        '对已划分的镜头进行详细内容分析，提取主体、对白、旁白等信息',
                        '你是一位专业的影视分镜师。我将给你一组已经划分好的镜头（每个镜头包含镜号和原剧本段落），请为每个镜头提取详细的分镜信息。

【镜头列表】
{shots_content}

【分镜拆分要求】
每个镜头必须输出以下内容：
- 镜号（保持与输入一致）
- 主体（必须包含：角色 / 场景）
- 语音内容
- 原剧本段落（保持与输入一致）

【主体类型限制】
主体类型只能是两类：角色、场景。
不出现道具、物品、情绪、抽象概念作为主体。

【核心总原则（非常重要）】
严格保持主角名字前后一致
当主体不明确时，优先使用主角名字。
只能严格使用剧本原文中的角色名字，如果角色名找不到，则用"女主角"，"男主角"。
不允许根据叙事常识、类型经验或"看起来应该有谁"来补人物。
若原文未出现人物姓名，可用唯一指代的身份代替名字（如"皇帝""老板""母亲"等）。

【关于语音内容的处理】
- **旁白**：第一人称的内心独白或解说
  - 使用具体人名作为说话人（如：李馨儿、萧景珩）
  - 第三方视角 → 使用"旁白"
  - 需要标注性别（女/男/中性）
  - 需要标注情绪（如：平静、悲伤、愤怒、带着哭腔等）
  - 角色内心的想法则标注（心里话、画外音等）

- **对话**：角色之间的对话
  - 按对话顺序列出，每句包含：说话人、对方、性别、情绪、对话内容
  - 示例：李馨儿对萧景珩说（带着哭腔）：你当真要我走？

【输出格式】
请严格按照以下JSON格式输出：
```json
{
  "shots": [
    {
      "shot_number": 1,
      "subjects": [
        {"name": "角色名", "type": "角色"},
        {"name": "场景名", "type": "场景"}
      ],
      "original_text": "该镜头对应的原文片段",
      "voice_type": "narration 或 dialogue 或 none",
      "narration": {
        "speaker": "具体角色名 或 旁白",
        "gender": "女 或 男 或 中性",
        "emotion": "情绪描述",
        "text": "旁白内容"
      },
      "dialogue": [
        {
          "speaker": "说话人名字",
          "target": "对方名字（如果是对某人说）或 null",
          "gender": "女 或 男",
          "emotion": "情绪描述",
          "text": "对话内容"
        }
      ]
    }
  ]
}
```

**注意**：
- 如果 voice_type 为 "narration"，则 dialogue 为 null
- 如果 voice_type 为 "dialogue"，则 narration 为 null
- 如果 voice_type 为 "none"（无语音），则 narration 和 dialogue 都为 null
- dialogue 数组可以包含多轮对话
- target 如果不是对特定人物说话（如自言自语、对众人说），则为 null
- 保持 original_text 与输入完全一致，不要修改
- 保持 shot_number 与输入完全一致
- 直接输出JSON对象，不要用markdown代码块包裹

请开始分析。',
                        1,
                        datetime('now'),
                        datetime('now')
                    )
                """))
                print("✓ 添加 detailed_storyboard_content_analysis 配置成功")
            else:
                print("✓ detailed_storyboard_content_analysis 配置已存在，跳过")

        except Exception as e:
            print(f"添加 detailed_storyboard_content_analysis 配置失败: {e}")
            raise

        # 3. 为episodes表添加batch_size字段
        try:
            conn.execute(text("""
                ALTER TABLE episodes
                ADD COLUMN batch_size INTEGER DEFAULT 500
            """))
            print("✓ 添加 batch_size 字段成功")
        except Exception as e:
            print(f"batch_size 字段可能已存在: {e}")

        # 4. 为episodes表添加simple_storyboard_data字段
        try:
            conn.execute(text("""
                ALTER TABLE episodes
                ADD COLUMN simple_storyboard_data TEXT DEFAULT ''
            """))
            print("✓ 添加 simple_storyboard_data 字段成功")
        except Exception as e:
            print(f"simple_storyboard_data 字段可能已存在: {e}")

        # 5. 为episodes表添加simple_storyboard_generating字段
        try:
            conn.execute(text("""
                ALTER TABLE episodes
                ADD COLUMN simple_storyboard_generating BOOLEAN DEFAULT FALSE
            """))
            print("✓ 添加 simple_storyboard_generating 字段成功")
        except Exception as e:
            print(f"simple_storyboard_generating 字段可能已存在: {e}")

        # 6. 为episodes表添加simple_storyboard_error字段
        try:
            conn.execute(text("""
                ALTER TABLE episodes
                ADD COLUMN simple_storyboard_error TEXT DEFAULT ''
            """))
            print("✓ 添加 simple_storyboard_error 字段成功")
        except Exception as e:
            print(f"simple_storyboard_error 字段可能已存在: {e}")

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
