import json
import time
from datetime import datetime
from decimal import Decimal
from threading import Thread
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import requests

import billing_service
import models
from ai_config import (
    DEFAULT_TEXT_MODEL_ID,
    RELAY_API_KEY,
    RELAY_BASE_URL,
    RELAY_CHAT_COMPLETIONS_URL,
    RELAY_MODELS_URL,
    RELAY_TIMEOUT,
)
from database import SessionLocal


PENDING_TEXT_RELAY_STATUSES = {"submitted", "queued", "running"}
TERMINAL_TEXT_RELAY_STATUSES = {"succeeded", "failed"}
TEXT_RELAY_POLL_INTERVAL_SECONDS = 2
TEXT_RELAY_PENDING_BATCH_SIZE = 20
TEXT_RELAY_USD_TO_RMB_RATE = Decimal("7")


def _safe_json_dumps(payload: Any) -> str:
    try:
        return json.dumps(payload or {}, ensure_ascii=False)
    except Exception:
        return ""


def _safe_json_loads(payload: Any) -> Dict[str, Any]:
    raw_value = str(payload or "").strip()
    if not raw_value:
        return {}
    try:
        result = json.loads(raw_value)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _to_decimal(value: Any) -> Decimal:
    try:
        return billing_service._quantize_money(Decimal(str(value or 0)))
    except Exception:
        return billing_service._quantize_money(Decimal("0"))


def _extract_cost_amount(payload: Dict[str, Any]) -> Tuple[Decimal, str]:
    if not isinstance(payload, dict):
        return _to_decimal(0), ""

    for key in ("cost", "cost_rmb", "amount_rmb"):
        value = payload.get(key)
        if value not in (None, ""):
            return _to_decimal(value), key

    for key in ("cost_usd", "amount_usd"):
        value = payload.get(key)
        if value not in (None, ""):
            try:
                converted = Decimal(str(value)) * TEXT_RELAY_USD_TO_RMB_RATE
            except Exception:
                converted = Decimal("0")
            return _to_decimal(converted), key

    return _to_decimal(0), ""


def _sync_dashboard_task(task_id: int, db) -> None:
    try:
        from dashboard_service import sync_text_relay_task_to_dashboard

        sync_text_relay_task_to_dashboard(int(task_id), db=db)
    except Exception as exc:
        print(f"[text-relay][dashboard] sync failed for task {task_id}: {str(exc)}")


def _build_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {RELAY_API_KEY}",
        "Content-Type": "application/json",
    }


def _build_default_poll_url(task_id: str) -> str:
    return f"{RELAY_BASE_URL}/v1/tasks/{str(task_id or '').strip()}"


def _resolve_username_from_episode_id(db, episode_id: Any) -> str:
    try:
        normalized_episode_id = int(episode_id or 0)
    except Exception:
        return ""
    if normalized_episode_id <= 0:
        return ""
    episode = db.query(models.Episode).filter(models.Episode.id == normalized_episode_id).first()
    if not episode or not getattr(episode, "script_id", None):
        return ""
    script = db.query(models.Script).filter(models.Script.id == int(episode.script_id)).first()
    if not script or not getattr(script, "user_id", None):
        return ""
    user = db.query(models.User).filter(models.User.id == int(script.user_id)).first()
    return str(getattr(user, "username", "") or "").strip()


def _resolve_username_from_card_id(db, card_id: Any) -> str:
    try:
        normalized_card_id = int(card_id or 0)
    except Exception:
        return ""
    if normalized_card_id <= 0:
        return ""
    card = db.query(models.SubjectCard).filter(models.SubjectCard.id == normalized_card_id).first()
    if not card or not getattr(card, "library_id", None):
        return ""
    library = db.query(models.StoryLibrary).filter(models.StoryLibrary.id == int(card.library_id)).first()
    if not library or not getattr(library, "user_id", None):
        return ""
    user = db.query(models.User).filter(models.User.id == int(library.user_id)).first()
    return str(getattr(user, "username", "") or "").strip()


def _resolve_username_from_shot_id(db, shot_id: Any) -> str:
    try:
        normalized_shot_id = int(shot_id or 0)
    except Exception:
        return ""
    if normalized_shot_id <= 0:
        return ""
    shot = db.query(models.StoryboardShot).filter(models.StoryboardShot.id == normalized_shot_id).first()
    if not shot:
        return ""
    return _resolve_username_from_episode_id(db, getattr(shot, "episode_id", None))


