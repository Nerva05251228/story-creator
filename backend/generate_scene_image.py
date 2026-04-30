#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
场景图片生成脚本
接收场景描述信息，调用Claude Code生成AI图片
Claude Code会通过skill自动生成 {stable_id}.json 文件
"""

import subprocess
import sys


def generate_scene_image(input_text: str):
    """
    生成单个场景的AI图片

    Args:
        input_text: 完整的输入文本，包含stable_id、原文文件路径、参考图等信息

    说明:
        此函数只负责调用Claude Code CLI
        实际的JSON文件生成由skill内部的format_output.py脚本完成
        生成的文件名为: {stable_id}.json
    """

    # 调用Claude Code CLI
    try:
        import locale
        system_encoding = locale.getpreferredencoding()

        result = subprocess.run(
            "claude -p --dangerously-skip-permissions --output-format text --no-session-persistence",
            input=input_text.encode(system_encoding),
            capture_output=True,
            timeout=600,  # 10分钟超时
            shell=True
        )

        # 解码输出（用于调试和保存）
        try:
            stdout = result.stdout.decode(system_encoding)
            stderr = result.stderr.decode(system_encoding)
        except UnicodeDecodeError:
            stdout = result.stdout.decode('utf-8', errors='ignore')
            stderr = result.stderr.decode('utf-8', errors='ignore')

        # 从input_text中提取stable_id
        stable_id = None
        for line in input_text.split('\n'):
            if line.startswith('stable_id:'):
                stable_id = line.split(':', 1)[1].strip()
                break

        # 保存Claude Code的完整输出（包含stdout和stderr）
        if stable_id:
            import os
            debug_file = f"claude_output_{stable_id}.txt"
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write("=== RETURN CODE ===\n")
                f.write(f"{result.returncode}\n\n")
                f.write("=== STDOUT ===\n")
                f.write(stdout)
                f.write("\n\n=== STDERR ===\n")
                f.write(stderr)
            print(f"[调试] Claude Code输出已保存到: {debug_file}", file=sys.stderr)

        if result.returncode != 0:
            print(f"错误：Claude Code执行失败", file=sys.stderr)
            print(stderr, file=sys.stderr)
            sys.exit(1)

        # 输出Claude Code的返回信息（用于调试）
        if stdout:
            print(stdout, file=sys.stderr)

    except subprocess.TimeoutExpired:
        print("错误：执行超时（超过10分钟）", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误：{e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


def main():
    """主函数"""
    if len(sys.argv) != 2:
        print("用法: python generate_scene_image.py <输入内容>", file=sys.stderr)
        print("", file=sys.stderr)
        print("示例（推荐使用文件方式）:", file=sys.stderr)
        print('  python generate_scene_image.py "stable_id: scene_001', file=sys.stderr)
        print('  原文文件: /path/to/scene.txt', file=sys.stderr)
        print('  输出目录: /path/to/output', file=sys.stderr)
        print('  参考图: https://example.com/ref.jpg', file=sys.stderr)
        print('  比例: 9:16', file=sys.stderr)
        print('  参考风格程度: 50', file=sys.stderr)
        print('  风格指令: 二次元风格"', file=sys.stderr)
        print("", file=sys.stderr)
        print("或直接传递原文:", file=sys.stderr)
        print('  python generate_scene_image.py "stable_id: scene_001', file=sys.stderr)
        print('  原文: | 00s-04s | [镜头1] ... | 台词 |', file=sys.stderr)
        print('  输出目录: /path/to/output', file=sys.stderr)
        print('  风格指令: 直接使用原文"', file=sys.stderr)
        sys.exit(1)

    input_text = sys.argv[1]
    generate_scene_image(input_text)


if __name__ == "__main__":
    main()
