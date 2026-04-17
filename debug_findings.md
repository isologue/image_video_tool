# 调试结果总结

## 问题定位

**错误信息**: "未找到图片数据"

## 根本原因

API 调用**成功**了，但是代码**无法解析返回的图片格式**。

### API 实际返回格式

```json
{
  "candidates": [{
    "content": {
      "parts": [{
        "text": "\n![Image](https://pro.filesystem.site/cdn/20260414/9e568b19-0e4b-4928-b4ef-21232e3c0a29.png)\n"
      }]
    }
  }]
}
```

图片是以 **Markdown 外部链接** 形式返回的：
```markdown
![Image](https://pro.filesystem.site/cdn/xxx.png)
```

### 代码期望的格式

代码中的正则表达式（server.py 和 app.py）：
```python
md_pattern = r'!\[.*?\]\(data:(image/[a-z]+);base64,([A-Za-z0-9+/=]+)\)'
```

这个正则只能匹配 **base64 Data URI** 格式：
```markdown
![image](data:image/png;base64,iVBORw0KGgo...)
```

### 不匹配导致的问题

1. 代码检查 `inlineData` 字段 → 不存在
2. 代码用正则匹配 Markdown → 不匹配（因为是 URL 不是 base64）
3. `output_paths` 为空列表
4. 返回错误："未找到图片数据"

## 可用模型

你的 API Key 可以访问以下图片生成模型：
- `gemini-2.5-flash-image` ✅
- `gemini-3-pro-image-preview`
- `gemini-3.1-flash-image-preview`

## 修复方案 (已完成)

已修改以下文件：

### 1. `server.py` - Flask 版本
- 在 `/api/text-to-image` 和 `/api/image-to-image` 中添加外部 URL 解析
- 新增正则：`r'!\[.*?\]\((https?://[^\s\)]+\.(png|jpg|jpeg|webp|gif))\)'`
- 自动下载 URL 图片并保存到 session 目录

### 2. `app.py` - Gradio 版本
- 在 `text_to_image()` 和 `image_to_image()` 函数中添加外部 URL 解析
- 同样的正则匹配和下载逻辑

### 3. 默认模型修正
- 从 `gemini-3.1-flash-image-preview` 改为 `gemini-2.5-flash-image`

## 测试结果

```
测试用例 1 (外部 URL):
  [OK] 匹配到外部 URL
  [OK] 图片下载成功，尺寸：(1024, 1024)

测试用例 2 (base64):
  [OK] 匹配到 base64 格式
```
