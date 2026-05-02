import re
from typing import List


_STYLE_TEMPLATE_REMOVAL_PHRASES = [
    "保持与该风格角色模板一致的整体画风与审美基调：",
    "角色风格：",
    "角色设定：",
    "人物设定：",
    "人物风格：",
    "人物：",
    "角色：",
    "人像：",
    "肖像：",
    "全身：",
    "半身：",
    "白底：",
    "纯白背景：",
]

_STYLE_TEMPLATE_BANNED_SEGMENT_KEYWORDS = [
    "人物高清画质",
    "人物高质量",
    "角色定妆",
    "角色站姿",
    "人物站姿",
    "全身",
    "半身",
    "白底",
    "纯白背景",
    "冷白皮",
    "皮肤",
    "发量",
    "发丝",
    "五官",
    "肖像",
    "人像",
    "眼神",
    "凝视",
    "表情",
    "服饰细节",
    "妆容",
    "真实情感",
    "专业人像",
]

_STYLE_TEMPLATE_SKIP_EXACT_CHUNKS = {
    "核心风格",
    "建模技术",
    "质感",
    "服饰细节",
    "光影效果",
}

_STYLE_TEMPLATE_LABEL_PREFIXES = [
    "核心风格",
    "建模技术",
    "质感",
    "皮肤质感",
    "服饰细节",
    "光影效果",
]


def _extract_style_core_from_character_template(character_content: str) -> str:
    text_content = str(character_content or "").strip()
    if not text_content:
        return ""

    normalized = text_content
    for phrase in _STYLE_TEMPLATE_REMOVAL_PHRASES:
        normalized = normalized.replace(phrase, "")

    raw_parts = re.split(r"[\n\r,，。；;、＋]+", normalized)
    cleaned_parts: List[str] = []
    seen_parts = set()

    for raw_part in raw_parts:
        chunk = re.sub(r"^[\-\s]+", "", str(raw_part or "").strip())
        chunk = chunk.strip(" ：:，。；;、")
        chunk = re.sub(r"[：:]{2,}", "：", chunk)
        chunk = re.sub(r"^(?:[：:]\s*)+", "", chunk)
        chunk = re.sub(r"(?:\s*[：:])+$", "", chunk)
        for label_prefix in _STYLE_TEMPLATE_LABEL_PREFIXES:
            chunk = re.sub(rf"^{re.escape(label_prefix)}\s*[：:]\s*", "", chunk)
        if not chunk:
            continue
        if chunk in _STYLE_TEMPLATE_SKIP_EXACT_CHUNKS:
            continue
        if any(keyword in chunk for keyword in _STYLE_TEMPLATE_BANNED_SEGMENT_KEYWORDS):
            continue
        normalized_key = re.sub(r"\s+", "", chunk)
        if not normalized_key or normalized_key in seen_parts:
            continue
        seen_parts.add(normalized_key)
        cleaned_parts.append(chunk)

    return "，".join(cleaned_parts[:16]).strip("，")


def _build_scene_style_template_content(character_content: str) -> str:
    style_core = _extract_style_core_from_character_template(character_content)
    if not style_core:
        return (
            "突出环境设计、空间层次、光影氛围、建筑与陈设细节、材质肌理与镜头感。"
            "不要出现人物，不要纯白背景，不要角色定妆式构图。"
        )
    return (
        f"{style_core}\n"
        "突出环境设计、空间层次、光影氛围、建筑与陈设细节、材质肌理与镜头感。"
        "不要出现人物，不要纯白背景，不要角色定妆式构图。"
    )


def _build_prop_style_template_content(character_content: str) -> str:
    style_core = _extract_style_core_from_character_template(character_content)
    if not style_core:
        return (
            "突出道具材质、结构造型、轮廓识别度、工艺细节、使用痕迹与局部特写。"
            "不要出现人物，不要纯白背景，不要角色站姿或人像式构图。"
        )
    return (
        f"{style_core}\n"
        "突出道具材质、结构造型、轮廓识别度、工艺细节、使用痕迹与局部特写。"
        "不要出现人物，不要纯白背景，不要角色站姿或人像式构图。"
    )


def _style_template_variant_needs_regeneration(content: str) -> bool:
    text_content = str(content or "").strip()
    if not text_content:
        return True
    if "??????????" in text_content or "?????" in text_content[:20]:
        return True
    if text_content.startswith("保持与该风格角色模板一致的整体画风与审美基调："):
        return True
    artifact_markers = [
        "＋＋",
        "核心风格，",
        "建模技术，",
        "发量丰盈",
        "质感：",
        "服饰细节：",
        "光影效果：",
        "真实情感",
        "写实凝视",
    ]
    return any(marker in text_content for marker in artifact_markers)
