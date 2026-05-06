import time
from datetime import datetime
from threading import Thread
from typing import Optional

import billing_service
import models
from database import SessionLocal
from sqlalchemy.orm import Session
from video_service import check_video_status, is_transient_video_status_error

from api.services.storyboard2_media import (
    is_storyboard2_video_processing,
    normalize_storyboard2_video_status,
)
from api.services.storyboard2_video_tasks import process_storyboard2_video_cover_and_cdn


def _save_debug(save_debug_fn, debug_dir: Optional[str], filename: str, payload: dict):
    if not save_debug_fn:
        return
    try:
        save_debug_fn(debug_dir, filename, payload)
    except Exception:
        pass


def poll_storyboard2_sub_shot_video_status(
    sub_shot_video_id: int,
    task_id: str,
    debug_dir: Optional[str] = None,
    save_debug_fn=None,
):
    polling_history = []
    try:
        while True:
            db = SessionLocal()
            try:
                video_record = db.query(models.Storyboard2SubShotVideo).filter(
                    models.Storyboard2SubShotVideo.id == sub_shot_video_id
                ).first()
                if not video_record:
                    return
                if bool(getattr(video_record, "is_deleted", False)):
                    return
            finally:
                db.close()

            status_info = check_video_status(task_id)
            if is_transient_video_status_error(status_info):
                print(
                    f"[poll] video_id={sub_shot_video_id} task_id={task_id} 上游暂时错误，5秒后重试: "
                    f"{status_info.get('error_message', '')}"
                )
                polling_history.append(
                    {
                        "polled_at": datetime.now().isoformat(),
                        "status": "query_failed",
                        "progress": 0,
                        "video_url": "",
                        "cdn_uploaded": False,
                        "error_message": str(status_info.get("error_message") or ""),
                    }
                )
                time.sleep(5)
                continue

            status = normalize_storyboard2_video_status(
                status_info.get("status"),
                default_value="processing",
            )
            progress_raw = status_info.get("progress")
            error_message = str(status_info.get("error_message") or "").strip()
            video_url = str(status_info.get("video_url") or "").strip()
            cdn_uploaded = bool(status_info.get("cdn_uploaded", False))

            try:
                progress = int(progress_raw) if progress_raw is not None else 0
            except Exception:
                progress = 0

            polling_history.append(
                {
                    "polled_at": datetime.now().isoformat(),
                    "status": status,
                    "progress": progress,
                    "video_url": video_url,
                    "cdn_uploaded": cdn_uploaded,
                    "error_message": error_message,
                }
            )
            print(
                f"[poll] video_id={sub_shot_video_id} task_id={task_id} "
                f"status={status} progress={progress} video_url={video_url[:60] if video_url else ''}"
            )

            should_sleep = False
            db = SessionLocal()
            try:
                video_record = db.query(models.Storyboard2SubShotVideo).filter(
                    models.Storyboard2SubShotVideo.id == sub_shot_video_id
                ).first()
                if not video_record:
                    return
                if bool(getattr(video_record, "is_deleted", False)):
                    return

                if is_storyboard2_video_processing(status):
                    video_record.status = status if status in {"pending", "processing"} else "processing"
                    video_record.progress = max(0, min(progress, 99))
                    video_record.error_message = ""
                    db.commit()
                    should_sleep = True
                elif status == "completed":
                    if not video_url:
                        video_record.status = "failed"
                        video_record.error_message = "任务完成但未返回视频地址"
                        billing_service.reverse_charge_entry(
                            db,
                            billing_key=f"video:storyboard2:{video_record.sub_shot_id}:task:{task_id}",
                            reason="completed_without_video_url",
                        )
                    else:
                        final_video_url = video_url
                        final_thumbnail_url = video_url
                        final_cdn_uploaded = cdn_uploaded

                        if not final_cdn_uploaded:
                            (
                                processed_video_url,
                                processed_thumbnail_url,
                                processed_cdn_uploaded,
                                _process_meta,
                            ) = process_storyboard2_video_cover_and_cdn(
                                video_record=video_record,
                                db=db,
                                upstream_video_url=video_url,
                                task_id=task_id,
                                debug_dir=debug_dir,
                            )
                            final_video_url = processed_video_url or final_video_url
                            final_thumbnail_url = processed_thumbnail_url or final_thumbnail_url
                            final_cdn_uploaded = bool(processed_cdn_uploaded)

                        video_record.status = "completed"
                        video_record.video_url = final_video_url
                        if final_thumbnail_url:
                            video_record.thumbnail_url = final_thumbnail_url
                        video_record.progress = 100
                        video_record.error_message = ""
                        video_record.cdn_uploaded = final_cdn_uploaded
                        billing_service.finalize_charge_entry(
                            db,
                            billing_key=f"video:storyboard2:{video_record.sub_shot_id}:task:{task_id}",
                        )
                    db.commit()
                    _save_debug(
                        save_debug_fn,
                        debug_dir,
                        "output.json",
                        {
                            "sub_shot_video_id": sub_shot_video_id,
                            "task_id": task_id,
                            "status": video_record.status,
                            "video_url": video_record.video_url,
                            "thumbnail_url": video_record.thumbnail_url,
                            "cdn_uploaded": video_record.cdn_uploaded,
                            "finished_at": datetime.now().isoformat(),
                        },
                    )
                    _save_debug(save_debug_fn, debug_dir, "polling_history.json", polling_history)
                    return
                elif status in {"failed", "cancelled"}:
                    video_record.status = "failed"
                    video_record.error_message = error_message or f"任务状态: {status}"
                    billing_service.reverse_charge_entry(
                        db,
                        billing_key=f"video:storyboard2:{video_record.sub_shot_id}:task:{task_id}",
                        reason=f"provider_{status}",
                    )
                    db.commit()
                    _save_debug(
                        save_debug_fn,
                        debug_dir,
                        "error.json",
                        {
                            "sub_shot_video_id": sub_shot_video_id,
                            "task_id": task_id,
                            "status": status,
                            "error_message": video_record.error_message,
                            "failed_at": datetime.now().isoformat(),
                        },
                    )
                    _save_debug(save_debug_fn, debug_dir, "polling_history.json", polling_history)
                    return
                else:
                    video_record.status = "processing"
                    video_record.progress = max(0, min(progress, 99))
                    db.commit()
                    should_sleep = True
            finally:
                db.close()

            if should_sleep:
                time.sleep(5)
    except Exception as exc:
        try:
            db = SessionLocal()
            try:
                db.rollback()
                failed_record = db.query(models.Storyboard2SubShotVideo).filter(
                    models.Storyboard2SubShotVideo.id == sub_shot_video_id
                ).first()
                if failed_record:
                    failed_record.status = "failed"
                    failed_record.error_message = str(exc)
                    db.commit()
            finally:
                db.close()
        except Exception:
            pass
        _save_debug(
            save_debug_fn,
            debug_dir,
            "exception.json",
            {
                "sub_shot_video_id": sub_shot_video_id,
                "task_id": task_id,
                "error": str(exc),
                "failed_at": datetime.now().isoformat(),
            },
        )
        _save_debug(save_debug_fn, debug_dir, "polling_history.json", polling_history)


