import requests
import asyncio
import threading
import os
import uuid
import subprocess
import time
from typing import Optional
from sqlalchemy.orm import Session
from database import SessionLocal
from runtime_load import request_load_tracker
from dashboard_service import sync_external_task_status_to_dashboard
import models
import billing_service
from video_api_config import (
    get_video_api_headers,
    get_video_task_status_url,
    get_video_task_urls_update_url,
)


TRANSIENT_VIDEO_STATUS_HTTP_CODES = {500, 502, 503, 504, 520, 521, 522, 524}
VIDEO_POLL_BUSY_INTERVAL_SECONDS = 12
VIDEO_STAGE1_BATCH_NORMAL = 8
VIDEO_STAGE1_BATCH_BUSY = 3
VIDEO_STAGE2_BATCH_NORMAL = 4
VIDEO_STAGE2_BATCH_BUSY = 1


def _build_query_failed_result(
    error_message: str,
    *,
    http_status: Optional[int] = None,
    transient: bool = True,
    raw_response: Optional[str] = None
) -> dict:
    result = {
        "status": "query_failed",
        "video_url": "",
        "error_message": error_message,
        "progress": 0,
        "cdn_uploaded": False,
        "price": 0.0,
        "info": "",
        "query_ok": False,
        "query_http_status": http_status,
        "query_transient": transient,
    }
    if raw_response is not None:
        result["raw_response"] = raw_response
    return result


def is_transient_video_status_error(status_result: Optional[dict]) -> bool:
    if not isinstance(status_result, dict):
        return False
    if status_result.get("query_ok") is False and bool(status_result.get("query_transient", False)):
        return True
    return str(status_result.get("status") or "").strip().lower() == "query_failed"


def normalize_video_generation_status(status: Optional[str], default_value: str = "processing") -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "success", "succeeded", "done"}:
        return "completed"
    if normalized in {"failed", "failure", "error", "cancelled", "canceled", "timeout", "timed_out"}:
        return "failed"
    if normalized in {"submitted", "pending", "queued", "waiting"}:
        return "pending"
    if normalized in {"processing", "running", "in_progress", "preparing", "starting"}:
        return "processing"
    return default_value


