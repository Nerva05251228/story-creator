"""
批量同步所有未完成镜头的视频状态

该脚本会：
1. 查询所有未完成的镜头（非 completed 状态或 CDN 未上传）
2. 并发调用后端 API 检查实际状态（最大并发数30）
3. 如果任务已完成，更新数据库状态
"""

import sys
import asyncio
import aiohttp
from typing import Tuple, Dict, List
from sqlalchemy.orm import Session
from database import SessionLocal
import models
from datetime import datetime
from video_api_config import get_video_task_status_url, VIDEO_API_TOKEN


async def check_video_status_async(task_id: str, session: aiohttp.ClientSession) -> Dict:
    """
    异步检查视频生成状态

    Args:
        task_id: 任务ID
        session: aiohttp 会话

    Returns:
        dict: 状态信息
    """
    try:
        url = get_video_task_status_url(task_id)
        headers = {
            "Authorization": f"Bearer {VIDEO_API_TOKEN}"
        }

        timeout = aiohttp.ClientTimeout(total=10)  # 总超时10秒
        async with session.get(url, headers=headers, timeout=timeout) as response:
            if response.status != 200:
                return {
                    "status": "failed",
                    "video_url": "",
                    "error_message": f"请求失败: {response.status}",
                    "progress": 0,
                    "cdn_uploaded": False,
                    "price": 0.0
                }

            result = await response.json()

            status = result.get('status', 'pending')
            video_url = result.get('video_url', '') or ''
            error_message = result.get('error_message', '') or ''
            progress = result.get('progress', 0)
            cdn_uploaded = result.get('cdn_uploaded', False)
            price = float(result.get('price') or 0.0)  # 处理None值

            return {
                "status": status,
                "video_url": video_url,
                "error_message": error_message,
                "progress": progress,
                "cdn_uploaded": cdn_uploaded,
                "price": price
            }

    except asyncio.TimeoutError:
        return {
            "status": "failed",
            "video_url": "",
            "error_message": "请求超时",
            "progress": 0,
            "cdn_uploaded": False,
            "price": 0.0
        }
    except Exception as e:
        return {
            "status": "failed",
            "video_url": "",
            "error_message": f"查询异常: {str(e)}",
            "progress": 0,
            "cdn_uploaded": False,
            "price": 0.0
        }


def sync_shot_status(db: Session, shot: models.StoryboardShot, api_result: Dict, verbose: bool = True) -> Tuple[bool, str]:
    """
    同步单个镜头的状态

    Args:
        db: 数据库会话
        shot: 镜头对象
        api_result: API 查询结果
        verbose: 是否打印详细信息

    Returns:
        (is_updated, message): 是否更新成功，状态消息
    """
    if not shot.task_id:
        return False, "无 task_id"

    api_status = api_result.get('status', '')
    video_url = api_result.get('video_url', '')
    cdn_uploaded = api_result.get('cdn_uploaded', False)
    error_message = api_result.get('error_message', '')
    price = api_result.get('price') or 0.0  # 获取价格，处理None值

    if verbose:
        print(f"  API 返回: status={api_status}, cdn_uploaded={cdn_uploaded}, price={price}")

    updated = False
    message = ""

    # 如果 API 返回成功
    if api_status == 'completed':
        if not video_url:
            return False, f"API 返回 completed 但没有 video_url"

        previous_status = shot.video_status
        previous_video_path = shot.video_path
        previous_thumbnail = shot.thumbnail_video_path

        # 更新视频状态为完成
        if shot.video_status != 'completed':
            shot.video_status = 'completed'
            shot.video_path = video_url
            if not previous_thumbnail or previous_thumbnail == previous_video_path:
                shot.thumbnail_video_path = video_url

            # 保存价格（单位：分，如80表示0.8元）
            shot.price = int(price * 100)

            # 添加到视频记录表（如果不存在）
            existing_video = db.query(models.ShotVideo).filter(
                models.ShotVideo.shot_id == shot.id,
                models.ShotVideo.video_path == video_url
            ).first()

            if not existing_video:
                new_video = models.ShotVideo(
                    shot_id=shot.id,
                    video_path=video_url
                )
                db.add(new_video)

            updated = True
            message = f"状态更新: {previous_status} -> completed"

        # 更新 CDN 上传状态
        if cdn_uploaded and not shot.cdn_uploaded:
            shot.cdn_uploaded = True
            shot.video_path = video_url  # 更新为 CDN URL
            if not previous_thumbnail or previous_thumbnail == previous_video_path:
                shot.thumbnail_video_path = video_url
            updated = True
            message = f"{message}, CDN 已上传" if message else "CDN 状态已更新"

        if not updated:
            message = "已是最新状态"

    # 如果 API 返回失败
    elif api_status in ['failed', 'cancelled']:
        if shot.video_status != 'failed':
            shot.video_status = 'failed'
            shot.video_path = f"error:{error_message}"
            updated = True
            message = f"标记为失败: {error_message}"
        else:
            message = "已标记为失败"

    # 如果还在处理中
    elif api_status in ['submitted', 'pending', 'processing']:
        previous_status = shot.video_status
        if shot.video_status != 'processing':
            shot.video_status = 'processing'
            # 记录提交时间（如果还没记录，或者从失败状态恢复）
            if not shot.video_submitted_at or previous_status == 'failed':
                shot.video_submitted_at = datetime.utcnow()
                message = f"状态更新为 processing（重置超时计时器）" if previous_status == 'failed' else f"状态更新为 processing"
            else:
                message = f"状态更新为 processing"
            updated = True
        else:
            message = "仍在处理中"

    else:
        message = f"未知状态: {api_status}"

    if updated:
        try:
            db.commit()
            return True, message
        except Exception as e:
            db.rollback()
            return False, f"数据库更新失败: {str(e)}"

    return False, message


