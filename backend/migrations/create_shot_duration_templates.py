"""
创建时长配置模板表和初始化四个预设模板（6s, 10s, 15s, 25s）

执行方式：python migrations/create_shot_duration_templates.py
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine
from storyboard_prompt_templates import build_large_shot_prompt_rule

# 四个时长的提示词配置
TEMPLATES_DATA = [
    {
        "duration": 6,
        "shot_count_min": 1,
        "shot_count_max": 2,
        "time_segments": 2,
        "simple_storyboard_rule": """你是一位专业的影视分镜师。我将给你一段剧本内容（可能包含多个分段），请将其拆分为多个镜头。

【剧本内容】
{content}

【分镜拆分要求】
每个镜头必须输出以下内容：
- 镜号
- 原剧本段落

【分镜规则（强约束）】
1. 每个分镜包含 1–2 个短句。
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
{{
  "shots": [
    {{
      "shot_number": 1,
      "original_text": "该镜头对应的原剧本段落（完整原文）"
    }},
    {{
      "shot_number": 2,
      "original_text": "该镜头对应的原剧本段落（完整原文）"
    }}
  ]
}}
```

**注意**：
- 只输出镜号和原文片段，不要输出主体、对白等其他信息
- 直接输出JSON对象，不要用markdown代码块包裹

请开始分析。""",
        "video_prompt_rule": """原剧本段落：
{script_excerpt}

出镜主体：
{subject_text}

输出 JSON，格式如下：
{{
  "timeline": [
    {{
      "time": "00s-03s",
      "visual": "[镜头1] [中景] 忠实描述文案动作 + [时间+远景描述+近景描述，场景描述要详细具体，至少20个字时间+远景描述+近景描述，场景描述要详细具体，至少20个字]",
      "audio": "[角色] 说/旁白："[原台词]""
    }},
   {{
      "time": "03s-06s",
      "visual": "[ [镜头2] [特写] 面部微表情(呼吸/眼神) + [时间+远景描述+近景描述，场景描述要详细具体，至少20个字]",
      "audio": " | (SFX音效) |"
    }}
  ]
}}

要求：
1. 时长总计 {safe_duration} 秒，分为2个时间段
2. time字段格式：00s-03s、03s-06s（连续不重叠，覆盖完整时长）
3. visual字段包含：
   - 镜头类型：如[推镜][拉镜][摇镜][跟镜][正反打][切镜][固定镜头]等（主要使用固定镜头）
   - 景别：如[远景][全景][中景][近景][特写][大特写]等（自由发挥）
   - 画面描述：忠实描述原剧本段落的动作和情绪，不添加额外��容
4. audio字段包含：
   - 角色台词：格式为 [角色名] 说/旁白："台词内容"（严格遵守原文台词）
   - 音效标记：格式为 (SFX:具体音效描述)
   - 如果既有台词又有音效，用顿号分隔：[角色]说/旁白："台词"、(SFX:音效)

5. 旁白的时候不要加"说"字
6. 文案清洗： * 带引号的 "..." -> 台词 (一字不改嵌入)。
7. 忠实还原 (Strict Adherence):
  - 严禁加戏： 绝对禁止添加文案中没有的攻击、破坏、逃跑等大幅度剧情动作，除非文案明确写了。
  - 动态填充： 仅添加符合当前情绪的"微演技"（眼神变化/手部抓紧/呼吸起伏）和"环境物理"（风吹/光影），以防止画面静止。
8. 二镜结构： [00s-03s] [镜头1] 格式，特写/中景优先。
9. 只输出 JSON，不要其他说明
10.只描述动作、微动作、情绪、场景，不改变男主和女主的服装

{extra_style}"""
    },
    {
        "duration": 10,
        "shot_count_min": 2,
        "shot_count_max": 3,
        "time_segments": 3,
        "simple_storyboard_rule": """你是一位专业的影视分镜师。我将给你一段剧本内容（可能包含多个分段），请将其拆分为多个镜头。

【剧本内容】
{content}

【分镜拆分要求】
每个镜头必须输出以下内容：
- 镜号
- 原剧本段落

【分镜规则（强约束）】
1. 每个分镜包含 2–3 个短句。
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
{{
  "shots": [
    {{
      "shot_number": 1,
      "original_text": "该镜头对应的原剧本段落（完整原文）"
    }},
    {{
      "shot_number": 2,
      "original_text": "该镜头对应的原剧本段落（完整原文）"
    }}
  ]
}}
```