def add_title_frame(input_path: str, output_path: str, title_text: str):
    """
    为视频添加封面帧（使用原视频第3秒的画面 + 标题文字）

    Args:
        input_path: 输入视频路径
        output_path: 输出视频路径
        title_text: 标题文字（如"镜头 #1"）
    """
    try:
        # 第1步：获取视频信息（分辨率、帧率、音频参数）
        # 获取视频流信息
        probe_video_cmd = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height,r_frame_rate',
            '-of', 'csv=p=0',
            input_path
        ]

        probe_result = subprocess.run(probe_video_cmd, capture_output=True, text=True, check=True)
        width, height, fps_str = probe_result.stdout.strip().split(',')

        # 计算帧率（如"30/1" -> 30）
        if '/' in fps_str:
            num, den = map(int, fps_str.split('/'))
            fps = num / den
        else:
            fps = float(fps_str)

        # 获取音频流信息
        probe_audio_cmd = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', 'a:0',
            '-show_entries', 'stream=sample_rate,channels',
            '-of', 'csv=p=0',
            input_path
        ]

        audio_result = subprocess.run(probe_audio_cmd, capture_output=True, text=True, check=False)
        if audio_result.returncode == 0 and audio_result.stdout.strip():
            audio_sample_rate, audio_channels = audio_result.stdout.strip().split(',')
        else:
            # 如果没有音频流，使用默认值
            audio_sample_rate = '48000'
            audio_channels = '2'

        # 封面帧显示1帧（最短时间）
        title_duration = 1.0 / fps  # 1帧的时间


        # 第2步：提取原视频第3秒的画面，并添加文字
        temp_title_path = input_path.replace('.mp4', '_title_temp.mp4')

        # FFmpeg命令：提取第3秒的画面 + 添加白色文字 + 静音音频
        # 使用与原始视频相同的音频参数，确保拼接时音频流一致
        channel_layout = 'stereo' if audio_channels == '2' else 'mono'

        # 从原视频提取第3秒的帧作为封面
        title_cmd = [
            'ffmpeg',
            '-ss', '3',  # 从第3秒开始
            '-i', input_path,  # 输入原视频
            '-f', 'lavfi',
            '-i', f'anullsrc=channel_layout={channel_layout}:sample_rate={audio_sample_rate}',
            '-frames:v', '1',  # 只提取1帧（输出选项，必须在所有输入之后）
            '-t', str(title_duration),  # 明确指定输出时长
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-r', str(fps),  # 设置帧率
            '-y',
            temp_title_path
        ]

        subprocess.run(title_cmd, check=True, capture_output=True)

        # 第3步：拼接封面帧 + 主视频
        # 创建文件列表（使用绝对路径）
        concat_list_path = input_path.replace('.mp4', '_concat_list.txt')

        # 转换为绝对路径
        abs_temp_title = os.path.abspath(temp_title_path)
        abs_input = os.path.abspath(input_path)

        with open(concat_list_path, 'w', encoding='utf-8') as f:
            # Windows路径需要转义反斜杠或使用正斜杠
            f.write(f"file '{abs_temp_title.replace(chr(92), '/')}'\n")
            f.write(f"file '{abs_input.replace(chr(92), '/')}'\n")

        # 第3步：拼接封面帧 + 主视频
        # 现在两个视频都有音频流了，可以安全地使用 -c copy
        concat_cmd = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_list_path,
            '-c', 'copy',  # 直接复制流（快速），因为两个视频流结构一致
            '-y',
            output_path
        ]

        subprocess.run(concat_cmd, check=True, capture_output=True)

        # 第4步：清理临时文件
        try:
            if os.path.exists(temp_title_path):
                os.remove(temp_title_path)
            if os.path.exists(concat_list_path):
                os.remove(concat_list_path)
        except Exception as e:
            print(f"清理临时文件失败: {e}")

        return True

    except subprocess.CalledProcessError as e:
        print(f"FFmpeg命令执行失败: {e}")
        print(f"stderr: {e.stderr.decode('utf-8') if e.stderr else 'N/A'}")
        return False
    except Exception as e:
        print(f"添加封面帧失败: {str(e)}")
        return False


def download_and_upload_video(remote_url: str, shot_id: int) -> str:
    """
    下载视频并上传到自己的CDN

    Args:
        remote_url: 远程视频URL（Sora返回的）
        shot_id: 镜头ID

    Returns:
        str: 自己CDN的视频URL
    """
    local_path = None
    output_path = None
    try:
        # 确保videos目录存在
        os.makedirs("videos", exist_ok=True)

        # 生成本地临时文件名
        ext = ".mp4"  # 默认扩展名
        if '.' in remote_url:
            ext = '.' + remote_url.split('.')[-1].split('?')[0]  # 提取扩展名，去掉查询参数

        filename = f"shot_{shot_id}_{uuid.uuid4().hex[:8]}{ext}"
        local_path = os.path.join("videos", filename)

        # 第1步：下载视频到本地
        response = requests.get(remote_url, timeout=120, stream=True)

        if response.status_code != 200:
            raise Exception(f"下载失败: HTTP {response.status_code}")

        # 写入本地文件
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)


        # 第2步：添加标题帧
        # 查询shot信息以获取镜头编号
        db = SessionLocal()
        try:
            shot = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.id == shot_id
            ).first()

            if shot:
                # 生成标题文字（如"镜头 #1"或"镜头 #1_1"）
                if shot.variant_index and shot.variant_index > 0:
                    title_text = f"镜头 #{shot.shot_number}_{shot.variant_index}"
                else:
                    title_text = f"镜头 #{shot.shot_number}"


                # 生成输出文件路径
                output_filename = filename.replace(ext, f"_with_title{ext}")
                output_path = os.path.join("videos", output_filename)

                # 添加标题帧
                success = add_title_frame(local_path, output_path, title_text)

                if success and os.path.exists(output_path):
                    # 删除原始下载的视频，使用带标题的视频
                    try:
                        os.remove(local_path)
                    except Exception as e:
                        print(f"删除原始视频失败: {e}")

                    # 将output_path设为新的local_path
                    local_path = output_path
                else:
                    # 保持使用local_path（原始视频）
                    pass

        finally:
            db.close()

        # 第3步：上传到自己的CDN
        from utils import upload_to_cdn

        cdn_url = upload_to_cdn(local_path)

        # 第4步：删除本地临时文件（可选）
        # 如果你想保留本地备份，可以注释掉这行
        try:
            os.remove(local_path)
        except Exception as e:
            print(f"删除临时文件失败: {str(e)}")

        return cdn_url

    except Exception as e:
        print(f"视频处理失败: {str(e)}")
        # 清理临时文件
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except:
                pass
        if output_path and output_path != local_path and os.path.exists(output_path):
            try:
                os.remove(output_path)
            except:
                pass
        # 处理失败时返回原始远程URL作为备用
        return remote_url


