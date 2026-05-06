import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

import models
from ai_config import get_ai_config
from ai_service import get_prompt_by_key
from storyboard_prompt_templates import inject_large_shot_template_content
from text_relay_service import submit_and_persist_text_task

from api.services import storyboard_prompt_context
from api.services import storyboard_reference_assets
from api.services import storyboard_video_settings


def _apply_episode_storyboard_video_settings_to_shot(shot, episode) -> Dict[str, Any]:
    settings = storyboard_video_settings.get_effective_storyboard_video_settings_for_shot(shot, episode)
    shot.storyboard_video_model = settings["model"]
    shot.storyboard_video_model_override_enabled = bool(settings["model_override_enabled"])
    shot.aspect_ratio = settings["aspect_ratio"]
    shot.duration = settings["duration"]
    shot.provider = settings["provider"]
    return settings


def build_storyboard_prompt_request_data(
    db: Session,
    *,
    shot: models.StoryboardShot,
    episode: models.Episode,
    script: models.Script,
    prompt_key: str = "generate_video_prompts",
    duration_template_field: str = "video_prompt_rule",
    large_shot_template_id: Optional[int] = None,
    reference_shot_id: Optional[int] = None,
):
    storyboard2_prompt_key = "generate_storyboard2_video_prompts"
    effective_video_settings = _apply_episode_storyboard_video_settings_to_shot(shot, episode)
    safe_duration = max(1, int(effective_video_settings["duration"] or 15))
    template_duration = 15 if safe_duration <= 15 else 25

    selected_ids = []
    try:
        selected_ids = json.loads(shot.selected_card_ids or "[]")
    except Exception:
        selected_ids = []

    selected_cards = storyboard_reference_assets.resolve_selected_cards(db, selected_ids)
    subject_text = storyboard_prompt_context.build_subject_text_for_ai(selected_cards)
    scene_text = (shot.scene_override or "").strip()
    custom_style = (script.sora_prompt_style or "").strip()
    template_field = (duration_template_field or "video_prompt_rule").strip() or "video_prompt_rule"
    excerpt = (shot.script_excerpt or "").strip()
    if not excerpt:
        raise ValueError("请先填写原剧本段落")

    large_shot_template_content = ""
    large_shot_template_name = ""
    if prompt_key == "generate_large_shot_prompts":
        large_shot_template = storyboard_prompt_context.resolve_large_shot_template(db, large_shot_template_id)
        if not large_shot_template:
            raise ValueError("大镜头模板不存在")
        large_shot_template_id = large_shot_template.id
        large_shot_template_name = (large_shot_template.name or "").strip()
        large_shot_template_content = (large_shot_template.content or "").strip()

    if custom_style:
        template_for_format = custom_style
        if prompt_key == "generate_large_shot_prompts":
            template_for_format = inject_large_shot_template_content(
                template_for_format,
                large_shot_template_content,
            )
        try:
            prompt = template_for_format.format(
                script_excerpt=excerpt,
                scene_description=scene_text,
                subject_text=subject_text,
                safe_duration=safe_duration,
                extra_style="",
                large_shot_template_content=large_shot_template_content,
            )
        except KeyError:
            prompt = template_for_format
    else:
        use_duration_template = prompt_key != storyboard2_prompt_key
        if use_duration_template:
            template = db.query(models.ShotDurationTemplate).filter(
                models.ShotDurationTemplate.duration == template_duration
            ).first()
            template_rule = str(getattr(template, template_field, "") or "").strip() if template else ""
            prompt_template = template_rule or get_prompt_by_key(prompt_key)
        else:
            prompt_template = get_prompt_by_key(prompt_key)
        template_for_format = prompt_template
        if prompt_key == "generate_large_shot_prompts":
            template_for_format = inject_large_shot_template_content(
                template_for_format,
                large_shot_template_content,
            )
        prompt = template_for_format.format(
            script_excerpt=excerpt,
            scene_description=scene_text,
            subject_text=subject_text,
            safe_duration=safe_duration,
            extra_style="",
            large_shot_template_content=large_shot_template_content,
        )

    reference_prompt = storyboard_prompt_context.resolve_sora_reference_prompt(db, episode.id, reference_shot_id)
    prompt = storyboard_prompt_context.append_sora_reference_prompt(prompt, reference_prompt)

    config = get_ai_config("video_prompt")
    request_data = {
        "model": config["model"],
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    task_payload = {
        "shot_id": int(shot.id),
        "episode_id": int(episode.id),
        "prompt_key": str(prompt_key or "generate_video_prompts"),
        "duration_template_field": template_field,
        "large_shot_template_id": int(large_shot_template_id or 0) if large_shot_template_id else None,
        "large_shot_template_name": large_shot_template_name,
        "large_shot_template_content": large_shot_template_content,
        "reference_shot_id": int(reference_shot_id or 0) if reference_shot_id else None,
    }
    return request_data, task_payload


def submit_storyboard_prompt_task(
    db: Session,
    *,
    shot: models.StoryboardShot,
    episode: models.Episode,
    script: models.Script,
    prompt_key: str = "generate_video_prompts",
    duration_template_field: str = "video_prompt_rule",
    large_shot_template_id: Optional[int] = None,
    reference_shot_id: Optional[int] = None,
):
    request_data, task_payload = build_storyboard_prompt_request_data(
        db,
        shot=shot,
        episode=episode,
        script=script,
        prompt_key=prompt_key,
        duration_template_field=duration_template_field,
        large_shot_template_id=large_shot_template_id,
        reference_shot_id=reference_shot_id,
    )
    return submit_and_persist_text_task(
        db,
        task_type="sora_prompt",
        owner_type="shot",
        owner_id=int(shot.id),
        stage_key=str(prompt_key or "video_prompt"),
        function_key="video_prompt",
        request_payload=request_data,
        task_payload=task_payload,
    )
