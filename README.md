# Gemini Image + GROK Video

一个基于 Flask 的双工作区工具站：

- **Gemini 画图**：支持文生图、图生图、多参考图、分辨率/比例选择
- **GROK 视频**：支持参考图上传、视频生成、任务列表、进度查询、结果预览

项目采用 **shell-page + 子页面隔离** 的结构：

- `/`：统一入口页，用于切换工作区
- `/gemini-app`：Gemini 图片工作区
- `/grok-app`：GROK 视频工作区

这样可以保留两套页面和流程的独立性，避免 CSS / JS 相互污染，同时又提供一个更像完整产品的统一入口。

## Features

### Gemini 图片工作区

- 文生图
- 图生图
- 最多 4 张参考图
- 分辨率选择：512 / 1K / 2K / 4K
- 图片比例选择
- Session 隔离
- 最近会话状态查看
- 图片放大预览与下载

### GROK 视频工作区

- 视频提示词输入
- 参考图上传（最多 4 张）
- 上传前压缩参考图
- 视频尺寸 / 质量 / 时长选择
- 模型列表获取
- 视频任务提交
- 任务列表查看
- 任务进度查询
- 视频结果预览与链接复制
- Session 草稿保存

### 产品结构

- 单入口切换 Gemini / GROK
- 两个 iframe 独立保活，切换时尽量不丢上下文
- 页面样式相互隔离，减少前端冲突

## Project Structure

```text
gemini_image&grok_video/
├── app.py
├── server.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── templates/
│   ├── index.html
│   ├── gemini_app.html
│   └── grok_app.html
├── output/          # 运行时输出（已建议 git ignore）
├── uploads/         # 上传文件（已建议 git ignore）
├── sessions/        # Session/草稿数据（已建议 git ignore）
└── __pycache__/
```

## Routes

| Route | Description |
|------|------|
| `/` | 统一入口页 |
| `/gemini-app` | Gemini 图片工作区 |
| `/grok-app` | GROK 视频工作区 |

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run

如果你的启动文件是 `server.py`：

```bash
python server.py
```

如果你实际用的是 `app.py`：

```bash
python app.py
```

### 3. Open in browser

```text
http://localhost:7863
```

## Recommended .gitignore targets

这些目录通常不建议提交到仓库：

- `output/`
- `uploads/`
- `sessions/`
- `__pycache__/`
- `.env`
- 本地调试输出文件

## Notes

### 1. 关于架构

这个项目不是把 Gemini 页面和 GROK 页面硬塞进同一个 HTML，而是：

- 用一个壳页面负责切换
- 用两个独立子页面承载各自功能

这样更容易继续演进，也更不容易互相影响。

### 2. 关于 Session

Gemini 和 GROK 都使用会话状态来保存各自的运行数据，因此更适合多轮操作和重复调试。

### 3. 关于上传内容

上传文件、运行结果、任务状态通常属于本地运行态数据，不建议直接提交到 Git 仓库。

## Roadmap

- 继续打磨 Gemini / GROK 的产品细节
- 统一 loading / success / error 提示体验
- 继续优化 GROK 视频 flow
- 进一步完善移动端观感

## License

MIT
