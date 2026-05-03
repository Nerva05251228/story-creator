import json
from datetime import datetime
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

import models
from database import SessionLocal


simple_storyboard_batch_update_lock = Lock()


def _parse_simple_storyboard_batch_shots(raw_value: Optional[str]) -> List[Dict[str, Any]]:
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return []
    if isinstance(parsed, dict):
        parsed = parsed.get("shots")
    return parsed if isinstance(parsed, list) else []


def _build_simple_storyboard_from_batches(batch_rows: List[models.SimpleStoryboardBatch]) -> Dict[str, Any]:
    ordered_rows = sorted(batch_rows, key=lambda row: int(getattr(row, "batch_index", 0) or 0))
    all_shots: List[Dict[str, Any]] = []
    shot_number = 1
    for row in ordered_rows:
        if str(getattr(row, "status", "") or "").strip() != "completed":
            continue
        for shot in _parse_simple_storyboard_batch_shots(getattr(row, "shots_data", "")):
            if not isinstance(shot, dict):
                continue
            normalized_shot = dict(shot)
            normalized_shot["shot_number"] = shot_number
            shot_number += 1
            all_shots.append(normalized_shot)
    return {"shots": all_shots}


def _serialize_simple_storyboard_batch(row: models.SimpleStoryboardBatch) -> Dict[str, Any]:
    shots = _parse_simple_storyboard_batch_shots(getattr(row, "shots_data", ""))
    retry_count = int(getattr(row, "retry_count", 0) or 0)
    status = str(getattr(row, "status", "") or "").strip() or "pending"
    return {
        "id": int(getattr(row, "id", 0) or 0),
        "batch_index": int(getattr(row, "batch_index", 0) or 0),
        "total_batches": int(getattr(row, "total_batches", 0) or 0),
        "status": status,
        "source_text": str(getattr(row, "source_text", "") or ""),
        "error_message": str(getattr(row, "error_message", "") or ""),
        "last_attempt": int(getattr(row, "last_attempt", 0) or 0),
        "retry_count": retry_count,
        "can_retry": status == "failed" and retry_count < 1,
        "shots_count": len(shots),
        "created_at": getattr(row, "created_at", None).isoformat() if getattr(row, "created_at", None) else None,
        "updated_at": getattr(row, "updated_at", None).isoformat() if getattr(row, "updated_at", None) else None,
    }


def _get_simple_storyboard_batch_rows(episode_id: int, db: Session) -> List[models.SimpleStoryboardBatch]:
    return db.query(models.SimpleStoryboardBatch).filter(
        models.SimpleStoryboardBatch.episode_id == episode_id
    ).order_by(models.SimpleStoryboardBatch.batch_index.asc(), models.SimpleStoryboardBatch.id.asc()).all()


def _get_simple_storyboard_batch_summary(episode_id: int, db: Session) -> Dict[str, Any]:
    db.flush()
    rows = _get_simple_storyboard_batch_rows(episode_id, db)
    completed_count = 0
    failed_count = 0
    submitting_count = 0
    total_batches = 0
    errors: List[Dict[str, Any]] = []
    for row in rows:
        total_batches = max(total_batches, int(getattr(row, "total_batches", 0) or 0), int(getattr(row, "batch_index", 0) or 0))
        status = str(getattr(row, "status", "") or "").strip()
        if status == "completed":
            completed_count += 1
        elif status == "failed":
            failed_count += 1
            error_message = str(getattr(row, "error_message", "") or "").strip()
            if error_message:
                errors.append({
                    "batch_index": int(getattr(row, "batch_index", 0) or 0),
                    "message": error_message,
                    "last_attempt": int(getattr(row, "last_attempt", 0) or 0),
                    "retry_count": int(getattr(row, "retry_count", 0) or 0),
                })
        elif status in {"submitting", "pending"}:
            submitting_count += 1
    aggregate = _build_simple_storyboard_from_batches(rows)
    return {
        "total_batches": total_batches or len(rows),
        "completed_batches": completed_count,
        "failed_batches": failed_count,
        "submitting_batches": submitting_count,
        "has_failures": failed_count > 0,
        "batches": [_serialize_simple_storyboard_batch(row) for row in rows],
        "failed_batch_errors": errors,
        "shots": aggregate.get("shots", []),
    }


def _refresh_episode_simple_storyboard_from_batches(episode: models.Episode, db: Session) -> Dict[str, Any]:
    summary = _get_simple_storyboard_batch_summary(int(episode.id), db)
    aggregate_data = {"shots": summary["shots"]}
    episode.simple_storyboard_data = json.dumps(aggregate_data, ensure_ascii=False)
    still_running = summary["submitting_batches"] > 0 or (
        summary["total_batches"] > 0 and summary["completed_batches"] + summary["failed_batches"] < summary["total_batches"]
    )
    if summary["has_failures"]:
        combined_error = "；".join(
            [f"Batch {item['batch_index']}: {item['message']}" for item in summary["failed_batch_errors"]]
        )
        episode.simple_storyboard_error = combined_error
        episode.simple_storyboard_generating = still_running
    else:
        episode.simple_storyboard_error = ""
        episode.simple_storyboard_generating = still_running
    return summary