**注意**：
- 只输出镜号和原文片段，不要输出主体、对白等其他信息
- 直接输出JSON对象，不要用markdown代码块包裹

请开始分析。""",
        "video_prompt_rule": """原剧本段落：
{script_excerpt}

出镜主体：
{subject_text}

输出 JSON，格式如下：
{{
  "timeline": [
    {{
      "time": "00s-03s",
      "visual": "[镜头1] [中景] 忠实描述文案动作 + [时间+远景描述+近景描述，场景描述要详细具体，至少20个字时间+远景描述+近景描述，场景描述要详细具体，至少20个字]",
      "audio": "[角色] 说/旁白："[原台词]""
    }},
   {{
      "time": "03s-06s",
      "visual": "[ [镜头2] [特写] 面部微表情(呼吸/眼神) + [时间+远景描述+近景描述，场景描述要详细具体，至少20个字]",
      "audio": " | (SFX音效) |"
    }},
   {{
      "time": "06s-10s",
      "visual": "[镜头3] [正反打] 对应文案的交互 + [时间+远景描述+近景描述，场景描述要详细具体，至少20个字]。",
      "audio": "| [角色]说/旁白："[原台词]" |"
    }}
  ]
}}

要求：
1. 时长总计 {safe_duration} 秒，分为3个时间段
2. time字段格式：00s-03s、03s-06s、06s-10s（连续不重叠，覆盖完整时长）
3. visual字段包含：
   - 镜头类型：如[推镜][拉镜][摇镜][跟镜][正反打][切镜][固定镜头]等（主要使用固定镜头）
   - 景别：如[远景][全景][中景][近景][特写][大特写]等（自由发挥）
   - 画面描述：忠实描述原剧本段落的动作和情绪，不添加额外内容
4. audio字段包含：
   - 角色台词：格式为 [角色名] 说/旁白："台词内容"（严格遵守原文台词）
   - 音效标记：格式为 (SFX:具体音效描述)
   - 如果既有台词又有音效，用顿号分隔：[角色]说/旁白："台词"、(SFX:音效)

5. 旁白的时候不要加"说"字
6. 文案清洗： * 带引号的 "..." -> 台词 (一字不改嵌入)。
7. 忠实还原 (Strict Adherence):
  - 严禁加戏： 绝对禁止添加文案中没有的攻击、破坏、逃跑等大幅度剧情动作，除非文案明确写了。
  - 动态填充： 仅添加符合当前情绪的"微演技"（眼神变化/手部抓紧/呼吸起伏）和"环境物理"（风吹/光影），以防止画面静止。
8. 三镜结构： [00s-03s] [镜头1] 格式，特写/中景优先。
9. 只输出 JSON，不要其他说明
10.只描述动作、微动作、情绪、场景，不改变男主和女主的服装

{extra_style}"""
    },
    {
        "duration": 15,
        "shot_count_min": 4,
        "shot_count_max": 5,
        "time_segments": 5,
        "simple_storyboard_rule": """你是一位专业的影视分镜师。我将给你一段剧本内容（可能包含多个分段），请将其拆分为多个镜头。

【剧本内容】
{content}

【分镜拆分要求】
每个镜头必须输出以下内容：
- 镜号
- 原剧本段落

