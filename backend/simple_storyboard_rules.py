from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


PAIR_OPEN_TO_CLOSE = {
    "“": "”",
    "‘": "’",
    "\"": "\"",
    "'": "'",
    "「": "」",
    "『": "』",
    "（": "）",
    "(": ")",
    "《": "》",
    "<": ">",
    "[": "]",
    "【": "】",
    "{": "}",
}

PAIR_CLOSE_TO_OPEN = {value: key for key, value in PAIR_OPEN_TO_CLOSE.items()}
DEFAULT_HARD_STOP_PUNCTUATIONS = ["。", "！", "？", "；", "……", "...", "？！", "！？", "!!", "??"]
DEFAULT_SOFT_STOP_PUNCTUATIONS = ["，", "、", "：", ":"]


@dataclass
class SimpleStoryboardRuleConfig:
    hard_stop_punctuations: List[str]
    soft_stop_punctuations: List[str]
    keep_dialogue_turn_intact: bool
    respect_quote_boundary: bool
    respect_paragraph_boundary: bool
    target_chars_min: int
    target_chars_max: int
    soft_max_chars: int
    hard_max_chars: int
    max_units_per_shot: int
    merge_short_fragment_threshold: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SentenceUnit:
    text: str
    is_dialogue_turn: bool = False


DEFAULT_RULES: Dict[int, SimpleStoryboardRuleConfig] = {
    15: SimpleStoryboardRuleConfig(
        hard_stop_punctuations=list(DEFAULT_HARD_STOP_PUNCTUATIONS),
        soft_stop_punctuations=list(DEFAULT_SOFT_STOP_PUNCTUATIONS),
        keep_dialogue_turn_intact=True,
        respect_quote_boundary=True,
        respect_paragraph_boundary=True,
        target_chars_min=35,
        target_chars_max=60,
        soft_max_chars=70,
        hard_max_chars=90,
        max_units_per_shot=2,
        merge_short_fragment_threshold=18,
    ),
    25: SimpleStoryboardRuleConfig(
        hard_stop_punctuations=list(DEFAULT_HARD_STOP_PUNCTUATIONS),
        soft_stop_punctuations=list(DEFAULT_SOFT_STOP_PUNCTUATIONS),
        keep_dialogue_turn_intact=True,
        respect_quote_boundary=True,
        respect_paragraph_boundary=True,
        target_chars_min=65,
        target_chars_max=110,
        soft_max_chars=125,
        hard_max_chars=150,
        max_units_per_shot=4,
        merge_short_fragment_threshold=24,
    ),
}


def get_default_rule_config(duration: int) -> SimpleStoryboardRuleConfig:
    normalized_duration = 25 if int(duration or 15) == 25 else 15
    base = DEFAULT_RULES[normalized_duration]
    return SimpleStoryboardRuleConfig(**base.to_dict())


def normalize_rule_config(raw_value: Optional[Dict[str, Any]], duration: int) -> SimpleStoryboardRuleConfig:
    config = get_default_rule_config(duration)
    if not isinstance(raw_value, dict):
        return config

    payload = dict(raw_value)
    if isinstance(payload.get("hard_stop_punctuations"), list) and payload["hard_stop_punctuations"]:
        config.hard_stop_punctuations = [str(item) for item in payload["hard_stop_punctuations"] if str(item)]
    if isinstance(payload.get("soft_stop_punctuations"), list) and payload["soft_stop_punctuations"]:
        config.soft_stop_punctuations = [str(item) for item in payload["soft_stop_punctuations"] if str(item)]

    for field_name in (
        "keep_dialogue_turn_intact",
        "respect_quote_boundary",
        "respect_paragraph_boundary",
    ):
        if field_name in payload:
            setattr(config, field_name, bool(payload[field_name]))

    for field_name in (
        "target_chars_min",
        "target_chars_max",
        "soft_max_chars",
        "hard_max_chars",
        "max_units_per_shot",
        "merge_short_fragment_threshold",
    ):
        if field_name in payload and payload[field_name] is not None:
            setattr(config, field_name, int(payload[field_name]))

    _validate_rule_config(config)
    return config


def _validate_rule_config(config: SimpleStoryboardRuleConfig) -> None:
    if not config.hard_stop_punctuations:
        raise ValueError("hard_stop_punctuations 不能为空")
    if config.target_chars_min <= 0:
        raise ValueError("target_chars_min 必须大于 0")
    if config.target_chars_max < config.target_chars_min:
        raise ValueError("target_chars_max 不能小于 target_chars_min")
    if config.soft_max_chars < config.target_chars_max:
        raise ValueError("soft_max_chars 不能小于 target_chars_max")
    if config.hard_max_chars < config.soft_max_chars:
        raise ValueError("hard_max_chars 不能小于 soft_max_chars")
    if config.max_units_per_shot <= 0:
        raise ValueError("max_units_per_shot 必须大于 0")
    if config.merge_short_fragment_threshold < 0:
        raise ValueError("merge_short_fragment_threshold 不能小于 0")


