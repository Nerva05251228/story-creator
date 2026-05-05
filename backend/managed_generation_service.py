"""Managed video generation service."""
import json
import time
import uuid
import requests
import asyncio
from threading import Thread
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from sqlalchemy import func
from database import SessionLocal
from runtime_load import request_load_tracker
import models
from api.services.storyboard_video_prompt_builder import build_sora_prompt
import billing_service
from video_service import (
    check_video_status,
    is_transient_video_status_error,
    normalize_video_generation_status,
)
from video_api_config import get_video_api_headers, get_video_task_create_url
from dashboard_service import sync_managed_task_to_dashboard
from text_llm_queue import run_text_llm_request

MAX_RETRY_PER_SHOT = 2
MANAGED_NON_RETRY_ERROR_SNIPPETS = (
    "因目前处于使用高峰期，暂时无法提交更多任务，请等待其他任务完成后再尝试提交",
    "上传音频总时长不能超过 15秒",
    "单个素材最短 2 秒",
    "识别到你上传的素材中包含人脸信息，请调整素材后再试试",
    "素材尺寸过小",
    "视频未通过审核，本次不消耗积分",
    "音频可能包含不当内容",
    "最多支持上传 3 个音频",
    "生成视频的音频审核未通过",
    "你上传的图片不符合平台规则，请修改后重试",
)
MANAGED_PROMPT_REVIEW_ERROR_SNIPPET = "你输入的文字描述不符合平台规则，请修改后重试"
MANAGED_NO_RETRY_NOTE = "该错误不允许自动重试"
MANAGED_POLL_INTERVAL_SECONDS = 8
MANAGED_POLL_BUSY_INTERVAL_SECONDS = 12
ACTIVE_MANAGED_SESSION_STATUSES = ("running", "detached")
MANAGED_PENDING_BATCH_NORMAL = 4
MANAGED_PENDING_BATCH_BUSY = 1
MANAGED_PROCESSING_BATCH_NORMAL = 10
MANAGED_PROCESSING_BATCH_BUSY = 4
MANAGED_PROCESSING_PARALLEL_WORKERS = 10
MANAGED_SESSION_BATCH_NORMAL = 12
MANAGED_SESSION_BATCH_BUSY = 4
MANAGED_RESERVED_SLOT_RECONCILE_BATCH_NORMAL = 20
MANAGED_RESERVED_SLOT_RECONCILE_BATCH_BUSY = 8
MANAGED_ORPHAN_ACTIVE_SHOT_TIMEOUT_MINUTES = 30


