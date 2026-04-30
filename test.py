#!/usr/bin/env python3
"""
Standalone upstream image API tester.

Edit the plain-text config block below, then run:
    python test.py

Supported models:
    - jimeng-4.0
    - jimeng-4.1
    - jimeng-4.5
    - jimeng-4.6
    - banana2
    - banana-pro
    - banana2-moti

Supported actions:
    - text2image
    - image2image
    - query

What differs by model:
    - Provider is different:
        jimeng-* -> jimeng
        banana2-moti -> moti
        banana2 / banana-pro -> banana
    - Aspect ratio support is different by model family.
    - Resolution support is different by model family.
    - Banana submit URL changes by model and mode:
        banana2/text-to-image
        banana2/image-to-image
        banana_pro/text-to-image
        banana_pro/image-to-image

Notes:
    - Current image-to-image flows in this project use remote image URLs.
    - The script prints the selected model's supported parameters before submit.
    - This script intentionally follows the current project integration behavior.
      Example: jimeng models are listed with 2K/4K capability in project config,
      but this standalone script does not upload a resolution field for jimeng.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import requests


# ============================================================================
# Plain-text config: fill these directly
# ============================================================================
JIMENG_BASE_URL = "https://api.ali-fc.moapp.net.cn/jimeng"
JIMENG_UID = "Moti_StoryCreator"

MOTI_BASE_URL = "https://api.ali-fc.moapp.net.cn/image_gen_control/v1"
MOTI_API_KEY = "sk-Zv2THcS1J7KDZkQ-griUI6UlRSNcgQhvTXu70tuvRBw"

BANANA_BASE_URL = "https://nb.gettoken.cn/openapi/v1"
BANANA_API_KEY = "sk-hrST9TrSTxknWmlcgZN6VaUvkja0qIZ3BXnaDanOz1g"

TIMEOUT = 120
POLL_INTERVAL_SECONDS = 3
MAX_POLL_ROUNDS = 120

SUPPORTED_ACTIONS = {"text2image", "image2image", "query"}
JIMENG_SUPPORTED_RATIOS = ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9", "9:21"]
JIMENG_SUPPORTED_RESOLUTIONS = ["2K", "4K"]
BANANA_SUPPORTED_RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]
BANANA_SUPPORTED_RESOLUTIONS = ["1K", "2K", "4K"]


# ============================================================================
# One-run config: change these for each call
# ============================================================================
PROVIDER = "banana"           # jimeng / moti / banana ; banana2-moti must use moti
MODEL = "banana2"             # jimeng-4.0 / jimeng-4.1 / jimeng-4.5 / jimeng-4.6 / banana2 / banana-pro / banana2-moti
ACTION = "text2image"         # text2image / image2image / query
AUTO_POLL = True              # only used for text2image / image2image

PROMPT = "A cinematic portrait of a young warrior, dramatic lighting, ultra detailed."
RATIO = "9:16"                # supported values depend on MODEL, see capability summary
RESOLUTION = "2K"             # banana2 / banana-pro: 1K, 2K, 4K ; banana2-moti: leave empty
NAME = "codex_test_image_task"  # jimeng / moti can use it
CW = 50                       # jimeng only

# Image-to-image uses remote image URLs in current upstream integration.
REFERENCE_IMAGE_URLS = [
    "https://example.com/your-reference-image.png",
]

# Query mode only
QUERY_TASK_ID = ""


JIMENG_ACTUAL_MODELS = {
    "jimeng-4.0": "图片 4.0",
    "jimeng-4.1": "图片 4.1",
    "jimeng-4.5": "图片 4.5",
    "jimeng-4.6": "图片 4.6",
}


def _build_capability(
    *,
    provider: str,
    display_name: str,
    ratios: List[str],
    resolutions: List[str],
    supports_reference: bool,
    submit_fields: List[str],
    query_method: str,
    submit_endpoints: Dict[str, str],
    notes: List[str],
    uploads_resolution: bool,
) -> Dict[str, Any]:
    return {
        "provider": provider,
        "display_name": display_name,
        "ratios": list(ratios),
        "resolutions": list(resolutions),
        "supports_reference": bool(supports_reference),
        "supports_actions": ["text2image", "image2image", "query"],
        "submit_fields": list(submit_fields),
        "query_method": query_method,
        "submit_endpoints": dict(submit_endpoints),
        "notes": list(notes),
        "uploads_resolution": bool(uploads_resolution),
    }


MODEL_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "jimeng-4.0": _build_capability(
        provider="jimeng",
        display_name="jimeng-4.0",
        ratios=JIMENG_SUPPORTED_RATIOS,
        resolutions=JIMENG_SUPPORTED_RESOLUTIONS,
        supports_reference=True,
        submit_fields=["model", "ratio", "cw", "cref", "name", "prompt_text"],
        query_method="GET /jimeng/task/{task_id}",
        submit_endpoints={
            "text2image": "POST /jimeng/task",
            "image2image": "POST /jimeng/task",
            "query": "GET /jimeng/task/{task_id}",
        },
        notes=[
            "ratio is uploaded",
            "reference image URLs are uploaded in cref for image2image",
            "current standalone script does not upload a resolution field for jimeng",
        ],
        uploads_resolution=False,
    ),
    "jimeng-4.1": _build_capability(
        provider="jimeng",
        display_name="jimeng-4.1",
        ratios=JIMENG_SUPPORTED_RATIOS,
        resolutions=JIMENG_SUPPORTED_RESOLUTIONS,
        supports_reference=True,
        submit_fields=["model", "ratio", "cw", "cref", "name", "prompt_text"],
        query_method="GET /jimeng/task/{task_id}",
        submit_endpoints={
            "text2image": "POST /jimeng/task",
            "image2image": "POST /jimeng/task",
            "query": "GET /jimeng/task/{task_id}",
        },
        notes=[
            "ratio is uploaded",
            "reference image URLs are uploaded in cref for image2image",
            "current standalone script does not upload a resolution field for jimeng",
        ],
        uploads_resolution=False,
    ),
    "jimeng-4.5": _build_capability(
        provider="jimeng",
        display_name="jimeng-4.5",
        ratios=JIMENG_SUPPORTED_RATIOS,
        resolutions=JIMENG_SUPPORTED_RESOLUTIONS,
        supports_reference=True,
        submit_fields=["model", "ratio", "cw", "cref", "name", "prompt_text"],
        query_method="GET /jimeng/task/{task_id}",
        submit_endpoints={
            "text2image": "POST /jimeng/task",
            "image2image": "POST /jimeng/task",
            "query": "GET /jimeng/task/{task_id}",
        },
        notes=[
            "ratio is uploaded",
            "reference image URLs are uploaded in cref for image2image",
            "current standalone script does not upload a resolution field for jimeng",
        ],
        uploads_resolution=False,
    ),
    "jimeng-4.6": _build_capability(
        provider="jimeng",
        display_name="jimeng-4.6",
        ratios=JIMENG_SUPPORTED_RATIOS,
        resolutions=JIMENG_SUPPORTED_RESOLUTIONS,
        supports_reference=True,
        submit_fields=["model", "ratio", "cw", "cref", "name", "prompt_text"],
        query_method="GET /jimeng/task/{task_id}",
        submit_endpoints={
            "text2image": "POST /jimeng/task",
            "image2image": "POST /jimeng/task",
            "query": "GET /jimeng/task/{task_id}",
        },
        notes=[
            "ratio is uploaded",
            "reference image URLs are uploaded in cref for image2image",
            "current standalone script does not upload a resolution field for jimeng",
        ],
        uploads_resolution=False,
    ),
    "banana2": _build_capability(
        provider="banana",
        display_name="banana2",
        ratios=BANANA_SUPPORTED_RATIOS,
        resolutions=BANANA_SUPPORTED_RESOLUTIONS,
        supports_reference=True,
        submit_fields=["prompt", "aspectRatio", "resolution", "imageUrls"],
        query_method="POST /query with {taskId}",
        submit_endpoints={
            "text2image": "POST /banana2/text-to-image",
            "image2image": "POST /banana2/image-to-image",
            "query": "POST /query",
        },
        notes=[
            "ratio is uploaded as aspectRatio",
            "resolution is uploaded when provided",
            "reference image URLs are uploaded in imageUrls for image2image",
        ],
        uploads_resolution=True,
    ),
    "banana-pro": _build_capability(
        provider="banana",
        display_name="banana-pro",
        ratios=BANANA_SUPPORTED_RATIOS,
        resolutions=BANANA_SUPPORTED_RESOLUTIONS,
        supports_reference=True,
        submit_fields=["prompt", "aspectRatio", "resolution", "imageUrls"],
        query_method="POST /query with {taskId}",
        submit_endpoints={
            "text2image": "POST /banana_pro/text-to-image",
            "image2image": "POST /banana_pro/image-to-image",
            "query": "POST /query",
        },
        notes=[
            "ratio is uploaded as aspectRatio",
            "resolution is uploaded when provided",
            "reference image URLs are uploaded in imageUrls for image2image",
        ],
        uploads_resolution=True,
    ),
    "banana2-moti": _build_capability(
        provider="moti",
        display_name="banana2-moti",
        ratios=BANANA_SUPPORTED_RATIOS,
        resolutions=[],
        supports_reference=True,
        submit_fields=["prompt", "ratio", "name", "image"],
        query_method="GET /images/generations/{task_id}",
        submit_endpoints={
            "text2image": "POST /images/generations",
            "image2image": "POST /images/generations",
            "query": "GET /images/generations/{task_id}",
        },
        notes=[
            "ratio is uploaded as ratio",
            "resolution is not supported by the current moti image interface",
            "reference image URLs are uploaded in image for image2image",
        ],
        uploads_resolution=False,
    ),
}


def pretty(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return {"raw_text": response.text}


def normalize_url(url: str) -> str:
    return str(url or "").rstrip("/")


def normalize_reference_urls(reference_urls: Optional[List[str]]) -> List[str]:
    return [str(item).strip() for item in (reference_urls or []) if str(item).strip()]


def get_model_capabilities(model: str) -> Dict[str, Any]:
    normalized_model = str(model or "").strip().lower()
    capabilities = MODEL_CAPABILITIES.get(normalized_model)
    if not capabilities:
        raise ValueError(
            f"Unknown MODEL={model!r}. Supported models: {', '.join(sorted(MODEL_CAPABILITIES.keys()))}"
        )
    return {
        "model": normalized_model,
        **capabilities,
        "ratios": list(capabilities.get("ratios") or []),
        "resolutions": list(capabilities.get("resolutions") or []),
        "supports_actions": list(capabilities.get("supports_actions") or []),
        "submit_fields": list(capabilities.get("submit_fields") or []),
        "notes": list(capabilities.get("notes") or []),
        "submit_endpoints": dict(capabilities.get("submit_endpoints") or {}),
    }


def validate_run_config(
    provider: str,
    model: str,
    action: str,
    ratio: str,
    resolution: str,
    reference_urls: Optional[List[str]],
) -> Dict[str, Any]:
    normalized_provider = str(provider or "").strip().lower()
    normalized_model = str(model or "").strip().lower()
    normalized_action = str(action or "").strip().lower()
    normalized_ratio = str(ratio or "").strip()
    normalized_resolution = str(resolution or "").strip()
    normalized_reference_urls = normalize_reference_urls(reference_urls)
    capabilities = get_model_capabilities(normalized_model)

    if normalized_provider != capabilities["provider"]:
        raise ValueError(
            f"Provider/model mismatch: MODEL={normalized_model} must use PROVIDER={capabilities['provider']}"
        )

    if normalized_action not in SUPPORTED_ACTIONS:
        raise ValueError(
            f"Unsupported ACTION={action!r}. Supported actions: {', '.join(sorted(SUPPORTED_ACTIONS))}"
        )

    if normalized_action != "query":
        if normalized_ratio and normalized_ratio not in capabilities["ratios"]:
            raise ValueError(
                f"Unsupported ratio {normalized_ratio!r} for MODEL={normalized_model}. "
                f"Supported ratios: {', '.join(capabilities['ratios'])}"
            )
        if normalized_resolution:
            if not capabilities["uploads_resolution"]:
                raise ValueError(
                    f"MODEL={normalized_model} does not upload resolution in the current interface. "
                    "Leave RESOLUTION empty."
                )
            if normalized_resolution not in capabilities["resolutions"]:
                raise ValueError(
                    f"Unsupported resolution {normalized_resolution!r} for MODEL={normalized_model}. "
                    f"Supported resolutions: {', '.join(capabilities['resolutions'])}"
                )
        if normalized_action == "image2image":
            if not capabilities["supports_reference"]:
                raise ValueError(f"MODEL={normalized_model} does not support image2image reference inputs")
            if not normalized_reference_urls:
                raise ValueError("ACTION=image2image requires at least one REFERENCE_IMAGE_URLS item")

    return {
        "provider": normalized_provider,
        "model": normalized_model,
        "action": normalized_action,
        "ratio": normalized_ratio,
        "resolution": normalized_resolution,
        "reference_urls": normalized_reference_urls,
        "capabilities": capabilities,
    }


def print_selected_model_capabilities(model: str, action: str) -> None:
    capabilities = get_model_capabilities(model)
    normalized_action = str(action or "").strip().lower()
    print("=" * 100)
    print(f"Selected model capability summary: {capabilities['model']}")
    print(f"Provider: {capabilities['provider']}")
    print(f"Supported actions: {', '.join(capabilities['supports_actions'])}")
    print(f"Supported ratios: {', '.join(capabilities['ratios'])}")
    if capabilities["resolutions"]:
        print(f"Supported resolutions: {', '.join(capabilities['resolutions'])}")
    else:
        print("Supported resolutions: none")
    print(f"Supports reference images: {capabilities['supports_reference']}")
    print(f"Resolution is uploaded by this script: {capabilities['uploads_resolution']}")
    print(f"Query method: {capabilities['query_method']}")
    if normalized_action in capabilities["submit_endpoints"]:
        print(f"Endpoint for ACTION={normalized_action}: {capabilities['submit_endpoints'][normalized_action]}")
    print(f"Fields uploaded by this script: {', '.join(capabilities['submit_fields'])}")
    for note in capabilities["notes"]:
        print(f"- {note}")


def jimeng_headers() -> Dict[str, str]:
    return {
        "x-uid": JIMENG_UID,
        "Content-Type": "application/json",
    }


def moti_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {MOTI_API_KEY}",
        "Content-Type": "application/json",
    }


def banana_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {BANANA_API_KEY}",
        "Content-Type": "application/json",
    }


def resolve_jimeng_actual_model(model: str) -> str:
    return JIMENG_ACTUAL_MODELS.get(model, "图片 4.0")


def get_banana_submit_url(model: str, mode: str) -> str:
    base_url = normalize_url(BANANA_BASE_URL)
    model_key = str(model or "").strip().lower()
    if model_key == "banana-pro":
        return f"{base_url}/banana_pro/{mode}"
    return f"{base_url}/banana2/{mode}"


def extract_task_id(provider: str, payload: Dict[str, Any]) -> str:
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider == "jimeng":
        return str(payload.get("task_id") or payload.get("taskId") or "").strip()
    if normalized_provider == "moti":
        data = payload.get("data") or {}
        return str(data.get("id") or data.get("_id") or data.get("task_id") or "").strip()
    return str(payload.get("taskId") or payload.get("task_id") or "").strip()


def extract_images_from_jimeng(payload: Dict[str, Any]) -> List[str]:
    images: List[str] = []
    for item in payload.get("images") or []:
        if isinstance(item, str):
            if item.strip():
                images.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        image_value = item.get("image_url") or item.get("url")
        if isinstance(image_value, list):
            images.extend(str(url).strip() for url in image_value if str(url).strip())
        elif str(image_value or "").strip():
            images.append(str(image_value).strip())
    return images


def extract_images_from_moti(payload: Dict[str, Any]) -> List[str]:
    images: List[str] = []
    data = payload.get("data") or {}
    for item in data.get("urls") or []:
        if isinstance(item, str):
            if item.strip():
                images.append(item.strip())
            continue
        if isinstance(item, dict):
            image_url = item.get("url") or item.get("image_url")
            if str(image_url or "").strip():
                images.append(str(image_url).strip())
    return images


def extract_images_from_banana(payload: Dict[str, Any]) -> List[str]:
    images: List[str] = []
    for item in payload.get("results") or []:
        if isinstance(item, dict):
            image_url = item.get("url")
            if str(image_url or "").strip():
                images.append(str(image_url).strip())
    return images


def normalize_query_result(provider: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized_provider = str(provider or "").strip().lower()

    if normalized_provider == "jimeng":
        raw_status = str(payload.get("status") or "").strip().upper()
        if raw_status in {"FINISHED", "COMPLETED", "SUCCEEDED", "SUCCESS"}:
            return {
                "status": "completed",
                "raw_status": raw_status,
                "progress": payload.get("progress", 100),
                "images": extract_images_from_jimeng(payload),
                "raw_payload": payload,
            }
        if raw_status in {"FAILED", "ERROR", "CANCELLED", "CANCELED"}:
            return {
                "status": "failed",
                "raw_status": raw_status,
                "progress": payload.get("progress", 0),
                "error": payload.get("error") or payload.get("error_msg") or payload.get("message") or "generation failed",
                "raw_payload": payload,
            }
        return {
            "status": "processing",
            "raw_status": raw_status,
            "progress": payload.get("progress", 0),
            "raw_payload": payload,
        }

    if normalized_provider == "moti":
        data = payload.get("data") or {}
        raw_status = str(data.get("status") or "").strip().upper()
        if raw_status in {"SUCCESS", "SUCCEEDED", "COMPLETED", "FINISHED"}:
            return {
                "status": "completed",
                "raw_status": raw_status,
                "progress": data.get("progress", 100),
                "images": extract_images_from_moti(payload),
                "raw_payload": payload,
            }
        if raw_status in {"FAILED", "FAIL", "ERROR", "CANCELLED", "CANCELED"}:
            return {
                "status": "failed",
                "raw_status": raw_status,
                "progress": data.get("progress", 0),
                "error": data.get("error_msg") or data.get("msg") or payload.get("msg") or payload.get("message") or "generation failed",
                "raw_payload": payload,
            }
        return {
            "status": "processing",
            "raw_status": raw_status,
            "progress": data.get("progress", 0),
            "raw_payload": payload,
        }

    raw_status = str(payload.get("status") or "").strip().upper()
    if raw_status == "SUCCESS":
        return {
            "status": "completed",
            "raw_status": raw_status,
            "progress": 100,
            "images": extract_images_from_banana(payload),
            "raw_payload": payload,
        }
    if raw_status in {"FAILED", "TIMEOUT"}:
        return {
            "status": "failed",
            "raw_status": raw_status,
            "progress": 0,
            "error": payload.get("errorMessage") or payload.get("errorCode") or "generation failed",
            "raw_payload": payload,
        }
    return {
        "status": "processing",
        "raw_status": raw_status,
        "progress": payload.get("progress", 0),
        "raw_payload": payload,
    }


def jimeng_submit_text2image(
    prompt: str,
    ratio: str = "1:1",
    name: str = "codex_test",
    cw: int = 50,
    model: str = "jimeng-4.0",
) -> Dict[str, Any]:
    url = f"{normalize_url(JIMENG_BASE_URL)}/task"
    payload = {
        "model": resolve_jimeng_actual_model(model),
        "ratio": ratio,
        "cw": int(cw),
        "cref": [],
        "name": name,
        "prompt_text": " ".join(str(prompt or "").replace("\r", " ").replace("\n", " ").split()),
        "desc": None,
        "source": None,
        "webhook": None,
        "request_id": None,
    }
    response = requests.post(url, headers=jimeng_headers(), json=payload, timeout=TIMEOUT)
    return {"url": url, "status_code": response.status_code, "payload": payload, "response": safe_json(response)}


def jimeng_submit_image2image(
    prompt: str,
    reference_urls: List[str],
    ratio: str = "1:1",
    name: str = "codex_test",
    cw: int = 50,
    model: str = "jimeng-4.0",
) -> Dict[str, Any]:
    url = f"{normalize_url(JIMENG_BASE_URL)}/task"
    payload = {
        "model": resolve_jimeng_actual_model(model),
        "ratio": ratio,
        "cw": int(cw),
        "cref": normalize_reference_urls(reference_urls),
        "name": name,
        "prompt_text": " ".join(str(prompt or "").replace("\r", " ").replace("\n", " ").split()),
        "desc": None,
        "source": None,
        "webhook": None,
        "request_id": None,
    }
    response = requests.post(url, headers=jimeng_headers(), json=payload, timeout=TIMEOUT)
    return {"url": url, "status_code": response.status_code, "payload": payload, "response": safe_json(response)}


def jimeng_query(task_id: str) -> Dict[str, Any]:
    url = f"{normalize_url(JIMENG_BASE_URL)}/task/{task_id}"
    response = requests.get(url, headers=jimeng_headers(), timeout=TIMEOUT)
    response_data = safe_json(response)
    return {
        "url": url,
        "status_code": response.status_code,
        "response": response_data,
        "normalized": normalize_query_result("jimeng", response_data if isinstance(response_data, dict) else {}),
    }


def moti_submit_text2image(prompt: str, ratio: str = "1:1", name: str = "") -> Dict[str, Any]:
    url = f"{normalize_url(MOTI_BASE_URL)}/images/generations"
    payload: Dict[str, Any] = {"prompt": prompt, "ratio": ratio}
    if str(name or "").strip():
        payload["name"] = str(name).strip()
    response = requests.post(url, headers=moti_headers(), json=payload, timeout=TIMEOUT)
    return {"url": url, "status_code": response.status_code, "payload": payload, "response": safe_json(response)}


def moti_submit_image2image(prompt: str, reference_urls: List[str], ratio: str = "1:1", name: str = "") -> Dict[str, Any]:
    url = f"{normalize_url(MOTI_BASE_URL)}/images/generations"
    payload: Dict[str, Any] = {
        "prompt": prompt,
        "ratio": ratio,
        "image": normalize_reference_urls(reference_urls),
    }
    if str(name or "").strip():
        payload["name"] = str(name).strip()
    response = requests.post(url, headers=moti_headers(), json=payload, timeout=TIMEOUT)
    return {"url": url, "status_code": response.status_code, "payload": payload, "response": safe_json(response)}


def moti_query(task_id: str) -> Dict[str, Any]:
    url = f"{normalize_url(MOTI_BASE_URL)}/images/generations/{task_id}"
    response = requests.get(url, headers=moti_headers(), timeout=TIMEOUT)
    response_data = safe_json(response)
    return {
        "url": url,
        "status_code": response.status_code,
        "response": response_data,
        "normalized": normalize_query_result("moti", response_data if isinstance(response_data, dict) else {}),
    }


def banana_submit_text2image(prompt: str, model: str = "banana2", aspect_ratio: str = "1:1", resolution: str = "") -> Dict[str, Any]:
    url = get_banana_submit_url(model=model, mode="text-to-image")
    payload: Dict[str, Any] = {
        "prompt": prompt,
        "aspectRatio": aspect_ratio,
    }
    if str(resolution or "").strip():
        payload["resolution"] = str(resolution).strip()
    response = requests.post(url, headers=banana_headers(), json=payload, timeout=TIMEOUT)
    return {"url": url, "status_code": response.status_code, "payload": payload, "response": safe_json(response)}


def banana_submit_image2image(
    prompt: str,
    reference_urls: List[str],
    model: str = "banana2",
    aspect_ratio: str = "1:1",
    resolution: str = "",
) -> Dict[str, Any]:
    url = get_banana_submit_url(model=model, mode="image-to-image")
    payload: Dict[str, Any] = {
        "prompt": prompt,
        "aspectRatio": aspect_ratio,
        "imageUrls": normalize_reference_urls(reference_urls),
    }
    if str(resolution or "").strip():
        payload["resolution"] = str(resolution).strip()
    response = requests.post(url, headers=banana_headers(), json=payload, timeout=TIMEOUT)
    return {"url": url, "status_code": response.status_code, "payload": payload, "response": safe_json(response)}


def banana_query(task_id: str) -> Dict[str, Any]:
    url = f"{normalize_url(BANANA_BASE_URL)}/query"
    payload = {"taskId": task_id}
    response = requests.post(url, headers=banana_headers(), json=payload, timeout=TIMEOUT)
    response_data = safe_json(response)
    return {
        "url": url,
        "status_code": response.status_code,
        "payload": payload,
        "response": response_data,
        "normalized": normalize_query_result("banana", response_data if isinstance(response_data, dict) else {}),
    }


def dispatch_submit(provider: str, action: str, model: str) -> Dict[str, Any]:
    normalized_provider = str(provider or "").strip().lower()
    normalized_action = str(action or "").strip().lower()

    if normalized_provider == "jimeng":
        if normalized_action == "image2image":
            return jimeng_submit_image2image(PROMPT, REFERENCE_IMAGE_URLS, ratio=RATIO, name=NAME, cw=CW, model=model)
        return jimeng_submit_text2image(PROMPT, ratio=RATIO, name=NAME, cw=CW, model=model)

    if normalized_provider == "moti":
        if normalized_action == "image2image":
            return moti_submit_image2image(PROMPT, REFERENCE_IMAGE_URLS, ratio=RATIO, name=NAME)
        return moti_submit_text2image(PROMPT, ratio=RATIO, name=NAME)

    if normalized_action == "image2image":
        return banana_submit_image2image(PROMPT, REFERENCE_IMAGE_URLS, model=model, aspect_ratio=RATIO, resolution=RESOLUTION)
    return banana_submit_text2image(PROMPT, model=model, aspect_ratio=RATIO, resolution=RESOLUTION)


def dispatch_query(provider: str, task_id: str) -> Dict[str, Any]:
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider == "jimeng":
        return jimeng_query(task_id)
    if normalized_provider == "moti":
        return moti_query(task_id)
    return banana_query(task_id)


def poll_until_done(provider: str, task_id: str) -> Dict[str, Any]:
    print("=" * 100)
    print(f"Polling task_id={task_id} provider={provider}")
    for round_index in range(1, MAX_POLL_ROUNDS + 1):
        result = dispatch_query(provider, task_id)
        normalized = result.get("normalized") or {}
        print(
            f"[Poll {round_index}] http={result.get('status_code')} "
            f"status={normalized.get('status')} raw_status={normalized.get('raw_status')} "
            f"progress={normalized.get('progress')}"
        )
        if normalized.get("status") == "completed":
            print("=" * 100)
            print("Final completed result")
            print(pretty(result))
            return result
        if normalized.get("status") == "failed":
            print("=" * 100)
            print("Final failed result")
            print(pretty(result))
            return result
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Polling timed out after {MAX_POLL_ROUNDS} rounds")


def main() -> None:
    validated = validate_run_config(
        provider=PROVIDER,
        model=MODEL,
        action=ACTION,
        ratio=RATIO,
        resolution=RESOLUTION,
        reference_urls=REFERENCE_IMAGE_URLS,
    )
    normalized_action = validated["action"]
    normalized_provider = validated["provider"]

    print_selected_model_capabilities(MODEL, ACTION)

    if normalized_action == "query":
        if not str(QUERY_TASK_ID or "").strip():
            raise RuntimeError("ACTION=query requires QUERY_TASK_ID")
        result = dispatch_query(normalized_provider, QUERY_TASK_ID)
        print("=" * 100)
        print("Query result")
        print(pretty(result))
        return

    submit_result = dispatch_submit(normalized_provider, normalized_action, MODEL)
    print("=" * 100)
    print("Submit result")
    print(pretty(submit_result))

    response_payload = submit_result.get("response")
    if not isinstance(response_payload, dict):
        raise RuntimeError("Submit response is not valid JSON")

    task_id = extract_task_id(normalized_provider, response_payload)
    if not task_id:
        raise RuntimeError(f"Submit response missing task id: {pretty(response_payload)}")

    print("=" * 100)
    print(f"task_id = {task_id}")

    if AUTO_POLL:
        poll_until_done(normalized_provider, task_id)


if __name__ == "__main__":
    main()