def split_units(text: str, rule: SimpleStoryboardRuleConfig) -> List[SentenceUnit]:
    raw_text = str(text or "")
    if not raw_text:
        return []

    units: List[SentenceUnit] = []
    start = 0
    index = 0
    pair_stack: List[str] = []
    hard_tokens = sorted(rule.hard_stop_punctuations, key=len, reverse=True)

    while index < len(raw_text):
        current = raw_text[index]
        if current in PAIR_OPEN_TO_CLOSE:
            pair_stack.append(current)
            index += 1
            continue
        if current in PAIR_CLOSE_TO_OPEN:
            if pair_stack and pair_stack[-1] == PAIR_CLOSE_TO_OPEN[current]:
                pair_stack.pop()
            index += 1
            continue

        if current == "\n" and rule.respect_paragraph_boundary and not pair_stack:
            end = index + 1
            while end < len(raw_text) and raw_text[end] == "\n":
                end += 1
            _append_unit(units, raw_text[start:end])
            start = end
            index = end
            continue

        matched_token = next((token for token in hard_tokens if raw_text.startswith(token, index)), None)
        if not matched_token:
            index += 1
            continue

        end = index + len(matched_token)
        while end < len(raw_text) and raw_text[end] in PAIR_CLOSE_TO_OPEN and pair_stack:
            if pair_stack[-1] == PAIR_CLOSE_TO_OPEN[raw_text[end]]:
                pair_stack.pop()
            end += 1

        if pair_stack:
            index = end
            continue

        _append_unit(units, raw_text[start:end])
        start = end
        index = end

    if start < len(raw_text):
        _append_unit(units, raw_text[start:])
    return _split_oversized_units(units, rule)


def _append_unit(units: List[SentenceUnit], text: str) -> None:
    if not text:
        return
    units.append(SentenceUnit(text=text, is_dialogue_turn=_looks_like_dialogue_turn(text)))


def _looks_like_dialogue_turn(text: str) -> bool:
    stripped = str(text or "").lstrip()
    if not stripped:
        return False
    max_speaker_length = min(12, len(stripped))
    for idx in range(max_speaker_length):
        if stripped[idx] in {"：", ":"}:
            return idx > 0
        if stripped[idx] in {"，", "。", "！", "？", "\n"}:
            return False
    return False


def _split_oversized_units(units: List[SentenceUnit], rule: SimpleStoryboardRuleConfig) -> List[SentenceUnit]:
    expanded: List[SentenceUnit] = []
    for unit in units:
        parts = [unit.text]
        if len(unit.text) > rule.hard_max_chars:
            parts = _split_text_by_boundary_tokens(
                unit.text,
                rule.hard_stop_punctuations,
                rule.target_chars_min,
            )
        refined_parts: List[str] = []
        for part in parts:
            if len(part) > rule.soft_max_chars:
                softer_parts = _split_text_by_boundary_tokens(
                    part,
                    rule.soft_stop_punctuations,
                    max(1, int(rule.target_chars_min * 0.6)),
                )
                if len(softer_parts) > 1:
                    refined_parts.extend(softer_parts)
                    continue
            refined_parts.append(part)
        for part in refined_parts:
            expanded.append(SentenceUnit(text=part, is_dialogue_turn=_looks_like_dialogue_turn(part)))
    return expanded


def _split_text_by_boundary_tokens(text: str, tokens: List[str], target_min: int) -> List[str]:
    parts: List[str] = []
    start = 0
    index = 0
    boundary_tokens = sorted([str(token) for token in tokens if str(token)], key=len, reverse=True)
    while index < len(text):
        matched_token = next((token for token in boundary_tokens if text.startswith(token, index)), None)
        if matched_token:
            end = index + len(matched_token)
            while end < len(text) and text[end] in PAIR_CLOSE_TO_OPEN:
                end += 1
            if end - start >= target_min:
                parts.append(text[start:end])
                start = end
                index = end
                continue
        index += 1
    if start < len(text):
        parts.append(text[start:])
    return [item for item in parts if item]


def merge_units_to_shots(units: List[SentenceUnit], rule: SimpleStoryboardRuleConfig) -> List[str]:
    if not units:
        return []

    shots: List[str] = []
    current_units: List[SentenceUnit] = []
    current_length = 0

    for unit in units:
        unit_length = len(unit.text)
        if not current_units:
            current_units = [unit]
            current_length = unit_length
            continue

        would_exceed_unit_limit = len(current_units) >= rule.max_units_per_shot
        would_exceed_target = current_length >= rule.target_chars_min and current_length + unit_length > rule.target_chars_max
        would_exceed_soft = current_length + unit_length > rule.soft_max_chars
        must_exceed_hard = current_length + unit_length > rule.hard_max_chars

        if must_exceed_hard or would_exceed_unit_limit or would_exceed_target or would_exceed_soft:
            shots.append("".join(item.text for item in current_units))
            current_units = [unit]
            current_length = unit_length
            continue

        current_units.append(unit)
        current_length += unit_length

    if current_units:
        shots.append("".join(item.text for item in current_units))
    return _merge_short_shots(shots, rule)


def _merge_short_shots(shots: List[str], rule: SimpleStoryboardRuleConfig) -> List[str]:
    if len(shots) <= 1:
        return shots

    merged: List[str] = []
    for shot in shots:
        if not merged:
            merged.append(shot)
            continue
        if len(shot) < rule.merge_short_fragment_threshold and len(merged[-1]) + len(shot) <= rule.soft_max_chars:
            merged[-1] = merged[-1] + shot
            continue
        merged.append(shot)
    return merged


def generate_simple_storyboard_shots(
    text: str,
    duration: int,
    rule_override: Optional[SimpleStoryboardRuleConfig] = None,
) -> List[Dict[str, Any]]:
    rule = rule_override or get_default_rule_config(duration)
    units = split_units(text, rule)
    shot_texts = merge_units_to_shots(units, rule)
    return [
        {
            "shot_number": index + 1,
            "original_text": shot_text,
        }
        for index, shot_text in enumerate(shot_texts)
        if shot_text
    ]
