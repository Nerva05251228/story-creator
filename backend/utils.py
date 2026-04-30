import requests
import json
import os

from env_config import get_env, load_app_env, require_env


load_app_env()


def upload_to_cdn(file_path):
    """
    上传文件到CDN

    Args:
        file_path: 本地文件路径

    Returns:
        str: CDN URL
    """
    api_url = require_env("CDN_UPLOAD_URL")
    upload_path = get_env("CDN_UPLOAD_PATH", "temp/demo")

    try:
        with open(file_path, 'rb') as f:
            response = requests.post(
                api_url,
                files={
                    'file': f
                },
                data={
                    'path': upload_path
                }
            )

        result = response.json()
        print(f"CDN上传响应: {result}")

        cdn_url = result['data']['url']
        print(f"CDN上传成功: {file_path} -> {cdn_url}")

        return cdn_url

    except Exception as e:
        raise Exception(f"CDN上传失败: {str(e)}")
