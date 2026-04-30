from typing import Any, Dict

from image_generation_service import query_image_task_status_raw
from video_service import check_video_status


IMAGE_QUERY_TASK_TYPES = {
    "card_image_generate",
    "detail_images",
    "storyboard2_image",
    "image_generation",
}

VIDEO_QUERY_TASK_TYPES = {
    "video_generate",
    "managed_video",
    "storyboard2_video",
}


def is_dashboard_task_query_supported(record: Any) -> bool:
    if not record:
        return False
    external_task_id = str(getattr(record, "external_task_id", "") or "").strip()
    if not external_task_id:
        return False
    task_type = str(getattr(record, "task_type", "") or "").strip()
    return task_type in IMAGE_QUERY_TASK_TYPES or task_type in VIDEO_QUERY_TASK_TYPES


def query_dashboard_task(record: Any) -> Dict[str, Any]:
    if not record:
        raise ValueError("任务不存在")

    external_task_id = str(getattr(record, "external_task_id", "") or "").strip()
    if not external_task_id:
        raise ValueError("当前任务缺少 task_id，无法查询")

    task_type = str(getattr(record, "task_type", "") or "").strip()
    provider = str(getattr(record, "provider", "") or "").strip()
    model_name = str(getattr(record, "model_name", "") or "").strip()
    api_url = str(getattr(record, "api_url", "") or "").strip()
    status_api_url = str(getattr(record, "status_api_url", "") or "").strip()

    if task_type in VIDEO_QUERY_TASK_TYPES:
        query_kind = "video"
        query_result = check_video_status(external_task_id, return_raw=True)
    elif task_type in IMAGE_QUERY_TASK_TYPES:
        query_kind = "image"
        query_result = query_image_task_status_raw(
            external_task_id,
            model_name=model_name,
            provider=provider,
        )
    else:
        raise ValueError("当前任务类型不支持查询")

    return {
        "task_id": getattr(record, "id", None),
        "task_type": task_type,
        "query_kind": query_kind,
        "external_task_id": external_task_id,
        "provider": provider,
        "model_name": model_name,
        "api_url": api_url,
        "status_api_url": status_api_url,
        "query_result": query_result,
    }
