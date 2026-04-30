from __future__ import annotations

import re
from pathlib import Path


DEFAULT_LARGE_SHOT_SEGMENTS = {
    6: 2,
    10: 3,
    15: 5,
    25: 6,
}

LEGACY_LARGE_SHOT_PROMPT_PREFIXES = (
    "你是专业的大镜头提示词生成助手",
    "浣犳槸涓撲笟鐨勫ぇ闀滃ご鎻愮ず璇嶇敓鎴愬姪鎵",
)

INTERIM_LARGE_SHOT_PROMPT_MARKERS = (
    "生成大镜头时间轴",
    "整体偏电影化表达",
    "time 字段不要再写 00s-03s 这类秒数",
    "大镜头/全景/中景优先，保证镜头之间的空间承接自然",
)

PROMPT_TXT_PATH = Path(__file__).resolve().parent.parent / "prompt.txt"

PROMPT_TXT_FALLBACK = """你是专业的分镜提示词生成助手。请根据以下信息生成分镜时间轴，并为这个镜头选择最合适的景别、拍摄角度和运镜方式。

原剧本段落：
{script_excerpt}

出镜主体：
{subject_text}

说明：
- 角色主体会按“主体名-性格描述”的格式提供，场景主体只写名称
- 请在画面设计、动作、表情和情绪表现中参考对应角色的性格信息

输出 JSON，格式如下：
{{
  "timeline": [
    {{
      "time": "[镜头1]",
      "visual": "[景别/拍摄角度/运镜方式] 时间+远景描述+近景描述，+ 人物站位（例如：A(左) vs B(右)） + 人物动作+神态（包括人物大动作, 动作要具体，例如：江澈停步仰望山道片刻，随即转身抄起斧头对准一棵细树，几斧砍下一根长木棍，动作干净利落，木屑随斧落飞散。如果是内心戏，则需要有面部具体表情，比如：脸顿时一黑，皱眉，微微挑眉，嘴角向右一歪露出一抹坏笑等）",
      "audio": "[角色] 伤心的说：“[原台词]”"
    }},
    {{
      "time": "[镜头2]",
      "visual": "[景别/拍摄角度/运镜方式] 人物动作+神态（包括人物大动作, 动作要具体，例如：江澈停步仰望山道片刻，随即转身抄起斧头对准一棵细树，几斧砍下一根长木棍，动作干净利落，木屑随斧落飞散。如果是内心戏，则需要有面部具体表情，比如：脸顿时一黑，皱眉，微微挑眉，嘴角向右一歪露出一抹坏笑等）",
      "audio": "SFX 或者 [角色]平静的说：“[原台词]” "
    }},
    {{
      "time": "[镜头3]",
      "visual": "[景别/拍摄角度/运镜方式]人物动作+神态（包括人物大动作, 动作要具体，例如：江澈停步仰望山道片刻，随即转身抄起斧头对准一棵细树，几斧砍下一根长木棍，动作干净利落，木屑随斧落飞散。如果是内心戏，则需要有面部具体表情，比如：脸顿时一黑，皱眉，微微挑眉，嘴角向右一歪露出一抹坏笑等）",
      "audio": "SFX 或者[角色]生气的说：“[原台词]”"
    }},
    {{
      "time": "[镜头4]",
      "visual": "[景别/拍摄角度/运镜方式]人物动作+神态（包括人物大动作, 动作要具体，例如：江澈停步仰望山道片刻，随即转身抄起斧头对准一棵细树，几斧砍下一根长木棍，动作干净利落，木屑随斧落飞散。如果是内心戏，则需要有面部具体表情，比如：脸顿时一黑，皱眉，微微挑眉，嘴角向右一歪露出一抹坏笑等）",
      "audio": "SFX 或者[角色]说：“[原台词]” "
    }}
  ]
}}


要求：
1. 时长总计 {safe_duration} 秒，分为3个或者4个时间段
2. time字段格式：[镜头1]、[镜头2]、[镜头3]……（连续不重叠，覆盖完整时长）
3. visual字段包含：
   - 镜头类型：景别/拍摄角度(突出内心戏或者表情的时候可以用近景或特写镜头)
   - 画面描述：忠实描述原剧本段落的动作和情绪
   - 场景描述：如果这段是2人场景或者多个人的互动，则需要把两个人的相对位置描述清楚，比如角色1和角色2并排坐在沙发上、角色1躺在床上角色2坐在角色1身边等）
   - 时间不确定的时候，优先使用晚上。
   - 第1个镜头需要有比较酷炫震撼的效果，建议采用下面几种的1种：
     (1)  FPV高速运镜，急速俯冲搭配大角度变向，从远景贴地快速拉近到中景，例如：FPV超高速运镜，从一艘长满青苔、摇摇欲坠的飞艇上俯冲向下，穿过丛林迷雾，最后到达人物所在的小屋内，聚焦在人物上半身，镜头螺旋式下降，绿叶穿梭而过。
     (2)  从人物身体的局部大特写猛然拉到中景。例如：超低机位贴地锁定赤足，镜头跟随脚步稳定前行，随后镜头垂直向上摇摄，平稳过渡到身体再到完整上半身。
     (3)  环绕运镜，镜头围绕主体360度旋转，突出情绪张力，常用来展示角色登场的高光时刻和情绪升华

4. audio字段包含：
   - 角色台词：格式为 [角色名]XX（XX可以是平静、伤心、难过、开心、傲慢、不屑、胆怯等情感词汇，突出说话人的语调）的说："台词内容"（严格遵守原文台词）
   - 音效标记：格式为 (SFX:具体音效描述)
   - 没有台词的时候，用 音效，不自己乱加别的台词
   - 如果既有台词又有音效，用顿号分隔：[角色][用XX的语气]说："台词"、(SFX:音效)

5. 文案保留： * 带引号的 “...” -> 台词 (一字不改嵌入)。  
6. 忠实还原 (Strict Adherence):  
  - 严禁加戏： 绝对禁止添加文案中没有的攻击、破坏、逃跑等大幅度剧情动作，除非文案明确写了。    
  - 动态填充： 仅添加符合当前情绪的“微演技”（眼神变化/手部抓紧/呼吸起伏）和“环境物理”（风吹/光影），以防止画面静止。 
  - 人物内心戏时，表情变化要细腻 （1）必须有头发吹动的描写 (2) 表情要有层次，例如： A 伤心表情：初始双眼瞪大接着眼眶迸发出泪，嘴角抽搐，然后哽咽，额纹因痛苦拧在一起，泪水糊住视线，下颚颤抖 B 古灵精怪的表情：初始眼神灵动闪烁，嘴角微微嘟起，接着眉梢上扬，满是惊喜调皮，随后嘴角咧开，露出两颗小虎牙，脸颊泛起红晕，眼睛眯成一条线 C 砰然心动表情：眼神轻轻流转，然后马上害羞低头，睫毛轻颤，嘴角微微勾起，一副不好意思的模样 D 激动兴奋表情：眼珠瞬间瞪大，嘴角疯狂上扬，脸颊肌肉笑到抽搐，捂嘴憋笑，眼神偷偷斜瞄 E 不情愿的表情：瞳孔警觉，笑意稍僵，眼尾轻扫暗角，藏起锋芒
  -人物动作要详细：例如：[镜头1] 冬日白天，大雪茫茫，镜头由远及近跟随江澈，他提着斧头小跑在雪地上，破草鞋踩入积雪发出嘎吱声，气息凝成白雾，破麻袄随步伐抖动，眼神前视，神情平稳从容。紧接着切至 [镜头2] 山脚中景，一片冰雪覆盖的山林入口，江澈停步仰望山道片刻，随即转身抄起斧头对准一棵细树，几斧砍下一根长木棍，动作干净利落，木屑随斧落飞散。随即切至 [镜头3] 手部特写，江澈蹲身，斧刃斜削木棍一端，木屑纷飞，寒光中削出锐利尖端，另一端保留圆钝，他攥紧木棍掂了掂，眼神里闪过一丝满意。[镜头4] 山林入口全景，江澈手持自制长矛、一手握斧，迈入郁郁葱葱的冬日山林，身影渐渐没入林间，枝头积雪飘落，寂静中只余脚步声远去。
7. 只输出 JSON，不要其他说明
8. 角色尽量用名字
9. 不要写角色穿什么衣服，只写动作、神情即可
10、一定不能出现下面的用词：被迫、压迫、轻咬下唇、肉体、阴鸷、挑逗、勾引、腰腹、抿嘴、擦、充血

{extra_style}"""

