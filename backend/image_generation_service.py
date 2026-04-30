import requests
import time
import json
import os
import re
import uuid
from datetime import datetime
from threading import Thread, Semaphore, Lock
from typing import Any, Dict, List, Optional
from database import SessionLocal
from runtime_load import request_load_tracker
from dashboard_service import sync_external_task_status_to_dashboard
import models
import billing_service
import image_platform_client
from utils import upload_to_cdn

# API配置
IMAGE_PLATFORM_USERNAME = os.getenv("IMAGE_PLATFORM_USERNAME", "story_creator")
IMAGE_PLATFORM_TASKS_URL = f"{image_platform_client._base_url()}/tasks"
BANANA_IMAGE_API_BASE_URL = image_platform_client.DEFAULT_IMAGE_PLATFORM_BASE_URL
BANANA_IMAGE_API_TOKEN = (
    os.getenv("IMAGE_PLATFORM_API_TOKEN")
    or os.getenv("IMAGE_SERVICE_API_KEY")
    or image_platform_client.DEFAULT_IMAGE_PLATFORM_API_TOKEN
)
MOTI_STANDARD_IMAGE_API_BASE_URL = image_platform_client.DEFAULT_IMAGE_PLATFORM_BASE_URL
MOTI_STANDARD_IMAGE_API_TOKEN = BANANA_IMAGE_API_TOKEN

# 兼容旧变量名
API_BASE_URL = BANANA_IMAGE_API_BASE_URL
API_TOKEN = BANANA_IMAGE_API_TOKEN

# 模型配置
MODEL_CONFIGS = {
    "jimeng-4.0": {
        "name": "即梦4.0",
        "sizes": ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9", "9:21"],
        "resolutions": ["2K", "4K"],
        "supports_reference": True
    },
    "jimeng-4.1": {
        "name": "即梦4.1",
        "sizes": ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9", "9:21"],
        "resolutions": ["2K", "4K"],
        "supports_reference": True
    },
    "jimeng-4.5": {
        "name": "即梦4.5",
        "sizes": ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9", "9:21"],
        "resolutions": ["2K", "4K"],
        "supports_reference": True
    },
    "jimeng-4.6": {
        "name": "即梦4.6",
        "sizes": ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9", "9:21"],
        "resolutions": ["2K", "4K"],
        "supports_reference": True
    },
    "banana2": {
        "name": "nano banana2",
        "sizes": ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        "resolutions": ["1K", "2K", "4K"],
        "supports_reference": True
    },
    "banana2-moti": {
        "name": "nano banana2 moti",
        "sizes": ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        "supports_reference": True
    },
    "banana-pro": {
        "name": "nano banana pro",
        "sizes": ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"],
        "resolutions": ["1K", "2K", "4K"],
        "supports_reference": True
    }
}

JIMENG_MODEL_ALIASES = {
    "jimeng",
    "jimeng-4.0",
    "jimeng-4.1",
    "jimeng-4.5",
    "jimeng-4.6",
    "seedream-4-5",
    "doubao-seedance-4-5",
}
MOTI_STANDARD_IMAGE_MODEL_ALIASES = {
    "banana2-moti",
}
BANANA_IMAGE_MODEL_ALIASES = {
    "banana2",
    "banana-pro",
}

IMAGE_POLL_BUSY_INTERVAL_SECONDS = 20
IMAGE_GENERATED_BATCH_NORMAL = 6
IMAGE_GENERATED_BATCH_BUSY = 2
IMAGE_STORYBOARD_BATCH_NORMAL = 4
IMAGE_STORYBOARD_BATCH_BUSY = 1
IMAGE_DETAIL_BATCH_NORMAL = 6
IMAGE_DETAIL_BATCH_BUSY = 2
IMAGE_TRANSIENT_QUERY_FAILURE_LIMIT = 10
TRANSIENT_IMAGE_STATUS_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}

IMAGE_MODEL_ALIASES = {
    "jimeng": "seedream-4.0",
    "jimeng-4.0": "seedream-4.0",
    "jimeng-4.1": "seedream-4.1",
    "jimeng-4.5": "seedream-4.5",
    "jimeng-4.6": "seedream-4.6",
    "seedream-4-0": "seedream-4.0",
    "seedream-4-1": "seedream-4.1",
    "seedream-4-5": "seedream-4.5",
    "seedream-4-6": "seedream-4.6",
    "doubao-seedance-4-5": "seedream-4.5",
    "banana2": "nano-banana-2",
    "banana2-moti": "nano-banana-2",
    "banana-pro": "nano-banana-pro",
    "nano banana 2": "nano-banana-2",
    "nano-banana-2": "nano-banana-2",
    "nano banana pro": "nano-banana-pro",
    "nano-banana-pro": "nano-banana-pro",
    "gpt-image-2": "gpt-image-2",
    "gpt image 2": "gpt-image-2",
}

LEGACY_PROVIDER_BY_MODEL = {
    "seedream-4.0": "jimeng",
    "seedream-4.1": "jimeng",
    "seedream-4.5": "jimeng",
    "seedream-4.6": "jimeng",
    "seedream-5.0-lite": "momo",
    "nano-banana-2": "momo",
    "nano-banana-pro": "momo",
    "gpt-image-2": "momo",
}


def normalize_image_model_key(model: Optional[str]) -> str:
    normalized = str(model or "").strip().lower()
    return IMAGE_MODEL_ALIASES.get(normalized, normalized)


def is_jimeng_image_model(model: Optional[str]) -> bool:
    return normalize_image_model_key(model).startswith("seedream-4.")


def is_moti_image_model(model: Optional[str]) -> bool:
    return False


def is_banana_image_model(model: Optional[str]) -> bool:
    return normalize_image_model_key(model) in {"nano-banana-2", "nano-banana-pro"}


def resolve_jimeng_actual_model(model: Optional[str]) -> str:
    normalized = normalize_image_model_key(model)
    actual_models = {
        "jimeng-4.0": "图片 4.0",
        "jimeng-4.1": "图片 4.1",
        "jimeng-4.5": "图片 4.5",
        "jimeng-4.6": "图片 4.6",
    }
    return actual_models.get(normalized, JIMENG_DEFAULT_MODEL)