def _resolve_username_from_storyboard2_shot_id(db, storyboard2_shot_id: Any) -> str:
    try:
        normalized_shot_id = int(storyboard2_shot_id or 0)
    except Exception:
        return ""
    if normalized_shot_id <= 0:
        return ""
    shot = db.query(models.Storyboard2Shot).filter(models.Storyboard2Shot.id == normalized_shot_id).first()
    if not shot:
        return ""
    return _resolve_username_from_episode_id(db, getattr(shot, "episode_id", None))


def _resolve_username_from_simple_storyboard_batch_id(db, batch_id: Any) -> str:
    try:
        normalized_batch_id = int(batch_id or 0)
    except Exception:
        return ""
    if normalized_batch_id <= 0:
        return ""
    batch_row = db.query(models.SimpleStoryboardBatch).filter(models.SimpleStoryboardBatch.id == normalized_batch_id).first()
    if not batch_row:
        return ""
    return _resolve_username_from_episode_id(db, getattr(batch_row, "episode_id", None))


def _resolve_system_username(
    db,
    *,
    owner_type: str,
    owner_id: Optional[int],
    task_payload: Optional[Dict[str, Any]],
) -> str:
    payload = task_payload or {}

    username = _resolve_username_from_episode_id(db, payload.get("episode_id"))
    if username:
        return username

    username = _resolve_username_from_card_id(db, payload.get("card_id"))
    if username:
        return username

    username = _resolve_username_from_shot_id(db, payload.get("shot_id"))
    if username:
        return username

    username = _resolve_username_from_storyboard2_shot_id(db, payload.get("storyboard2_shot_id"))
    if username:
        return username

    username = _resolve_username_from_simple_storyboard_batch_id(db, payload.get("batch_row_id"))
    if username:
        return username

    normalized_owner_type = str(owner_type or "").strip().lower()
    if normalized_owner_type == "episode":
        return _resolve_username_from_episode_id(db, owner_id)
    if normalized_owner_type == "card":
        return _resolve_username_from_card_id(db, owner_id)
    if normalized_owner_type == "shot":
        return _resolve_username_from_shot_id(db, owner_id)
    if normalized_owner_type == "storyboard2_shot":
        return _resolve_username_from_storyboard2_shot_id(db, owner_id)
    if normalized_owner_type == "simple_storyboard_batch":
        return _resolve_username_from_simple_storyboard_batch_id(db, owner_id)
    return ""


def _with_username(
    db,
    *,
    request_payload: Dict[str, Any],
    owner_type: str,
    owner_id: Optional[int],
    task_payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    payload = dict(request_payload or {})
    existing_username = str(payload.get("username") or "").strip()
    if existing_username:
        return payload

    username = _resolve_system_username(
        db,
        owner_type=owner_type,
        owner_id=owner_id,
        task_payload=task_payload,
    )
    if username:
        payload["username"] = username
    return payload


def _normalize_poll_url(poll_url: Optional[str], task_id: str) -> str:
    normalized_poll_url = str(poll_url or "").strip()
    if normalized_poll_url.startswith("/"):
        base_parts = urlsplit(RELAY_BASE_URL)
        return urlunsplit((base_parts.scheme, base_parts.netloc, normalized_poll_url, "", ""))
    if normalized_poll_url:
        return normalized_poll_url
    return _build_default_poll_url(task_id)


def submit_chat_completion_task(request_payload: Dict[str, Any]) -> Dict[str, Any]:
    response = requests.post(
        RELAY_CHAT_COMPLETIONS_URL,
        headers=_build_headers(),
        json=request_payload,
        timeout=RELAY_TIMEOUT,
    )
    try:
        data = response.json()
    except Exception:
        data = {"raw_text": getattr(response, "text", "")}

    if int(getattr(response, "status_code", 0) or 0) != 202:
        raise RuntimeError(f"Relay submit failed: HTTP {response.status_code} {data}")

    external_task_id = str(data.get("id") or "").strip()
    if not external_task_id:
        raise RuntimeError("Relay submit response missing task id")

    return {
        "external_task_id": external_task_id,
        "poll_url": _normalize_poll_url(data.get("poll_url"), external_task_id),
        "response_data": data,
    }


def fetch_task_status(*, external_task_id: str, poll_url: Optional[str] = None) -> Dict[str, Any]:
    target_url = _normalize_poll_url(poll_url, external_task_id)
    response = requests.get(
        target_url,
        headers=_build_headers(),
        timeout=RELAY_TIMEOUT,
    )
    try:
        data = response.json()
    except Exception:
        data = {"raw_text": getattr(response, "text", "")}

    if int(getattr(response, "status_code", 0) or 0) >= 400:
        raise RuntimeError(f"Relay poll failed: HTTP {response.status_code} {data}")
    return data


def sync_models_from_upstream(db) -> Dict[str, Any]:
    response = requests.get(
        RELAY_MODELS_URL,
        headers=_build_headers(),
        timeout=RELAY_TIMEOUT,
    )
    try:
        data = response.json()
    except Exception:
        data = {"raw_text": getattr(response, "text", "")}

    if int(getattr(response, "status_code", 0) or 0) != 200:
        raise RuntimeError(f"Relay model sync failed: HTTP {response.status_code} {data}")

    raw_items = []
    if isinstance(data, dict):
        for key in ("data", "models", "items"):
            value = data.get(key)
            if isinstance(value, list):
                raw_items = value
                break
    elif isinstance(data, list):
        raw_items = data

    synced_at = datetime.utcnow()
    db.query(models.RelayModel).delete()
    created_count = 0
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("model") or "").strip()
        if not model_id:
            continue
        db.add(
            models.RelayModel(
                model_id=model_id,
                owned_by=str(item.get("owned_by") or item.get("ownedBy") or ""),
                available_providers_count=int(item.get("available_providers_count") or item.get("availableProvidersCount") or 0),
                raw_metadata=_safe_json_dumps(item),
                synced_at=synced_at,
            )
        )
        created_count += 1

    if created_count == 0:
        db.add(
            models.RelayModel(
                model_id=DEFAULT_TEXT_MODEL_ID,
                owned_by="",
                available_providers_count=0,
                raw_metadata=_safe_json_dumps({"id": DEFAULT_TEXT_MODEL_ID}),
                synced_at=synced_at,
            )
        )
        created_count = 1

    db.flush()
    return {
        "count": created_count,
        "synced_at": synced_at.isoformat(),
        "raw_response": data,
    }