PROMPT_TXT_EXAMPLES = [
    (
        "[景别/拍摄角度/运镜方式] 时间+远景描述+近景描述，+ 人物站位（例如：A(左) vs B(右)） + 人物动作+神态（包括人物大动作, 动作要具体，例如：江澈停步仰望山道片刻，随即转身抄起斧头对准一棵细树，几斧砍下一根长木棍，动作干净利落，木屑随斧落飞散。如果是内心戏，则需要有面部具体表情，比如：脸顿时一黑，皱眉，微微挑眉，嘴角向右一歪露出一抹坏笑等）",
        "[角色] 伤心的说：“[原台词]”",
    ),
    (
        "[景别/拍摄角度/运镜方式] 人物动作+神态（包括人物大动作, 动作要具体，例如：江澈停步仰望山道片刻，随即转身抄起斧头对准一棵细树，几斧砍下一根长木棍，动作干净利落，木屑随斧落飞散。如果是内心戏，则需要有面部具体表情，比如：脸顿时一黑，皱眉，微微挑眉，嘴角向右一歪露出一抹坏笑等）",
        "SFX 或者 [角色]平静的说：“[原台词]” ",
    ),
    (
        "[景别/拍摄角度/运镜方式]人物动作+神态（包括人物大动作, 动作要具体，例如：江澈停步仰望山道片刻，随即转身抄起斧头对准一棵细树，几斧砍下一根长木棍，动作干净利落，木屑随斧落飞散。如果是内心戏，则需要有面部具体表情，比如：脸顿时一黑，皱眉，微微挑眉，嘴角向右一歪露出一抹坏笑等）",
        "SFX 或者[角色]生气的说：“[原台词]”",
    ),
    (
        "[景别/拍摄角度/运镜方式]人物动作+神态（包括人物大动作, 动作要具体，例如：江澈停步仰望山道片刻，随即转身抄起斧头对准一棵细树，几斧砍下一根长木棍，动作干净利落，木屑随斧落飞散。如果是内心戏，则需要有面部具体表情，比如：脸顿时一黑，皱眉，微微挑眉，嘴角向右一歪露出一抹坏笑等）",
        "SFX 或者[角色]说：“[原台词]” ",
    ),
]

