#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
格式化输出脚本
从文件读取原文，结合其他参数生成标准JSON输出
"""

import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(description='Format output for scene image generation')
    parser.add_argument('--stable-id', required=True, help='Stable ID of the scene')
    parser.add_argument('--original-text-file', required=True, help='Path to original text file')
    parser.add_argument('--optimized-prompt', required=True, help='Optimized prompt for image generation')
    parser.add_argument('--mcp-result', required=True, help='MCP result JSON string')
    parser.add_argument('--debug', action='store_true', help='Enable debug output to stderr')

    args = parser.parse_args()

    # Debug output to stderr
    if args.debug:
        print(f"[DEBUG] Reading original text from: {args.original_text_file}", file=sys.stderr)

    # Read original text from file
    try:
        with open(args.original_text_file, 'r', encoding='utf-8') as f:
            original_text = f.read().strip()
    except Exception as e:
        print(f"[ERROR] Failed to read original text file: {e}", file=sys.stderr)
        original_text = ""

    # Parse MCP result
    try:
        mcp_result = json.loads(args.mcp_result)
        images = mcp_result.get('images', [])
    except Exception as e:
        print(f"[ERROR] Failed to parse MCP result: {e}", file=sys.stderr)
        images = []

    # Build output JSON
    output = {
        "stable_id": args.stable_id,
        "original_text": original_text,
        "optimized_prompt": args.optimized_prompt,
        "images": images
    }

    # Output JSON to stdout
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