def get_cached_models_payload(db) -> Dict[str, Any]:
    rows = db.query(models.RelayModel).order_by(models.RelayModel.model_id.asc()).all()
    latest_synced_at = max((row.synced_at for row in rows if getattr(row, "synced_at", None)), default=None)
    return {
        "models": [
            {
                "model_id": str(row.model_id or ""),
                "owned_by": str(getattr(row, "owned_by", "") or ""),
                "available_providers_count": int(getattr(row, "available_providers_count", 0) or 0),
                "raw_metadata": _safe_json_loads(getattr(row, "raw_metadata", "")),
                "synced_at": row.synced_at.isoformat() if getattr(row, "synced_at", None) else None,
            }
            for row in rows
        ],
        "last_synced_at": latest_synced_at.isoformat() if latest_synced_at else None,
    }


def create_text_relay_task(
    db,
    *,
    task_type: str,
    owner_type: str,
    owner_id: Optional[int],
    stage_key: str,
    function_key: str,
    model_id: str,
    external_task_id: str,
    poll_url: str,
    request_payload: Dict[str, Any],
    task_payload: Dict[str, Any],
    status: str = "submitted",
):
    row = models.TextRelayTask(
        task_type=str(task_type or "").strip(),
        owner_type=str(owner_type or "").strip(),
        owner_id=int(owner_id) if owner_id is not None else None,
        stage_key=str(stage_key or "").strip(),
        function_key=str(function_key or "").strip(),
        model_id=str(model_id or DEFAULT_TEXT_MODEL_ID).strip() or DEFAULT_TEXT_MODEL_ID,
        external_task_id=str(external_task_id or "").strip(),
        poll_url=str(poll_url or "").strip(),
        status=str(status or "submitted").strip() or "submitted",
        request_payload=_safe_json_dumps(request_payload),
        task_payload=_safe_json_dumps(task_payload),
        result_payload="",
        error_message="",
        billing_status="pending",
    )
    db.add(row)
    db.flush()
    return row


def submit_and_persist_text_task(
    db,
    *,
    task_type: str,
    owner_type: str,
    owner_id: Optional[int],
    stage_key: str,
    function_key: str,
    request_payload: Dict[str, Any],
    task_payload: Dict[str, Any],
):
    normalized_request_payload = _with_username(
        db,
        request_payload=request_payload,
        owner_type=owner_type,
        owner_id=owner_id,
        task_payload=task_payload,
    )
    submit_result = submit_chat_completion_task(normalized_request_payload)
    row = create_text_relay_task(
        db,
        task_type=task_type,
        owner_type=owner_type,
        owner_id=owner_id,
        stage_key=stage_key,
        function_key=function_key,
        model_id=str(normalized_request_payload.get("model") or DEFAULT_TEXT_MODEL_ID),
        external_task_id=submit_result["external_task_id"],
        poll_url=submit_result["poll_url"],
        request_payload=normalized_request_payload,
        task_payload=task_payload,
        status="submitted",
    )
    _sync_dashboard_task(int(row.id), db)
    return row


