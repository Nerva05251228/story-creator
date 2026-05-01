import os
import shutil
import subprocess
import tempfile
from typing import Any, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from fastapi import UploadFile
from sqlalchemy.orm import Session

import models
from utils import upload_to_cdn


SOUND_CARD_TYPE = "声音"


def _safe_audio_duration_seconds(value: Any) -> float:
    try:
        duration_seconds = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return duration_seconds if duration_seconds > 0 else 0.0


def _probe_media_duration_seconds(file_path: str) -> float:
    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        file_path,
    ]
    probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    raw_duration = (probe_result.stdout or "").strip()
    duration_seconds = float(raw_duration)
    if duration_seconds <= 0:
        raise ValueError("音频时长无效")
    return round(duration_seconds, 3)


def _download_remote_audio_to_temp(audio_path: str) -> str:
    parsed = urlparse(audio_path)
    suffix = os.path.splitext(parsed.path or "")[1] or ".tmp"
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir="uploads")
    temp_path = temp_file.name
    temp_file.close()
    try:
        response = requests.get(audio_path, timeout=60, stream=True)
        response.raise_for_status()
        with open(temp_path, "wb") as buffer:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    buffer.write(chunk)
        return temp_path
    except Exception:
        try:
            os.remove(temp_path)
        except Exception:
            pass
        raise


def _ensure_audio_duration_seconds_cached(audio: Optional[models.SubjectCardAudio], db: Session) -> float:
    if not audio:
        return 0.0

    cached_duration = _safe_audio_duration_seconds(getattr(audio, "duration_seconds", 0))
    if cached_duration > 0:
        return cached_duration

    audio_path = str(getattr(audio, "audio_path", "") or "").strip()
    if not audio_path:
        return 0.0

    temp_path = None
    try:
        probe_path = audio_path
        if audio_path.startswith("http://") or audio_path.startswith("https://"):
            temp_path = _download_remote_audio_to_temp(audio_path)
            probe_path = temp_path
        duration_seconds = _probe_media_duration_seconds(probe_path)
        audio.duration_seconds = duration_seconds
        db.flush()
        return duration_seconds
    except Exception as e:
        print(f"[声音素材] 回填音频时长失败 audio_id={getattr(audio, 'id', None)}: {str(e)}")
        return 0.0
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def _backfill_audio_duration_cache(audios: List[models.SubjectCardAudio], db: Session) -> bool:
    updated_any = False
    for audio in audios or []:
        if _safe_audio_duration_seconds(getattr(audio, "duration_seconds", 0)) > 0:
            continue
        if _ensure_audio_duration_seconds_cached(audio, db) > 0:
            updated_any = True
    return updated_any


def _save_upload_to_local_path(upload_file: UploadFile, suffix: str) -> str:
    filename = f"{os.urandom(16).hex()}{suffix}"
    local_path = os.path.join("uploads", filename)
    with open(local_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)
    return local_path


def save_and_upload_to_cdn(upload_file: UploadFile) -> str:
    """保存上传的文件，上传到CDN，并返回CDN URL。"""
    local_path = None
    try:
        ext = os.path.splitext(upload_file.filename)[1]
        local_path = _save_upload_to_local_path(upload_file, ext)
        cdn_url = upload_to_cdn(local_path)
        try:
            os.remove(local_path)
        except Exception as e:
            print(f"删除临时文件失败: {str(e)}")
        return cdn_url
    except Exception as e:
        print(f"图片上传CDN失败: {str(e)}")
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except Exception:
                pass
        raise Exception(f"图片上传CDN失败: {str(e)}")


def save_audio_and_upload_to_cdn(upload_file: UploadFile) -> Tuple[str, float]:
    """保存音频到本地，缓存时长后上传到CDN。"""
    local_path = None
    try:
        ext = os.path.splitext(upload_file.filename)[1]
        local_path = _save_upload_to_local_path(upload_file, ext)
        duration_seconds = _probe_media_duration_seconds(local_path)
        cdn_url = upload_to_cdn(local_path)
        try:
            os.remove(local_path)
        except Exception as e:
            print(f"删除音频临时文件失败: {str(e)}")
        return cdn_url, duration_seconds
    except Exception as e:
        print(f"音频上传CDN失败: {str(e)}")
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except Exception:
                pass
        raise Exception(f"音频上传CDN失败: {str(e)}")