def process_and_upload_video_with_cover(
    remote_url: str,
    task_id: str = "",
    name_tag: str = "storyboard2_video"
) -> dict:
    """
    Download remote video, prepend a short cover segment, upload to CDN,
    and optionally sync the task URLs back to upstream.
    """
    local_path = None
    cover_image_path = None
    cover_video_path = None
    output_path = None

    try:
        os.makedirs("videos", exist_ok=True)

        safe_tag = str(name_tag or "storyboard2_video")
        for ch in ['\\', '/', ':', '*', '?', '"', '<', '>', '|', ' ']:
            safe_tag = safe_tag.replace(ch, '_')
        safe_tag = safe_tag.strip('_') or "storyboard2_video"

        filename = f"{safe_tag}_{uuid.uuid4().hex[:8]}.mp4"
        local_path = os.path.join("videos", filename)

        response = requests.get(remote_url, timeout=120, stream=True)
        if response.status_code != 200:
            raise Exception(f"download failed: HTTP {response.status_code}")

        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        fps = 24.0
        try:
            probe_cmd = [
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=r_frame_rate',
                '-of', 'csv=p=0',
                local_path
            ]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
            fps_str = probe_result.stdout.strip()
            if '/' in fps_str:
                num, den = fps_str.split('/')
                fps = float(num) / max(float(den), 1.0)
            elif fps_str:
                fps = float(fps_str)
            if fps <= 0:
                fps = 24.0
        except Exception:
            fps = 24.0

        cover_image_path = local_path.replace('.mp4', '_cover.jpg')
        extract_frame_cmd = [
            'ffmpeg',
            '-ss', '3',
            '-i', local_path,
            '-vframes', '1',
            '-y',
            cover_image_path
        ]
        subprocess.run(extract_frame_cmd, check=True, capture_output=True)

        cover_video_path = local_path.replace('.mp4', '_cover_video.mp4')
        create_cover_cmd = [
            'ffmpeg',
            '-loop', '1',
            '-i', cover_image_path,
            '-f', 'lavfi',
            '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
            '-t', '0.1',
            '-pix_fmt', 'yuv420p',
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-crf', '23',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-r', str(fps),
            '-y',
            cover_video_path
        ]
        subprocess.run(create_cover_cmd, check=True, capture_output=True)

        output_path = local_path.replace('.mp4', '_final.mp4')
        concat_cmd = [
            'ffmpeg',
            '-i', cover_video_path,
            '-i', local_path,
            '-filter_complex', '[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]',
            '-map', '[outv]',
            '-map', '[outa]',
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-crf', '23',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-y',
            output_path
        ]
        subprocess.run(concat_cmd, check=True, capture_output=True)

        from utils import upload_to_cdn
        cdn_url = upload_to_cdn(output_path)

        upstream_updated = False
        upstream_status_code = None
        upstream_response = None
        normalized_task_id = str(task_id or "").strip()
        if normalized_task_id:
            try:
                update_response = requests.put(
                    get_video_task_urls_update_url(normalized_task_id),
                    headers=get_video_api_headers(),
                    json={
                        "cdn_video_url": cdn_url,
                        "cdn_thumbnail_url": cdn_url
                    },
                    timeout=30
                )
                upstream_status_code = update_response.status_code
                try:
                    upstream_response = update_response.json()
                except Exception:
                    upstream_response = update_response.text
                upstream_updated = update_response.status_code == 200
            except Exception as update_error:
                upstream_response = {"error": str(update_error)}

        return {
            "success": True,
            "cdn_url": cdn_url,
            "upstream_updated": upstream_updated,
            "upstream_status_code": upstream_status_code,
            "upstream_response": upstream_response
        }

    except Exception as e:
        return {
            "success": False,
            "cdn_url": "",
            "error": str(e),
            "upstream_updated": False
        }

    finally:
        for temp_file in [local_path, cover_image_path, cover_video_path, output_path]:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass


