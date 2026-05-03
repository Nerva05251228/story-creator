from typing import Any, Callable, Dict, Optional

import image_platform_client
import models
from image_generation_service import normalize_image_model_key


DETAIL_IMAGE_MODEL_KEYS = {
    "seedream-4.0",
    "seedream-4.1",
    "seedream-4.5",
    "seedream-4.6",
    "nano-banana-2",
    "nano-banana-pro",
    "gpt-image-2",
}


def get_pydantic_fields_set(payload: Any) -> set:
    fields_set = getattr(payload, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(payload, "__fields_set__", set())
    return set(fields_set or set())


def normalize_detail_images_provider(
    value: Optional[str],
    default_provider: str = "",
) -> str:
    aliases = {
        "jimeng": "jimeng",
        "momo": "momo",
        "banana": "momo",
        "moti": "momo",
        "moapp": "momo",
        "gettoken": "momo",
    }
    raw = str(value or "").strip().lower()
    if raw:
        return aliases.get(raw, raw)
    fallback = str(default_provider or "").strip().lower()
    return aliases.get(fallback, fallback)


def resolve_episode_detail_images_provider(
    episode: Optional[models.Episode],
    default_provider: str = "",
) -> str:
    return normalize_detail_images_provider(
        getattr(episode, "detail_images_provider", None) if episode is not None else None,
        default_provider=default_provider,
    )


def normalize_detail_images_model(
    value: Optional[str],
    default_model: str = "seedream-4.0",
) -> str:
    raw = str(value or "").strip()
    fallback_raw = str(default_model or "").strip() or "seedream-4.0"
    normalized = normalize_image_model_key(raw or fallback_raw)
    try:
        route = image_platform_client.resolve_image_route(normalized)
        return str(route.get("key") or normalized)
    except Exception:
        if raw and normalized in DETAIL_IMAGE_MODEL_KEYS:
            return normalized
        fallback = normalize_image_model_key(fallback_raw)
        try:
            route = image_platform_client.resolve_image_route(fallback)
            return str(route.get("key") or fallback)
        except Exception:
            return fallback or "seedream-4.0"


def normalize_storyboard2_video_duration(value: Optional[int], default_value: int = 6) -> int:
    allowed = {6, 10}
    try:
        parsed = int(value) if value is not None else int(default_value)
    except Exception:
        parsed = int(default_value) if default_value in allowed else 6
    if parsed in allowed:
        return parsed
    return int(default_value) if default_value in allowed else 6


def normalize_storyboard2_image_cw(value: Optional[int], default_value: int = 50) -> int:
    try:
        parsed = int(value) if value is not None else int(default_value)
    except Exception:
        parsed = int(default_value) if default_value is not None else 50
    return max(1, min(100, parsed))


def get_first_episode_for_storyboard_defaults(script_id: int, db):
    return db.query(models.Episode).filter(
        models.Episode.script_id == script_id
    ).order_by(
        models.Episode.created_at.asc(),
        models.Episode.id.asc(),
    ).first()


def build_episode_storyboard_sora_create_values(
    script_id: int,
    episode_payload: Any,
    db,
    *,
    default_storyboard_video_model: str,
    storyboard_video_model_config: Dict[str, Dict[str, Any]],
    normalize_storyboard_video_model: Callable[..., str],
    normalize_storyboard_video_aspect_ratio: Callable[..., str],
    normalize_storyboard_video_duration: Callable[..., int],
    normalize_storyboard_video_resolution_name: Callable[..., str],
    normalize_jimeng_ratio: Callable[..., str],
    normalize_storyboard_video_appoint_account: Callable[..., str],
) -> Dict[str, Any]:
    fields_set = get_pydantic_fields_set(episode_payload)
    source_episode = get_first_episode_for_storyboard_defaults(script_id, db)

    def resolve_value(field_name: str, fallback: Any = None):
        if field_name in fields_set:
            return getattr(episode_payload, field_name, fallback)
        if source_episode is not None:
            return getattr(source_episode, field_name, fallback)
        return getattr(episode_payload, field_name, fallback)

    raw_model = normalize_storyboard_video_model(
        resolve_value("storyboard_video_model", default_storyboard_video_model),
        default_model=default_storyboard_video_model,
    )
    raw_aspect_ratio = normalize_storyboard_video_aspect_ratio(
        resolve_value("storyboard_video_aspect_ratio", None),
        model=raw_model,
        default_ratio=storyboard_video_model_config[raw_model]["default_ratio"],
    )
    raw_duration = normalize_storyboard_video_duration(
        resolve_value("storyboard_video_duration", None),
        model=raw_model,
        default_duration=storyboard_video_model_config[raw_model]["default_duration"],
    )
    raw_shot_image_size = normalize_jimeng_ratio(
        resolve_value("shot_image_size", raw_aspect_ratio),
        default_ratio=raw_aspect_ratio,
    )

    raw_video_style_template_id = resolve_value("video_style_template_id", None)
    try:
        normalized_video_style_template_id = int(raw_video_style_template_id) if raw_video_style_template_id else None
    except Exception:
        normalized_video_style_template_id = None

    return {
        "shot_image_size": raw_shot_image_size,
        "detail_images_model": normalize_detail_images_model(
            resolve_value("detail_images_model", "seedream-4.0"),
            default_model="seedream-4.0",
        ),
        "detail_images_provider": normalize_detail_images_provider(
            resolve_value("detail_images_provider", ""),
        ),
        "storyboard2_image_cw": normalize_storyboard2_image_cw(
            resolve_value("storyboard2_image_cw", 50),
            default_value=50,
        ),
        "storyboard2_include_scene_references": bool(
            resolve_value("storyboard2_include_scene_references", False)
        ),
        "storyboard_video_model": raw_model,
        "storyboard_video_aspect_ratio": raw_aspect_ratio,
        "storyboard_video_duration": raw_duration,
        "storyboard_video_resolution_name": normalize_storyboard_video_resolution_name(
            resolve_value("storyboard_video_resolution_name", None),
            model=raw_model,
            default_resolution=storyboard_video_model_config[raw_model].get("default_resolution", ""),
        ),
        "storyboard_video_appoint_account": normalize_storyboard_video_appoint_account(
            resolve_value("storyboard_video_appoint_account", "")
        ),
        "video_style_template_id": normalized_video_style_template_id,
    }
