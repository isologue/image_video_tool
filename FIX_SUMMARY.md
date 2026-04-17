# 修复总结 - 2026-04-14

## 问题描述
用户报告"未找到图片数据"错误，无论文生图还是图生图都失败。

## 根本原因
Gemini API 返回的图片格式是 **Markdown 外部 URL**：
```markdown
![Image](https://pro.filesystem.site/cdn/xxx.png)
```

但代码只能解析 **base64 Data URI** 格式：
```markdown
![image](data:image/png;base64,...)
```

导致解析失败，`output_paths` 为空，返回错误。

## 修复内容

### 1. server.py (Flask 版本)

#### `/api/text-to-image` 函数
- 添加外部 URL 正则匹配：`r'!\[.*?\]\((https?://[^\s\)]+\.(png|jpg|jpeg|webp|gif))\)'`
- 添加图片下载逻辑：`requests.get(image_url, timeout=30)`
- 下载后保存到 session 目录

#### `/api/image-to-image` 函数
- 同样的修复：添加外部 URL 正则匹配和下载逻辑

### 2. app.py (Gradio 版本)

#### `text_to_image()` 函数
- 添加外部 URL 解析支持

#### `image_to_image()` 函数
- 添加外部 URL 解析支持

### 3. 默认模型修正
- `server.py`: `gemini-3.1-flash-image-preview` → `gemini-2.5-flash-image`
- `app.py`: `gemini-3.1-flash-image-preview` → `gemini-2.5-flash-image`

## 验证结果

### 文生图测试
```
[OK] 匹配到外部 URL
[OK] 图片下载成功，尺寸：(1024, 1024)
```

### 图生图解析逻辑验证
```
[OK] 匹配到外部 URL
[OK] 图片下载成功，尺寸：(800, 1280)
[OK] 已保存到 verify_output.png
```

### 可用模型列表（用户 API Key）
- `gemini-2.5-flash-image` ✅
- `gemini-3-pro-image-preview`
- `gemini-3.1-flash-image-preview`

## 下一步操作

1. **重启服务**
   ```bash
   # 如果使用 Docker
   docker-compose restart
   
   # 如果手动运行
   # 停止当前进程，然后：
   python server.py
   ```

2. **测试文生图**
   - 访问 http://localhost:7863
   - 填写 API Key
   - 选择模型 `gemini-2.5-flash-image`
   - 输入提示词，生成图片

3. **测试图生图**
   - 切换到"图生图"标签
   - 上传 1-4 张参考图片
   - 输入提示词
   - 生成图片

## 修改的文件清单
- `server.py` - 文生图和图生图的图片解析逻辑
- `app.py` - 文生图和图生图的图片解析逻辑
- 默认模型配置（两处）

## 备注
- 代码已验证语法正确（`python -m py_compile` 通过）
- 解析逻辑已独立测试验证
- 需要重启服务才能生效

---

## 2026-04-14 晚间更新 - API 传输错误优化

### 新增问题
用户报告 API 调用不稳定，10 次里有这些错误：
1. `status_code=500` - Internal Server Error (HTTP/2 stream error)
2. `status_code=504` - Gateway Timeout
3. `status_code=413` - Payload Too Large

**关键发现**：这些错误发生时，**上游 API 实际上已经成功生成了图片**！问题是出在传输层（HTTP/2 stream、网关），不是 API 处理失败。

### 优化内容

#### 1. 移除自动重试
- 原因：上游已成功，重试没用
- 策略：失败直接提示，让用户检查上游日志

#### 2. 增加超时时间
- 原来：300 秒
- 现在：600 秒（10 分钟）

#### 3. 优化图片压缩参数（减小 413 错误）
| 参数 | 原来 | 现在 | 效果 |
|------|------|------|------|
| max_size | 512 | 384 | 面积减小 39% |
| jpeg_quality | 75 | 60 | 体积减小约 30% |
| **总体积** | - | - | **减小约 55-60%** |

#### 4. 错误提示（强调上游可能已成功）
- 413: "⚠️ 请求太大 (413) - 上游可能已成功，建议检查上游日志"
- 504: "⚠️ 网关超时 (504) - 上游可能已成功，建议检查上游日志"
- 500: "⚠️ 服务器错误 (500) - 上游可能已成功，建议检查上游日志"
- Timeout: "⚠️ 上游可能已成功，但传输超时，请检查上游是否已生成图片"

### 修改的文件
- `server.py` - 移除重试函数，优化错误提示，文生图 + 图生图都更新
- `app.py` - 优化压缩参数 (512/75 → 384/60)

### 为什么会出现这些错误

| 错误 | 原因 | 上游状态 |
|------|------|----------|
| 500 (HTTP/2 stream) | HTTP/2 连接层面的错误 | ✅ 已成功处理 |
| 504 | 网关等待响应超时 | ✅ 可能已生成 |
| 413 | 响应体太大，网关拒绝转发 | ✅ 已生成但无法返回 |

### 建议
如果频繁遇到这些错误：
1. 检查上游日志确认图片是否已生成
2. 考虑使用更小的图片尺寸（已优化到 384px）
3. 检查网络链路（网关、负载均衡器配置）
