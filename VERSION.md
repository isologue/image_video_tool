# Gemini Image Tool V4 - 版本说明

## 版本信息
- **版本号**: V4.0
- **发布日期**: 2026-04-09
- **基于版本**: V3.0

## 新增功能

### ✅ 多参考图支持（图生图）
- **最多支持 4 张参考图**
- 支持上传多张图片作为参考
- 所有参考图会一起发送给 Gemini API
- 适合需要多角度/多元素参考的场景

## 核心功能（继承 V3）

### ✅ 分辨率选择（通过 Gemini API 原生支持）
- 512 (512x512)
- 1K (1024x1024)
- 2K (2048x2048)
- 4K (4096x4096)

### ✅ 图片比例选择
- 1:1（正方形）
- 16:9（横屏）
- 9:16（竖屏）
- 4:3
- 3:4

### ✅ 多用户 Session 隔离
- 每个用户独立的图片存储空间
- 每用户的 `last_image` 独立存储，互不干扰
- 一键清除会话数据
- 自动清理过期 Session

### ✅ 文生图 / 图生图
- 支持连续对话（基于上一张图编辑）
- 支持负面提示词
- 支持多张图片生成

## 技术实现

### 多参考图 API 格式
```json
{
  "contents": [{
    "parts": [
      {"inlineData": {"mimeType": "image/jpeg", "data": "base64_1"}},
      {"inlineData": {"mimeType": "image/jpeg", "data": "base64_2"}},
      {"inlineData": {"mimeType": "image/jpeg", "data": "base64_3"}},
      {"inlineData": {"mimeType": "image/jpeg", "data": "base64_4"}},
      {"text": "prompt"}
    ]
  }],
  "generationConfig": {
    "responseModalities": ["IMAGE"],
    "imageConfig": {
      "aspectRatio": "16:9",
      "imageSize": "4K"
    }
  }
}
```

### 关键修改
- **server.py**: `/api/image-to-image` 接口现在接收 `image_data_list` 数组
- **index.html**: 图生图 Tab 支持多文件上传，最多 4 张
- 前端预览支持显示所有上传的参考图

## 支持的模型
- `gemini-3.1-flash-image-preview` ✅（支持 512/1K/2K/4K）
- `gemini-2.0-flash-exp-image-generation` ✅

## 中转站
- **默认**: `https://moai.wiki` ✅（已测试支持 `imageConfig` 参数）

## 启动方式

### Docker Compose（推荐）
```bash
cd D:\test\gemini_image_tool_v4
docker-compose up -d --build
```

访问：http://localhost:7863

### 手动运行
```bash
pip install -r requirements.txt
python server.py
```

## 端口说明
- V1: 7860
- V2: 7861
- V3: 7862
- **V4: 7863** ← 当前版本

## 文件结构
```
gemini_image_tool_v4/
├── server.py              # 主服务（支持多参考图）
├── templates/
│   └── index.html         # 前端（多文件上传 UI）
├── output/                # 图片输出（每用户独立子目录）
├── sessions/              # Session 状态文件
├── uploads/               # 临时上传目录
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── README.md
├── CHANGELOG.md
└── VERSION.md             # 本文件
```

## 使用场景

### 单参考图
- 基于单张图片进行风格迁移
- 修改图片中的某个元素

### 多参考图（2-4 张）
- 融合多张图片的元素
- 参考多个角度的设计
- 结合多个角色的特征
- 综合多个场景的氛围

## 注意事项
1. 最多支持 4 张参考图，超过会拒绝
2. 多参考图会增加 API 调用时间和 token 消耗
3. 参考图越多，生成结果越综合
4. 必须使用支持 `imageConfig` 参数的中转站

---

**创建时间**: 2026-04-09 18:47
