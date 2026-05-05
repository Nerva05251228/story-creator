from typing import Optional


def normalize_jimeng_ratio(value: Optional[str], default_ratio: str = "9:16") -> str:
    allowed_ratios = {"21:9", "16:9", "3:2", "4:3", "1:1", "3:4", "2:3", "9:16"}
    legacy_map = {
        "1:2": "9:16",
        "2:1": "16:9",
    }
    raw = (value or "").strip()
    normalized = legacy_map.get(raw, raw)
    if normalized in allowed_ratios:
        return normalized
    fallback = legacy_map.get((default_ratio or "").strip(), (default_ratio or "").strip())
    return fallback if fallback in allowed_ratios else "9:16"


def normalize_storyboard2_video_status(status: str, default_value: str = "processing") -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"completed", "success", "succeeded", "done"}:
        return "completed"
    if normalized in {"failed", "failure", "error", "cancelled", "canceled", "timeout", "timed_out"}:
        return "failed"
    if normalized in {"submitted", "pending", "queued", "waiting"}:
        return "pending"
    if normalized in {"processing", "running", "in_progress", "preparing", "starting"}:
        return "processing"
    return default_value


def is_storyboard2_video_processing(status: str) -> bool:
    return normalize_storyboard2_video_status(status, default_value="processing") in {"pending", "processing"}
