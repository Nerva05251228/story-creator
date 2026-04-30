#!/usr/bin/env python3
"""
场景图片生成脚本
使用即梦API生成场景图片
"""

import requests
import time
import json
import sys

API_BASE_URL = "https://api.apimart.ai/v1"
API_TOKEN = "sk-D46oekqNC1lEiXp3ZcN5Yehww9EKA1CW0Q6k8vnuyyxQWws0"


def submit_image_generation(prompt, size="1:1", reference_images=None):
    """提交图片生成任务"""
    url = f"{API_BASE_URL}/images/generations"

    payload = {
        "model": "jimeng-4.5",
        "prompt": prompt,
        "size": size,
        "n": 4  # 生成4张图片
    }

    # 添加参考图
    if reference_images:
        payload["image_urls"] = reference_images

    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }

    response = requests.post(url, json=payload, headers=headers, timeout=120)

    if response.status_code != 200:
        raise Exception(f"提交任务失败: {response.status_code} - {response.text}")

    result = response.json()

    if result.get("code") != 200:
        raise Exception(f"API返回错误: {result.get('msg', '未知错误')}")

    task_id = result["data"][0]["task_id"]
    return task_id


def check_task_status(task_id):
    """检查任务状态"""
    url = f"{API_BASE_URL}/tasks/{task_id}"

    headers = {
        "Authorization": f"Bearer {API_TOKEN}"
    }

    params = {
        "language": "zh"
    }

    response = requests.get(url, headers=headers, params=params, timeout=60)

    if response.status_code != 200:
        raise Exception(f"查询任务失败: {response.status_code}")

    result = response.json()

    if result.get("code") != 200:
        raise Exception(f"API返回错误: {result.get('msg', '未知错误')}")

    data = result["data"]
    status = data["status"]
    progress = data.get("progress", 0)

    response_data = {
        "status": status,
        "progress": progress
    }

    if status == "completed":
        images = []
        result_data = data.get("result", {})
        print(f"DEBUG: result_data = {json.dumps(result_data, ensure_ascii=False)}", file=sys.stderr)

        for img in result_data.get("images", []):
            urls = img.get("url", [])
            print(f"DEBUG: urls type = {type(urls)}, value = {urls}", file=sys.stderr)

            if isinstance(urls, list):
                images.extend(urls)
            elif isinstance(urls, str):
                # 如果是逗号分隔的字符串，拆分成数组
                images.extend([u.strip() for u in urls.split(",") if u.strip()])

        print(f"DEBUG: final images = {images}", file=sys.stderr)
        response_data["images"] = images

    return response_data


def wait_for_completion(task_id, max_wait_time=300):
    """等待任务完成"""
    start_time = time.time()

    while True:
        if time.time() - start_time > max_wait_time:
            raise Exception(f"任务超时（超过{max_wait_time}秒）")

        result = check_task_status(task_id)
        status = result["status"]
        progress = result.get("progress", 0)

        print(f"任务状态: {status}, 进度: {progress}%", file=sys.stderr)

        if status == "completed":
            return result["images"]
        elif status == "failed":
            raise Exception("图片生成失败")

        time.sleep(5)


def main():
    """主函数"""
    # 从命令行参数获取输入
    if len(sys.argv) < 4:
        print("用法: python generate_scene_images.py <stable_id> <prompt> <size> [reference_images]", file=sys.stderr)
        sys.exit(1)

    stable_id = sys.argv[1]
    prompt = sys.argv[2]
    size = sys.argv[3]

    reference_images = []
    if len(sys.argv) >= 5:
        # 参考图URL列表，用逗号分隔
        ref_str = sys.argv[4]
        if ref_str:
            reference_images = [url.strip() for url in ref_str.split(",")]

    try:
        # 提交任务
        print(f"提交图片生成任务...", file=sys.stderr)
        task_id = submit_image_generation(prompt, size, reference_images)
        print(f"任务ID: {task_id}", file=sys.stderr)

        # 等待完成
        print(f"等待生成完成...", file=sys.stderr)
        images = wait_for_completion(task_id)

        # 构建结果
        result = {
            "stable_id": stable_id,
            "optimized_prompt": prompt,
            "images": images
        }

        # 输出到stdout（供调用方解析）
        print(json.dumps(result, ensure_ascii=False))

        # 保存到文件
        output_file = f"{stable_id}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"结果已保存到: {output_file}", file=sys.stderr)

    except Exception as e:
        error_result = {
            "stable_id": stable_id,
            "optimized_prompt": prompt,
            "images": [],
            "error": str(e)
        }
        print(json.dumps(error_result, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
