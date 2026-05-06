from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

import models
from ai_config import get_ai_config
from ai_service import get_prompt_by_key
from text_relay_service import submit_and_persist_text_task


DEFAULT_OPENING_TEMPLATE = "我想把这个片段做成一个短视频，需要一个精彩吸引人的开头，请你帮我写一个开头"


def _build_request_payload(model: str, prompt: str, *, response_format_json: bool = False) -> Dict[str, Any]:
    request_payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "stream": False,
    }
    if response_format_json:
        request_payload["response_format"] = {"type": "json_object"}
    return request_payload


def resolve_narration_template(
    episode: models.Episode,
    db: Session,
    custom_template: Optional[str] = None,
) -> str:
    template = str(custom_template or "").strip()
    if template:
        return template

    script = getattr(episode, "script", None)
    if script and getattr(script, "narration_template", None):
        template = str(script.narration_template or "").strip()
        if template:
            return template

    template_setting = db.query(models.GlobalSettings).filter(
        models.GlobalSettings.key == "narration_conversion_template"
    ).first()
    return str(getattr(template_setting, "value", "") or "").strip()


def resolve_opening_template(db: Session, custom_template: Optional[str] = None) -> str:
    template = str(custom_template or "").strip()
    if template:
        return template

    template_setting = db.query(models.GlobalSettings).filter(
        models.GlobalSettings.key == "opening_generation_template"
    ).first()
    template = str(getattr(template_setting, "value", "") or "").strip()
    if template:
        return template

    return DEFAULT_OPENING_TEMPLATE


def submit_episode_text_relay_task(
    db: Session,
    *,
    episode: models.Episode,
    task_type: str,
    function_key: str,
    prompt: str,
    response_format_json: bool = False,
    extra_task_payload: Optional[Dict[str, Any]] = None,
):
    config = get_ai_config(function_key)
    request_payload = _build_request_payload(
        config["model"],
        prompt,
        response_format_json=response_format_json,
    )
    task_payload = dict(extra_task_payload or {})
    task_payload.update(
        {
            "episode_id": int(episode.id),
            "task_type": task_type,
            "function_key": function_key,
        }
    )

    return submit_and_persist_text_task(
        db,
        task_type=task_type,
        owner_type="episode",
        owner_id=int(episode.id),
        stage_key=task_type,
        function_key=function_key,
        request_payload=request_payload,
        task_payload=task_payload,
    )


def submit_detailed_storyboard_stage1_task(
    db: Session,
    *,
    episode_id: int,
    simple_shots: List[Dict[str, Any]],
):
    shots_content = ""
    for shot in simple_shots:
        shot_num = shot.get("shot_number", "?")
        original_text = shot.get("original_text", "")
        shots_content += f"镜头{shot_num}:\n{original_text}\n\n"

    prompt_template = get_prompt_by_key("detailed_storyboard_content_analysis")
    prompt = prompt_template.format(shots_content=shots_content)
    config = get_ai_config("detailed_storyboard_s1")
    request_payload = _build_request_payload(
        config["model"],
        prompt,
        response_format_json=True,
    )
    task_payload = {
        "episode_id": int(episode_id),
        "simple_shots": simple_shots,
    }

    return submit_and_persist_text_task(
        db,
        task_type="detailed_storyboard_stage1",
        owner_type="episode",
        owner_id=int(episode_id),
        stage_key="detailed_storyboard_stage1",
        function_key="detailed_storyboard_s1",
        request_payload=request_payload,
        task_payload=task_payload,
    )