def check_video_status(task_id: str, return_raw: bool = False) -> dict:
    """
    Query video task status.
    """
    try:
        response = requests.get(
            get_video_task_status_url(task_id),
            headers={"Authorization": get_video_api_headers()["Authorization"]},
            timeout=30
        )

        if response.status_code != 200:
            if response.status_code in TRANSIENT_VIDEO_STATUS_HTTP_CODES:
                return _build_query_failed_result(
                    f"\u72b6\u6001\u67e5\u8be2\u5931\u8d25: HTTP {response.status_code}",
                    http_status=response.status_code,
                    transient=True,
                    raw_response=response.text if return_raw else None
                )

            return {
                "status": "failed",
                "video_url": "",
                "error_message": f"\u8bf7\u6c42\u5931\u8d25: {response.status_code}",
                "progress": 0,
                "cdn_uploaded": False,
                "price": 0.0,
                "info": "",
                "query_ok": False,
                "query_http_status": response.status_code,
                "query_transient": False,
                "raw_response": response.text if return_raw else None
            }

        result = response.json()
        if return_raw:
            return result

        status = result.get('status', 'pending')
        video_url = result.get('video_url', '') or ''
        error_message = result.get('error_message', '') or ''
        progress = result.get('progress', 0)
        cdn_uploaded = result.get('cdn_uploaded', False)
        price = float(result.get('price') or 0.0)
        info = str(result.get('info') or '').strip()
        return {
            "status": status,
            "video_url": video_url,
            "error_message": error_message,
            "progress": progress,
            "cdn_uploaded": cdn_uploaded,
            "price": price,
            "info": info,
            "query_ok": True,
            "query_http_status": 200,
            "query_transient": False
        }

    except Exception as e:
        return _build_query_failed_result(
            f"\u67e5\u8be2\u5f02\u5e38: {str(e)}",
            transient=True
        )


