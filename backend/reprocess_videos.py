#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
重新处理已完成但还是远程URL的视频
下载并上传到自己的CDN
"""

import sqlite3
from video_service import download_and_upload_video

def reprocess_all_sora_videos():
    """重新处理所有Sora URL的视频"""
    conn = sqlite3.connect('story_creator.db')
    cursor = conn.cursor()

    # 查找所有已完成且video_path是远程URL的镜头
    cursor.execute('''
        SELECT id, video_path
        FROM storyboard_shots
        WHERE video_status = 'completed'
        AND video_path LIKE 'https://openpt%'
    ''')

    shots = cursor.fetchall()

    if not shots:
        print("没有需要重新处理的视频")
        conn.close()
        return

    print(f"找到 {len(shots)} 个需要重新处理的视频")

    for shot_id, video_path in shots:
        print(f"\n处理镜头 {shot_id}...")
        print(f"  原始URL: {video_path}")

        try:
            # 下载并上传到CDN
            cdn_url = download_and_upload_video(video_path, shot_id)
            print(f"  CDN URL: {cdn_url}")

            # 更新数据库
            cursor.execute('''
                UPDATE storyboard_shots
                SET video_path = ?
                WHERE id = ?
            ''', (cdn_url, shot_id))

            conn.commit()
            print(f"  ✓ 镜头 {shot_id} 处理成功")

        except Exception as e:
            print(f"  ✗ 镜头 {shot_id} 处理失败: {str(e)}")
            conn.rollback()

    conn.close()
    print("\n处理完成！")

if __name__ == '__main__':
    reprocess_all_sora_videos()
