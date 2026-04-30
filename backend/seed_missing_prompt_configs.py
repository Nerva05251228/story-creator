"""
补齐缺失的 prompt_configs 默认项。

当前主要用于空项目初始化时补齐：
- detailed_storyboard_content_analysis

默认行为：
- 只在 key 不存在时插入

可选参数：
- --overwrite: 如果 key 已存在，也覆盖为当前默认内容

运行方式：
    cd backend
    python seed_missing_prompt_configs.py

    cd backend
    python seed_missing_prompt_configs.py --overwrite
"""

from __future__ import annotations

import sys
from pathlib import Path


CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.append(str(CURRENT_DIR))

from database import SessionLocal, engine
import models


PROMPT_CONFIGS = [
    {
        "key": "detailed_storyboard_content_analysis",
        "name": "详细分镜：内容分析",
        "description": "对已划分的镜头进行详细内容分析，提取主体、对白、旁白等信息",
        "content": """你是一位专业的影视分镜师。我将给你一组已经划分好的镜头（每个镜头包含镜号和原剧本段落），请为每个镜头提取详细的分镜信息。

【镜头列表】
{shots_content}

【分镜拆分要求】
每个镜头必须输出以下内容：
- 镜号（保持输入的一致）
- 主体（角色和场景）
- 旁白或对白
- 原剧本段落（保持输入的一致）

【主体提取原则】
主体类型只能输出两类：角色、场景。
主体必须包含至少一个场景（即：故事发生的地点）

【核心总原则（非常重要）】
严格保持输入数据的一致
如果输入不明确时，优先使用描述性文字。
只能严格使用剧本原文中的角色名字，如果角色没有姓名，也不要生成"女主角"、"男配角"。
你可以输入"警察"等职业常识性名词或用"警察应该叫做谁"来描述。
对原文未命名的，如果必须命名，可以使用唯一指代性的代词描述（例如"旗袍女""老板娘""母亲"等）。

【处理旁白及对白的处理】
- **旁白**：第三人称的旁述或独白或心理描述
  - 使用句号断句，句尾作为说话人，例如："某某内心道……"
  - 如果语句接近 → 使用"旁白"
  - 需要填写注意性别（女/男/无性别）
  - 需要填写注意情绪（例如：平静、伤心、悲愤、惊恐、兴奋等）
  - 角色内心的独白法则需注意叙事话语、细节等

- **对话**：角色之间的对话
  - 按对话顺序列出，每个对象包含说话人、对方名字（或描述）、对话内容
  - 示例：【某某内心道："……"，惊恐地（询问）："你当真要……？"】

【输出格式】
请严格按照以下JSON格式输出：
```json
{{
  "shots": [
    {{
      "shot_number": 1,
      "subjects": [
        {{"name": "角色名", "type": "角色"}},
        {{"name": "场景名", "type": "场景"}}
      ],
      "original_text": "该镜头对应的原文片段",
      "voice_type": "narration 或 dialogue 或 none",
      "narration": {{
        "speaker": "旁白角色名 或 旁白",
        "gender": "女 或 男 或 无 或 未知",
        "emotion": "情绪描述",
        "text": "旁白内容"
      }},
      "dialogue": [
        {{
          "speaker": "说话人名字",
          "target": "对方名字（如果是对某人说的）或 null",
          "gender": "女 或 男",
          "emotion": "情绪描述",
          "text": "对话内容"
        }}
      ]
    }}
  ]
}}
```

**注意**：
- 如果 voice_type 为 "narration"，则 dialogue 为 null
- 如果 voice_type 为 "dialogue"，则 narration 为 null
- 如果 voice_type 为 "none"，则两者（两个都是 narration 和 dialogue 均为 null
- dialogue 可以是旁白，可以是多段对话
- target 代表是对谁说的（如果对特定人说的），如果是广播性质或说话人为 null
- 保持 original_text 与输入完全一致，不要修改
- 保持 shot_number 与输入完全一致
- 直接输出JSON对象，不要用markdown代码块包裹

请开始分析。""",
    }
]


def main() -> int:
    overwrite = "--overwrite" in sys.argv

    models.Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        created_count = 0
        updated_count = 0
        skipped_count = 0

        for item in PROMPT_CONFIGS:
            existing = db.query(models.PromptConfig).filter(
                models.PromptConfig.key == item["key"]
            ).first()

            if existing is None:
                db.add(models.PromptConfig(**item))
                created_count += 1
                continue

            if not overwrite:
                skipped_count += 1
                continue

            existing.name = item["name"]
            existing.description = item["description"]
            existing.content = item["content"]
            updated_count += 1

        db.commit()

        print("prompt_configs 补齐完成")
        print(f"- created: {created_count}")
        print(f"- updated: {updated_count}")
        print(f"- skipped: {skipped_count}")
        print(f"- overwrite: {overwrite}")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"prompt_configs 补齐失败: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