def update_shot_status(shot_id: int):
    db = SessionLocal()
    try:
        shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id == shot_id
        ).first()

        if not shot or not shot.task_id:
            return

        # ✅ 如果已经失败，停止轮询
        if shot.video_status == 'failed':
            return

        # ✅ 如果CDN已上传，停止轮询
        if shot.cdn_uploaded:
            return

        task_id = shot.task_id

        result = check_video_status(task_id)
        if is_transient_video_status_error(result):
            print(f"video status query transient failure for shot {shot_id} (task: {task_id}): {result.get('error_message', '')}")
            return
        status = normalize_video_generation_status(result.get('status'), default_value='')
        cdn_uploaded = result.get('cdn_uploaded', False)

        if status == 'completed':
            video_url = result.get('video_url', '')
            if not video_url:
                return

            shot = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.id == shot_id
            ).first()
            if not shot:
                return

            previous_video_path = shot.video_path
            previous_thumbnail = shot.thumbnail_video_path

            # ✅ 第一阶段：视频生成完成
            if not shot.video_path or shot.video_status != 'completed':
                shot.video_status = 'completed'
                shot.video_path = video_url
                if not previous_thumbnail or previous_thumbnail == previous_video_path:
                    shot.thumbnail_video_path = video_url

                new_video = models.ShotVideo(
                    shot_id=shot.id,
                    video_path=video_url
                )
                db.add(new_video)
                print(f"video completed for shot {shot_id}, starting background post-processing")

                # 启动后台任务处理视频（下载、添加封面、上传CDN）
                task_id = shot.task_id
                thread = threading.Thread(
                    target=post_process_video_background,
                    args=(task_id, shot_id, video_url),
                    daemon=True
                )
                thread.start()
                print(f"background post-processing started for shot {shot_id}")

            # ✅ 第二阶段：CDN上传完成
            if cdn_uploaded and not shot.cdn_uploaded:
                shot.cdn_uploaded = True
                shot.video_path = video_url  # 更新为CDN URL
                if not previous_thumbnail or previous_thumbnail == previous_video_path:
                    shot.thumbnail_video_path = video_url

                # ✅ 同步更新ShotVideo表中最新的记录
                latest_shot_video = db.query(models.ShotVideo).filter(
                    models.ShotVideo.shot_id == shot_id
                ).order_by(models.ShotVideo.created_at.desc()).first()

                if latest_shot_video:
                    latest_shot_video.video_path = video_url

                print(f"CDN upload completed for shot {shot_id}")

            billing_service.finalize_charge_entry(
                db,
                billing_key=f"video:shot:{shot.id}:task:{task_id}",
            )
            db.commit()
            sync_external_task_status_to_dashboard(
                external_task_id=task_id,
                status="completed",
                output_data={
                    "task_id": task_id,
                    "video_url": shot.video_path,
                    "thumbnail_video_url": shot.thumbnail_video_path,
                    "cdn_uploaded": bool(shot.cdn_uploaded),
                    "progress": result.get("progress", 100),
                    "provider_result": result,
                },
                stage="video_generate",
            )

        elif status == 'failed':
            error_message = result.get('error_message', '')
            shot.video_status = 'failed'
            shot.video_path = f"error:{error_message}"
            shot.video_error_message = error_message  # 保存错误信息到专用字段
            db.commit()
            billing_service.reverse_charge_entry(
                db,
                billing_key=f"video:shot:{shot.id}:task:{task_id}",
                reason="provider_failed",
            )
            db.commit()
            sync_external_task_status_to_dashboard(
                external_task_id=task_id,
                status="failed",
                raw_response={
                    "task_id": task_id,
                    "error": error_message,
                    "provider_result": result,
                },
                stage="video_generate",
            )
            print(f"video failed for shot {shot_id} (task: {task_id}): {error_message}")

        elif status in ['pending', 'processing']:
            shot.video_status = 'processing'
            # ✅ 记录提交时间（如果还没记录）
            if not shot.video_submitted_at:
                from datetime import datetime
                shot.video_submitted_at = datetime.utcnow()
            db.commit()

    except Exception as e:
        print(f"video status update failed for shot {shot_id}: {str(e)}")
        db.rollback()
    finally:
        db.close()