【分镜规则（强约束）】
1. 每个分镜包含 4–5 个短句。
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
{{
  "shots": [
    {{
      "shot_number": 1,
      "original_text": "该镜头对应的原剧本段落（完整原文）"
    }},
    {{
      "shot_number": 2,
      "original_text": "该镜头对应的原剧本段落（完整原文）"
    }}
  ]
}}
```

**注意**：
- 只输出镜号和原文片段，不要输出主体、对白等其他信息
- 直接输出JSON对象，不要用markdown代码块包裹

请开始分析。""",
        "video_prompt_rule": """原剧本段落：
{script_excerpt}

出镜主体：
{subject_text}

输出 JSON，格式如下：
{{
  "timeline": [
    {{
      "time": "00s-03s",
      "visual": "[镜头1] [中景] 忠实描述文案动作 + [时间+远景描述+近景描述，场景描述要详细具体，至少20个字时间+远景描述+近景描述，场景描述要详细具体，至少20个字]",
      "audio": "[角色] 说/旁白："[原台词]""
    }},
   {{
      "time": "03s-06s",
      "visual": "[ [镜头2] [特写] 面部微表情(呼吸/眼神) + [时间+远景描述+近景描述，场景描述要详细具体，至少20个字]",
      "audio": " | (SFX音效) |"
    }},
   {{
      "time": "06s-09s",
      "visual": "[镜头3] [正反打] 对应文案的交互 + [时间+远景描述+近景描述，场景描述要详细具体，至少20个字]。",
      "audio": "| [角色]说/旁白："[原台词]" |"
    }},
   {{
      "time": "09s-12s",
      "visual": "[镜头4] [中景] 画面描述",
      "audio": "[角色] 说/旁白："[原台词]""
    }},
   {{
      "time": "12s-15s",
      "visual": "[镜头5] [特写] 画面描述",
      "audio": " | (SFX音效) |"
    }}
  ]
}}

要求：
1. 时长总计 {safe_duration} 秒，分为5个时间段
2. time字段格式：00s-03s、03s-06s、06s-09s、09s-12s、12s-15s（连续不重叠，覆盖完整时长）
3. visual字段包含：
   - 镜头类型：如[推镜][拉镜][摇镜][跟镜][正反打][切镜][固定镜头]等（主要使用固定镜头）
   - 景别：如[远景][全景][中景][近景][特写][大特写]等（自由发挥）
   - 画面描述：忠实描述原剧本段落的动作和情绪，不添加额外内容
4. audio字段包含：
   - 角色台词：格式为 [角色名] 说/旁白："台词内容"（严格遵守原文台词）
   - 音效标记：格式为 (SFX:具体音效描述)
   - 如果既有台词又有音效，用顿号分隔：[角色]说/旁白："台词"、(SFX:音效)

5. 旁白的时候不要加"说"字
6. 文案清洗： * 带引号的 "..." -> 台词 (一字不改嵌入)。
7. 忠实还原 (Strict Adherence):
  - 严禁加戏： 绝对禁止添加文案中没有的攻击、破坏、逃跑等大幅度剧情动作，除非文案明确写了。
  - 动态填充： 仅添加符合当前情绪的"微演技"（眼神变化/手部抓紧/呼吸起伏）和"环境物理"（风吹/光影），以防止画面静止。
8. 五镜结构： [00s-03s] [镜头1] 格式，特写/中景优先。
9. 只输出 JSON，不要其他说明
10.只描述动作、微动作、情绪、场景，不改变男主和女主的服装

{extra_style}"""
    },
    {
        "duration": 25,
        "shot_count_min": 5,
        "shot_count_max": 6,
        "time_segments": 6,
        "simple_storyboard_rule": """你是一位专业的影视分镜师。我将给你一段剧本内容（可能包含多个分段），请将其拆分为多个镜头。

【剧本内容】
{content}

【分镜拆分要求】
每个镜头必须输出以下内容：
- 镜号
- 原剧本段落

【分镜规则（强约束）】
1. 每个分镜包含 5–6 个短句。
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
{{
  "shots": [
    {{
      "shot_number": 1,
      "original_text": "该镜头对应的原剧本段落（完整原文）"
    }},
    {{
      "shot_number": 2,
      "original_text": "该镜头对应的原剧本段落（完整原文）"
    }}
  ]
}}
```

**注意**：
- 只输出镜号和原文片段，不要输出主体、对白等其他信息
- 直接输出JSON对象，不要用markdown代码块包裹

请开始分析。""",
        "video_prompt_rule": """原剧本段落：
{script_excerpt}

出镜主体：
{subject_text}

输出 JSON，格式如下：
{{
  "timeline": [
    {{
      "time": "00s-04s",
      "visual": "[镜头1] [中景] 忠实描述文案动作 + [时间+远景描述+近景描述，场景描述要详细具体，至少20个字时间+远景描述+近景描述，场景描述要详细具体，至少20个字]",
      "audio": "[角色] 说/旁白："[原台词]""
    }},
   {{
      "time": "04s-08s",
      "visual": "[ [镜头2] [特写] 面部微表情(呼吸/眼神) + [时间+远景描述+近景描述，场景描述要详细具体，至少20个字]",
      "audio": " | (SFX音效) |"
    }},
   {{
      "time": "08s-12s",
      "visual": "[镜头3] [正反打] 对应文案的交互 + [时间+远景描述+近景描述，场景描述要详细具体，至少20个字]。",
      "audio": "| [角色]说/旁白："[原台词]" |"
    }},
   {{
      "time": "12s-16s",
      "visual": "[镜头4] [中景] 画面描述",
      "audio": "[角色] 说/旁白："[原台词]""
    }},
   {{
      "time": "16s-20s",
      "visual": "[镜头5] [特写] 画面描述",
      "audio": " | (SFX音效) |"
    }},
   {{
      "time": "20s-25s",
      "visual": "[镜头6] [全景] 画面描述",
      "audio": "[角色] 说/旁白："[原台词]""
    }}
  ]
}}