LARGE_SHOT_TEMPLATE_CONTENT_PLACEHOLDER = "{large_shot_template_content}"
LARGE_SHOT_TEMPLATE_HEADER = "   - 第1个镜头需要有比较酷炫震撼的效果，优先参考当前选中的大镜头模板："
LARGE_SHOT_TEMPLATE_LEGACY_HEADER = "   - 第1个镜头需要有比较酷炫震撼的效果，建议采用下面几种的1种："
LARGE_SHOT_TEMPLATE_AUDIO_MARKER = "\n4. audio字段包含："
LARGE_SHOT_TEMPLATE_DEFAULTS = [
    {
        "name": "FPV高速俯冲",
        "content": "(1)  FPV高速运镜，急速俯冲搭配大角度变向，从远景贴地快速拉近到中景，例如：FPV超高速运镜，从一艘长满青苔、摇摇欲坠的飞艇上俯冲向下，穿过丛林迷雾，最后到达人物所在的小屋内，聚焦在人物上半身，镜头螺旋式下降，绿叶穿梭而过。",
    },
    {
        "name": "局部大特写拉中景",
        "content": "(2)  从人物身体的局部大特写猛然拉到中景。例如：超低机位贴地锁定赤足，镜头跟随脚步稳定前行，随后镜头垂直向上摇摄，平稳过渡到身体再到完整上半身。",
    },
    {
        "name": "360环绕运镜",
        "content": "(3)  环绕运镜，镜头围绕主体360度旋转，突出情绪张力，常用来展示角色登场的高光时刻和情绪升华",
    },
]


def _normalize_large_shot_template_duration(duration: int) -> int:
    try:
        numeric_duration = int(duration or 15)
    except Exception:
        numeric_duration = 15

    if numeric_duration <= 6:
        return 6
    if numeric_duration <= 10:
        return 10
    if numeric_duration <= 15:
        return 15
    return 25


def _resolve_large_shot_segment_count(duration: int, time_segments: int | None = None) -> int:
    try:
        explicit_count = int(time_segments or 0)
    except Exception:
        explicit_count = 0
    if explicit_count > 0:
        return explicit_count
    normalized_duration = _normalize_large_shot_template_duration(duration)
    return DEFAULT_LARGE_SHOT_SEGMENTS.get(normalized_duration, 5)