async def batch_sync_all_shots(skip_completed: bool = True, verbose: bool = True, max_concurrent: int = 10, delay: float = 1.0):
    """
    批量同步所有镜头的状态（并发查询）

    Args:
        skip_completed: 是否跳过已完成且 CDN 已上传的镜头
        verbose: 是否打印详细信息
        max_concurrent: 最大并发数量（默认10）
        delay: 每个请求之间的延迟秒数（默认1秒）
    """
    db = SessionLocal()
    try:
        # 第一步：从数据库收集所有需要同步的镜头信息
        query = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.task_id != '',
            models.StoryboardShot.task_id.isnot(None)
        )

        if skip_completed:
            # 跳过已完成且 CDN 已上传的镜头
            query = query.filter(
                (models.StoryboardShot.video_status != 'completed') |
                (models.StoryboardShot.cdn_uploaded == False)
            )

        shots = query.all()

        if not shots:
            print("没有需要同步的镜头")
            return

        # 收集所有镜头的基本信息（避免在 HTTP 请求期间持有数据库连接）
        shot_info_list = []
        for shot in shots:
            episode = db.query(models.Episode).filter(
                models.Episode.id == shot.episode_id
            ).first()

            username = "未知用户"
            if episode:
                script = db.query(models.Script).filter(
                    models.Script.id == episode.script_id
                ).first()
                if script:
                    user = db.query(models.User).filter(
                        models.User.id == script.user_id
                    ).first()
                    if user:
                        username = user.username

            shot_info_list.append({
                'shot_id': shot.id,
                'task_id': shot.task_id,
                'video_status': shot.video_status,
                'cdn_uploaded': shot.cdn_uploaded,
                'username': username
            })

        # 关闭初始数据库连接
        db.close()

        print(f"找到 {len(shot_info_list)} 个需要检查的镜头")
        print(f"最大并发数: {max_concurrent}")
        print(f"每个请求延迟: {delay}s")
        print(f"HTTP 请求超时: 10s")
        print("-" * 80)

        # 统计信息
        stats = {
            'total': len(shot_info_list),
            'updated': 0,
            'failed': 0,
            'skipped': 0,
            'completed_count': 0,
            'processing_count': 0,
            'failed_count': 0,
            'processed': 0  # 已处理的任务数
        }

        # 创建信号量限制并发数
        semaphore = asyncio.Semaphore(max_concurrent)
        # 创建锁保护统计信息
        stats_lock = asyncio.Lock()

        # 创建单个共享的 HTTP 会话（提高性能）
        connector = aiohttp.TCPConnector(limit=max_concurrent)
        http_session = aiohttp.ClientSession(connector=connector)

        async def fetch_and_update(shot_info: dict, index: int):
            """获取单个镜头状态并更新"""
            async with semaphore:
                shot_id = shot_info['shot_id']
                task_id = shot_info['task_id']
                username = shot_info['username']

                if verbose:
                    print(f"\n[{index}/{len(shot_info_list)}] 镜头 ID: {shot_id}, 用户: {username}")
                    print(f"  当前状态: video_status={shot_info['video_status']}, cdn_uploaded={shot_info['cdn_uploaded']}")
                    print(f"  Task ID: {task_id}")

                start_time = asyncio.get_event_loop().time()

                try:
                    # 第二步：异步查询 API（不持有数据库连接）
                    api_result = await check_video_status_async(task_id, http_session)

                    elapsed = asyncio.get_event_loop().time() - start_time
                    if verbose:
                        print(f"  API 响应时间: {elapsed:.2f}s")

                    # 第三步：使用独立的数据库会话更新（快速操作）
                    task_db = SessionLocal()
                    try:
                        shot = task_db.query(models.StoryboardShot).filter(
                            models.StoryboardShot.id == shot_id
                        ).first()

                        if not shot:
                            return False, "镜头不存在"

                        # 同步数据库状态
                        is_updated, message = sync_shot_status(task_db, shot, api_result, verbose=verbose)

                        # 使用锁更新统计信息
                        async with stats_lock:
                            stats['processed'] += 1
                            if is_updated:
                                stats['updated'] += 1
                                if verbose:
                                    print(f"  ✓ {message} [进度: {stats['processed']}/{stats['total']}]")
                            else:
                                if "失败" in message or "错误" in message:
                                    stats['failed'] += 1
                                else:
                                    stats['skipped'] += 1
                                if verbose:
                                    print(f"  - {message} [进度: {stats['processed']}/{stats['total']}]")

                            # 更新统计
                            if shot.video_status == 'completed':
                                stats['completed_count'] += 1
                            elif shot.video_status == 'processing':
                                stats['processing_count'] += 1
                            elif shot.video_status == 'failed':
                                stats['failed_count'] += 1

                        return is_updated, message

                    finally:
                        task_db.close()

                except Exception as e:
                    # 捕获任何错误，避免单个任务失败影响整体
                    error_msg = f"处理失败: {str(e)}"
                    if verbose:
                        elapsed = asyncio.get_event_loop().time() - start_time
                        print(f"  ✗ {error_msg} (耗时: {elapsed:.2f}s)")
                    async with stats_lock:
                        stats['processed'] += 1
                        stats['failed'] += 1
                        if verbose:
                            print(f"  [进度: {stats['processed']}/{stats['total']}]")
                    return False, error_msg

                finally:
                    # 任务完成后延迟（避免请求过快）
                    if delay > 0:
                        await asyncio.sleep(delay)

        try:
            # 并发执行所有查询
            tasks = [fetch_and_update(shot_info, i + 1) for i, shot_info in enumerate(shot_info_list)]
            await asyncio.gather(*tasks)
        finally:
            # 关闭共享的 HTTP 会话
            await http_session.close()

        # 打印统计信息
        print("\n" + "=" * 80)
        print("同步完成！")
        print(f"总数: {stats['total']}")
        print(f"已更新: {stats['updated']}")
        print(f"已跳过: {stats['skipped']}")
        print(f"失败: {stats['failed']}")
        print("\n当前状态分布:")
        print(f"  - 已完成: {stats['completed_count']}")
        print(f"  - 处理中: {stats['processing_count']}")
        print(f"  - 失败: {stats['failed_count']}")
        print("=" * 80)

    except Exception as e:
        print(f"\n批量同步失败: {str(e)}")
        import traceback
        traceback.print_exc()