要求：
1. 时长总计 {safe_duration} 秒，分为6个时间段
2. time字段格式：00s-04s、04s-08s、08s-12s、12s-16s、16s-20s、20s-25s（连续不重叠，覆盖完整时长）
3. visual字段包含：
   - 镜头类型：如[推镜][拉镜][摇镜][跟镜][正反打][切镜][固定镜头]等（主要使用固定镜头）
   - 景别：如[远景][全景][中景][近景][特写][大特写]等（自由发挥）
   - 画面描述：忠实描述原剧本段落的动作和情绪，不添加额外内容
4. audio字段包含：
   - 角色台词：格式为 [角色名] 说/旁白："台词内容"（严格遵守原文台词）
   - 音效标记：格式为 (SFX:具体音效描述)
   - 如果既有台词又有音效，用顿号分隔：[角色]说/旁白："台词"、(SFX:音效)

5. 旁白的时候不要加"说"字
6. 文案清洗： * 带引号的 "..." -> 台词 (一字不改嵌入)。
7. 忠实还原 (Strict Adherence):
  - 严禁加戏： 绝对禁止添加文案中没有的攻击、破坏、逃跑等大幅度剧情动作，除非文案明确写了。
  - 动态填充： 仅添加符合当前情绪的"微演技"（眼神变化/手部抓紧/呼吸起伏）和"环境物理"（风吹/光影），以防止画面静止。
8. 六镜结构： [00s-04s] [镜头1] 格式，特写/中景优先。
9. 只输出 JSON，不要其他说明
10.只描述动作、微动作、情绪、场景，不改变男主和女主的服装

{extra_style}"""
    }
]

def upgrade():
    """创建表并初始化数据"""
    with engine.connect() as conn:
        # 1. 创建表
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS shot_duration_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    duration INTEGER NOT NULL UNIQUE,
                    shot_count_min INTEGER NOT NULL,
                    shot_count_max INTEGER NOT NULL,
                    time_segments INTEGER NOT NULL,
                    simple_storyboard_rule TEXT NOT NULL,
                    video_prompt_rule TEXT NOT NULL,
                    large_shot_prompt_rule TEXT NOT NULL DEFAULT '',
                    is_default BOOLEAN DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
            print("创建 shot_duration_templates 表成功")
        except Exception as e:
            print(f"创建表失败: {e}")
            raise

        # 2. 插入四个预设模板
        try:
            for idx, template in enumerate(TEMPLATES_DATA):
                is_default = 1 if template["duration"] == 15 else 0
                conn.execute(text("""
                    INSERT OR IGNORE INTO shot_duration_templates
                    (duration, shot_count_min, shot_count_max, time_segments, simple_storyboard_rule, video_prompt_rule, large_shot_prompt_rule, is_default)
                    VALUES (:duration, :shot_count_min, :shot_count_max, :time_segments, :simple_storyboard_rule, :video_prompt_rule, :large_shot_prompt_rule, :is_default)
                """), {
                    "duration": template["duration"],
                    "shot_count_min": template["shot_count_min"],
                    "shot_count_max": template["shot_count_max"],
                    "time_segments": template["time_segments"],
                    "simple_storyboard_rule": template["simple_storyboard_rule"],
                    "video_prompt_rule": template["video_prompt_rule"],
                    "large_shot_prompt_rule": build_large_shot_prompt_rule(template["duration"], template["time_segments"]),
                    "is_default": is_default
                })
            print("插入四个预设模板成功")
        except Exception as e:
            print(f"插入数据失败: {e}")
            raise

        # 3. 为episodes表添加storyboard2_duration字段
        try:
            conn.execute(text("""
                ALTER TABLE episodes
                ADD COLUMN storyboard2_duration INTEGER DEFAULT 15
            """))
            print("为episodes表添加 storyboard2_duration 字段成功")
        except Exception as e:
            print(f"episodes表的storyboard2_duration 字段可能已存在: {e}")

        conn.commit()
        print("\n数据库迁移完成！")

if __name__ == "__main__":
    print("开始数据库迁移...")
    upgrade()