def _normalize_template_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u2028", "\n").replace("\u2029", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def _load_prompt_txt_base() -> str:
    try:
        return _normalize_template_text(PROMPT_TXT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _normalize_template_text(PROMPT_TXT_FALLBACK)


def _build_prompt_txt_example_json(segment_count: int) -> str:
    example_items: list[str] = []
    for index in range(1, segment_count + 1):
        visual, audio = PROMPT_TXT_EXAMPLES[min(index - 1, len(PROMPT_TXT_EXAMPLES) - 1)]
        example_items.append(
            "    {{\n"
            f"      \"time\": \"[镜头{index}]\",\n"
            f"      \"visual\": \"{visual}\",\n"
            f"      \"audio\": \"{audio}\"\n"
            "    }}"
        )
    return ",\n".join(example_items)


def _replace_timeline_example_block(template_text: str, example_json: str) -> str:
    start_marker = '  "timeline": ['
    end_marker = "\n  ]\n}}"
    start_index = template_text.find(start_marker)
    if start_index < 0:
        return template_text
    start_index += len(start_marker)
    end_index = template_text.find(end_marker, start_index)
    if end_index < 0:
        return template_text
    return f"{template_text[:start_index]}\n{example_json}{template_text[end_index:]}"


def get_default_large_shot_templates() -> list[dict[str, str]]:
    return [
        {
            "name": item["name"],
            "content": _normalize_template_text(item["content"]),
        }
        for item in LARGE_SHOT_TEMPLATE_DEFAULTS
    ]


def _render_large_shot_template_content(template_content: str) -> str:
    normalized = _normalize_template_text(template_content)
    if not normalized:
        normalized = _normalize_template_text(LARGE_SHOT_TEMPLATE_DEFAULTS[0]["content"])

    rendered_lines = []
    for raw_line in normalized.split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            continue
        rendered_lines.append(f"     {stripped}")
    return "\n".join(rendered_lines).strip()


def _build_large_shot_template_block(template_content: str) -> str:
    return f"{LARGE_SHOT_TEMPLATE_HEADER}\n{template_content}"


def _replace_first_shot_template_block(template_text: str, replacement_block: str) -> str:
    normalized = _normalize_template_text(template_text)
    start_index = normalized.find(LARGE_SHOT_TEMPLATE_LEGACY_HEADER)
    if start_index < 0:
        start_index = normalized.find(LARGE_SHOT_TEMPLATE_HEADER)
    if start_index < 0:
        return normalized

    end_index = normalized.find(LARGE_SHOT_TEMPLATE_AUDIO_MARKER, start_index)
    if end_index < 0:
        end_index = normalized.find("\n{extra_style}", start_index)
    if end_index < 0:
        end_index = len(normalized)

    return f"{normalized[:start_index]}{replacement_block}{normalized[end_index:]}"


def inject_large_shot_template_content(template_text: str, template_content: str) -> str:
    normalized = _normalize_template_text(template_text)
    rendered_content = _render_large_shot_template_content(template_content)
    if LARGE_SHOT_TEMPLATE_CONTENT_PLACEHOLDER in normalized:
        return normalized.replace(LARGE_SHOT_TEMPLATE_CONTENT_PLACEHOLDER, rendered_content)

    replacement_block = _build_large_shot_template_block(rendered_content)
    replaced_text = _replace_first_shot_template_block(normalized, replacement_block)
    if replaced_text != normalized:
        return replaced_text

    fallback_block = f"\n{replacement_block}\n"
    audio_index = normalized.find(LARGE_SHOT_TEMPLATE_AUDIO_MARKER)
    if audio_index >= 0:
        return f"{normalized[:audio_index]}{fallback_block}{normalized[audio_index:]}"

    extra_style_index = normalized.find("\n{extra_style}")
    if extra_style_index >= 0:
        return f"{normalized[:extra_style_index]}{fallback_block}{normalized[extra_style_index:]}"

    return f"{normalized}\n\n{replacement_block}"


def build_large_shot_prompt_rule(duration: int, time_segments: int | None = None) -> str:
    normalized_duration = _normalize_large_shot_template_duration(duration)
    segment_count = _resolve_large_shot_segment_count(normalized_duration, time_segments)
    base_text = _load_prompt_txt_base()
    example_json = _build_prompt_txt_example_json(segment_count)
    prompt_text = _replace_timeline_example_block(base_text, example_json)
    prompt_text = _replace_first_shot_template_block(
        prompt_text,
        _build_large_shot_template_block(LARGE_SHOT_TEMPLATE_CONTENT_PLACEHOLDER)
    )
    prompt_text = re.sub(
        r"1\. 时长总计 \{safe_duration\} 秒，分为[^\n]+时间段",
        f"1. 时长总计 {{safe_duration}} 秒，分为{segment_count}个时间段",
        prompt_text,
        count=1,
    )
    return prompt_text


def is_legacy_large_shot_prompt_rule(prompt_text: str) -> bool:
    text_value = str(prompt_text or "").strip()
    if not text_value:
        return False
    if any(text_value.startswith(prefix) for prefix in LEGACY_LARGE_SHOT_PROMPT_PREFIXES):
        return True
    if any(marker in text_value for marker in INTERIM_LARGE_SHOT_PROMPT_MARKERS):
        return True
    if re.search(r'"time"\s*:\s*"\d{2}s-\d{2}s"', text_value):
        return True
    if "time字段格式：00s-" in text_value or "time字段格式：00s-03s" in text_value:
        return True
    if "[00s-03s] [镜头1]" in text_value:
        return True
    return False