def _split_simple_storyboard_batches(content: str, batch_size: int) -> List[str]:
    paragraphs = [p.strip() for p in str(content or "").split('\n') if p.strip()]
    if not paragraphs:
        return []

    split_batches: List[str] = []
    current_batch: List[str] = []
    current_length = 0
    normalized_batch_size = max(1, int(batch_size or 1))

    for para in paragraphs:
        para_length = len(para)
        if current_length + para_length >= normalized_batch_size and current_batch:
            split_batches.append('\n\n'.join(current_batch))
            current_batch = [para]
            current_length = para_length
        else:
            current_batch.append(para)
            current_length += para_length

    if current_batch:
        split_batches.append('\n\n'.join(current_batch))

    return split_batches


def _group_simple_storyboard_shots_into_batches(
    shots: List[Dict[str, Any]],
    batch_size: int,
) -> List[Dict[str, Any]]:
    if not shots:
        return []

    normalized_batch_size = max(1, int(batch_size or 1))
    grouped: List[Dict[str, Any]] = []
    current_shots: List[Dict[str, Any]] = []
    current_length = 0

    for shot in shots:
        shot_text = str((shot or {}).get("original_text") or "")
        shot_length = len(shot_text)
        if current_shots and current_length + shot_length > normalized_batch_size:
            grouped.append({
                "source_text": "".join(str(item.get("original_text") or "") for item in current_shots),
                "shots": current_shots,
            })
            current_shots = [dict(shot)]
            current_length = shot_length
            continue
        current_shots.append(dict(shot))
        current_length += shot_length

    if current_shots:
        grouped.append({
            "source_text": "".join(str(item.get("original_text") or "") for item in current_shots),
            "shots": current_shots,
        })
    return grouped


def _persist_programmatic_simple_storyboard_batches(
    episode_id: int,
    shots: List[Dict[str, Any]],
    batch_size: int,
    db: Session,
) -> List[models.SimpleStoryboardBatch]:
    grouped_batches = _group_simple_storyboard_shots_into_batches(shots, batch_size)
    db.query(models.SimpleStoryboardBatch).filter(models.SimpleStoryboardBatch.episode_id == episode_id).delete()
    now = datetime.utcnow()
    total_batches = len(grouped_batches)
    rows: List[models.SimpleStoryboardBatch] = []
    for index, batch_payload in enumerate(grouped_batches, start=1):
        row = models.SimpleStoryboardBatch(
            episode_id=episode_id,
            batch_index=index,
            total_batches=total_batches,
            status="completed",
            source_text=str(batch_payload.get("source_text") or ""),
            shots_data=json.dumps(batch_payload.get("shots") or [], ensure_ascii=False),
            error_message="",
            last_attempt=1,
            retry_count=0,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def _reset_simple_storyboard_batches_for_episode(episode_id: int, total_batches: int, batch_texts: List[str], db: Session) -> None:
    db.query(models.SimpleStoryboardBatch).filter(models.SimpleStoryboardBatch.episode_id == episode_id).delete()
    now = datetime.utcnow()
    for index, batch_text in enumerate(batch_texts, start=1):
        db.add(models.SimpleStoryboardBatch(
            episode_id=episode_id,
            batch_index=index,
            total_batches=total_batches,
            status="pending",
            source_text=str(batch_text or ""),
            shots_data="",
            error_message="",
            last_attempt=0,
            retry_count=0,
            created_at=now,
            updated_at=now,
        ))


def _touch_episode_simple_storyboard_activity(episode_id: int, db: Session) -> None:
    try:
        db.query(models.Episode).filter(models.Episode.id == episode_id).update({
            models.Episode.created_at: models.Episode.created_at
        }, synchronize_session=False)
        db.flush()
    except Exception:
        pass


def _apply_simple_storyboard_batch_update(
    episode_id: int,
    payload: Dict[str, Any],
    *,
    session_factory: Callable[[], Session] = SessionLocal,
) -> None:
    with simple_storyboard_batch_update_lock:
        local_db = session_factory()
        try:
            episode = local_db.query(models.Episode).filter(models.Episode.id == episode_id).first()
            if not episode:
                return
            batch_index = int(payload.get("batch_index") or 0)
            if batch_index <= 0:
                return
            row = local_db.query(models.SimpleStoryboardBatch).filter(
                models.SimpleStoryboardBatch.episode_id == episode_id,
                models.SimpleStoryboardBatch.batch_index == batch_index
            ).first()
            if not row:
                return
            row.status = str(payload.get("status") or row.status or "pending").strip() or "pending"
            if "shots" in payload:
                row.shots_data = json.dumps({"shots": payload.get("shots") or []}, ensure_ascii=False)
            if "error_message" in payload:
                row.error_message = str(payload.get("error_message") or "")
            if "last_attempt" in payload:
                row.last_attempt = int(payload.get("last_attempt") or 0)
            if "retry_count" in payload:
                row.retry_count = int(payload.get("retry_count") or 0)
            row.updated_at = datetime.utcnow()
            _refresh_episode_simple_storyboard_from_batches(episode, local_db)
            _touch_episode_simple_storyboard_activity(episode_id, local_db)
            local_db.commit()
        except Exception:
            local_db.rollback()
            raise
        finally:
            local_db.close()


def _build_simple_storyboard_batch_runtime_items(batch_rows: List[models.SimpleStoryboardBatch]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in batch_rows:
        status = str(getattr(row, "status", "") or "").strip()
        if status == "completed":
            continue
        items.append({
            "batch_index": int(getattr(row, "batch_index", 0) or 0),
            "content": str(getattr(row, "source_text", "") or ""),
            "retry_count": int(getattr(row, "retry_count", 0) or 0),
        })
    return items