async def sync_single_shot(shot_id: int):
    """
    同步单个镜头的状态

    Args:
        shot_id: 镜头 ID
    """
    db = SessionLocal()
    try:
        shot = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.id == shot_id
        ).first()

        if not shot:
            print(f"镜头 {shot_id} 不存在")
            return

        print(f"镜头 ID: {shot.id}")
        print(f"当前状态: video_status={shot.video_status}, cdn_uploaded={shot.cdn_uploaded}")
        print(f"Task ID: {shot.task_id}")
        print("-" * 80)

        # 异步查询 API
        async with aiohttp.ClientSession() as session:
            api_result = await check_video_status_async(shot.task_id, session)

        is_updated, message = sync_shot_status(db, shot, api_result, verbose=True)

        if is_updated:
            print(f"✓ {message}")
        else:
            print(f"- {message}")

    except Exception as e:
        print(f"同步失败: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    # 检查命令行参数
    if len(sys.argv) > 1:
        if sys.argv[1] == "--all":
            # 同步所有镜头（包括已完成的）
            print("同步所有镜头（包括已完成的）...\n")
            asyncio.run(batch_sync_all_shots(skip_completed=False, verbose=True, max_concurrent=10, delay=1.0))
        elif sys.argv[1] == "--shot-id":
            # 同步单个镜头
            if len(sys.argv) < 3:
                print("用法: python batch_sync_video_status.py --shot-id <shot_id>")
                sys.exit(1)
            shot_id = int(sys.argv[2])
            asyncio.run(sync_single_shot(shot_id))
        elif sys.argv[1] == "--concurrent":
            # 自定义并发数
            if len(sys.argv) < 3:
                print("用法: python batch_sync_video_status.py --concurrent <并发数> [延迟秒数]")
                sys.exit(1)
            max_concurrent = int(sys.argv[2])
            delay = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
            print(f"同步所有未完成的镜头（并发数: {max_concurrent}, 延迟: {delay}s）...\n")
            asyncio.run(batch_sync_all_shots(skip_completed=True, verbose=True, max_concurrent=max_concurrent, delay=delay))
        elif sys.argv[1] == "--help":
            print("用法:")
            print("  python batch_sync_video_status.py                        # 同步所有未完成的镜头（默认并发数10，延迟1s）")
            print("  python batch_sync_video_status.py --all                  # 同步所有镜头（包括已完成的）")
            print("  python batch_sync_video_status.py --shot-id <id>        # 同步单个镜头")
            print("  python batch_sync_video_status.py --concurrent <N> [D]  # 自定义并发数N和延迟D秒")
            print("\n示例:")
            print("  python batch_sync_video_status.py --concurrent 20 0.5   # 并发20，每个任务延迟0.5秒")
            sys.exit(0)
        else:
            print(f"未知参数: {sys.argv[1]}")
            print("使用 --help 查看帮助")
            sys.exit(1)
    else:
        # 默认：只同步未完成的镜头，并发10，延迟1秒
        print("同步所有未完成的镜头（并发数: 10, 延迟: 1s）...\n")
        asyncio.run(batch_sync_all_shots(skip_completed=True, verbose=True, max_concurrent=10, delay=1.0))
