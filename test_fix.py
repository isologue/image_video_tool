#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试修复后的图片解析逻辑
"""

import re
import requests
import io
from PIL import Image

# 模拟 API 返回的文本
test_cases = [
    # 外部 URL 格式
    '\n![Image](https://pro.filesystem.site/cdn/20260414/9e568b19-0e4b-4928-b4ef-21232e3c0a29.png)\n',
    # base64 格式
    '![image](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==)',
]

def test_url_parsing(text):
    print(f"测试文本：{text[:80]}...")
    
    # 方式 1: base64
    md_pattern = r'!\[.*?\]\(data:(image/[a-z]+);base64,([A-Za-z0-9+/=]+)\)'
    match = re.search(md_pattern, text)
    if match:
        print("  [OK] 匹配到 base64 格式")
        return 'base64'
    
    # 方式 2: 外部 URL
    url_pattern = r'!\[.*?\]\((https?://[^\s\)]+\.(png|jpg|jpeg|webp|gif))\)'
    url_match = re.search(url_pattern, text, re.IGNORECASE)
    if url_match:
        image_url = url_match.group(1)
        print(f"  [OK] 匹配到外部 URL: {image_url}")
        
        # 尝试下载
        try:
            img_response = requests.get(image_url, timeout=30)
            img_response.raise_for_status()
            image = Image.open(io.BytesIO(img_response.content))
            print(f"  [OK] 图片下载成功，尺寸：{image.size}")
            return 'url'
        except Exception as e:
            print(f"  [ERROR] 下载失败：{e}")
            return 'url_download_error'
    
    print("  [ERROR] 未匹配任何格式")
    return 'none'

print("=" * 60)
print("测试图片解析逻辑")
print("=" * 60)

for i, text in enumerate(test_cases, 1):
    print(f"\n测试用例 {i}:")
    test_url_parsing(text)