def _normalize_image_provider(provider: Optional[str]) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized in {"jimeng"}:
        return "jimeng"
    if normalized in {"moti", "moapp", "banana", "gettoken", "momo"}:
        return "momo"
    return normalized


def _resolve_image_provider(model_name: Optional[str] = None, provider: Optional[str] = None) -> str:
    normalized_provider = _normalize_image_provider(provider)
    if normalized_provider:
        return normalized_provider
    normalized_model = normalize_image_model_key(model_name)
    try:
        route = image_platform_client.resolve_image_route(normalized_model)
        return str(route.get("provider") or "").strip().lower()
    except Exception:
        return LEGACY_PROVIDER_BY_MODEL.get(normalized_model, "momo")


def get_image_submit_api_url(
    model_name: Optional[str] = None,
    provider: Optional[str] = None,
    has_reference_images: bool = False
) -> str:
    return f"{image_platform_client._base_url()}/tasks"


def get_image_status_api_url(
    task_id: Optional[str] = None,
    model_name: Optional[str] = None,
    provider: Optional[str] = None
) -> str:
    task_token = str(task_id or "{task_id}").strip() or "{task_id}"
    return f"{image_platform_client._base_url()}/tasks/{task_token}"


def _normalize_reference_image_urls(reference_images: Optional[list]) -> list:
    normalized_urls = []
    for item in reference_images or []:
        url = _normalize_remote_image_url(item)
        if url and url not in normalized_urls:
            normalized_urls.append(url)
    return normalized_urls


def _normalize_remote_image_url(raw_url: Any) -> str:
    url = str(raw_url or "").strip()
    if not url:
        return ""

    duplicate_port_pattern = re.compile(r"^(https?://[^/?#:]+):(\d+):\2(?=[:/?#]|$)", re.IGNORECASE)
    previous_url = None
    while url and url != previous_url:
        previous_url = url
        url = duplicate_port_pattern.sub(r"\1:\2", url, count=1)

    return url


def _build_image_query_failed_result(
    error_message: str,
    *,
    http_status: Optional[int] = None,
    transient: bool = False,
    raw_response: Optional[Any] = None,
) -> dict:
    result = {
        "status": "query_failed",
        "images": [],
        "progress": 0,
        "error_message": str(error_message or "").strip() or "状态查询失败",
        "query_ok": False,
        "query_http_status": http_status,
        "query_transient": bool(transient),
    }
    if raw_response is not None:
        result["raw_response"] = raw_response
    return result


def is_transient_image_status_error(status_result: Optional[dict]) -> bool:
    if not isinstance(status_result, dict):
        return False
    if status_result.get("query_ok") is False and bool(status_result.get("query_transient", False)):
        return True
    return str(status_result.get("status") or "").strip().lower() == "query_failed"


def _has_any_saved_images(detail_img: models.ShotDetailImage) -> bool:
    try:
        existing_images = json.loads(detail_img.images_json or "[]")
    except Exception:
        existing_images = []
    if not isinstance(existing_images, list):
        return False
    return any(isinstance(url, str) and url.strip() for url in existing_images)


def _extract_platform_task_id(payload: Dict[str, Any]) -> str:
    return str(
        payload.get("id")
        or payload.get("task_id")
        or payload.get("taskId")
        or payload.get("upstream_task_id")
        or ""
    ).strip()


