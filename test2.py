#!/usr/bin/env python3

import json
import sys

import requests


API_URL = "https://ne.mocatter.cn/api_sora/v2/tasks"
HEADERS = {
    "Authorization": "Bearer sk-Zv2THcS1J7KDZkQ-griUI6UlRSNcgQhvTXu70tuvRBw",
    "Content-Type": "application/json",
}

PAYLOAD = {
    "username": "yu是你",
    "model": "doubao-seedance-2-0-260128-fast",
    "typography": "全能参考",
    "content": [
        {
            "type": "text",
            "text": (
                "桑桑[图片1]吴大夫[图片2]五金[图片3]"
                "| [镜头1] | [近景/缓慢推镜头] 白天室外，光线明亮。桑桑独自站在画面左侧，目光紧紧看着画外右侧玩得十分投入的众人，发现自己完全被忘记了。"
                "她睁大两只眼睛，小手在身侧不由自主地握成小拳头，身体由于情绪起伏而微前倾，眼神中透着被冷落的急躁。 | (SFX:远处众人欢乐的嬉闹声) | "
                "| [镜头2] | [大特写/固定镜头] 桑桑面部大特写。她脑袋微微向上扬起，小嘴明显地高高撅起，两道眉头瞬间紧紧皱在一起形成一个小疙瘩，双颊也因委屈生气而微微鼓起来。"
                "她猛地深吸一口气，胸膛起伏，朝着前方大喊出声，发丝随着动作微微甩动。 | (SFX:深呼吸声)、[桑桑]生气高昂的说：“师虎！五金！” | "
                "| [镜头3] | [全景/快速平移运镜] 画面右侧正在玩耍的五金听见呼唤声骤然停下动作，两只耳朵警觉地竖起。紧接着，它的后腿猛地向地上一蹬，身形犹如离弦之箭般弹射起步，向着画面左侧凌空跃去，"
                "在半空中划出利落而机敏的弧线。 | (SFX:嗖的一声灵动起跳的破空声) | "
                "| [镜头4] | [近景推特写/平移镜头转固定] 镜头跟随五金的落点，它精准地落在了桑桑的右边肩膀上，四爪轻触肩膀瞬间借力缓冲，随后迅速顺从地收拢四肢，规规矩矩地稳稳蹲伏好。"
                "桑桑感受到肩上的动静，紧皱着的脸部神情终于稍微舒展，微微偏头看向右肩上的五金。 | (SFX:扑哒一声轻柔落地的声音) |"
            ),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": "https://alicdn.mopic.mozigu.net/apps/uniapp_base/file/cfs-proxy/temp/demo/be3df42b71424dd18d8cdc39b2a98f43.jpg"
            },
            "role": "reference_image",
        },
        {
            "type": "image_url",
            "image_url": {
                "url": "https://alicdn.mopic.mozigu.net/apps/uniapp_base/file/cfs-proxy/temp/demo/c239650700584f05bc2e706dd85fb805.jfif"
            },
            "role": "reference_image",
        },
        {
            "type": "image_url",
            "image_url": {
                "url": "https://alicdn.mopic.mozigu.net/apps/uniapp_base/file/cfs-proxy/temp/demo/3cb8a12b521b410a95f1051102501c4e.jpg"
            },
            "role": "reference_image",
        },
    ],
    "ratio": "16:9",
    "duration": 6,
    "watermark": False,
}


def main() -> int:
    try:
        response = requests.post(API_URL, headers=HEADERS, json=PAYLOAD, timeout=120)
    except requests.RequestException as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1

    print(f"HTTP {response.status_code}")

    try:
        data = response.json()
    except Exception:
        print(response.text)
        return 1

    print(json.dumps(data, ensure_ascii=False, indent=2))

    task_id = str(data.get("task_id") or data.get("taskId") or "").strip()
    if task_id:
        print(f"task_id={task_id}")
        return 0

    print("task_id not found", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
