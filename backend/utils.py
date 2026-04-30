import requests
import json
import os


def upload_to_cdn(file_path):
    """
    上传文件到CDN

    Args:
        file_path: 本地文件路径

    Returns:
        str: CDN URL
    """
    API = 'https://api.upload.moapp.net.cn/cfs/file'

    try:
        with open(file_path, 'rb') as f:
            response = requests.post(
                API,
                files={
                    'file': f
                },
                data={
                    'path': 'temp/demo'
                }
            )

        result = response.json()
        print(f"CDN上传响应: {result}")

        cdn_url = result['data']['url']
        print(f"CDN上传成功: {file_path} -> {cdn_url}")

        return cdn_url

    except Exception as e:
        raise Exception(f"CDN上传失败: {str(e)}")