class VideoStatusPoller:
    def __init__(self, check_interval: int = 5):
        self.check_interval = check_interval
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        print(f"video poller started: {self.check_interval}s")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        print("video poller stopped")

    def _poll_loop(self):
        while self.running:
            try:
                db = SessionLocal()
                try:
                    busy_mode = request_load_tracker.should_throttle_background_tasks()
                    stage1_limit = request_load_tracker.choose_batch_size(
                        VIDEO_STAGE1_BATCH_NORMAL,
                        VIDEO_STAGE1_BATCH_BUSY,
                    )
                    stage2_limit = request_load_tracker.choose_batch_size(
                        VIDEO_STAGE2_BATCH_NORMAL,
                        VIDEO_STAGE2_BATCH_BUSY,
                    )

                    # ✅ 第一阶段：只取一小批处理中镜头，避免每轮全表扫描后的大批状态查询
                    stage1_shots = db.query(models.StoryboardShot.id).filter(
                        models.StoryboardShot.task_id != '',
                        models.StoryboardShot.video_status == 'processing',
                        models.StoryboardShot.cdn_uploaded == False
                    ).order_by(
                        models.StoryboardShot.video_submitted_at.asc().nullsfirst(),
                        models.StoryboardShot.id.asc()
                    ).limit(stage1_limit).all()

                    # ✅ 第二阶段：CDN后处理更重，前台繁忙时进一步缩小批次
                    stage2_shots = db.query(models.StoryboardShot.id).filter(
                        models.StoryboardShot.task_id != '',
                        models.StoryboardShot.video_status == 'completed',
                        models.StoryboardShot.cdn_uploaded == False
                    ).order_by(
                        models.StoryboardShot.id.asc()
                    ).limit(stage2_limit).all()
                finally:
                    db.close()

                # ✅ 第一阶段：每次都轮询
                if stage1_shots:
                    for shot_id in stage1_shots:
                        try:
                            update_shot_status(shot_id.id if hasattr(shot_id, "id") else shot_id)
                        except Exception as e:
                            current_shot_id = shot_id.id if hasattr(shot_id, "id") else shot_id
                            print(f"shot {current_shot_id} poll failed: {str(e)}")
                            continue

                # ✅ 第二阶段：前台繁忙时降低频率和批次，给用户请求让路
                stage2_mod = 5 if busy_mode else 3
                if stage2_shots and hasattr(self, '_poll_count') and self._poll_count % stage2_mod == 0:
                    for shot_id in stage2_shots:
                        try:
                            update_shot_status(shot_id.id if hasattr(shot_id, "id") else shot_id)
                        except Exception as e:
                            current_shot_id = shot_id.id if hasattr(shot_id, "id") else shot_id
                            print(f"shot {current_shot_id} CDN poll failed: {str(e)}")
                            continue

                # 计数器
                if not hasattr(self, '_poll_count'):
                    self._poll_count = 0
                self._poll_count += 1

            except Exception as e:
                print(f"video poller error: {str(e)}")

            sleep_seconds = request_load_tracker.choose_interval(
                self.check_interval,
                VIDEO_POLL_BUSY_INTERVAL_SECONDS,
            )
            time.sleep(sleep_seconds)