def _record_task_billing(db, task: models.TextRelayTask, upstream_payload: Dict[str, Any], task_payload: Dict[str, Any]):
    if str(getattr(task, "billing_status", "") or "").strip() == "recorded":
        return None

    cost_rmb, cost_source = _extract_cost_amount(upstream_payload)
    task.cost_rmb = cost_rmb
    if cost_rmb <= 0:
        task.billing_status = "skipped"
        return None

    billing_key = f"text:relay:{task.id}"
    operation_key = f"text:relay:{task.task_type}:{task.owner_type}:{task.owner_id or 0}"
    detail_payload = {
        "task_type": str(task.task_type or ""),
        "owner_type": str(task.owner_type or ""),
        "owner_id": int(task.owner_id or 0) if task.owner_id is not None else None,
        "stage_key": str(task.stage_key or ""),
        "function_key": str(task.function_key or ""),
        "cost": str(cost_rmb),
        "cost_source": str(cost_source or ""),
    }
    detail_payload.update(task_payload or {})

    if str(task.owner_type or "") == "card":
        entry = billing_service.record_text_task_cost_for_card(
            db,
            card_id=int(task.owner_id or 0),
            stage=str(task.stage_key or task.task_type or ""),
            model_name=str(upstream_payload.get("model") or task.model_id or DEFAULT_TEXT_MODEL_ID),
            cost_rmb=cost_rmb,
            external_task_id=str(task.external_task_id or ""),
            billing_key=billing_key,
            operation_key=operation_key,
            detail_payload=detail_payload,
        )
    elif str(task.owner_type or "") == "shot":
        entry = billing_service.record_text_task_cost_for_shot(
            db,
            shot_id=int(task.owner_id or 0),
            stage=str(task.stage_key or task.task_type or ""),
            model_name=str(upstream_payload.get("model") or task.model_id or DEFAULT_TEXT_MODEL_ID),
            cost_rmb=cost_rmb,
            external_task_id=str(task.external_task_id or ""),
            billing_key=billing_key,
            operation_key=operation_key,
            detail_payload=detail_payload,
        )
    else:
        episode_id = int(task_payload.get("episode_id") or task.owner_id or 0)
        entry = billing_service.record_text_task_cost_for_episode(
            db,
            episode_id=episode_id,
            stage=str(task.stage_key or task.task_type or ""),
            model_name=str(upstream_payload.get("model") or task.model_id or DEFAULT_TEXT_MODEL_ID),
            cost_rmb=cost_rmb,
            external_task_id=str(task.external_task_id or ""),
            billing_key=billing_key,
            operation_key=operation_key,
            detail_payload=detail_payload,
        )

    task.billing_status = "recorded" if entry else "skipped"
    return entry


