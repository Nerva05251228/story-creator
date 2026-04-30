"""
同步ShotVideo表中的CDN URL

问题：旧版本在CDN上传完成时只更新了StoryboardShot.video_path，没有更新ShotVideo.video_path
导致：复制剧本时，ShotVideo表中复制的还是上游API的URL，而不是CDN URL

这个脚本会将所有已上传CDN的视频URL同步到ShotVideo表
执行方式：python migrations/sync_shot_video_cdn_urls.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
import models

def sync_shot_video_cdn_urls():
    db = SessionLocal()
    try:
        # 查询所有CDN已上传的镜头
        cdn_uploaded_shots = db.query(models.StoryboardShot).filter(
            models.StoryboardShot.cdn_uploaded == True,
            models.StoryboardShot.video_path != "",
            models.StoryboardShot.video_path.isnot(None)
        ).all()

        count = len(cdn_uploaded_shots)
        print(f"找到 {count} 个CDN已上传的镜头")

        if count == 0:
            print("无需同步")
            return

        updated_count = 0
        for shot in cdn_uploaded_shots:
            try:
                # 查找该镜头对应的ShotVideo记录（按创建时间倒序，取最新的）
                shot_video = db.query(models.ShotVideo).filter(
                    models.ShotVideo.shot_id == shot.id
                ).order_by(models.ShotVideo.created_at.desc()).first()

                if not shot_video:
                    print(f"  - 镜头 {shot.id} (镜号 {shot.shot_number}) 没有ShotVideo记录，跳过")
                    continue

                # 检查是否需要更新
                if shot_video.video_path == shot.video_path:
                    print(f"  - 镜头 {shot.id} (镜号 {shot.shot_number}) 的ShotVideo已是CDN URL，跳过")
                    continue

                # 更新ShotVideo的video_path为CDN URL
                old_url = shot_video.video_path
                shot_video.video_path = shot.video_path
                updated_count += 1

                print(f"  ✓ 镜头 {shot.id} (镜号 {shot.shot_number})")
                print(f"    旧URL: {old_url[:60]}...")
                print(f"    新URL: {shot.video_path[:60]}...")

            except Exception as e:
                print(f"  ✗ 处理镜头 {shot.id} 失败: {str(e)}")
                continue

        db.commit()
        print(f"\n成功同步 {updated_count}/{count} 个ShotVideo记录的CDN URL")
        print("现在复制剧本时，视频URL将正确显示为CDN链接")

    except Exception as e:
        print(f"同步失败: {str(e)}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    print("=" * 80)
    print("同步ShotVideo表中的CDN URL")
    print("=" * 80)
    print("\n说明：")
    print("- 此脚本会将所有已上传CDN的视频URL同步到ShotVideo表")
    print("- 只会更新需要更新的记录，已经是CDN URL的记录会跳过")
    print("- 此操作是安全的，不会删除任何数据\n")

    confirm = input("确认要同步ShotVideo的CDN URL吗？(y/n): ")
    if confirm.lower() == 'y':
        sync_shot_video_cdn_urls()
    else:
        print("已取消")
