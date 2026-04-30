"""
补齐 shot_duration_templates 默认数据（6 / 10 / 15 / 25 秒）。

默认行为：
- 只插入缺失的模板，不覆盖已有内容

可选参数：
- --overwrite: 将这 4 个时长模板重置为当前项目默认内容

运行方式：
    cd backend
    python seed_shot_duration_templates.py

    cd backend
    python seed_shot_duration_templates.py --overwrite
"""

from __future__ import annotations

import base64
import json
import sys
import zlib
from pathlib import Path


CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.append(str(CURRENT_DIR))

from database import SessionLocal, engine
import models
from storyboard_prompt_templates import build_large_shot_prompt_rule


_EMBEDDED_TEMPLATES_DATA_ZLIB_BASE64 = "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"


def _load_embedded_templates_data() -> list[dict]:
    compressed = base64.b64decode(_EMBEDDED_TEMPLATES_DATA_ZLIB_BASE64.encode("ascii"))
    payload = zlib.decompress(compressed).decode("utf-8")
    return json.loads(payload)


try:
    from migrations.create_shot_duration_templates import TEMPLATES_DATA as _MIGRATION_TEMPLATES_DATA
    TEMPLATES_DATA = _MIGRATION_TEMPLATES_DATA
except Exception:
    TEMPLATES_DATA = _load_embedded_templates_data()


TARGET_DURATIONS = (15, 25)


def _remove_scene_description_placeholder_block(prompt_text: str) -> str:
    text_value = str(prompt_text or "")
    if not text_value:
        return text_value

    replacements = (
        (
            "{script_excerpt}\n\n场景描述：\n{scene_description}\n\n出镜主体：\n{subject_text}",
            "{script_excerpt}\n\n出镜主体：\n{subject_text}",
        ),
        (
            "{script_excerpt}\r\n\r\n场景描述：\r\n{scene_description}\r\n\r\n出镜主体：\r\n{subject_text}",
            "{script_excerpt}\r\n\r\n出镜主体：\r\n{subject_text}",
        ),
        ("\n\n场景描述：\n{scene_description}", ""),
        ("\r\n\r\n场景描述：\r\n{scene_description}", ""),
    )

    updated = text_value
    for old_value, new_value in replacements:
        updated = updated.replace(old_value, new_value, 1)
    return updated


def _subject_personality_hint_text() -> str:
    return (
        "说明：\n"
        "- 角色主体会按“主体名-性格描述”的格式提供，场景主体只写名称\n"
        "- 请在画面设计、动作、表情和情绪表现中参考对应角色的性格信息"
    )


