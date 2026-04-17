#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
优化建议 - 减少 API 错误
"""

print("""
============================================================
API 错误优化建议
============================================================

## 1. 500 Internal Server Error
原因：API 服务端内部错误
解决：添加重试机制

## 2. 504 Gateway Timeout  
原因：API 响应超时（默认 300 秒可能不够）
解决：
- 增加超时时间到 600 秒
- 添加重试机制
- 减小图片尺寸（减少生成时间）

## 3. 413 Payload Too Large
原因：请求体太大（图片 base64 编码后体积膨胀 33%）
解决：
- 减小参考图尺寸（从 512 降到 384）
- 降低 JPEG 质量（从 75 降到 60）
- 如果是文生图，检查是否意外发送了大图片

============================================================
建议的代码修改
============================================================
""")

print("""
### 修改 1: 添加重试函数

```python
def call_api_with_retry(url, payload, headers, max_retries=3, timeout=600):
    import time
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            
            # 413 错误不重试（重试也没用）
            if response.status_code == 413:
                return response
            
            response.raise_for_status()
            return response
            
        except requests.exceptions.Timeout:
            logging.warning(f"超时，第 {attempt+1} 次重试...")
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))  # 递增等待时间
            continue
            
        except requests.exceptions.RequestException as e:
            logging.warning(f"请求错误，第 {attempt+1} 次重试：{e}")
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
            continue
    
    return response  # 返回最后一次响应
```

### 修改 2: 优化图片压缩参数

```python
# 原来：max_size=512, jpeg_quality=75
# 优化后：max_size=384, jpeg_quality=60
base64_image = encode_image_to_base64(image_path, max_size=384, jpeg_quality=60)
```

### 修改 3: 更好的错误提示

```python
if response.status_code == 413:
    return jsonify({'success': False, 'message': '请求太大，请尝试上传更小的图片'})
elif response.status_code == 504:
    return jsonify({'success': False, 'message': 'API 超时，请稍后重试'})
elif response.status_code == 500:
    return jsonify({'success': False, 'message': 'API 服务器错误，请稍后重试'})
```
""")
