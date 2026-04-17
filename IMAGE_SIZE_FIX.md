# 图片大小优化 - 2026-04-14

## 问题确认

用户上传测试：
- ❌ 5MB 图片 → 502 错误
- ✅ 1MB 图片 → 成功

**原因**：base64 编码会让体积膨胀 33%，5MB 图片编码后约 6.7MB，超过 Gemini API/中转站的请求限制。

## 解决方案

### 1. 前端自动压缩（index.html）

添加了 `compressImage()` 函数：
- **触发条件**：文件 > 2MB 自动压缩
- **压缩参数**：
  - 最大边长：800px
  - JPEG 质量：0.7 (70%)
- **预期效果**：5MB → 压缩到 ~500KB-1MB

```javascript
// 自动压缩大图片
if (file.size > 2 * 1024 * 1024) {
    const compressed = await compressImage(file, 800, 0.7);
    uploadedImagesBase64.push(compressed);
}
```

### 2. 后端大小检查（server.py）

在 `/api/image-to-image` 添加检查：
```python
# 检查图片总大小
total_size_mb = total_size * 3 / 4 / 1024 / 1024
if total_size_mb > 5:
    return jsonify({'error': f'参考图总大小过大 ({total_size_mb:.2f}MB)...'})
```

### 3. 后端压缩参数优化

已修改 `encode_image_to_base64()` 默认参数：
- `max_size`: 512 → 384
- `jpeg_quality`: 75 → 60

## 使用建议

### 最佳实践
1. **上传前手动压缩** - 用系统自带工具压缩到 2MB 以下
2. **让前端自动压缩** - 直接上传，>2MB 会自动处理
3. **避免一次性上传太多大图** - 4 张 5MB 图片肯定会失败

### 图片大小限制

| 场景 | 建议大小 | 最大限制 |
|------|---------|---------|
| 单张参考图 | < 2MB | 4MB |
| 总参考图 (4 张) | < 4MB | 5MB |
| 文生图连续对话 | < 2MB | 4MB |

## 修改的文件

1. `templates/index.html` - 添加前端压缩功能
2. `server.py` - 添加后端大小检查
3. `server.py` - 优化压缩参数 (384px/60%)
4. `app.py` - 优化压缩参数 (384px/60%)

## 测试方法

1. 上传一张 5MB 的图片
2. 观察控制台日志：`文件 xxx.png 太大 (5.00MB)，正在压缩...`
3. 压缩完成后检查预览图
4. 生成图片应该成功（不再 502）