def _is_platform_transient_exception(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
    except Exception:
        status_code = 0
    return status_code == 0 or status_code in TRANSIENT_IMAGE_STATUS_HTTP_CODES


def _submit_platform_image_task(
    *,
    prompt: str,
    model: str,
    provider: Optional[str] = None,
    size: str = "1:1",
    resolution: Optional[str] = None,
    n: int = 1,
    reference_images: Optional[list] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    requested_model = str(model or "").strip()
    route_model = normalize_image_model_key(requested_model)
    normalized_provider = _normalize_image_provider(provider) or None
    route = image_platform_client.resolve_image_route(requested_model or route_model, provider=normalized_provider)
    resolved_provider = str(route.get("provider") or normalized_provider or "")
    submit_resolution = None if resolved_provider.strip().lower() == "jimeng" else resolution
    normalized_reference_images = _normalize_reference_image_urls(reference_images)
    supports_reference = bool(route.get("supports_reference", True))
    action = "image2image" if normalized_reference_images and supports_reference else "text2image"
    submitted = image_platform_client.submit_image_task(
        prompt=prompt,
        model=str(route.get("model") or requested_model or route_model),
        username=IMAGE_PLATFORM_USERNAME,
        provider=resolved_provider,
        action=action,
        ratio=size,
        resolution=submit_resolution,
        reference_images=normalized_reference_images if normalized_reference_images and supports_reference else None,
        extra={"n": max(1, int(n or 1))},
        metadata={
            "source": "story_creator",
            "requested_model": requested_model or route_model,
            **(metadata or {}),
        },
    )
    task_id = _extract_platform_task_id(submitted)
    if not task_id:
        raise Exception(f"图片平台任务提交失败: 未获取到任务ID, result={submitted}")
    submitted["_story_creator_task_id"] = task_id
    submitted["_story_creator_route"] = route
    return submitted


def _normalize_platform_status_payload(payload: Dict[str, Any]) -> dict:
    normalized = image_platform_client.normalize_task_status_response(payload)
    raw_status = str(normalized.get("status") or payload.get("status") or "").strip()
    status_lower = raw_status.lower()
    if status_lower in {"success", "succeeded", "finished", "completed"}:
        status = "completed"
        progress = normalized.get("progress")
        if progress is None:
            progress = 100
    elif status_lower in {"failed", "fail", "error", "cancelled", "canceled", "timeout"}:
        status = "failed"
        progress = normalized.get("progress") or 0
    elif status_lower in {"submitted", "queued", "pending", "processing", "running", "in_progress", ""}:
        status = "processing"
        progress = normalized.get("progress") or 0
    else:
        status = "processing"
        progress = normalized.get("progress") or 0

    images = [
        _normalize_remote_image_url(url)
        for url in (normalized.get("images") or [])
        if _normalize_remote_image_url(url)
    ]
    response_data = {
        "status": status,
        "progress": progress,
        "images": images,
        "cost": normalized.get("cost"),
        "provider": normalized.get("provider") or payload.get("provider"),
        "model": normalized.get("model") or payload.get("model"),
        "resolution": payload.get("resolution"),
        "raw_status": raw_status,
        "raw_response": payload,
    }
    error_message = normalized.get("error_message")
    if error_message:
        response_data["error"] = error_message
        response_data["error_message"] = error_message
    return response_data


def create_jimeng_image_task(
    prompt_text: str,
    ratio: str = "1:1",
    cref: Optional[list] = None,
    name: str = "mcp_image",
    cw: Optional[int] = None,
    model: Optional[str] = None
) -> str:
    result = _submit_platform_image_task(
        prompt=prompt_text,
        model=model or "seedream-4.0",
        provider="jimeng",
        size=ratio,
        reference_images=cref,
        metadata={"name": name, "cw": _normalize_jimeng_cw(cw, default_value=JIMENG_CW) if "JIMENG_CW" in globals() else cw},
    )
    return result["_story_creator_task_id"]


def get_image_task_status(
    task_id: str,
    model_name: Optional[str] = None,
    provider: Optional[str] = None
) -> dict:
    try:
        return _normalize_platform_status_payload(image_platform_client.get_image_task(task_id))
    except Exception as exc:
        return _build_image_query_failed_result(
            f"查询异常: {str(exc)}",
            transient=_is_platform_transient_exception(exc),
        )


def query_image_task_status_raw(
    task_id: str,
    model_name: Optional[str] = None,
    provider: Optional[str] = None,
) -> dict:
    return image_platform_client.get_image_task(task_id)


def submit_image_generation(
    prompt: str,
    model: str,
    size: str = "1:1",
    resolution: Optional[str] = None,
    n: int = 1,
    reference_images: Optional[list] = None,
    provider: Optional[str] = None,
) -> str:
    """
    提交图片生成任务

    Returns:
        task_id: 任务ID
    """
    result = _submit_platform_image_task(
        prompt=prompt,
        model=model,
        provider=provider,
        size=size,
        resolution=resolution,
        n=n,
        reference_images=reference_images,
    )
    return result["_story_creator_task_id"]


def submit_moti_standard_image_generation(
    prompt: str,
    ratio: str = "1:1",
    reference_images: Optional[list] = None,
    name: Optional[str] = None
) -> str:
    """
    提交 Moti Standard 图片生成任务

    Returns:
        task_id: 任务ID
    """
    result = _submit_platform_image_task(
        prompt=prompt,
        model="nano-banana-2",
        provider="momo",
        size=ratio,
        reference_images=reference_images,
        metadata={"name": name} if name else None,
    )
    return result["_story_creator_task_id"]


def check_task_status(task_id: str, return_raw: bool = False) -> dict:
    """
    检查任务状态

    Returns:
        {
            "status": "submitted/processing/completed/failed",
            "progress": 0-100,
            "images": ["url1", "url2", ...]  # 如果completed
        }
    """
    try:
        payload = image_platform_client.get_image_task(task_id)
    except Exception as exc:
        return _build_image_query_failed_result(
            f"查询异常: {str(exc)}",
            transient=_is_platform_transient_exception(exc),
        )
    if return_raw:
        return payload
    return _normalize_platform_status_payload(payload)

    url = get_image_status_api_url(task_id=task_id, provider="banana")

    headers = {
        "Authorization": f"Bearer {BANANA_IMAGE_API_TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json={"taskId": task_id},
            timeout=60
        )
    except Exception as exc:
        return _build_image_query_failed_result(
            f"查询异常: {str(exc)}",
            transient=True,
        )

    if response.status_code != 200:
        if response.status_code in TRANSIENT_IMAGE_STATUS_HTTP_CODES:
            return _build_image_query_failed_result(
                f"状态查询失败: HTTP {response.status_code}",
                http_status=response.status_code,
                transient=True,
                raw_response=response.text if return_raw else None,
            )
        return {
            "status": "failed",
            "progress": 0,
            "raw_status": f"HTTP_{response.status_code}",
            "error": f"查询任务失败: {response.status_code}",
            "query_ok": False,
            "query_http_status": response.status_code,
            "query_transient": False,
            "raw_response": response.text if return_raw else None,
        }

    try:
        result = response.json()
    except Exception as exc:
        return _build_image_query_failed_result(
            f"查询异常: {str(exc)}",
            http_status=200,
            transient=True,
            raw_response=response.text if return_raw else None,
        )

    if return_raw:
        return result

    raw_status = str(result.get("status") or "").strip().upper()

    response_data = {
        "status": "processing",
        "progress": 0,
        "raw_status": raw_status,
    }

    if raw_status == "SUCCESS":
        images = []
        for item in result.get("results") or []:
            url_value = _normalize_remote_image_url(item.get("url")) if isinstance(item, dict) else ""
            if url_value:
                images.append(url_value)
        response_data["status"] = "completed"
        response_data["progress"] = 100
        response_data["images"] = images
        return response_data

    if raw_status in {"FAILED", "TIMEOUT"}:
        response_data["status"] = "failed"
        response_data["error"] = (
            result.get("errorMessage")
            or result.get("errorCode")
            or "生成失败"
        )
        return response_data

    return response_data


def check_moti_standard_image_status(task_id: str, return_raw: bool = False) -> dict:
    """
    查询 Moti Standard 图片任务状态
    """
    return check_task_status(task_id, return_raw=return_raw)

    url = get_image_status_api_url(task_id=task_id, provider="moti")

    headers = {
        "Authorization": f"Bearer {MOTI_STANDARD_IMAGE_API_TOKEN}"
    }

    try:
        response = requests.get(url, headers=headers, timeout=60)
    except Exception as exc:
        return _build_image_query_failed_result(
            f"查询异常: {str(exc)}",
            transient=True,
        )

    if response.status_code != 200:
        if response.status_code in TRANSIENT_IMAGE_STATUS_HTTP_CODES:
            return _build_image_query_failed_result(
                f"状态查询失败: HTTP {response.status_code}",
                http_status=response.status_code,
                transient=True,
                raw_response=response.text if return_raw else None,
            )
        return {
            "status": "failed",
            "progress": 0,
            "raw_status": f"HTTP_{response.status_code}",
            "error": f"Moti图片任务查询失败: {response.status_code}",
            "query_ok": False,
            "query_http_status": response.status_code,
            "query_transient": False,
            "raw_response": response.text if return_raw else None,
        }

    try:
        result = response.json()
    except Exception as exc:
        return _build_image_query_failed_result(
            f"查询异常: {str(exc)}",
            http_status=200,
            transient=True,
            raw_response=response.text if return_raw else None,
        )

    if return_raw:
        return result

    code = result.get("code")
    if code not in (None, 0, 200, "0", "200"):
        return {
            "status": "failed",
            "progress": 0,
            "raw_status": str(code),
            "error": result.get("msg") or result.get("message") or "未知错误",
            "query_ok": True,
            "query_http_status": 200,
            "query_transient": False,
        }

    data = result.get("data") or {}
    raw_status = str(data.get("status") or "").strip().upper()
    response_data = {
        "status": "processing",
        "progress": data.get("progress", 0),
        "raw_status": raw_status,
    }

    if raw_status in {"SUCCESS", "SUCCEEDED", "COMPLETED", "FINISHED"}:
        images = []
        for item in data.get("urls") or []:
            if isinstance(item, str):
                url_value = _normalize_remote_image_url(item)
                if url_value:
                    images.append(url_value)
                continue
            if isinstance(item, dict):
                url_value = _normalize_remote_image_url(item.get("url") or item.get("image_url"))
                if url_value:
                    images.append(url_value)
        response_data["status"] = "completed"
        response_data["images"] = images
        return response_data

    if raw_status in {"FAILED", "FAIL", "ERROR", "CANCELLED", "CANCELED"}:
        response_data["status"] = "failed"
        response_data["error"] = (
            data.get("error_msg")
            or data.get("msg")
            or result.get("msg")
            or result.get("message")
            or "生成失败"
        )
        return response_data

    return response_data


def download_and_upload_image(image_url: str, generated_image_id: int) -> str:
    """
    下载图片并上传到CDN

    Returns:
        cdn_url: CDN链接
    """
    # 下载图片
    image_url = _normalize_remote_image_url(image_url)
    response = requests.get(image_url, timeout=60)
    if response.status_code != 200:
        raise Exception(f"下载图片失败: {response.status_code}")

    # 保存到临时文件
    ext = ".png"
    filename = f"{uuid.uuid4()}{ext}"
    temp_path = os.path.join("uploads", filename)

    with open(temp_path, "wb") as f:
        f.write(response.content)

    try:
        # 上传到CDN
        cdn_url = upload_to_cdn(temp_path)

        # 删除临时文件
        if os.path.exists(temp_path):
            os.remove(temp_path)

        return cdn_url
    except Exception as e:
        # 清理临时文件
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        raise e


class ImageGenerationPoller:
    """图片生成任务轮询器（后台线程）"""

    def __init__(self):
        self.running = False
        self.thread = None

    def start(self):
        """启动轮询"""
        if self.running:
            return

        self.running = True
        self.thread = Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        print("图片生成轮询器已启动")

    def stop(self):
        """停止轮询"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        print("图片生成轮询器已停止")

    def _poll_loop(self):
        """轮询循环"""
        while self.running:
            try:
                self._poll_once()
            except Exception as e:
                print(f"轮询图片生成任务出错: {str(e)}")

            sleep_seconds = request_load_tracker.choose_interval(15, IMAGE_POLL_BUSY_INTERVAL_SECONDS)
            time.sleep(sleep_seconds)

    def _poll_once(self):
        """执行一次轮询"""
        db = SessionLocal()
        try:
            generated_limit = request_load_tracker.choose_batch_size(
                IMAGE_GENERATED_BATCH_NORMAL,
                IMAGE_GENERATED_BATCH_BUSY,
            )
            storyboard_limit = request_load_tracker.choose_batch_size(
                IMAGE_STORYBOARD_BATCH_NORMAL,
                IMAGE_STORYBOARD_BATCH_BUSY,
            )
            detail_limit = request_load_tracker.choose_batch_size(
                IMAGE_DETAIL_BATCH_NORMAL,
                IMAGE_DETAIL_BATCH_BUSY,
            )

            # 1. 查找所有处理中的主体生成图片任务
            processing_images = db.query(models.GeneratedImage).filter(
                models.GeneratedImage.status == "processing",
                models.GeneratedImage.task_id != ""
            ).order_by(
                models.GeneratedImage.created_at.asc(),
                models.GeneratedImage.id.asc()
            ).limit(generated_limit).all()

            for gen_img in processing_images:
                try:
                    # 查询任务状态
                    result = get_image_task_status(gen_img.task_id, gen_img.model_name)
                    status = result["status"]

                    if status == "completed":
                        # 下载并上传到CDN
                        images = result.get("images", [])
                        if images:
                            cdn_url = download_and_upload_image(images[0], gen_img.id)
                            gen_img.image_path = cdn_url
                            gen_img.status = "completed"

                            # ✅ 更新卡片的生成状态
                            card = db.query(models.SubjectCard).filter(
                                models.SubjectCard.id == gen_img.card_id
                            ).first()
                            if not card or card.card_type != "场景":
                                db.query(models.GeneratedImage).filter(
                                    models.GeneratedImage.card_id == gen_img.card_id,
                                    models.GeneratedImage.id != gen_img.id  # 排除当前图片
                                ).update({"is_reference": False})
                                gen_img.is_reference = True
                            if card:
                                card.generating_count = max(0, card.generating_count - 1)
                                if card.generating_count == 0:
                                    card.is_generating_images = False
                            billing_service.record_image_task_cost_for_card(
                                db,
                                card_id=int(gen_img.card_id),
                                stage="card_image_generate",
                                provider=str(result.get("provider") or ""),
                                model_name=str(result.get("model") or gen_img.model_name or ""),
                                resolution=str(result.get("resolution") or ""),
                                cost_rmb=result.get("cost"),
                                external_task_id=str(gen_img.task_id or ""),
                                billing_key=f"image:card:{gen_img.card_id}:task:{gen_img.task_id}:cost",
                                operation_key=f"image:card:{gen_img.card_id}",
                                detail_payload={
                                    "generated_image_id": int(gen_img.id),
                                    "upstream_images": images,
                                    "cdn_image": cdn_url,
                                },
                            )

                            db.commit()
                            sync_external_task_status_to_dashboard(
                                external_task_id=gen_img.task_id,
                                status="completed",
                                output_data={
                                    "task_id": gen_img.task_id,
                                    "generated_image_id": gen_img.id,
                                    "images": [cdn_url],
                                    "upstream_images": images,
                                    "model": gen_img.model_name,
                                },
                                stage="card_image_generate",
                            )
                            continue

                    elif status == "failed":
                        gen_img.status = "failed"

                        # ✅ 更新卡片的生成状态（失败也要减计数）
                        card = db.query(models.SubjectCard).filter(
                            models.SubjectCard.id == gen_img.card_id
                        ).first()
                        if card:
                            card.generating_count = max(0, card.generating_count - 1)
                            if card.generating_count == 0:
                                card.is_generating_images = False
                        billing_service.reverse_charge_entry(
                            db,
                            billing_key=f"image:card:{gen_img.card_id}:task:{gen_img.task_id}",
                            reason="provider_failed",
                        )
                        db.commit()
                        sync_external_task_status_to_dashboard(
                            external_task_id=gen_img.task_id,
                            status="failed",
                            raw_response={
                                "task_id": gen_img.task_id,
                                "error": result.get("error") or "生成失败",
                                "provider_result": result,
                                "model": gen_img.model_name,
                            },
                            stage="card_image_generate",
                        )
                        continue

                    db.commit()

                except Exception as e:
                    import traceback
                    print(f"处理图片任务 {gen_img.id} 失败: {str(e)}")
                    print(traceback.format_exc())
                    try:
                        db.rollback()
                    except Exception:
                        pass  # 如果rollback失败，继续下一个任务

            # 2. 查找所有处理中的分镜图任务
            processing_shots = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.storyboard_image_status == "processing",
                models.StoryboardShot.storyboard_image_task_id != ""
            ).order_by(
                models.StoryboardShot.id.asc()
            ).limit(storyboard_limit).all()

            for shot in processing_shots:
                try:
                    # 查询任务状态
                    result = get_image_task_status(
                        shot.storyboard_image_task_id,
                        getattr(shot, "storyboard_image_model", "")
                    )
                    status = result["status"]

                    if status == "completed":
                        # 下载并上传到CDN
                        images = result.get("images", [])
                        if images:
                            cdn_url = download_and_upload_image(images[0], shot.id)
                            shot.storyboard_image_path = cdn_url
                            shot.storyboard_image_status = "completed"
                            billing_service.record_image_task_cost_for_shot(
                                db,
                                shot_id=int(shot.id),
                                stage="storyboard_image_generate",
                                provider=str(result.get("provider") or ""),
                                model_name=str(result.get("model") or getattr(shot, "storyboard_image_model", "") or ""),
                                resolution=str(result.get("resolution") or ""),
                                cost_rmb=result.get("cost"),
                                external_task_id=str(shot.storyboard_image_task_id or ""),
                                billing_key=f"image:storyboard:{shot.id}:task:{shot.storyboard_image_task_id}:cost",
                                operation_key=f"image:storyboard:{shot.id}",
                                detail_payload={
                                    "upstream_images": images,
                                    "cdn_image": cdn_url,
                                },
                            )
                            print(f"[分镜图轮询] 镜头 {shot.id} 分镜图生成完成: {cdn_url}")
                        else:
                            print(f"[分镜图轮询] 警告：镜头 {shot.id} API返回completed但images为空")
                            shot.storyboard_image_status = "failed"
                            shot.storyboard_image_path = "error:API未返回图片"
                            billing_service.reverse_charge_entry(
                                db,
                                billing_key=f"image:storyboard:{shot.id}:task:{shot.storyboard_image_task_id}",
                                reason="provider_completed_without_images",
                            )

                    elif status == "failed":
                        shot.storyboard_image_status = "failed"
                        shot.storyboard_image_path = "error:生成失败"
                        billing_service.reverse_charge_entry(
                            db,
                            billing_key=f"image:storyboard:{shot.id}:task:{shot.storyboard_image_task_id}",
                            reason="provider_failed",
                        )
                        print(f"[分镜图轮询] 镜头 {shot.id} 分镜图生成失败")

                    db.commit()
                    print(f"[分镜图轮询] 镜头 {shot.id} 数据已提交，状态={shot.storyboard_image_status}, 路径={shot.storyboard_image_path[:50] if shot.storyboard_image_path else 'None'}...")

                except Exception as e:
                    import traceback
                    print(f"处理分镜图任务 {shot.id} 失败: {str(e)}")
                    print(traceback.format_exc())
                    try:
                        db.rollback()
                    except Exception:
                        pass  # 如果rollback失败，继续下一个任务

            # 3. 查找所有处理中且已提交 task_id 的镜头细化图任务
            processing_detail_images = db.query(models.ShotDetailImage).filter(
                models.ShotDetailImage.status == "processing",
                models.ShotDetailImage.task_id != ""
            ).order_by(
                models.ShotDetailImage.submitted_at.asc(),
                models.ShotDetailImage.id.asc()
            ).limit(detail_limit).all()

            for detail_img in processing_detail_images:
                try:
                    result = get_image_task_status(
                        detail_img.task_id,
                        getattr(detail_img, "model_name", ""),
                        getattr(detail_img, "provider", ""),
                    )
                    detail_img.last_query_at = datetime.utcnow()

                    if is_transient_image_status_error(result):
                        detail_img.query_error_count = min(
                            IMAGE_TRANSIENT_QUERY_FAILURE_LIMIT,
                            int(getattr(detail_img, "query_error_count", 0) or 0) + 1,
                        )
                        detail_img.last_query_error = str(
                            result.get("error_message")
                            or result.get("error")
                            or "状态查询失败"
                        ).strip()

                        if detail_img.query_error_count >= IMAGE_TRANSIENT_QUERY_FAILURE_LIMIT:
                            final_status = "completed" if _has_any_saved_images(detail_img) else "failed"
                            detail_img.status = final_status
                            detail_img.error_message = (
                                f"连续查询异常 {IMAGE_TRANSIENT_QUERY_FAILURE_LIMIT} 次："
                                f"{detail_img.last_query_error}"
                            )
                            if final_status == "completed":
                                billing_service.finalize_charge_entry(
                                    db,
                                    billing_key=f"image:detail:{detail_img.id}:task:{detail_img.task_id}",
                                )
                            else:
                                billing_service.reverse_charge_entry(
                                    db,
                                    billing_key=f"image:detail:{detail_img.id}:task:{detail_img.task_id}",
                                    reason="query_failed_retries_exhausted",
                                )
                            db.commit()
                            sync_external_task_status_to_dashboard(
                                external_task_id=detail_img.task_id,
                                status="failed",
                                raw_response={
                                    "task_id": detail_img.task_id,
                                    "error": detail_img.error_message,
                                    "provider_result": result,
                                    "model": getattr(detail_img, "model_name", ""),
                                    "provider": getattr(detail_img, "provider", ""),
                                    "query_error_count": detail_img.query_error_count,
                                },
                                stage="detail_images",
                            )
                            continue

                        db.commit()
                        continue

                    detail_img.query_error_count = 0
                    detail_img.last_query_error = ""

                    status = str(result.get("status") or "").strip().lower()
                    if status == "completed":
                        images = result.get("images", []) or []
                        if images:
                            uploaded_images: List[str] = []
                            for image_url in images:
                                try:
                                    cdn_url = download_and_upload_image(image_url, detail_img.id)
                                except Exception as upload_error:
                                    print(f"[detail_images] 上传候选图失败: task_id={detail_img.task_id}, url={image_url}, error={upload_error}")
                                    continue
                                normalized_cdn_url = str(cdn_url or "").strip()
                                if normalized_cdn_url and normalized_cdn_url not in uploaded_images:
                                    uploaded_images.append(normalized_cdn_url)

                            if not uploaded_images:
                                raise Exception("上游已返回图片，但全部上传CDN失败")

                            try:
                                old_images = json.loads(detail_img.images_json or "[]")
                            except Exception:
                                old_images = []
                            if not isinstance(old_images, list):
                                old_images = []

                            merged_images: List[str] = []
                            for url in uploaded_images + old_images:
                                normalized_url = str(url or "").strip()
                                if normalized_url and normalized_url not in merged_images:
                                    merged_images.append(normalized_url)

                            detail_img.images_json = json.dumps(merged_images, ensure_ascii=False)
                            detail_img.status = "completed"
                            detail_img.error_message = ""
                            billing_service.record_image_task_cost_for_shot(
                                db,
                                shot_id=int(detail_img.shot_id),
                                stage="detail_images",
                                provider=str(result.get("provider") or getattr(detail_img, "provider", "") or ""),
                                model_name=str(result.get("model") or getattr(detail_img, "model_name", "") or ""),
                                resolution=str(result.get("resolution") or ""),
                                cost_rmb=result.get("cost"),
                                external_task_id=str(detail_img.task_id or ""),
                                billing_key=f"image:detail:{detail_img.id}:task:{detail_img.task_id}:cost",
                                operation_key=f"image:detail:{detail_img.shot_id}:sub{detail_img.sub_shot_index}",
                                detail_payload={
                                    "detail_image_id": int(detail_img.id),
                                    "sub_shot_index": int(detail_img.sub_shot_index or 0),
                                    "upstream_images": images,
                                    "cdn_images": uploaded_images,
                                },
                            )

                            shot_record = db.query(models.StoryboardShot).filter(
                                models.StoryboardShot.id == detail_img.shot_id
                            ).first()
                            if shot_record:
                                shot_record.storyboard_image_path = uploaded_images[0]
                                shot_record.storyboard_image_status = "completed"

                            db.commit()
                            sync_external_task_status_to_dashboard(
                                external_task_id=detail_img.task_id,
                                status="completed",
                                output_data={
                                    "task_id": detail_img.task_id,
                                    "detail_image_id": detail_img.id,
                                    "images": uploaded_images,
                                    "upstream_images": images,
                                    "model": getattr(detail_img, "model_name", ""),
                                    "provider": getattr(detail_img, "provider", ""),
                                },
                                stage="detail_images",
                            )
                            continue

                        final_status = "completed" if _has_any_saved_images(detail_img) else "failed"
                        detail_img.status = final_status
                        detail_img.error_message = "生成任务已完成，但未返回图片"
                        if final_status == "completed":
                            billing_service.finalize_charge_entry(
                                db,
                                billing_key=f"image:detail:{detail_img.id}:task:{detail_img.task_id}",
                            )
                        else:
                            billing_service.reverse_charge_entry(
                                db,
                                billing_key=f"image:detail:{detail_img.id}:task:{detail_img.task_id}",
                                reason="provider_completed_without_images",
                            )
                        db.commit()
                        sync_external_task_status_to_dashboard(
                            external_task_id=detail_img.task_id,
                            status="failed",
                            raw_response={
                                "task_id": detail_img.task_id,
                                "error": detail_img.error_message,
                                "provider_result": result,
                                "model": getattr(detail_img, "model_name", ""),
                                "provider": getattr(detail_img, "provider", ""),
                            },
                            stage="detail_images",
                        )
                        continue

                    if status == "failed":
                        final_status = "completed" if _has_any_saved_images(detail_img) else "failed"
                        detail_img.status = final_status
                        detail_img.error_message = str(
                            result.get("error")
                            or result.get("error_message")
                            or "生成失败"
                        ).strip() or "生成失败"
                        if final_status == "completed":
                            billing_service.finalize_charge_entry(
                                db,
                                billing_key=f"image:detail:{detail_img.id}:task:{detail_img.task_id}",
                            )
                        else:
                            billing_service.reverse_charge_entry(
                                db,
                                billing_key=f"image:detail:{detail_img.id}:task:{detail_img.task_id}",
                                reason="provider_failed",
                            )
                        db.commit()
                        sync_external_task_status_to_dashboard(
                            external_task_id=detail_img.task_id,
                            status="failed",
                            raw_response={
                                "task_id": detail_img.task_id,
                                "error": detail_img.error_message,
                                "provider_result": result,
                                "model": getattr(detail_img, "model_name", ""),
                                "provider": getattr(detail_img, "provider", ""),
                            },
                            stage="detail_images",
                        )
                        continue

                    db.commit()

                except Exception as e:
                    import traceback
                    print(f"处理镜头细化图任务 {detail_img.id} 失败: {str(e)}")
                    print(traceback.format_exc())
                    try:
                        db.rollback()
                    except Exception:
                        pass

        except Exception as e:
            import traceback
            print(f"轮询外层异常: {str(e)}")
            print(traceback.format_exc())
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            try:
                db.close()
            except Exception:
                pass


# 创建全局轮询器实例
image_poller = ImageGenerationPoller()


# 工具函数：更新生成图片状态（供手动调用）
def update_generated_image_status(generated_image_id: int, db_session):
    """手动更新某个生成图片的状态"""
    gen_img = db_session.query(models.GeneratedImage).filter(
        models.GeneratedImage.id == generated_image_id
    ).first()

    if not gen_img or gen_img.status != "processing":
        return

    try:
        result = get_image_task_status(gen_img.task_id, gen_img.model_name)
        status = result["status"]

        if status == "completed":
            images = result.get("images", [])
            if images:
                cdn_url = download_and_upload_image(images[0], gen_img.id)
                gen_img.image_path = cdn_url
                gen_img.status = "completed"

                card = db_session.query(models.SubjectCard).filter(
                    models.SubjectCard.id == gen_img.card_id
                ).first()
                if not card or card.card_type != "场景":
                    db_session.query(models.GeneratedImage).filter(
                        models.GeneratedImage.card_id == gen_img.card_id,
                        models.GeneratedImage.id != gen_img.id  # 排除当前图片
                    ).update({"is_reference": False})
                    gen_img.is_reference = True

        elif status == "failed":
            gen_img.status = "failed"

        db_session.commit()

    except Exception as e:
        db_session.rollback()
        raise e


# ==================== 即梦API ====================

# 即梦API配置
JIMENG_API_BASE = image_platform_client.DEFAULT_IMAGE_PLATFORM_BASE_URL
JIMENG_UID = "Moti_StoryCreator"
JIMENG_DEFAULT_MODEL = "图片 4.0"
JIMENG_CW = 50  # 参考强度
JIMENG_GLOBAL_MAX_CONCURRENT = 3  # 即梦接口全局并发上限（进程内）
_jimeng_global_semaphore = Semaphore(JIMENG_GLOBAL_MAX_CONCURRENT)
_jimeng_global_state_lock = Lock()
_jimeng_global_waiting = 0
_jimeng_global_running = 0


def _normalize_jimeng_cw(value: Optional[int], default_value: int = JIMENG_CW) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default_value)
    return max(1, min(100, parsed))

def _normalize_jimeng_prompt_text(prompt_text: str) -> str:
    # 即梦 prompt_text 不允许换行，统一压平为空格。
    return " ".join(str(prompt_text or "").replace("\r", " ").replace("\n", " ").split())

def jimeng_create_task(
    prompt_text: str,
    ratio: str = "1:1",
    cref: Optional[list] = None,
    name: str = "mcp_image",
    cw: Optional[int] = None,
    model: Optional[str] = None
) -> dict:
    """
    创建即梦图片生成任务

    Args:
        prompt_text: 提示文本
        ratio: 图片比例，如 "1:1", "16:9", "9:16"
        cref: 参考图URL列表
        name: 任务名称

    Returns:
        {"task_id": "xxx"}
    """
    result = _submit_platform_image_task(
        prompt=prompt_text,
        model=model or "seedream-4.0",
        provider="jimeng",
        size=ratio,
        reference_images=cref,
        metadata={"name": name, "cw": _normalize_jimeng_cw(cw, default_value=JIMENG_CW)},
    )
    return {
        **result,
        "task_id": result["_story_creator_task_id"],
    }

    url = f"{JIMENG_API_BASE}/task"
    headers = {
        "x-uid": JIMENG_UID,
        "Content-Type": "application/json"
    }

    payload = {
        "model": resolve_jimeng_actual_model(model),
        "ratio": ratio,
        "cw": _normalize_jimeng_cw(cw, default_value=JIMENG_CW),
        "cref": cref if cref else [],
        "name": name,
        "prompt_text": _normalize_jimeng_prompt_text(prompt_text),
        "desc": None,
        "source": None,
        "webhook": None,
        "request_id": None
    }

    response = requests.post(url, json=payload, headers=headers, timeout=120)

    if response.status_code != 200:
        raise Exception(f"即梦API创建任务失败: {response.status_code} - {response.text}")

    result = response.json()
    return result


def jimeng_get_task_status(task_id: str, return_raw: bool = False) -> dict:
    """
    查询即梦任务状态

    Args:
        task_id: 任务ID

    Returns:
        任务状态信息，包含 status, images 等字段
    """
    try:
        payload = image_platform_client.get_image_task(task_id)
    except Exception as exc:
        return _build_image_query_failed_result(
            f"查询异常: {str(exc)}",
            transient=_is_platform_transient_exception(exc),
        )
    if return_raw:
        return payload
    return _normalize_platform_status_payload(payload)

    url = f"{JIMENG_API_BASE}/task/{task_id}"
    headers = {
        "x-uid": JIMENG_UID,
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, timeout=60)
    except Exception as exc:
        return _build_image_query_failed_result(
            f"查询异常: {str(exc)}",
            transient=True,
        )

    if response.status_code != 200:
        if response.status_code in TRANSIENT_IMAGE_STATUS_HTTP_CODES:
            return _build_image_query_failed_result(
                f"状态查询失败: HTTP {response.status_code}",
                http_status=response.status_code,
                transient=True,
                raw_response=response.text if return_raw else None,
            )
        return {
            "status": "failed",
            "progress": 0,
            "raw_status": f"HTTP_{response.status_code}",
            "error": f"即梦API查询任务失败: {response.status_code}",
            "query_ok": False,
            "query_http_status": response.status_code,
            "query_transient": False,
            "raw_response": response.text if return_raw else None,
        }

    try:
        result = response.json()
    except Exception as exc:
        return _build_image_query_failed_result(
            f"查询异常: {str(exc)}",
            http_status=200,
            transient=True,
            raw_response=response.text if return_raw else None,
        )

    if return_raw:
        return result
    return result


def jimeng_generate_image_with_polling(
    prompt_text: str,
    ratio: str = "1:1",
    cref: Optional[list] = None,
    name: str = "mcp_image",
    timeout: int = 600,
    cw: Optional[int] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    n: int = 4,
) -> dict:
    """
    创建即梦任务并轮询直到完成

    Args:
        prompt_text: 提示文本
        ratio: 图片比例
        cref: 参考图URL列表
        name: 任务名称
        timeout: 超时时间（秒），默认600秒（10分钟）

    Returns:
        {
            "success": True/False,
            "images": ["url1", "url2", ...],  # 如果成功
            "error": "错误信息"  # 如果失败
        }
    """
    try:
        submit_result = _submit_platform_image_task(
            prompt=prompt_text,
            model=model or "seedream-4.0",
            provider=provider,
            size=ratio,
            n=max(1, int(n or 1)),
            reference_images=cref,
            metadata={"name": name, "cw": _normalize_jimeng_cw(cw, default_value=JIMENG_CW)},
        )
        task_id = submit_result["_story_creator_task_id"]
        start_time = time.time()
        while time.time() - start_time < timeout:
            status_result = get_image_task_status(task_id, model_name=model, provider=provider)
            if is_transient_image_status_error(status_result):
                time.sleep(5)
                continue
            status = str(status_result.get("status") or "").strip().lower()
            if status == "completed":
                return {
                    "success": True,
                    "images": status_result.get("images") or [],
                    "task_id": task_id,
                    "provider": status_result.get("provider") or provider,
                    "model": status_result.get("model") or model,
                    "resolution": status_result.get("resolution"),
                    "cost": status_result.get("cost"),
                    "raw_response": status_result.get("raw_response"),
                }
            if status == "failed":
                return {
                    "success": False,
                    "error": status_result.get("error") or status_result.get("error_message") or "生成失败",
                    "task_id": task_id,
                    "provider": status_result.get("provider") or provider,
                    "model": status_result.get("model") or model,
                    "resolution": status_result.get("resolution"),
                    "cost": status_result.get("cost"),
                    "raw_response": status_result.get("raw_response"),
                }
            time.sleep(5)
        return {
            "success": False,
            "error": f"生成超时（超过{timeout}秒）",
            "task_id": task_id,
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "task_id": "",
        }

    global _jimeng_global_waiting, _jimeng_global_running

    acquired_global_slot = False
    with _jimeng_global_state_lock:
        _jimeng_global_waiting += 1
        waiting_now = _jimeng_global_waiting
        running_now = _jimeng_global_running
    print(
        f"[即梦并发控制] 任务进入排队: name={name}, "
        f"running={running_now}, waiting={waiting_now}, max={JIMENG_GLOBAL_MAX_CONCURRENT}"
    )

    _jimeng_global_semaphore.acquire()
    acquired_global_slot = True
    with _jimeng_global_state_lock:
        _jimeng_global_waiting = max(0, _jimeng_global_waiting - 1)
        _jimeng_global_running += 1
        waiting_now = _jimeng_global_waiting
        running_now = _jimeng_global_running
    print(
        f"[即梦并发控制] 获取执行槽位: name={name}, "
        f"running={running_now}, waiting={waiting_now}, max={JIMENG_GLOBAL_MAX_CONCURRENT}"
    )

    try:
        # 创建任务
        normalized_cw = _normalize_jimeng_cw(cw, default_value=JIMENG_CW)
        print(
            f"[即梦API] 创建任务: name={name}, ratio={ratio}, "
            f"cref数量={len(cref) if cref else 0}, cw={normalized_cw}"
        )
        create_result = jimeng_create_task(prompt_text, ratio, cref, name, cw=normalized_cw, model=model)
        task_id = create_result.get("task_id")

        if not task_id:
            print(f"[即梦API] 错误：未获取到task_id")
            return {
                "success": False,
                "error": "未获取到task_id",
                "task_id": "",
            }

        print(f"[即梦API] 任务已创建，task_id={task_id}，开始轮询...")

        # 轮询任务状态
        start_time = time.time()
        poll_count = 0
        while time.time() - start_time < timeout:
            poll_count += 1
            status_result = jimeng_get_task_status(task_id)
            status = status_result.get("status")
            elapsed = int(time.time() - start_time)

            print(f"[即梦API] 轮询#{poll_count} ({elapsed}s) - 状态: {status}")

            if status == "FINISHED":
                # 提取图片URLs
                images = status_result.get("images", [])
                image_urls = [
                    _normalize_remote_image_url(img["image_url"])
                    for img in images
                    if "image_url" in img and _normalize_remote_image_url(img["image_url"])
                ]

                print(f"[即梦API] ✓ 任务完成，生成{len(image_urls)}张图片")
                return {
                    "success": True,
                    "images": image_urls,
                    "task_id": task_id,
                }
            elif status == "FAILED":
                error_msg = status_result.get("error_msg", "生成失败")
                print(f"[即梦API] ✗ 任务失败: {error_msg}")
                return {
                    "success": False,
                    "error": error_msg,
                    "task_id": task_id,
                }

            # 等待5秒后再次查询
            time.sleep(5)

        # 超时
        print(f"[即梦API] ✗ 任务超时（{timeout}秒）")
        return {
            "success": False,
            "error": f"生成超时（超过{timeout}秒）",
            "task_id": task_id,
        }
    finally:
        if acquired_global_slot:
            _jimeng_global_semaphore.release()
            with _jimeng_global_state_lock:
                _jimeng_global_running = max(0, _jimeng_global_running - 1)
                waiting_now = _jimeng_global_waiting
                running_now = _jimeng_global_running
            print(
                f"[即梦并发控制] 释放执行槽位: name={name}, "
                f"running={running_now}, waiting={waiting_now}, max={JIMENG_GLOBAL_MAX_CONCURRENT}"
            )
