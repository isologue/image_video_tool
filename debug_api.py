#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调试脚本：直接调用 Gemini API 查看返回格式
"""

import requests
import json

API_KEY = "sk-s1m7Stw3vgkMuCH1NPUbTWsFuy3rVfFZltB5lswIgcTtx1Jv"
BASE_URL = "https://moai.wiki"
MODEL_ID = "gemini-2.5-flash-image"

def debug_list_models():
    print("=" * 60)
    print("获取可用模型列表")
    print("=" * 60)
    
    url = f"{BASE_URL}/v1beta/models"
    headers = {"X-Goog-Api-Key": API_KEY}
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        print(f"\n[RECV] 状态码：{response.status_code}")
        result = response.json()
        print(f"\n[OK] 可用模型:")
        if "models" in result:
            for m in result["models"]:
                name = m.get("name", "")
                if name.startswith("models/"):
                    name = name[7:]
                print(f"  - {name}")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"\n[ERROR] {e}")

def debug_text_to_image():
    print("=" * 60)
    print("开始调试 Gemini API 文生图功能")
    print("=" * 60)
    
    url = f"{BASE_URL}/v1beta/models/{MODEL_ID}:generateContent"
    
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY
    }
    
    payload = {
        "contents": [{
            "parts": [{
                "text": "一只可爱的猫咪在草地上晒太阳，高清图片"
            }]
        }],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {
                "aspectRatio": "1:1",
                "imageSize": "1K"
            }
        }
    }
    
    print(f"\n[SEND] 请求 URL: {url}")
    print(f"\n[SEND] 请求 Payload:")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=120)
        
        print(f"\n[RECV] 响应状态码：{response.status_code}")
        print(f"[RECV] 响应头：{dict(response.headers)}")
        
        # 尝试解析 JSON
        try:
            result = response.json()
            print(f"\n[OK] 响应内容 (完整 JSON):")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            
            # 检查关键路径
            print("\n[CHECK] 检查响应结构:")
            print(f"  - 'candidates' 存在：{'candidates' in result}")
            if 'candidates' in result:
                print(f"  - candidates 数量：{len(result['candidates'])}")
                if len(result['candidates']) > 0:
                    candidate = result['candidates'][0]
                    print(f"  - candidate 键：{candidate.keys()}")
                    if 'content' in candidate:
                        content = candidate['content']
                        print(f"    - content 键：{content.keys()}")
                        if 'parts' in content:
                            parts = content['parts']
                            print(f"    - parts 数量：{len(parts)}")
                            for i, part in enumerate(parts):
                                print(f"      - part[{i}] 键：{part.keys()}")
                                if 'inlineData' in part:
                                    print(f"        - inlineData mimeType: {part['inlineData'].get('mimeType')}")
                                    print(f"        - inlineData data 长度：{len(part['inlineData'].get('data', ''))}")
                                if 'text' in part:
                                    print(f"        - text 预览：{part['text'][:100]}...")
                    
            # 检查是否有其他图片字段
            print("\n[CHECK] 检查其他可能的图片字段:")
            for key in result.keys():
                print(f"  - 顶层键：{key}")
                
        except json.JSONDecodeError as e:
            print(f"\n[ERROR] 无法解析 JSON: {e}")
            print(f"原始响应内容：{response.text[:500]}")
            
    except requests.exceptions.RequestException as e:
        print(f"\n[ERROR] 请求错误：{e}")

if __name__ == "__main__":
    debug_list_models()
    print("\n\n")
    debug_text_to_image()
