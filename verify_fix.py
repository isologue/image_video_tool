#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证 server.py 的解析逻辑是否正确
"""

import re
import requests
import io
from PIL import Image

# 模拟 API 返回的响应文本（图生图的返回格式）
test_text = '\n![Image](https://pro.filesystem.site/cdn/20260414/f501b450-8b55-4e09-bc05-65e499ea659b.png)\n'

print("=" * 60)
print("验证图生图片解析逻辑")
print("=" * 60)
print(f"\n测试文本：{test_text.strip()}")

# 方式 1: base64
md_pattern = r'!\[.*?\]\(data:(image/[a-z]+);base64,([A-Za-z0-9+/=]+)\)'
match = re.search(md_pattern, test_text)
if match:
    print("\n[OK] 匹配到 base64 格式")
else:
    print("\n[INFO] 未匹配 base64 格式")

# 方式 2: 外部 URL
url_pattern = r'!\[.*?\]\((https?://[^\s\)]+\.(png|jpg|jpeg|webp|gif))\)'
url_match = re.search(url_pattern, test_text, re.IGNORECASE)
if url_match:
    image_url = url_match.group(1)
    print(f"[OK] 匹配到外部 URL: {image_url}")
    
    # 尝试下载
    try:
        img_response = requests.get(image_url, timeout=30)
        img_response.raise_for_status()
        image = Image.open(io.BytesIO(img_response.content))
        print(f"[OK] 图片下载成功，尺寸：{image.size}")
        image.save("verify_output.png")
        print("[OK] 已保存到 verify_output.png")
    except Exception as e:
        print(f"[ERROR] 下载失败：{e}")
else:
    print("[ERROR] 未匹配到外部 URL")

print("\n" + "=" * 60)
print("验证完成")
print("=" * 60)