class ManagedGenerationPoller:
    """doc"""

    def __init__(self):
        self.running = False
        self.thread = None

    def start(self):
        """doc"""
        if self.running:
            return

        self.running = True
        self.thread = Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        print("[managed] managed video poller started")

    def stop(self):
        """doc"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        print("[managed] managed video poller stopped")

    def _poll_loop(self):
        """doc"""
        while self.running:
            try:
                self._poll_once()
            except Exception as e:
                print(f"[managed] poll loop error: {str(e)}")

            sleep_seconds = request_load_tracker.choose_interval(
                MANAGED_POLL_INTERVAL_SECONDS,
                MANAGED_POLL_BUSY_INTERVAL_SECONDS,
            )
            time.sleep(sleep_seconds)

    def _poll_once(self):
        """doc"""
        db = SessionLocal()
        try:
            # 1. Process pending tasks
            self._process_pending_tasks(db)

            # 2. Process processing tasks
            self._process_processing_tasks(db)

            # 3. Repair reserved slots that are still stuck in a transient UI status.
            self._reconcile_reserved_slot_shot_states(db)

            # 4. Check session completion
            self._check_sessions_completion(db)

        finally:
            db.close()

    def _get_session_variant_target(self, session) -> int:
        try:
            parsed = int(getattr(session, "variant_count", 1) or 1)
        except (TypeError, ValueError):
            parsed = 1
        return max(1, min(10, parsed))

    def _get_task_group_counts(self, session_id, shot_stable_id, db):
        counts = {
            "pending": 0,
            "processing": 0,
            "completed": 0,
            "failed": 0,
        }
        rows = db.query(
            models.ManagedTask.status,
            func.count(models.ManagedTask.id)
        ).filter(
            models.ManagedTask.session_id == session_id,
            models.ManagedTask.shot_stable_id == shot_stable_id
        ).group_by(models.ManagedTask.status).all()

        for status, count in rows:
            counts[str(status or "").strip().lower()] = int(count or 0)

        return counts

    def _get_shot_retry_count(self, session_id, shot_stable_id, target_count, db) -> int:
        group_counts = self._get_task_group_counts(session_id, shot_stable_id, db)
        total_count = sum(int(group_counts.get(status, 0) or 0) for status in ("pending", "processing", "completed", "failed"))
        return max(0, total_count - int(target_count or 0))

    def _get_slot_task_counts(self, session_id, shot_id, db):
        counts = {
            "pending": 0,
            "processing": 0,
            "completed": 0,
            "failed": 0,
        }
        if not shot_id:
            return counts

        rows = db.query(
            models.ManagedTask.status,
            func.count(models.ManagedTask.id)
        ).filter(
            models.ManagedTask.session_id == session_id,
            models.ManagedTask.shot_id == shot_id
        ).group_by(models.ManagedTask.status).all()

        for status, count in rows:
            counts[str(status or "").strip().lower()] = int(count or 0)

        return counts

    def _get_slot_retry_count(self, session_id, shot_id, db) -> int:
        slot_counts = self._get_slot_task_counts(session_id, shot_id, db)
        total_count = sum(int(slot_counts.get(status, 0) or 0) for status in ("pending", "processing", "completed", "failed"))
        return max(0, total_count - 1)

    def _get_reserved_slot_shot(self, task, db):
        shot_id = int(getattr(task, "shot_id", 0) or 0)
        if shot_id <= 0:
            return None
        return db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id == shot_id
        ).first()

    def _normalize_error_message(self, error_message) -> str:
        return str(error_message or "").strip()

    def _should_skip_retry_for_error(self, error_message) -> bool:
        normalized_error = self._normalize_error_message(error_message)
        if not normalized_error:
            return True
        return any(snippet in normalized_error for snippet in MANAGED_NON_RETRY_ERROR_SNIPPETS)

    def _should_optimize_prompt_for_error(self, error_message) -> bool:
        normalized_error = self._normalize_error_message(error_message)
        return bool(normalized_error and MANAGED_PROMPT_REVIEW_ERROR_SNIPPET in normalized_error)

    def _append_no_retry_note(self, error_message) -> str:
        normalized_error = self._normalize_error_message(error_message)
        if MANAGED_NO_RETRY_NOTE in normalized_error:
            return normalized_error
        return f"{normalized_error}；{MANAGED_NO_RETRY_NOTE}" if normalized_error else MANAGED_NO_RETRY_NOTE

    def _is_no_retry_terminal_task(self, task) -> bool:
        return MANAGED_NO_RETRY_NOTE in self._normalize_error_message(getattr(task, "error_message", ""))

    def _get_task_prompt_override(self, task, reserved_shot):
        task_prompt = str(getattr(task, "prompt_text", "") or "").strip()
        if task_prompt:
            return task_prompt

        if reserved_shot and bool(getattr(reserved_shot, "sora_prompt_is_full", False)):
            reserved_prompt = str(getattr(reserved_shot, "sora_prompt", "") or "").strip()
            if reserved_prompt:
                return reserved_prompt

        return None

    def _optimize_retry_prompt(self, full_prompt: str, error_message: str, shot_id: int = 0) -> str:
        from ai_config import get_ai_config
        from ai_service import get_prompt_by_key, _extract_ai_response_content
        from main import MANAGED_PROMPT_OPTIMIZE_KEY

        prompt_template = get_prompt_by_key(MANAGED_PROMPT_OPTIMIZE_KEY)
        prompt = prompt_template.format(
            full_prompt=str(full_prompt or "").strip(),
            error_message=str(error_message or "").strip()
        )

        config = get_ai_config("managed_prompt_optimize")
        payload = {
            "model": config["model"],
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "stream": False
        }

        response = run_text_llm_request(
            stage="managed_prompt_optimize",
            url=config["api_url"],
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=config["timeout"],
            provider_key=str(config.get("provider_key") or ""),
            model=str(config.get("model_id") or config.get("model") or ""),
            request_tag=f"shot={int(shot_id or 0) or ''}",
            proxies={"http": None, "https": None}
        )

        if response.status_code == 200 and shot_id:
            billing_service.record_text_request_success_for_shot(
                shot_id=int(shot_id),
                stage="managed_prompt_optimize",
                provider=str(config.get("provider_key") or ""),
                model_name=str(config.get("model_id") or config.get("model") or ""),
                billing_key=f"text:managed_prompt_optimize:{shot_id}:{uuid.uuid4().hex[:8]}",
                operation_key=f"text:managed_prompt_optimize:{shot_id}",
                attempt_index=1,
                detail_json=json.dumps({
                    "shot_id": int(shot_id),
                    "error_message": str(error_message or ""),
                }, ensure_ascii=False),
            )

        if response.status_code != 200:
            raise Exception(f"优化提示词AI请求失败: {response.status_code} - {response.text}")

        try:
            result = response.json()
        except Exception as exc:
            raise Exception(f"优化提示词AI响应解析失败: {str(exc)}")

        optimized_prompt = _extract_ai_response_content(result).strip()
        if not optimized_prompt:
            raise Exception("优化提示词AI返回为空")

        return optimized_prompt

    def _submit_optimize_retry_prompt_task(self, db, *, managed_task, full_prompt: str, error_message: str, reserved_shot_id: int = 0):
        from ai_config import get_ai_config
        from ai_service import get_prompt_by_key
        from main import MANAGED_PROMPT_OPTIMIZE_KEY
        from text_relay_service import submit_and_persist_text_task

        prompt_template = get_prompt_by_key(MANAGED_PROMPT_OPTIMIZE_KEY)
        prompt = prompt_template.format(
            full_prompt=str(full_prompt or "").strip(),
            error_message=str(error_message or "").strip()
        )
        config = get_ai_config("managed_prompt_optimize")
        payload = {
            "model": config["model"],
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "stream": False
        }
        session = db.query(models.ManagedSession).filter(
            models.ManagedSession.id == int(getattr(managed_task, "session_id", 0) or 0)
        ).first()
        task_payload = {
            "managed_task_id": int(managed_task.id),
            "session_id": int(managed_task.session_id),
            "episode_id": int(getattr(session, "episode_id", 0) or 0),
            "reserved_shot_id": int(reserved_shot_id or 0),
            "shot_id": int(getattr(managed_task, "shot_id", 0) or 0),
            "shot_stable_id": str(getattr(managed_task, "shot_stable_id", "") or ""),
        }
        return submit_and_persist_text_task(
            db,
            task_type="managed_prompt_optimize",
            owner_type="episode",
            owner_id=int(getattr(managed_task, "shot_id", 0) or 0),
            stage_key="managed_prompt_optimize",
            function_key="managed_prompt_optimize",
            request_payload=payload,
            task_payload=task_payload,
        )

    def _mark_reserved_slot_failed(self, task, error_message, db):
        reserved_shot = self._get_reserved_slot_shot(task, db)
        if not reserved_shot:
            return

        normalized_error = str(error_message or "").strip()
        reserved_shot.video_status = "failed"
        reserved_shot.video_error_message = normalized_error
        reserved_shot.video_path = f"error:{normalized_error}" if normalized_error else ""
        reserved_shot.thumbnail_video_path = ""
        reserved_shot.task_id = ""

    def _sync_dashboard_task(self, task):
        try:
            if task and getattr(task, "id", None):
                sync_managed_task_to_dashboard(int(task.id))
        except Exception as exc:
            print(f"[managed][dashboard] sync failed for task {getattr(task, 'id', None)}: {str(exc)}")

    def _is_session_active(self, session) -> bool:
        return str(getattr(session, "status", "") or "").strip() in ACTIVE_MANAGED_SESSION_STATUSES

    def _ensure_original_shot_stable_id(self, original_shot, db) -> str:
        stable_id = str(getattr(original_shot, "stable_id", "") or "").strip()
        if stable_id:
            return stable_id

        stable_id = str(uuid.uuid4())
        original_shot.stable_id = stable_id
        db.flush()
        return stable_id

    def _sync_shot_family_stable_ids(self, original_shot, db) -> str:
        stable_id = self._ensure_original_shot_stable_id(original_shot, db)
        family_shots = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.episode_id == original_shot.episode_id,
            models.StoryboardShot.shot_number == original_shot.shot_number
        ).all()
        for shot in family_shots:
            if str(getattr(shot, "stable_id", "") or "").strip():
                continue
            shot.stable_id = stable_id
        db.flush()
        return stable_id

    def _get_next_variant_index(self, original_shot, db) -> int:
        max_variant = db.query(func.max(models.StoryboardShot.variant_index)).filter(
            models.StoryboardShot.episode_id == original_shot.episode_id,
            models.StoryboardShot.shot_number == original_shot.shot_number
        ).scalar()
        family_count = db.query(func.count(models.StoryboardShot.id)).filter(
            models.StoryboardShot.episode_id == original_shot.episode_id,
            models.StoryboardShot.shot_number == original_shot.shot_number
        ).scalar()
        return max(int(max_variant or 0), int(family_count or 0)) + 1

    def _process_pending_tasks(self, db):
        """doc"""
        limit = request_load_tracker.choose_batch_size(
            MANAGED_PENDING_BATCH_NORMAL,
            MANAGED_PENDING_BATCH_BUSY,
        )
        # Query pending tasks in active sessions
        pending_tasks = db.query(models.ManagedTask).join(
            models.ManagedSession,
            models.ManagedTask.session_id == models.ManagedSession.id
        ).filter(
            models.ManagedTask.status == "pending",
            models.ManagedSession.status.in_(ACTIVE_MANAGED_SESSION_STATUSES)
        ).order_by(
            models.ManagedTask.created_at.asc(),
            models.ManagedTask.id.asc()
        ).limit(limit).all()

        for task in pending_tasks:
            try:
                # Get session and provider
                session = db.query(models.ManagedSession).filter(
                    models.ManagedSession.id == task.session_id
                ).first()

                if not session:
                    task.status = "failed"
                    task.error_message = "managed session not found"
                    task.completed_at = datetime.utcnow()
                    self._mark_reserved_slot_failed(task, task.error_message, db)
                    db.commit()
                    self._sync_dashboard_task(task)
                    continue

                if not self._is_session_active(session):
                    continue

                reserved_shot = self._get_reserved_slot_shot(task, db)
                if reserved_shot and reserved_shot.video_status == "completed" and reserved_shot.video_path:
                    task.status = "failed"
                    task.error_message = "托管目标槽位已完成，跳过冗余任务"
                    task.completed_at = datetime.utcnow()
                    db.commit()
                    self._sync_dashboard_task(task)
                    continue

                target_count = self._get_session_variant_target(session)
                if int(getattr(task, "shot_id", 0) or 0) <= 0:
                    group_counts = self._get_task_group_counts(task.session_id, task.shot_stable_id, db)
                    active_without_current = group_counts["processing"] + max(0, group_counts["pending"] - 1)
                    if group_counts["completed"] + active_without_current >= target_count:
                        task.status = "failed"
                        task.error_message = "托管目标已满足，跳过冗余任务"
                        task.completed_at = datetime.utcnow()
                        db.commit()
                        self._sync_dashboard_task(task)
                        continue
                elif reserved_shot:
                    reserved_shot.video_status = "processing"
                    reserved_shot.video_error_message = ""
                    reserved_shot.video_path = ""
                    reserved_shot.thumbnail_video_path = ""
                    reserved_shot.task_id = ""

                # Get original shot by stable_id
                original_shot = db.query(models.StoryboardShot).filter(
                    models.StoryboardShot.stable_id == task.shot_stable_id,
                    models.StoryboardShot.variant_index == 0
                ).first()

                if not original_shot:
                    task.status = "failed"
                    task.error_message = "original shot not found"
                    task.completed_at = datetime.utcnow()
                    self._mark_reserved_slot_failed(task, task.error_message, db)
                    db.commit()
                    self._sync_dashboard_task(task)
                    self._retry_if_needed(task, db)
                    continue

                self._sync_shot_family_stable_ids(original_shot, db)

                prompt_override = self._get_task_prompt_override(task, reserved_shot)

                # Submit video generation with provider
                task_id, error_message, submitted_prompt = self._submit_video_generation(
                    original_shot,
                    session.provider,
                    db,
                    prompt_source_shot=reserved_shot or original_shot,
                    prompt_override=prompt_override,
                )
                if submitted_prompt:
                    task.prompt_text = submitted_prompt

                if task_id:
                    task.status = "processing"
                    task.task_id = task_id
                    task.error_message = ""
                    if reserved_shot:
                        reserved_shot.video_status = "processing"
                        reserved_shot.video_error_message = ""
                        reserved_shot.task_id = task_id
                        reserved_shot.provider = session.provider
                        reserved_shot.video_submitted_at = reserved_shot.video_submitted_at or datetime.utcnow()
                    charge_shot = reserved_shot or original_shot
                    if charge_shot:
                        try:
                            context = billing_service.get_shot_episode_context(db, shot_id=int(charge_shot.id))
                            if context:
                                billing_service.create_charge_entry(
                                    db,
                                    user_id=int(context["user_id"]),
                                    script_id=int(context["script_id"]),
                                    episode_id=int(context["episode_id"]),
                                    category="video",
                                    stage="managed_video_generate",
                                    provider=str(session.provider or ""),
                                    model_name="grok" if str(session.provider or "").strip().lower() in {"yijia-grok", "yijia"} else "sora-2",
                                    quantity=max(1, int(getattr(charge_shot, "duration", 0) or 0)),
                                    billing_key=f"video:managed:{task.id}:task:{task_id}",
                                    operation_key=f"video:managed:{task.session_id}:{task.shot_stable_id}",
                                    initial_status="pending",
                                    shot_id=int(charge_shot.id),
                                    attempt_index=1,
                                    external_task_id=str(task_id or ""),
                                    detail_json=json.dumps({
                                        "managed_task_id": int(task.id),
                                        "session_id": int(task.session_id),
                                    }, ensure_ascii=False),
                                )
                        except ValueError:
                            pass
                    print(f"[managed] task {task.id} submitted, task_id={task_id}, shot={original_shot.shot_number}")
                else:
                    task.status = "failed"
                    task.error_message = error_message or "Failed to submit video generation task"
                    task.completed_at = datetime.utcnow()
                    self._mark_reserved_slot_failed(task, task.error_message, db)

                db.commit()
                self._sync_dashboard_task(task)
                if task.status == "failed":
                    self._retry_if_needed(task, db)

            except Exception as e:
                print(f"[managed] process pending task {task.id} failed: {str(e)}")
                task.status = "failed"
                task.error_message = str(e)
                task.completed_at = datetime.utcnow()
                self._mark_reserved_slot_failed(task, task.error_message, db)
                db.commit()
                self._sync_dashboard_task(task)
                self._retry_if_needed(task, db)

    def _submit_video_generation(self, original_shot, provider, db, prompt_source_shot=None, prompt_override=None):
        """doc"""
        try:
            # Import required functions
            from main import (
                generate_collage_image,
                _build_unified_storyboard_video_task_payload,
                _get_episode_storyboard_video_settings,
                _is_moti_storyboard_video_model,
                _resolve_storyboard_video_model_by_provider,
            )

            # Build full sora prompt
            prompt_shot = prompt_source_shot or original_shot
            full_prompt = str(prompt_override or "").strip() or build_sora_prompt(prompt_shot, db)
            if not full_prompt:
                return None, "failed to build sora prompt", ""

            effective_provider = (provider or getattr(original_shot, "provider", None) or "yijia").strip() or "yijia"
            if effective_provider == "yijia-grok":
                effective_provider = "yijia"

            owner_username = ""
            episode = db.query(models.Episode).filter(
                models.Episode.id == original_shot.episode_id
            ).first()
            if episode:
                script = db.query(models.Script).filter(
                    models.Script.id == episode.script_id
                ).first()
                if script:
                    owner = db.query(models.User).filter(
                        models.User.id == script.user_id
                    ).first()
                    if owner and owner.username:
                        owner_username = owner.username.strip()

            model_name = _resolve_storyboard_video_model_by_provider(
                effective_provider,
                default_model=(
                    getattr(prompt_shot, "storyboard_video_model", None)
                    or getattr(original_shot, "storyboard_video_model", None)
                    or (getattr(episode, "storyboard_video_model", None) if episode else None)
                    or ("grok" if effective_provider == "yijia" else "sora-2")
                ),
            )

            episode_settings = _get_episode_storyboard_video_settings(episode) if episode else {
                "resolution_name": "",
                "provider": effective_provider,
            }

            selected_collage = None
            if not _is_moti_storyboard_video_model(model_name):
                selected_collage = db.query(models.ShotCollage).filter(
                    models.ShotCollage.shot_id == original_shot.id,
                    models.ShotCollage.is_selected == True
                ).first()

            if not _is_moti_storyboard_video_model(model_name) and not selected_collage:
                # No selected collage, create one
                print(f"[managed] shot {original_shot.id} has no selected collage, creating one...")
                try:
                    collage_url = generate_collage_image(original_shot.id, db, include_scenes=False, aspect_ratio=original_shot.aspect_ratio or "16:9")

                    # Create and save new collage record
                    new_collage = models.ShotCollage(
                        shot_id=original_shot.id,
                        collage_path=collage_url,
                        is_selected=True
                    )
                    db.add(new_collage)
                    db.commit()
                    db.refresh(new_collage)

                    selected_collage = new_collage
                    print(f"[managed] collage created: {collage_url}")
                except Exception as e:
                    return None, f"鐢熸垚鎷煎浘澶辫触: {str(e)}", full_prompt

            request_data = _build_unified_storyboard_video_task_payload(
                shot=prompt_shot,
                db=db,
                username=owner_username,
                model_name=model_name,
                provider=effective_provider,
                full_prompt=full_prompt,
                aspect_ratio=original_shot.aspect_ratio,
                duration=original_shot.duration,
                first_frame_image_url=(selected_collage.collage_path if selected_collage else ""),
                resolution_name=episode_settings.get("resolution_name", ""),
                appoint_account=episode_settings.get("appoint_account", ""),
            )
            submit_timeout = 60 if _is_moti_storyboard_video_model(model_name) else 30

            submit_response = requests.post(
                get_video_task_create_url(),
                headers=get_video_api_headers(),
                json=request_data,
                timeout=submit_timeout
            )

            if submit_response.status_code != 200:
                return None, (
                    f"Video request failed with status code {submit_response.status_code}: "
                    f"{submit_response.text}"
                ), full_prompt

            submit_result = submit_response.json()
            task_id = submit_result.get('task_id')

            if not task_id:
                return None, f"Video task submission failed: {submit_result.get('message', 'unknown error')}", full_prompt

            print(f"[managed] video generation submitted, task_id={task_id}")
            return task_id, None, full_prompt

        except Exception as e:
            print(f"[managed] submit video generation failed: {str(e)}")
            fallback_prompt = ""
            try:
                fallback_prompt = str(locals().get("full_prompt", "") or "").strip() or str(prompt_override or "").strip()
            except Exception:
                fallback_prompt = str(prompt_override or "").strip()
            return None, str(e), fallback_prompt

    def _process_processing_tasks(self, db):
        """doc"""
        processing_tasks = db.query(models.ManagedTask).join(
            models.ManagedSession,
            models.ManagedTask.session_id == models.ManagedSession.id
        ).filter(
            models.ManagedTask.status == "processing",
            models.ManagedSession.status.in_(ACTIVE_MANAGED_SESSION_STATUSES)
        ).order_by(
            models.ManagedTask.id.asc()
        ).all()

        if not processing_tasks:
            return

        print(f"[managed] 本轮查询 {len(processing_tasks)} 条 processing 任务")

        # 并行查询上游状态（10个worker自动排队，全部跑完再处理DB）
        def fetch_status(task):
            return task.id, check_video_status(task.task_id)

        status_results = {}
        with ThreadPoolExecutor(max_workers=MANAGED_PROCESSING_PARALLEL_WORKERS) as executor:
            futures = {executor.submit(fetch_status, t): t.id for t in processing_tasks}
            for future in as_completed(futures):
                try:
                    task_id, result = future.result()
                    status_results[task_id] = result
                except Exception as e:
                    print(f"[managed] fetch status error: {e}")

        # 串行处理结果并写DB
        for task in processing_tasks:
            try:
                video_status_result = status_results.get(task.id)

                if not video_status_result:
                    continue

                if is_transient_video_status_error(video_status_result):
                    print(
                        f"[managed] task {task.id} status query transient failure: "
                        f"{video_status_result.get('error_message', '')}"
                    )
                    continue

                # Get session
                session = db.query(models.ManagedSession).filter(
                    models.ManagedSession.id == task.session_id
                ).first()

                if not session:
                    task.status = "failed"
                    task.error_message = "managed session not found"
                    task.completed_at = datetime.utcnow()
                    self._mark_reserved_slot_failed(task, task.error_message, db)
                    db.commit()
                    self._sync_dashboard_task(task)
                    continue

                if not self._is_session_active(session):
                    continue

                status = normalize_video_generation_status(
                    video_status_result.get('status'),
                    default_value='processing',
                )

                if status == 'completed':
                    # Video generation completed
                    video_url = video_status_result.get('video_url')
                    cdn_url = video_status_result.get('cdn_url', video_url)
                    thumbnail_url = video_status_result.get('thumbnail_url', '')
                    price = video_status_result.get('price') or 0.0

                    if not video_url:
                        task.status = "failed"
                        task.error_message = "Video generation completed but no video URL returned"
                        task.completed_at = datetime.utcnow()
                        self._mark_reserved_slot_failed(task, task.error_message, db)
                        billing_service.reverse_charge_entry(
                            db,
                            billing_key=f"video:managed:{task.id}:task:{task.task_id}",
                            reason="completed_without_video_url",
                        )
                        db.commit()
                        self._sync_dashboard_task(task)
                        self._retry_if_needed(task, db)
                        continue

                    # Get original shot by stable_id
                    original_shot = db.query(models.StoryboardShot).filter(
                        models.StoryboardShot.stable_id == task.shot_stable_id,
                        models.StoryboardShot.variant_index == 0
                    ).first()

                    if not original_shot:
                        task.status = "failed"
                        task.error_message = "original shot not found"
                        task.completed_at = datetime.utcnow()
                        self._mark_reserved_slot_failed(task, task.error_message, db)
                        billing_service.reverse_charge_entry(
                            db,
                            billing_key=f"video:managed:{task.id}:task:{task.task_id}",
                            reason="original_shot_missing",
                        )
                        db.commit()
                        self._sync_dashboard_task(task)
                        self._retry_if_needed(task, db)
                        continue

                    self._sync_shot_family_stable_ids(original_shot, db)
                    reserved_shot = self._get_reserved_slot_shot(task, db)
                    if reserved_shot:
                        reserved_shot.video_path = cdn_url
                        reserved_shot.thumbnail_video_path = thumbnail_url or cdn_url
                        reserved_shot.video_status = 'completed'
                        reserved_shot.task_id = task.task_id
                        reserved_shot.provider = session.provider
                        reserved_shot.video_error_message = ""
                        reserved_shot.video_submitted_at = reserved_shot.video_submitted_at or datetime.utcnow()
                        reserved_shot.price = int(price * 100)
                        db.flush()

                        task.status = "completed"
                        task.video_path = cdn_url
                        task.completed_at = datetime.utcnow()
                        billing_service.finalize_charge_entry(
                            db,
                            billing_key=f"video:managed:{task.id}:task:{task.task_id}",
                        )
                        db.commit()
                        self._sync_dashboard_task(task)

                        print(
                            f"[managed] task {task.id} completed, "
                            f"filled reserved slot={reserved_shot.shot_number}_{reserved_shot.variant_index}"
                        )
                    elif not original_shot.video_path or original_shot.video_path.startswith('error:'):
                        # Legacy fallback: no reserved slot, update original shot
                        original_shot.video_path = cdn_url
                        original_shot.thumbnail_video_path = thumbnail_url or cdn_url
                        original_shot.video_status = 'completed'
                        original_shot.task_id = task.task_id
                        original_shot.provider = session.provider
                        original_shot.video_error_message = ""
                        original_shot.price = int(price * 100)
                        db.flush()

                        task.shot_id = original_shot.id
                        task.status = "completed"
                        task.video_path = cdn_url
                        task.completed_at = datetime.utcnow()
                        billing_service.finalize_charge_entry(
                            db,
                            billing_key=f"video:managed:{task.id}:task:{task.task_id}",
                        )
                        db.commit()
                        self._sync_dashboard_task(task)

                        print(f"[managed] task {task.id} completed, updated original shot={original_shot.shot_number}")
                    else:
                        # Legacy fallback: no reserved slot, create a new variant
                        new_variant_index = self._get_next_variant_index(original_shot, db)

                        new_shot = models.StoryboardShot(
                            episode_id=original_shot.episode_id,
                            shot_number=original_shot.shot_number,
                            stable_id=original_shot.stable_id,
                            variant_index=new_variant_index,
                            prompt_template=original_shot.prompt_template,
                            script_excerpt=original_shot.script_excerpt,
                            storyboard_video_prompt=original_shot.storyboard_video_prompt,
                            storyboard_audio_prompt=original_shot.storyboard_audio_prompt,
                            storyboard_dialogue=original_shot.storyboard_dialogue,
                            scene_override=original_shot.scene_override,
                            sora_prompt=original_shot.sora_prompt,
                            sora_prompt_is_full=bool(getattr(original_shot, "sora_prompt_is_full", False)),
                            sora_prompt_status=original_shot.sora_prompt_status,
                            selected_card_ids=original_shot.selected_card_ids,
                            selected_sound_card_ids=getattr(original_shot, "selected_sound_card_ids", None),
                            aspect_ratio=original_shot.aspect_ratio,
                            duration=original_shot.duration,
                            duration_override_enabled=bool(getattr(original_shot, "duration_override_enabled", False)),
                            provider=session.provider,
                            video_path=cdn_url,
                            thumbnail_video_path=thumbnail_url or cdn_url,
                            video_status='completed',
                            task_id=task.task_id,
                            video_submitted_at=datetime.utcnow(),
                            price=int(price * 100)
                        )
                        db.add(new_shot)
                        db.flush()

                        task.shot_id = new_shot.id
                        task.status = "completed"
                        task.video_path = cdn_url
                        task.completed_at = datetime.utcnow()
                        billing_service.finalize_charge_entry(
                            db,
                            billing_key=f"video:managed:{task.id}:task:{task.task_id}",
                        )
                        db.commit()
                        self._sync_dashboard_task(task)

                        print(f"[managed] task {task.id} completed, created shot variant={new_shot.shot_number}_{new_variant_index}")

                    # Update session progress
                    self._update_session_progress(task.session_id, db)

                elif status == 'failed':
                    # Video generation failed or cancelled
                    error_msg = video_status_result.get('error_message', '') or f'video generation {status}'
                    task.status = "failed"
                    task.error_message = error_msg
                    task.completed_at = datetime.utcnow()
                    self._mark_reserved_slot_failed(task, error_msg, db)
                    if str(getattr(task, "task_id", "") or "").strip():
                        billing_service.reverse_charge_entry(
                            db,
                            billing_key=f"video:managed:{task.id}:task:{task.task_id}",
                            reason="provider_failed",
                        )
                    db.commit()
                    self._sync_dashboard_task(task)

                    print(f"[managed] task {task.id} {status}: {error_msg}")

                    # Retry if needed
                    self._retry_if_needed(task, db)

            except Exception as e:
                print(f"[managed] process processing task {task.id} failed: {str(e)}")
                import traceback
                traceback.print_exc()

    def _reconcile_reserved_slot_shot_states(self, db):
        """Repair reserved storyboard shots whose managed task has already reached a terminal state."""
        limit = request_load_tracker.choose_batch_size(
            MANAGED_RESERVED_SLOT_RECONCILE_BATCH_NORMAL,
            MANAGED_RESERVED_SLOT_RECONCILE_BATCH_BUSY,
        )
        candidate_shots = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.video_status.in_(["submitting", "preparing", "processing"])
        ).order_by(
            models.StoryboardShot.video_submitted_at.asc().nullsfirst(),
            models.StoryboardShot.id.asc()
        ).limit(limit).all()

        if not candidate_shots:
            return 0

        shot_ids = [int(shot.id) for shot in candidate_shots if int(getattr(shot, "id", 0) or 0) > 0]
        if not shot_ids:
            return 0

        latest_tasks = {}
        rows = db.query(models.ManagedTask).filter(
            models.ManagedTask.shot_id.in_(shot_ids)
        ).order_by(
            models.ManagedTask.shot_id.asc(),
            models.ManagedTask.id.desc()
        ).all()
        for task in rows:
            if task.shot_id not in latest_tasks:
                latest_tasks[task.shot_id] = task

        touched = 0
        for shot in candidate_shots:
            latest_task = latest_tasks.get(int(getattr(shot, "id", 0) or 0))
            if not latest_task:
                shot_task_id = str(getattr(shot, "task_id", "") or "").strip()
                reference_time = getattr(shot, "video_submitted_at", None) or getattr(shot, "created_at", None)
                is_stale = False
                if reference_time:
                    try:
                        is_stale = reference_time <= datetime.utcnow() - timedelta(minutes=MANAGED_ORPHAN_ACTIVE_SHOT_TIMEOUT_MINUTES)
                    except Exception:
                        is_stale = False
                if not shot_task_id and is_stale:
                    error_message = "任务提交状态已丢失，请重新生成"
                    if (shot.video_status or "").strip().lower() != "failed":
                        shot.video_status = "failed"
                        touched += 1
                    if str(getattr(shot, "video_error_message", "") or "").strip() != error_message:
                        shot.video_error_message = error_message
                        touched += 1
                    target_video_path = f"error:{error_message}"
                    if str(getattr(shot, "video_path", "") or "").strip() != target_video_path:
                        shot.video_path = target_video_path
                        touched += 1
                    if str(getattr(shot, "thumbnail_video_path", "") or "").strip():
                        shot.thumbnail_video_path = ""
                        touched += 1
                continue

            latest_status = str(getattr(latest_task, "status", "") or "").strip().lower()
            if latest_status == "processing":
                normalized_task_id = str(getattr(latest_task, "task_id", "") or "").strip()
                if normalized_task_id and str(getattr(shot, "task_id", "") or "").strip() != normalized_task_id:
                    shot.task_id = normalized_task_id
                    touched += 1
                if (shot.video_status or "").strip().lower() != "processing":
                    shot.video_status = "processing"
                    touched += 1
                if not shot.video_submitted_at:
                    shot.video_submitted_at = latest_task.created_at or datetime.utcnow()
                    touched += 1
                continue

            if latest_status == "completed":
                video_path = str(getattr(latest_task, "video_path", "") or "").strip()
                if not video_path:
                    continue
                changed = False
                if (shot.video_status or "").strip().lower() != "completed":
                    shot.video_status = "completed"
                    changed = True
                if str(getattr(shot, "video_path", "") or "").strip() != video_path:
                    shot.video_path = video_path
                    changed = True
                if str(getattr(shot, "thumbnail_video_path", "") or "").strip() != video_path:
                    shot.thumbnail_video_path = video_path
                    changed = True
                latest_task_id = str(getattr(latest_task, "task_id", "") or "").strip()
                if latest_task_id and str(getattr(shot, "task_id", "") or "").strip() != latest_task_id:
                    shot.task_id = latest_task_id
                    changed = True
                if str(getattr(shot, "video_error_message", "") or "").strip():
                    shot.video_error_message = ""
                    changed = True
                if changed:
                    touched += 1
                continue

            if latest_status == "failed":
                previous_status = str(getattr(shot, "video_status", "") or "").strip().lower()
                previous_error = str(getattr(shot, "video_error_message", "") or "").strip()
                self._mark_reserved_slot_failed(latest_task, latest_task.error_message, db)
                updated_error = str(getattr(shot, "video_error_message", "") or "").strip()
                if previous_status != "failed" or previous_error != updated_error:
                    touched += 1

        if touched:
            db.commit()
        return touched

    def _update_session_progress(self, session_id, db):
        """doc"""
        session = db.query(models.ManagedSession).filter(
            models.ManagedSession.id == session_id
        ).first()

        if not session:
            return {
                "has_terminal_failures": False,
                "has_active_tasks": False,
            }

        target_count = self._get_session_variant_target(session)

        # Get all tasks for this session
        tasks = db.query(models.ManagedTask).filter(
            models.ManagedTask.session_id == session_id
        ).all()

        # Group by stable_id
        stable_id_groups = {}
        for task in tasks:
            if task.shot_stable_id not in stable_id_groups:
                stable_id_groups[task.shot_stable_id] = []
            stable_id_groups[task.shot_stable_id].append(task)

        # Count terminal stable_ids:
        # 1. all reserved slots for this original shot reached terminal states
        # 2. legacy sessions fallback to the old count-based behaviour
        completed_count = 0
        has_terminal_failures = False
        has_active_tasks = any(t.status in {"pending", "processing", "prompt_optimizing"} for t in tasks)
        for _, group_tasks in stable_id_groups.items():
            slot_groups = {}
            for group_task in group_tasks:
                slot_id = int(getattr(group_task, "shot_id", 0) or 0)
                if slot_id > 0:
                    slot_groups.setdefault(slot_id, []).append(group_task)

            if len(slot_groups) >= target_count:
                terminal_slot_count = 0
                failed_slot_count = 0
                for slot_id, slot_tasks in slot_groups.items():
                    if any(t.status == "completed" for t in slot_tasks):
                        terminal_slot_count += 1
                        continue

                    active_slot_tasks = [t for t in slot_tasks if t.status in {"pending", "processing", "prompt_optimizing"}]
                    retry_count = max(0, len(slot_tasks) - 1)
                    has_no_retry_terminal = any(self._is_no_retry_terminal_task(t) for t in slot_tasks)
                    if not active_slot_tasks and (retry_count >= MAX_RETRY_PER_SHOT or has_no_retry_terminal):
                        terminal_slot_count += 1
                        failed_slot_count += 1

                if terminal_slot_count >= target_count:
                    completed_count += 1
                    if failed_slot_count > 0:
                        has_terminal_failures = True
                continue

            completed_tasks = [t for t in group_tasks if t.status == "completed"]
            active_tasks = [t for t in group_tasks if t.status in {"pending", "processing", "prompt_optimizing"}]
            retry_count = max(0, len(group_tasks) - target_count)
            has_no_retry_terminal = any(self._is_no_retry_terminal_task(t) for t in group_tasks)
            if len(completed_tasks) >= target_count:
                completed_count += 1
            elif not active_tasks and (retry_count >= MAX_RETRY_PER_SHOT or has_no_retry_terminal):
                completed_count += 1
                has_terminal_failures = True

        session.completed_shots = completed_count
        db.commit()
        return {
            "has_terminal_failures": has_terminal_failures,
            "has_active_tasks": has_active_tasks,
        }

    def _retry_if_needed(self, failed_task, db):
        """doc"""
        session = db.query(models.ManagedSession).filter(
            models.ManagedSession.id == failed_task.session_id
        ).first()

        if not session or not self._is_session_active(session):
            return False

        normalized_error = self._normalize_error_message(failed_task.error_message)
        should_skip_retry = self._should_skip_retry_for_error(normalized_error)
        should_optimize_prompt = self._should_optimize_prompt_for_error(normalized_error)
        original_shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.stable_id == failed_task.shot_stable_id,
            models.StoryboardShot.variant_index == 0
        ).first()

        reserved_shot_id = int(getattr(failed_task, "shot_id", 0) or 0)
        if reserved_shot_id > 0:
            reserved_shot = self._get_reserved_slot_shot(failed_task, db)
            slot_counts = self._get_slot_task_counts(
                failed_task.session_id,
                reserved_shot_id,
                db
            )
            completed_count = slot_counts["completed"]
            active_count = slot_counts["pending"] + slot_counts["processing"]
            retry_count = self._get_slot_retry_count(
                failed_task.session_id,
                reserved_shot_id,
                db
            )

            if completed_count > 0 or active_count > 0:
                return False

            if should_skip_retry:
                failed_task.error_message = self._append_no_retry_note(normalized_error)
                self._mark_reserved_slot_failed(failed_task, failed_task.error_message, db)
                db.commit()
                self._update_session_progress(failed_task.session_id, db)
                print(
                    f"[managed] retry disabled by error policy for reserved slot shot_id={reserved_shot_id}: "
                    f"{normalized_error or '<empty>'}"
                )
                return False

            if retry_count >= MAX_RETRY_PER_SHOT:
                retry_limit_note = f"已达到最大重试次数({MAX_RETRY_PER_SHOT})，该镜头不再重试"
                if retry_limit_note not in str(failed_task.error_message or ""):
                    base_error = str(failed_task.error_message or "").strip()
                    failed_task.error_message = f"{base_error}；{retry_limit_note}" if base_error else retry_limit_note
                self._mark_reserved_slot_failed(failed_task, failed_task.error_message, db)
                db.commit()
                self._update_session_progress(failed_task.session_id, db)
                print(
                    f"[managed] retry skipped for reserved slot shot_id={reserved_shot_id}, "
                    f"retry_count={retry_count}, limit={MAX_RETRY_PER_SHOT}"
                )
                return False

            retry_prompt_text = self._get_task_prompt_override(failed_task, reserved_shot)
            if not retry_prompt_text and original_shot:
                retry_prompt_text = build_sora_prompt(original_shot, db)

            if should_optimize_prompt:
                if not retry_prompt_text:
                    failed_task.error_message = self._append_no_retry_note("缺少可优化的完整提示词")
                    self._mark_reserved_slot_failed(failed_task, failed_task.error_message, db)
                    db.commit()
                    self._update_session_progress(failed_task.session_id, db)
                    return False
                new_task = models.ManagedTask(
                    session_id=failed_task.session_id,
                    shot_id=reserved_shot_id,
                    shot_stable_id=failed_task.shot_stable_id,
                    status="prompt_optimizing",
                    prompt_text=retry_prompt_text or ""
                )
                db.add(new_task)
                db.flush()
                try:
                    self._submit_optimize_retry_prompt_task(
                        db,
                        managed_task=new_task,
                        full_prompt=retry_prompt_text,
                        error_message=normalized_error,
                        reserved_shot_id=reserved_shot_id,
                    )
                    if reserved_shot:
                        reserved_shot.video_status = "processing"
                        reserved_shot.video_error_message = "优化提示词中"
                        reserved_shot.video_path = ""
                        reserved_shot.thumbnail_video_path = ""
                        reserved_shot.task_id = ""
                    db.commit()
                    self._sync_dashboard_task(new_task)
                    return True
                except Exception as exc:
                    optimize_error = self._append_no_retry_note(f"提交优化提示词任务失败: {str(exc)}")
                    new_task.status = "failed"
                    new_task.error_message = optimize_error
                    new_task.completed_at = datetime.utcnow()
                    self._mark_reserved_slot_failed(new_task, optimize_error, db)
                    db.commit()
                    self._update_session_progress(failed_task.session_id, db)
                    print(f"[managed] retry optimization submit failed for reserved slot shot_id={reserved_shot_id}: {str(exc)}")
                    return False

            new_task = models.ManagedTask(
                session_id=failed_task.session_id,
                shot_id=reserved_shot_id,
                shot_stable_id=failed_task.shot_stable_id,
                status="pending",
                prompt_text=retry_prompt_text or ""
            )
            db.add(new_task)
            if reserved_shot:
                reserved_shot.video_status = "processing"
                reserved_shot.video_error_message = ""
                reserved_shot.video_path = ""
                reserved_shot.thumbnail_video_path = ""
                reserved_shot.task_id = ""
            db.commit()
            self._sync_dashboard_task(new_task)
            print(
                f"[managed] retry task created for reserved slot shot_id={reserved_shot_id}, "
                f"retry_count={retry_count + 1}/{MAX_RETRY_PER_SHOT}"
            )
            return True

        target_count = self._get_session_variant_target(session)
        group_counts = self._get_task_group_counts(failed_task.session_id, failed_task.shot_stable_id, db)
        completed_count = group_counts["completed"]
        active_count = group_counts["pending"] + group_counts["processing"]
        retry_count = self._get_shot_retry_count(
            failed_task.session_id,
            failed_task.shot_stable_id,
            target_count,
            db
        )

        if completed_count + active_count >= target_count:
            return

        if should_skip_retry:
            failed_task.error_message = self._append_no_retry_note(normalized_error)
            db.commit()
            self._update_session_progress(failed_task.session_id, db)
            print(
                f"[managed] retry disabled by error policy for stable_id={failed_task.shot_stable_id}: "
                f"{normalized_error or '<empty>'}"
            )
            return False

        if retry_count >= MAX_RETRY_PER_SHOT:
            retry_limit_note = f"已达到最大重试次数({MAX_RETRY_PER_SHOT})，该镜头不再重试"
            if retry_limit_note not in str(failed_task.error_message or ""):
                base_error = str(failed_task.error_message or "").strip()
                failed_task.error_message = f"{base_error}；{retry_limit_note}" if base_error else retry_limit_note
                db.commit()
            print(
                f"[managed] retry skipped for stable_id={failed_task.shot_stable_id}, "
                f"retry_count={retry_count}, limit={MAX_RETRY_PER_SHOT}"
            )
            return False

        retry_prompt_text = str(getattr(failed_task, "prompt_text", "") or "").strip()
        if not retry_prompt_text and original_shot:
            retry_prompt_text = build_sora_prompt(original_shot, db)

        if should_optimize_prompt:
            if not retry_prompt_text:
                failed_task.error_message = self._append_no_retry_note("缺少可优化的完整提示词")
                db.commit()
                self._update_session_progress(failed_task.session_id, db)
                return False
            new_task = models.ManagedTask(
                session_id=failed_task.session_id,
                shot_id=0,
                shot_stable_id=failed_task.shot_stable_id,
                status="prompt_optimizing",
                prompt_text=retry_prompt_text or ""
            )
            db.add(new_task)
            db.flush()
            try:
                self._submit_optimize_retry_prompt_task(
                    db,
                    managed_task=new_task,
                    full_prompt=retry_prompt_text,
                    error_message=normalized_error,
                    reserved_shot_id=0,
                )
                db.commit()
                self._sync_dashboard_task(new_task)
                return True
            except Exception as exc:
                new_task.status = "failed"
                new_task.error_message = self._append_no_retry_note(f"提交优化提示词任务失败: {str(exc)}")
                new_task.completed_at = datetime.utcnow()
                db.commit()
                self._update_session_progress(failed_task.session_id, db)
                print(f"[managed] retry optimization submit failed for stable_id={failed_task.shot_stable_id}: {str(exc)}")
                return False

        new_task = models.ManagedTask(
            session_id=failed_task.session_id,
            shot_id=0,
            shot_stable_id=failed_task.shot_stable_id,
            status="pending",
            prompt_text=retry_prompt_text or ""
        )
        db.add(new_task)
        db.commit()
        self._sync_dashboard_task(new_task)
        print(
            f"[managed] retry task created for stable_id={failed_task.shot_stable_id}, "
            f"completed={completed_count}, active={active_count}, target={target_count}, "
            f"retry_count={retry_count + 1}/{MAX_RETRY_PER_SHOT}"
        )
        return True

    def _check_sessions_completion(self, db):
        """doc"""
        limit = request_load_tracker.choose_batch_size(
            MANAGED_SESSION_BATCH_NORMAL,
            MANAGED_SESSION_BATCH_BUSY,
        )
        running_sessions = db.query(models.ManagedSession).filter(
            models.ManagedSession.status.in_(ACTIVE_MANAGED_SESSION_STATUSES)
        ).order_by(
            models.ManagedSession.id.asc()
        ).limit(limit).all()

        for session in running_sessions:
            progress = self._update_session_progress(session.id, db)
            db.refresh(session)

            # Check if all shots are completed
            if session.completed_shots >= session.total_shots:
                session.status = "failed" if progress.get("has_terminal_failures") else "completed"
                session.completed_at = datetime.utcnow()
                db.commit()
                print(f"[managed] session {session.id} {session.status}")

# Create global managed poller instance
managed_poller = ManagedGenerationPoller()

