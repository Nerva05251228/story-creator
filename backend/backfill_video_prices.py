"""
回填已完成视频的价格
"""
import asyncio
import aiohttp
import sqlite3
from video_api_config import get_video_task_status_url, VIDEO_API_TOKEN


async def backfill_prices():
    """回填所有已完成但price为0的镜头的价格"""
    db_path = "story_creator.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # 查询所有已完成但price为0的镜头
        cursor.execute("""
            SELECT id, task_id
            FROM storyboard_shots
            WHERE video_status = 'completed'
            AND task_id IS NOT NULL
            AND task_id != ''
            AND price = 0
        """)

        shots = cursor.fetchall()

        if not shots:
            print("没有需要回填价格的镜头")
            return

        print(f"找到 {len(shots)} 个需要回填价格的镜头")

        # 创建HTTP会话
        connector = aiohttp.TCPConnector(limit=10)
        session = aiohttp.ClientSession(connector=connector)

        updated_count = 0
        failed_count = 0

        try:
            for i, (shot_id, task_id) in enumerate(shots, 1):
                try:
                    print(f"[{i}/{len(shots)}] 查询镜头 {shot_id} (任务ID: {task_id})")

                    # 查询API
                    url = get_video_task_status_url(task_id)
                    headers = {
                        "Authorization": f"Bearer {VIDEO_API_TOKEN}"
                    }

                    timeout = aiohttp.ClientTimeout(total=10)
                    async with session.get(url, headers=headers, timeout=timeout, ssl=False) as response:
                        if response.status == 200:
                            result = await response.json()
                            price = result.get('price')

                            if price is not None:
                                # 更新价格（单位：分）
                                price_cents = int(float(price) * 100)
                                cursor.execute(
                                    "UPDATE storyboard_shots SET price = ? WHERE id = ?",
                                    (price_cents, shot_id)
                                )
                                conn.commit()
                                updated_count += 1
                                print(f"  ✓ 更新成功: price={price}元 ({price_cents}分)")
                            else:
                                print(f"  - 跳过: API返回price=null")
                        else:
                            print(f"  ✗ API请求失败: {response.status}")
                            failed_count += 1

                    # 延迟避免请求过快
                    await asyncio.sleep(0.5)

                except Exception as e:
                    print(f"  ✗ 处理失败: {str(e)}")
                    failed_count += 1

        finally:
            await session.close()

        print("\n" + "=" * 60)
        print("回填完成！")
        print(f"总数: {len(shots)}")
        print(f"成功更新: {updated_count}")
        print(f"失败: {failed_count}")
        print("=" * 60)

    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(backfill_prices())
