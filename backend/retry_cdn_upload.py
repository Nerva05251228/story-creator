#!/usr/bin/env python3
"""
重试CDN上传脚本

检查所有成功的任务（video_status='completed'）中未上传到CDN的镜头（cdn_uploaded=False），
然后并发进行上传和后处理（包含设置封面、上传CDN、更新上游URL等），并发数量为10。

使用方法：
    python retry_cdn_upload.py
"""

import sys
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal
import models
from video_service import post_process_video_background


def get_pending_uploads():
    """
    查询所有需要上传的镜头：
    - video_status = 'completed'
    - cdn_uploaded = False
    - video_path 不为空
    - task_id 不为空

    返回: [(shot_id, task_id, video_url), ...]
    """
    db = SessionLocal()
    try:
        shots = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.video_status == 'completed',
            models.StoryboardShot.cdn_uploaded == False,
            models.StoryboardShot.video_path != '',
            models.StoryboardShot.task_id != ''
        ).all()

        pending = [
            (shot.id, shot.task_id, shot.video_path)
            for shot in shots
        ]

        return pending
    finally:
        db.close()


def process_shot_upload(shot_id, task_id, video_url):
    """
    处理单个镜头的上传和后处理

    Args:
        shot_id: 镜头ID
        task_id: 上游任务ID
        video_url: 上游视频URL
    """
    try:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始处理 shot_id={shot_id}, task_id={task_id}")

        # 调用后台处理函数
        post_process_video_background(task_id, shot_id, video_url)

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✓ 成功完成 shot_id={shot_id}")
        return {
            'shot_id': shot_id,
            'status': 'success',
            'message': '上传和后处理完成'
        }
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✗ 失败 shot_id={shot_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            'shot_id': shot_id,
            'status': 'failed',
            'message': str(e)
        }


def main():
    """主函数"""
    print(f"\n{'='*70}")
    print(f"CDN上传重试脚本 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    # 第1步：查询需要上传的镜头
    print("正在查询需要上传的镜头...")
    pending_uploads = get_pending_uploads()

    if not pending_uploads:
        print("✓ 没有待上传的镜头，所有任务都已上传到CDN")
        return

    print(f"找到 {len(pending_uploads)} 个待上传镜头\n")

    for i, (shot_id, task_id, video_url) in enumerate(pending_uploads, 1):
        print(f"  {i}. shot_id={shot_id}, task_id={task_id}")
        print(f"     URL: {video_url[:80]}..." if len(video_url) > 80 else f"     URL: {video_url}")

    print(f"\n{'='*70}")
    print(f"准备以10个并发线程进行上传...\n")

    # 第2步：创建线程池，并发处理
    completed_count = 0
    failed_count = 0
    results = []

    start_time = time.time()

    with ThreadPoolExecutor(max_workers=10) as executor:
        # 提交所有任务
        futures = {
            executor.submit(process_shot_upload, shot_id, task_id, video_url): (shot_id, task_id)
            for shot_id, task_id, video_url in pending_uploads
        }

        # 处理完成的任务
        for future in as_completed(futures):
            result = future.result()
            results.append(result)

            if result['status'] == 'success':
                completed_count += 1
            else:
                failed_count += 1

    elapsed_time = time.time() - start_time

    # 第3步：输出总结报告
    print(f"\n{'='*70}")
    print(f"上传完成总结")
    print(f"{'='*70}")
    print(f"总计处理:  {len(pending_uploads)} 个镜头")
    print(f"成功:      {completed_count} 个")
    print(f"失败:      {failed_count} 个")
    print(f"耗时:      {elapsed_time:.2f} 秒")
    print(f"平均耗时:  {elapsed_time/len(pending_uploads):.2f} 秒/镜头\n")

    # 显示失败的镜头
    if failed_count > 0:
        print("失败的镜头:")
        for result in results:
            if result['status'] == 'failed':
                print(f"  - shot_id={result['shot_id']}: {result['message']}")

    print(f"\n{'='*70}\n")


if __name__ == '__main__':
    main()