def recover_storyboard2_video_polling():
    print("[recover] 开始扫描需要恢复的 storyboard2 视频任务...")
    db = SessionLocal()
    try:
        processing_records = db.query(models.Storyboard2SubShotVideo).filter(
            models.Storyboard2SubShotVideo.is_deleted == False,
            models.Storyboard2SubShotVideo.task_id != "",
            models.Storyboard2SubShotVideo.status.in_(["submitted", "pending", "processing"]),
        ).all()
        recovered = [(record.id, record.task_id) for record in processing_records]
    finally:
        db.close()

    print(f"[recover] 扫描完成，找到 {len(recovered)} 条需要恢复的任务")

    for record_id, task_id in recovered:
        print(f"[recover] 恢复轮询: video_id={record_id} task_id={task_id}")
        poll_thread = Thread(
            target=poll_storyboard2_sub_shot_video_status,
            args=(record_id, task_id),
        )
        poll_thread.daemon = True
        poll_thread.start()

    if recovered:
        print(f"[recover] 已启动 {len(recovered)} 个恢复轮询线程: ids={[record[0] for record in recovered]}")
    else:
        print("[recover] 无需恢复，没有处理中的任务")


def sync_storyboard2_processing_videos(episode_id: int, db: Session, max_count: int = 20) -> int:
    processing_videos = (
        db.query(models.Storyboard2SubShotVideo)
        .join(
            models.Storyboard2SubShot,
            models.Storyboard2SubShotVideo.sub_shot_id == models.Storyboard2SubShot.id,
        )
        .join(
            models.Storyboard2Shot,
            models.Storyboard2SubShot.storyboard2_shot_id == models.Storyboard2Shot.id,
        )
        .filter(
            models.Storyboard2Shot.episode_id == episode_id,
            models.Storyboard2SubShotVideo.is_deleted == False,
            models.Storyboard2SubShotVideo.status.in_(["submitted", "pending", "processing"]),
        )
        .order_by(
            models.Storyboard2SubShotVideo.created_at.asc(),
            models.Storyboard2SubShotVideo.id.asc(),
        )
        .limit(max_count)
        .all()
    )

    if not processing_videos:
        return 0

    updated_count = 0
    for video in processing_videos:
        task_id = (video.task_id or "").strip()
        if not task_id:
            if (video.status or "").strip().lower() != "failed":
                video.status = "failed"
                video.error_message = "缺少task_id，无法查询任务状态"
                video.progress = 0
                updated_count += 1
            continue

        try:
            status_info = check_video_status(task_id)
        except Exception as exc:
            status_info = {
                "status": "query_failed",
                "video_url": "",
                "error_message": f"查询异常: {str(exc)}",
                "progress": 0,
                "cdn_uploaded": False,
                "query_ok": False,
                "query_transient": True,
            }

        if is_transient_video_status_error(status_info):
            continue

        normalized_status = normalize_storyboard2_video_status(
            status_info.get("status"),
            default_value="processing",
        )
        try:
            progress = int(status_info.get("progress", 0) or 0)
        except Exception:
            progress = 0
        progress = max(0, min(progress, 100))
        error_message = str(status_info.get("error_message") or "").strip()
        video_url = str(status_info.get("video_url") or "").strip()
        cdn_uploaded = bool(status_info.get("cdn_uploaded", False))

        if normalized_status == "completed":
            if not video_url:
                normalized_status = "failed"
                error_message = error_message or "任务完成但未返回视频地址"
            else:
                final_video_url = video_url
                final_thumbnail_url = video_url
                final_cdn_uploaded = cdn_uploaded

                if not final_cdn_uploaded:
                    (
                        processed_video_url,
                        processed_thumbnail_url,
                        processed_cdn_uploaded,
                        _process_meta,
                    ) = process_storyboard2_video_cover_and_cdn(
                        video_record=video,
                        db=db,
                        upstream_video_url=video_url,
                        task_id=task_id,
                        debug_dir=None,
                    )
                    final_video_url = processed_video_url or final_video_url
                    final_thumbnail_url = processed_thumbnail_url or final_thumbnail_url
                    final_cdn_uploaded = bool(processed_cdn_uploaded)

                if (
                    (video.status or "").strip().lower() != "completed"
                    or (video.video_url or "").strip() != final_video_url
                    or int(video.progress or 0) != 100
                    or bool(video.cdn_uploaded) != final_cdn_uploaded
                    or (video.error_message or "")
                ):
                    video.status = "completed"
                    video.video_url = final_video_url
                    if final_thumbnail_url:
                        video.thumbnail_url = final_thumbnail_url
                    video.progress = 100
                    video.error_message = ""
                    video.cdn_uploaded = final_cdn_uploaded
                    updated_count += 1
                billing_service.finalize_charge_entry(
                    db,
                    billing_key=f"video:storyboard2:{video.sub_shot_id}:task:{task_id}",
                )
                continue

        if normalized_status == "failed":
            final_error = error_message or "任务失败"
            if (
                (video.status or "").strip().lower() != "failed"
                or (video.error_message or "") != final_error
                or int(video.progress or 0) != 0
            ):
                video.status = "failed"
                video.error_message = final_error
                video.progress = 0
                updated_count += 1
            billing_service.reverse_charge_entry(
                db,
                billing_key=f"video:storyboard2:{video.sub_shot_id}:task:{task_id}",
                reason="provider_failed",
            )
            continue

        target_status = normalized_status if normalized_status in {"pending", "processing"} else "processing"
        target_progress = max(0, min(progress, 99))
        if (
            (video.status or "").strip().lower() != target_status
            or int(video.progress or 0) != target_progress
            or (video.error_message or "")
        ):
            video.status = target_status
            video.progress = target_progress
            video.error_message = ""
            updated_count += 1

    if updated_count > 0:
        db.commit()

    return updated_count