def process_pending_tasks_once(limit: int = TEXT_RELAY_PENDING_BATCH_SIZE) -> int:
    db = SessionLocal()
    processed_count = 0
    try:
        rows = db.query(models.TextRelayTask).filter(
            models.TextRelayTask.status.in_(list(PENDING_TEXT_RELAY_STATUSES))
        ).order_by(
            models.TextRelayTask.updated_at.asc(),
            models.TextRelayTask.created_at.asc(),
            models.TextRelayTask.id.asc(),
        ).limit(max(1, int(limit or 1))).all()

        for row in rows:
            upstream_payload = None
            upstream_status = ""
            try:
                task_payload = _safe_json_loads(getattr(row, "task_payload", ""))
                upstream_payload = fetch_task_status(
                    external_task_id=str(getattr(row, "external_task_id", "") or ""),
                    poll_url=str(getattr(row, "poll_url", "") or ""),
                )
                upstream_status = str(upstream_payload.get("status") or "").strip().lower() or "submitted"
                previous_status = str(getattr(row, "status", "") or "").strip().lower()

                row.status = upstream_status
                row.updated_at = datetime.utcnow()

                if upstream_status in {"queued", "running"}:
                    if upstream_status != previous_status:
                        _sync_dashboard_task(int(row.id), db)
                    db.commit()
                    processed_count += 1
                    continue

                if upstream_status == "succeeded":
                    row.result_payload = _safe_json_dumps(upstream_payload)
                    row.cost_rmb = _extract_cost_amount(upstream_payload)[0]
                    row.error_message = ""
                    row.completed_at = datetime.utcnow()

                    from main import handle_text_relay_task_success

                    handle_text_relay_task_success(db, row, upstream_payload)
                    _record_task_billing(db, row, upstream_payload, task_payload)
                    _sync_dashboard_task(int(row.id), db)
                    db.commit()
                    processed_count += 1
                    continue

                if upstream_status == "failed":
                    row.result_payload = _safe_json_dumps(upstream_payload)
                    row.error_message = str(
                        upstream_payload.get("error")
                        or upstream_payload.get("message")
                        or "relay task failed"
                    )
                    row.completed_at = datetime.utcnow()

                    from main import handle_text_relay_task_failure

                    handle_text_relay_task_failure(db, row, upstream_payload)
                    if str(getattr(row, "billing_status", "") or "").strip() == "pending":
                        row.billing_status = "skipped"
                    _sync_dashboard_task(int(row.id), db)
                    db.commit()
                    processed_count += 1
                    continue

                if upstream_status != previous_status:
                    _sync_dashboard_task(int(row.id), db)
                db.commit()
                processed_count += 1
            except Exception as exc:
                db.rollback()
                failed_row = db.query(models.TextRelayTask).filter(
                    models.TextRelayTask.id == int(row.id)
                ).first()
                if failed_row:
                    terminal_status = str(upstream_status or "").strip().lower()
                    current_status = str(getattr(failed_row, "status", "") or "").strip().lower()
                    failed_row.error_message = str(exc)
                    failed_row.updated_at = datetime.utcnow()
                    if terminal_status in TERMINAL_TEXT_RELAY_STATUSES:
                        failed_row.status = "failed"
                        failed_row.result_payload = _safe_json_dumps(upstream_payload)
                        failed_row.completed_at = datetime.utcnow()
                        failed_row.billing_status = "skipped"
                        try:
                            from main import handle_text_relay_task_failure

                            handle_text_relay_task_failure(
                                db,
                                failed_row,
                                {
                                    "error": str(exc),
                                    "message": str(exc),
                                    "status": terminal_status,
                                },
                            )
                        except Exception:
                            pass
                        _sync_dashboard_task(int(failed_row.id), db)
                        db.commit()
                        processed_count += 1
                        continue
                    if current_status in PENDING_TEXT_RELAY_STATUSES and not getattr(failed_row, "completed_at", None):
                        db.commit()
                        continue
                    failed_row.status = "failed"
                    failed_row.completed_at = datetime.utcnow()
                    failed_row.billing_status = "skipped"
                    try:
                        from main import handle_text_relay_task_failure

                        handle_text_relay_task_failure(
                            db,
                            failed_row,
                            {"error": str(exc), "message": str(exc)},
                        )
                    except Exception:
                        pass
                    _sync_dashboard_task(int(failed_row.id), db)
                    db.commit()
                    processed_count += 1

        return processed_count
    finally:
        db.close()


def backfill_text_relay_records(limit: Optional[int] = None) -> Dict[str, int]:
    db = SessionLocal()
    counts = {
        "tasks_scanned": 0,
        "dashboard_synced": 0,
        "billing_recorded": 0,
    }
    try:
        query = db.query(models.TextRelayTask).order_by(
            models.TextRelayTask.created_at.asc(),
            models.TextRelayTask.id.asc(),
        )
        if limit is not None:
            query = query.limit(max(1, int(limit or 1)))

        rows = query.all()
        for row in rows:
            counts["tasks_scanned"] += 1
            _sync_dashboard_task(int(row.id), db)
            counts["dashboard_synced"] += 1

            if str(getattr(row, "status", "") or "").strip().lower() != "succeeded":
                if str(getattr(row, "status", "") or "").strip().lower() == "failed" and str(getattr(row, "billing_status", "") or "").strip() == "pending":
                    row.billing_status = "skipped"
                continue

            upstream_payload = _safe_json_loads(getattr(row, "result_payload", ""))
            task_payload = _safe_json_loads(getattr(row, "task_payload", ""))
            extracted_cost, _cost_source = _extract_cost_amount(upstream_payload)
            if extracted_cost > 0 and (
                str(getattr(row, "billing_status", "") or "").strip() != "recorded"
                or _to_decimal(getattr(row, "cost_rmb", 0)) <= 0
            ):
                entry = _record_task_billing(db, row, upstream_payload, task_payload)
                if entry:
                    counts["billing_recorded"] += 1

        db.commit()
        return counts
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class TextRelayPoller:
    def __init__(self):
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        print("[text-relay] poller started")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        print("[text-relay] poller stopped")

    def _poll_loop(self):
        while self.running:
            try:
                process_pending_tasks_once()
            except Exception as exc:
                print(f"[text-relay] poll loop error: {str(exc)}")
            time.sleep(TEXT_RELAY_POLL_INTERVAL_SECONDS)


text_relay_poller = TextRelayPoller()
