from copy import deepcopy
from threading import Lock
from typing import Any, Dict

import requests

from video_api_config import get_video_api_headers, get_video_provider_accounts_url


def _empty_accounts_payload(provider: str, error: str = "", loaded: bool = False) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "provider": str(provider or "").strip().lower(),
        "total": 0,
        "records": [],
        "loaded": bool(loaded),
    }
    if error:
        payload["error"] = error
    return payload


def _normalize_accounts_payload(provider: str, payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return _empty_accounts_payload(provider, "invalid accounts response")

    records = payload.get("records")
    if not isinstance(records, list):
        records = []

    normalized_records = [
        dict(record)
        for record in records
        if isinstance(record, dict)
    ]

    try:
        total = int(payload.get("total", len(normalized_records)) or 0)
    except Exception:
        total = len(normalized_records)

    return {
        "provider": str(provider or "").strip().lower(),
        "total": total,
        "records": normalized_records,
        "loaded": True,
    }


def resolve_video_provider_account_robot_id(payload: Any, account_value: Any) -> str:
    normalized_account_value = str(account_value or "").strip()
    if not normalized_account_value:
        return ""

    records = []
    if isinstance(payload, dict):
        raw_records = payload.get("records")
        if isinstance(raw_records, list):
            records = [record for record in raw_records if isinstance(record, dict)]

    for record in records:
        record_account_id = str(record.get("account_id") or "").strip()
        record_robot_id = str(record.get("robot_id") or "").strip()
        if not record_robot_id:
            continue
        if normalized_account_value == record_account_id or normalized_account_value == record_robot_id:
            return record_robot_id

    return ""


class VideoProviderAccountsCache:
    def __init__(self, fetcher=None, timeout: int = 180):
        self._fetcher = fetcher or requests
        self._timeout = timeout
        self._lock = Lock()
        self._payloads: Dict[str, Dict[str, Any]] = {}

    def refresh(self, provider: str = "moti") -> Dict[str, Any]:
        normalized_provider = str(provider or "").strip().lower()
        if not normalized_provider:
            normalized_provider = "moti"

        request_headers = get_video_api_headers()
        auth_prefix = str(request_headers.get("Authorization") or "")[:24]
        target_url = get_video_provider_accounts_url(normalized_provider)
        print(
            f"[video-provider-accounts] refresh start provider={normalized_provider} "
            f"url={target_url} timeout={self._timeout}s auth_prefix={auth_prefix}..."
        )

        try:
            response = self._fetcher.get(
                target_url,
                headers=request_headers,
                timeout=self._timeout,
            )
            print(
                f"[video-provider-accounts] upstream status={getattr(response, 'status_code', 0)} "
                f"content_type={getattr(response, 'headers', {}).get('Content-Type', '')}"
            )
            if getattr(response, "status_code", 0) != 200:
                body_preview = str(getattr(response, "text", "") or "")[:500]
                print(f"[video-provider-accounts] upstream error body={body_preview}")
                raise RuntimeError(f"HTTP {getattr(response, 'status_code', 0)}")
            payload = _normalize_accounts_payload(normalized_provider, response.json())
            print(
                f"[video-provider-accounts] refresh success provider={normalized_provider} "
                f"total={payload.get('total', 0)} loaded={payload.get('loaded')}"
            )
        except Exception as exc:
            print(f"[video-provider-accounts] refresh failed provider={normalized_provider} error={exc}")
            payload = _empty_accounts_payload(normalized_provider, str(exc), loaded=True)

        with self._lock:
            self._payloads[normalized_provider] = payload

        return deepcopy(payload)

    def get(self, provider: str = "moti") -> Dict[str, Any]:
        normalized_provider = str(provider or "").strip().lower() or "moti"
        with self._lock:
            payload = self._payloads.get(normalized_provider)
            if payload is None:
                payload = _empty_accounts_payload(normalized_provider, loaded=False)
                self._payloads[normalized_provider] = payload
            return deepcopy(payload)


video_provider_accounts_cache = VideoProviderAccountsCache()


def refresh_video_provider_accounts(provider: str = "moti") -> Dict[str, Any]:
    return video_provider_accounts_cache.refresh(provider)


def get_cached_video_provider_accounts(provider: str = "moti") -> Dict[str, Any]:
    return video_provider_accounts_cache.get(provider)
