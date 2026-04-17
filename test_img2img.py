#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试图生图 API 调用和解析
"""

import requests
import json
import base64
import io
from PIL import Image
import re

API_KEY = "sk-s1m7Stw3vgkMuCH1NPUbTWsFuy3rVfFZltB5lswIgcTtx1Jv"
BASE_URL = "https://moai.wiki"
MODEL_ID = "gemini-2.5-flash-image"

def encode_image_to_base64(image_path, max_size=512, jpeg_quality=75):
    """将图片编码为 base64"""
    img = Image.open(image_path)
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGB')
    if max(img.size) > max_size:
        ratio = max_size / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")

def test_image_to_image():
    print("=" * 60)
    print("测试图生图 API")
    print("=" * 60)
    
    # 找一张本地图片做测试
    import os
    test_image = None
    output_dir = "D:/test/gemini_image_tool_v4/output"
    if os.path.exists(output_dir):
        for f in os.listdir(output_dir):
            if f.endswith('.png') or f.endswith('.jpg'):
                test_image = os.path.join(output_dir, f)
                break
    
    if not test_image:
        print("[ERROR] 未找到测试图片，请先运行一次文生图")
        return
    
    print(f"\n使用测试图片：{test_image}")
    
    # 编码图片
    base64_image = encode_image_to_base64(test_image)
    print(f"图片编码完成，base64 长度：{len(base64_image)}")
    
    # 构建请求
    url = f"{BASE_URL}/v1beta/models/{MODEL_ID}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY
    }
    
    payload = {
        "contents": [{
            "parts": [
                {"inlineData": {"mimeType": "image/jpeg", "data": base64_image}},
                {"text": "让这张图片更明亮，色彩更鲜艳"}
            ]
        }],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {
                "aspectRatio": "1:1",
                "imageSize": "1K"
            }
        }
    }
    
    print(f"\n发送请求到：{url}")
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=120)
        print(f"\n响应状态码：{response.status_code}")
        
        result = response.json()
        print(f"\n完整响应:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        
        # 解析图片
        print("\n" + "=" * 60)
        print("开始解析图片")
        print("=" * 60)
        
        if "candidates" in result and len(result["candidates"]) > 0:
            candidate = result["candidates"][0]
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            
            for part in parts:
                print(f"\n检查 part 键：{part.keys()}")
                
                # 检查 inlineData
                if "inlineData" in part:
                    print("  [找到] inlineData 格式")
                    inline_data = part["inlineData"]
                    mime_type = inline_data.get("mimeType", "image/png")
                    if mime_type.startswith("image/"):
                        image_data = base64.b64decode(inline_data["data"])
                        image = Image.open(io.BytesIO(image_data))
                        print(f"  [OK] inlineData 解析成功，尺寸：{image.size}")
                        image.save("test_img2img_output.png")
                        print("  [OK] 已保存到 test_img2img_output.png")
                        return
                
                # 检查 text (Markdown)
                if "text" in part:
                    text = part["text"]
                    print(f"  [找到] text 内容：{text[:100]}...")
                    
                    # 方式 1: base64
                    md_pattern = r'!\[.*?\]\(data:(image/[a-z]+);base64,([A-Za-z0-9+/=]+)\)'
                    match = re.search(md_pattern, text)
                    if match:
                        print("  [匹配] base64 格式")
                        image_data = base64.b64decode(match.group(2))
                        image = Image.open(io.BytesIO(image_data))
                        print(f"  [OK] base64 解析成功，尺寸：{image.size}")
                        image.save("test_img2img_output.png")
                        print("  [OK] 已保存到 test_img2img_output.png")
                        return
                    
                    # 方式 2: 外部 URL
                    url_pattern = r'!\[.*?\]\((https?://[^\s\)]+\.(png|jpg|jpeg|webp|gif))\)'
                    url_match = re.search(url_pattern, text, re.IGNORECASE)
                    if url_match:
                        image_url = url_match.group(1)
                        print(f"  [匹配] 外部 URL: {image_url}")
                        
                        img_response = requests.get(image_url, timeout=30)
                        img_response.raise_for_status()
                        image = Image.open(io.BytesIO(img_response.content))
                        print(f"  [OK] URL 下载成功，尺寸：{image.size}")
                        image.save("test_img2img_output.png")
                        print("  [OK] 已保存到 test_img2img_output.png")
                        return
                    
                    print("  [ERROR] 未匹配任何图片格式")
        
        print("\n[ERROR] 未找到图片数据")
        
    except Exception as e:
        print(f"\n[ERROR] 错误：{e}")

if __name__ == "__main__":
    test_image_to_image()
