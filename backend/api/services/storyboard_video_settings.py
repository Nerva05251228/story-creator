from typing import Any, Optional

from api.schemas.episodes import DEFAULT_STORYBOARD_VIDEO_MODEL


STORYBOARD_VIDEO_MODEL_CONFIG = {
    "sora-2": {
        "aspect_ratios": ("16:9", "9:16"),
        "durations": (10, 15, 25),
        "default_ratio": "16:9",
        "default_duration": 15,
        "resolution_names": (),
        "default_resolution": "",
        "provider": "yijia",
    },
    "grok": {
        "aspect_ratios": ("21:9", "16:9", "3:2", "4:3", "1:1", "3:4", "2:3", "9:16"),
        "durations": (10, 20, 30),
        "default_ratio": "9:16",
        "default_duration": 10,
        "resolution_names": ("480p", "720p"),
        "default_resolution": "720p",
        "provider": "yijia",
    },
    "Seedance 2.0 Fast VIP": {
        "aspect_ratios": ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"),
        "durations": tuple(range(4, 16)),
        "default_ratio": "16:9",
        "default_duration": 10,
        "resolution_names": (),
        "default_resolution": "",
        "provider": "moti",
    },
    "Seedance 2.0 Fast": {
        "aspect_ratios": ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"),
        "durations": tuple(range(4, 16)),
        "default_ratio": "16:9",
        "default_duration": 10,
        "resolution_names": (),
        "default_resolution": "",
        "provider": "moti",
    },
    "Seedance 2.0 VIP": {
        "aspect_ratios": ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"),
        "durations": tuple(range(4, 16)),
        "default_ratio": "16:9",
        "default_duration": 10,
        "resolution_names": (),
        "default_resolution": "",
        "provider": "moti",
    },
    "Seedance 2.0": {
        "aspect_ratios": ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"),
        "durations": tuple(range(4, 16)),
        "default_ratio": "16:9",
        "default_duration": 10,
        "resolution_names": (),
        "default_resolution": "",
        "provider": "moti",
    },
}

MOTI_STORYBOARD_VIDEO_MODELS = (
    "Seedance 2.0 Fast VIP",
    "Seedance 2.0 Fast",
    "Seedance 2.0 VIP",
    "Seedance 2.0",
)


def normalize_storyboard_video_appoint_account(value: Any, default_value: str = "") -> str:
    return str(value if value is not None else default_value or "").strip()


def normalize_storyboard_video_model(
    value: Optional[str],
    default_model: str = DEFAULT_STORYBOARD_VIDEO_MODEL,
) -> str:
    raw = (value or "").strip()
    if raw in STORYBOARD_VIDEO_MODEL_CONFIG:
        return raw
    fallback = (default_model or "").strip()
    if fallback in STORYBOARD_VIDEO_MODEL_CONFIG:
        return fallback
    return DEFAULT_STORYBOARD_VIDEO_MODEL


def normalize_storyboard_video_aspect_ratio(
    value: Optional[str],
    model: str,
    default_ratio: str = "16:9",
) -> str:
    model_key = normalize_storyboard_video_model(model, default_model=DEFAULT_STORYBOARD_VIDEO_MODEL)
    config = STORYBOARD_VIDEO_MODEL_CONFIG[model_key]
    allowed = tuple(config["aspect_ratios"])
    legacy_map = {
        "1:2": "9:16",
        "2:1": "16:9",
    }
    raw = (value or "").strip()
    normalized = legacy_map.get(raw, raw)
    if normalized in allowed:
        return normalized
    fallback_raw = (default_ratio or "").strip()
    fallback = legacy_map.get(fallback_raw, fallback_raw)
    if fallback in allowed:
        return fallback
    default_value = config["default_ratio"]
    if default_value in allowed:
        return default_value
    return allowed[0]


def normalize_storyboard_video_duration(
    value: Optional[int],
    model: str,
    default_duration: Optional[int] = None,
) -> int:
    model_key = normalize_storyboard_video_model(model, default_model=DEFAULT_STORYBOARD_VIDEO_MODEL)
    config = STORYBOARD_VIDEO_MODEL_CONFIG[model_key]
    allowed = tuple(int(item) for item in config["durations"])
    if default_duration is None:
        fallback = int(config["default_duration"])
    else:
        try:
            fallback = int(default_duration)
        except Exception:
            fallback = int(config["default_duration"])
    if fallback not in allowed:
        fallback = int(config["default_duration"])
    try:
        parsed = int(value) if value is not None else fallback
    except Exception:
        parsed = fallback
    if parsed in allowed:
        return parsed
    return fallback


def normalize_storyboard_video_resolution_name(
    value: Optional[str],
    model: str,
    default_resolution: str = "",
) -> str:
    model_key = normalize_storyboard_video_model(model, default_model=DEFAULT_STORYBOARD_VIDEO_MODEL)
    config = STORYBOARD_VIDEO_MODEL_CONFIG[model_key]
    allowed = tuple(str(item).strip() for item in config.get("resolution_names", ()) if str(item).strip())
    if not allowed:
        return ""
    fallback_raw = str(default_resolution or config.get("default_resolution") or "").strip().lower()
    fallback = (
        fallback_raw
        if fallback_raw in allowed
        else str(config.get("default_resolution") or allowed[0]).strip().lower()
    )
    raw = str(value or "").strip().lower()
    if raw in allowed:
        return raw
    return fallback


def resolve_storyboard_video_provider(model: str) -> str:
    model_key = normalize_storyboard_video_model(model, default_model=DEFAULT_STORYBOARD_VIDEO_MODEL)
    return str(STORYBOARD_VIDEO_MODEL_CONFIG[model_key]["provider"])


def is_moti_storyboard_video_model(model: Optional[str]) -> bool:
    return normalize_storyboard_video_model(
        model,
        default_model=DEFAULT_STORYBOARD_VIDEO_MODEL,
    ) in MOTI_STORYBOARD_VIDEO_MODELS


def resolve_storyboard_video_model_by_provider(
    provider: Optional[str],
    default_model: str = DEFAULT_STORYBOARD_VIDEO_MODEL,
) -> str:
    raw = (provider or "").strip().lower()
    if raw in {"yijia-grok", "yijia"}:
        normalized_default = normalize_storyboard_video_model(
            default_model,
            default_model=DEFAULT_STORYBOARD_VIDEO_MODEL,
        )
        if normalized_default in {"sora-2", "grok"}:
            return normalized_default
        return "grok"
    if raw == "moti":
        normalized_default = normalize_storyboard_video_model(
            default_model,
            default_model=DEFAULT_STORYBOARD_VIDEO_MODEL,
        )
        if is_moti_storyboard_video_model(normalized_default):
            return normalized_default
        return DEFAULT_STORYBOARD_VIDEO_MODEL
    return normalize_storyboard_video_model(default_model, default_model=DEFAULT_STORYBOARD_VIDEO_MODEL)
