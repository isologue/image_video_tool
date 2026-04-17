# 🎨 Gemini 图片生成工具 - 多参考图版 v4

基于 Flask 的 Gemini 图片生成与编辑工具，支持**最多 4 张参考图**的图生图功能，完整的 Session 隔离。

## ✨ 新功能 (v4)

| 功能 | 说明 |
|------|------|
| 🖼️ **多参考图** | 支持上传最多 4 张参考图片进行图生图 |
| 📊 **网格预览** | 实时预览所有参考图，支持单张移除 |
| 🔢 **计数提示** | 显示已选择图片数量 (X/4) |
| 🖼️ **分辨率选择** | 支持 512/1K/2K/4K 分辨率 |
| 🔐 **Session 隔离** | 每个用户独立的图片存储空间 |
| 🗑️ **一键清除** | 用户可随时清除自己的会话数据 |

## 📁 目录结构

```
gemini_image_tool_v4/
├── server.py              # 主服务（多参考图 + Session 隔离）
├── templates/
│   └── index.html         # 前端（多文件上传 UI）
├── output/                # 图片输出（每用户独立子目录）
│   └── {session_id}/
│       ├── img2img_1.png
│       └── ...
├── sessions/              # Session 状态文件
│   └── {session_id}.json
├── docker-compose.yml
└── Dockerfile
```

## 🚀 快速启动

### Docker Compose（推荐）

```bash
cd gemini_image_tool_v4
docker-compose up -d --build
```

访问：http://localhost:7863

> **注意：** v4 默认使用 **7863** 端口

### 手动运行

```bash
pip install -r requirements.txt
python server.py
```

## 🖼️ 多参考图使用场景

### 单参考图
- 基于单张图片进行风格迁移
- 修改图片中的某个元素
- 调整颜色、亮度等

### 多参考图（2-4 张）
- 融合多张图片的元素和风格
- 参考多个角度的设计稿
- 结合多个角色的特征
- 综合多个场景的氛围
- 对比参考不同风格

## 🖼️ 分辨率说明

### 支持的模型

| 模型 | 512 | 1K | 2K | 4K |
|------|-----|----|----|----|
| gemini-3.1-flash-image-preview | ✅ | ✅ | ✅ | ✅ |
| gemini-2.0-flash-exp-image-generation | ✅ | ✅ | ✅ | ✅ |

### 分辨率与尺寸对照表

| 比例 | 1K | 2K | 4K |
|------|-----|------|-------|
| 1:1 | 1024x1024 | 2048x2048 | 4096x4096 |
| 16:9 | 1376x768 | 2752x1536 | 5504x3072 |
| 9:16 | 768x1376 | 1536x2752 | 3072x5504 |

## 🔐 Session 隔离机制

### 用户识别
1. 首次访问自动生成 UUID4 Session ID
2. Session ID 通过 Cookie 存储（7 天有效期）
3. 每次请求自动携带 Session ID

### 数据隔离
```
用户 A (session_abc) → output/session_abc/ + sessions/session_abc.json
用户 B (session_xyz) → output/session_xyz/ + sessions/session_xyz.json
```

## 📡 API 接口

### 文生图
```http
POST /api/text-to-image
Content-Type: application/json

{
  "api_key": "...",
  "model_id": "gemini-3.1-flash-image-preview",
  "prompt": "一只可爱的猫咪",
  "resolution": "2K (2048x2048)",
  "aspect_ratio": "16:9"
}
```

### 图生图（多参考图）
```http
POST /api/image-to-image
Content-Type: application/json

{
  "api_key": "...",
  "model_id": "gemini-3.1-flash-image-preview",
  "prompt": "融合这些图片的风格",
  "resolution": "4K (4096x4096)",
  "aspect_ratio": "1:1",
  "image_data_list": [
    "data:image/png;base64,...",
    "data:image/png;base64,...",
    "data:image/png;base64,..."
  ]
}
```

**参数说明：**
- `image_data_list`: 数组格式，包含 1-4 张参考图的 base64 数据
- 少于 1 张或多于 4 张会被拒绝

### 获取 Session 信息
```http
GET /api/session
```

### 清除 Session
```http
POST /api/session/clear
```

### 清理过期 Session
```http
POST /api/cleanup
Content-Type: application/json

{"max_age_hours": 24}
```

## ⚙️ 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OUTPUT_DIR` | `/app/output` | 图片输出根目录 |
| `SESSIONS_DIR` | `/app/sessions` | Session 状态目录 |
| `DEFAULT_BASE_URL` | `https://moai.wiki` | Gemini API 中转站地址 |
| `DEFAULT_MODEL` | `gemini-3.1-flash-image-preview` | 默认模型 ID |

## 🔧 版本对比

| 特性 | v1 | v2 | v3 | v4 |
|------|----|----|----|----|
| 图片存储 | 扁平目录 | 每用户子目录 | 每用户子目录 | 每用户子目录 |
| 连续对话 | 全局变量 | 每用户独立 | 每用户独立 | 每用户独立 |
| 多用户 | ❌ | ✅ | ✅ | ✅ |
| 分辨率选择 | ❌ | ❌ | ✅ | ✅ |
| 多参考图 | ❌ | ❌ | ❌ | ✅ (最多 4 张) |
| 默认端口 | 7860 | 7861 | 7862 | 7863 |

## 📝 注意事项

1. **端口变更**：v4 默认使用 **7863** 端口
2. **参考图限制**：最多支持 4 张参考图
3. **API 消耗**：多参考图会增加 token 消耗和生成时间
4. **磁盘空间**：定期清理过期 Session
5. **分辨率限制**：4K 分辨率需要更多时间和 API 配额

## 🐛 已知问题

无

## 📄 License

MIT