def _is_subject_personality_hint_line(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False

    if stripped == "说明：":
        return True

    hint_keywords = (
        "主体名-性格描述",
        "角色主体会按",
        "场景主体只写名称",
        "画面设计",
        "动作、表情和情绪表现",
        "性格信息",
        "subjects 数组",
        "后面的性格描述写进去",
    )
    if any(keyword in stripped for keyword in hint_keywords):
        return True

    normalized = stripped.replace(" ", "").replace("-", "").replace("·", "").replace(":", "").replace("：", "")
    if not normalized:
        return False

    question_ratio = normalized.count("?") / len(normalized)
    return question_ratio >= 0.4


def _find_subject_personality_section_end(text_value: str, start_index: int) -> int:
    section_markers = (
        "\n\n输出 JSON",
        "\n\n可选的景别",
        "\n\n要求：",
        "\n\n输出要求",
        "\n\n输出格式",
        "\n\n请输出",
        "\n\n示例：",
    )
    positions = []
    for marker in section_markers:
        marker_index = text_value.find(marker, start_index)
        if marker_index != -1:
            positions.append(marker_index)
    return min(positions) if positions else -1


def _inject_subject_personality_hint(prompt_text: str) -> str:
    text_value = str(prompt_text or "")
    if not text_value:
        return text_value

    hint_text = _subject_personality_hint_text()

    if "{subject_text}" in text_value:
        subject_end = text_value.find("{subject_text}") + len("{subject_text}")
        section_end = _find_subject_personality_section_end(text_value, subject_end)
        if section_end != -1:
            existing_block = text_value[subject_end:section_end]
            preserved_lines = []
            for raw_line in existing_block.splitlines():
                stripped = raw_line.strip()
                if not stripped:
                    continue
                if _is_subject_personality_hint_line(stripped):
                    continue
                preserved_lines.append(raw_line.rstrip())

            normalized_block = "\n\n" + hint_text
            if preserved_lines:
                normalized_block += "\n\n" + "\n".join(preserved_lines)
            return text_value[:subject_end] + normalized_block + text_value[section_end:]

        return text_value.replace("{subject_text}", "{subject_text}\n\n" + hint_text, 1)

    if "说明：" in text_value or "主体名-性格描述" in text_value or "subjects 数组" in text_value:
        cleaned_lines = []
        for raw_line in text_value.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                cleaned_lines.append("")
                continue
            if _is_subject_personality_hint_line(stripped):
                continue
            cleaned_lines.append(raw_line.rstrip())
        text_value = "\n".join(cleaned_lines).strip()

    return f"{text_value.rstrip()}\n\n{hint_text}".strip()


def _build_template_payload(raw_template: dict) -> dict:
    video_prompt_rule = str(raw_template["video_prompt_rule"] or "")
    video_prompt_rule = _remove_scene_description_placeholder_block(video_prompt_rule)
    video_prompt_rule = _inject_subject_personality_hint(video_prompt_rule)
    duration = int(raw_template["duration"])
    time_segments = int(raw_template["time_segments"])

    return {
        "duration": duration,
        "shot_count_min": int(raw_template["shot_count_min"]),
        "shot_count_max": int(raw_template["shot_count_max"]),
        "time_segments": time_segments,
        "simple_storyboard_rule": str(raw_template["simple_storyboard_rule"] or ""),
        "video_prompt_rule": video_prompt_rule,
        "large_shot_prompt_rule": build_large_shot_prompt_rule(duration, time_segments),
        "is_default": duration == 15,
    }


def main() -> int:
    overwrite = "--overwrite" in sys.argv

    models.Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        existing_map = {
            item.duration: item
            for item in db.query(models.ShotDurationTemplate).filter(
                models.ShotDurationTemplate.duration.in_(TARGET_DURATIONS)
            ).all()
        }

        created_count = 0
        updated_count = 0
        skipped_count = 0

        for raw_template in TEMPLATES_DATA:
            payload = _build_template_payload(raw_template)
            duration = payload["duration"]
            existing = existing_map.get(duration)

            if existing is None:
                db.add(models.ShotDurationTemplate(**payload))
                created_count += 1
                continue

            if not overwrite:
                skipped_count += 1
                continue

            existing.shot_count_min = payload["shot_count_min"]
            existing.shot_count_max = payload["shot_count_max"]
            existing.time_segments = payload["time_segments"]
            existing.simple_storyboard_rule = payload["simple_storyboard_rule"]
            existing.video_prompt_rule = payload["video_prompt_rule"]
            existing.large_shot_prompt_rule = payload["large_shot_prompt_rule"]
            existing.is_default = payload["is_default"]
            updated_count += 1

        for template in db.query(models.ShotDurationTemplate).filter(
            models.ShotDurationTemplate.duration.in_(TARGET_DURATIONS)
        ).all():
            template.is_default = template.duration == 15

        db.commit()

        print("shot_duration_templates 补齐完成")
        print(f"- created: {created_count}")
        print(f"- updated: {updated_count}")
        print(f"- skipped: {skipped_count}")
        print(f"- overwrite: {overwrite}")
        print(f"- durations: {list(TARGET_DURATIONS)}")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"shot_duration_templates 补齐失败: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
