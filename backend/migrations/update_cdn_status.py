"""
更新已有视频的CDN上传状态
对所有 video_status='completed' 的镜头重新检查CDN状态
执行方式：python migrations/update_cdn_status.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
import models
from video_service import check_video_status

def update_cdn_status():
    db = SessionLocal()
    try:
        # 查询所有已完成但CDN未上传的镜头
        completed_shots = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.video_status == "completed",
            models.StoryboardShot.cdn_uploaded == False,
            models.StoryboardShot.task_id != ""
        ).all()

        count = len(completed_shots)
        print(f"���到 {count} 个已完成但CDN未上传的镜头")

        if count == 0:
            print("无需更新")
            return

        updated_count = 0
        for shot in completed_shots:
            try:
                print(f"\n检查镜头 {shot.id} (镜号 {shot.shot_number}, task_id: {shot.task_id})")

                # 调用API检查状态
                result = check_video_status(shot.task_id)
                status = result.get('status', '')
                cdn_uploaded = result.get('cdn_uploaded', False)
                video_url = result.get('video_url', '')

                if status == 'completed' and cdn_uploaded:
                    # 更新CDN状态和URL
                    shot.cdn_uploaded = True
                    if video_url:
                        shot.video_path = video_url
                        shot.thumbnail_video_path = video_url
                    updated_count += 1
                    print(f"  ✓ CDN已上传，更新URL: {video_url[:50]}...")
                elif status == 'completed' and not cdn_uploaded:
                    print(f"  - 视频已完成，但CDN尚未上传")
                else:
                    print(f"  - 状态: {status}")

            except Exception as e:
                print(f"  ✗ 检查失败: {str(e)}")
                continue

        db.commit()
        print(f"\n成功更新 {updated_count}/{count} 个镜头的CDN状态")

    except Exception as e:
        print(f"更新失败: {str(e)}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    print("=" * 80)
    print("更新已有视频的CDN上传状态")
    print("=" * 80)

    confirm = input("确认要检查并更新所有已完成视频的CDN状态吗？(y/n): ")
    if confirm.lower() == 'y':
        update_cdn_status()
    else:
        print("已取消")