def post_process_video_background(task_id: str, shot_id: int, upstream_url: str):
    """
    后台处理视频：下载 -> 添加封面帧 -> 上传CDN -> 更新上游URL

    Args:
        task_id: 上游任务ID
        shot_id: 镜头ID
        upstream_url: 上游视频URL
    """
    local_path = None
    cover_image_path = None
    cover_video_path = None
    output_path = None

    try:
        print(f"[后台处理] 开始处理 shot {shot_id}, task {task_id}")

        # 第1步：下载视频
        os.makedirs("videos", exist_ok=True)
        filename = f"shot_{shot_id}_{uuid.uuid4().hex[:8]}.mp4"
        local_path = os.path.join("videos", filename)

        print(f"[后台处理] 下载视频: {upstream_url}")
        response = requests.get(upstream_url, timeout=120, stream=True)

        if response.status_code != 200:
            raise Exception(f"下载失败: HTTP {response.status_code}")

        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        print(f"[后台处理] 下载完成: {local_path}")

        # 第2步：提取第3秒的画面作为封面
        cover_image_path = local_path.replace('.mp4', '_cover.jpg')
        extract_cmd = [
            'ffprobe',
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height,r_frame_rate',
            '-of', 'csv=p=0',
            local_path
        ]

        probe_result = subprocess.run(extract_cmd, capture_output=True, text=True, check=True)
        width, height, fps_str = probe_result.stdout.strip().split(',')

        # 计算帧率
        if '/' in fps_str:
            num, den = map(int, fps_str.split('/'))
            fps = num / den
        else:
            fps = float(fps_str)

        # 提取第3秒的画面
        print(f"[后台处理] 提取封面帧")
        extract_frame_cmd = [
            'ffmpeg',
            '-ss', '3',
            '-i', local_path,
            '-vframes', '1',
            '-y',
            cover_image_path
        ]
        subprocess.run(extract_frame_cmd, check=True, capture_output=True)

        # 第3步：创建0.1秒的封面视频段（静态图 + 静音音频）
        cover_video_path = local_path.replace('.mp4', '_cover_video.mp4')

        print(f"[后台处理] 创建封面视频段 (0.1秒)")
        create_cover_cmd = [
            'ffmpeg',
            '-loop', '1',
            '-i', cover_image_path,
            '-f', 'lavfi',
            '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
            '-t', '0.1',
            '-pix_fmt', 'yuv420p',
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-crf', '23',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-r', str(fps),
            '-y',
            cover_video_path
        ]
        subprocess.run(create_cover_cmd, check=True, capture_output=True)

        # 第4步：拼接封面视频 + 原视频（使用filter_complex重新编码）
        output_path = local_path.replace('.mp4', '_final.mp4')

        print(f"[后台处理] 拼接视频")
        concat_cmd = [
            'ffmpeg',
            '-i', cover_video_path,
            '-i', local_path,
            '-filter_complex', '[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]',
            '-map', '[outv]',
            '-map', '[outa]',
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-crf', '23',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-y',
            output_path
        ]
        subprocess.run(concat_cmd, check=True, capture_output=True)

        # 第5步：上传到CDN
        print(f"[后台处理] 上传到CDN")
        from utils import upload_to_cdn
        cdn_url = upload_to_cdn(output_path)
        print(f"[后台处理] CDN上传完成: {cdn_url}")

        # 第6步：调用上游API更新URL
        print(f"[后台处理] 更新上游URL")
        update_response = requests.put(
            get_video_task_urls_update_url(task_id),
            headers=get_video_api_headers(),
            json={
                "cdn_video_url": cdn_url,
                "cdn_thumbnail_url": cdn_url
            },
            timeout=30
        )

        if update_response.status_code == 200:
            print(f"[后台处理] 上游URL更新成功")
        else:
            print(f"[后台处理] 上游URL更新失败: {update_response.status_code} - {update_response.text}")
            # 即使上游更新失败，本地数据库仍然更新为CDN URL

        # 第7步：更新本地数据库
        db = SessionLocal()
        try:
            shot = db.query(models.StoryboardShot).filter(
                models.StoryboardShot.id == shot_id
            ).first()

            if shot:
                previous_video_path = shot.video_path
                previous_thumbnail = shot.thumbnail_video_path

                shot.video_path = cdn_url
                shot.cdn_uploaded = True

                if not previous_thumbnail or previous_thumbnail == previous_video_path:
                    shot.thumbnail_video_path = cdn_url

                # 更新ShotVideo表
                latest_shot_video = db.query(models.ShotVideo).filter(
                    models.ShotVideo.shot_id == shot_id
                ).order_by(models.ShotVideo.created_at.desc()).first()

                if latest_shot_video:
                    latest_shot_video.video_path = cdn_url

                db.commit()
                sync_external_task_status_to_dashboard(
                    external_task_id=task_id,
                    status="completed",
                    output_data={
                        "task_id": task_id,
                        "video_url": cdn_url,
                        "thumbnail_video_url": shot.thumbnail_video_path or cdn_url,
                        "cdn_uploaded": True,
                    },
                    stage="video_generate",
                )
                print(f"[后台处理] 数据库更新完成 shot {shot_id}")
        finally:
            db.close()

        # 第8步：清理临时文件
        for temp_file in [local_path, cover_image_path, cover_video_path, output_path]:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception as e:
                    print(f"[后台处理] 清理临时文件失败 {temp_file}: {e}")

        print(f"[后台处理] 完成 shot {shot_id}")

    except Exception as e:
        print(f"[后台处理] 失败 shot {shot_id}: {str(e)}")
        import traceback
        traceback.print_exc()

        # 清理临时文件
        for temp_file in [local_path, cover_image_path, cover_video_path, output_path]:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass


poller = VideoStatusPoller(check_interval=8)
