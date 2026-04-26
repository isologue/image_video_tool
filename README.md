# Gemini Image + Grok Video + GPT Image

一个基于 Flask 的本地多工作区工具站，集成三个独立模块：

- **Gemini 生图**：文生图、图生图、多参考图、连续对话、结果历史恢复。
- **Grok 视频**：参考图生成视频、任务列表、进度查询、视频地址复制、按需预览。
- **GPT 生图**：文生图、编辑图、原生/兼容接口模式、结果历史和参考图草稿恢复。

入口页 `/` 使用 iframe 承载三个子应用，避免不同页面的 CSS/JS 互相影响。每个模块都有独立 session cookie，刷新页面后会恢复 API Key、提示词、参考图和最近结果。

## 功能概览

### Gemini 生图

- 文生图和图生图
- 最多 4 张参考图
- 分辨率和画面比例选择
- 负面提示词
- 基于上一张结果继续生成
- API Key、提示词、参考图草稿保存到 session
- 生成结果保存到当前 session history，刷新后恢复最近结果
- 图片预览和下载

### Grok 视频

- 视频提示词和参考图上传
- 上传前压缩参考图
- 视频尺寸、质量、时长选择
- 模型列表获取
- 任务列表和进度查询
- 结果视频默认隐藏，通过“预览视频”按钮展开/收起
- 视频地址复制/打开
- API Key、提示词、参考图草稿保存到 session

### GPT 生图

- 文生图和编辑图
- 原生接口：`/v1/images/generations`、`/v1/images/edits`
- 兼容接口：`/v1/chat/completions`
- 最多 4 张参考图
- 最近结果复用
- API Key、提示词、参考图草稿保存到 session
- 结果历史、预览和下载

## 项目结构

```text
.
├── server.py                 # Flask 主入口，Docker 默认启动它
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── templates/
│   ├── index.html            # 三模块统一入口
│   ├── gemini_app.html
│   ├── grok_app.html
│   └── gpt_image_app.html
├── output/                   # 运行时生成结果，已 git ignore
├── sessions/                 # session 草稿/任务/历史，已 git ignore
└── uploads/                  # 上传运行态目录，已 git ignore
```

## 路由

| 路由 | 说明 |
| --- | --- |
| `/` | 统一入口，可切换 Gemini / Grok / GPT |
| `/gemini-app` | Gemini 图片工作区 |
| `/grok-app` | Grok 视频工作区 |
| `/gpt-image-app` | GPT 图片工作区 |

## Docker 启动

```bash
docker compose up -d --build
```

默认访问：

```text
http://localhost:7863
```

容器内 Flask 监听 `7860`，`docker-compose.yml` 默认映射到宿主机 `7863`。

## 本地 Python 启动

```bash
pip install -r requirements.txt
python server.py
```

默认访问：

```text
http://localhost:7860
```

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OUTPUT_DIR` | `/app/output` | 生成结果目录 |
| `SESSIONS_DIR` | `/app/sessions` | session 草稿、任务和历史目录 |
| `DEFAULT_BASE_URL` | `https://moai.wiki` | Gemini 默认接口地址 |
| `DEFAULT_MODEL` | `gemini-2.5-flash-image` | Gemini 默认模型 |
| `DEFAULT_VIDEO_BASE_URL` | `DEFAULT_BASE_URL` | Grok 视频默认接口地址 |
| `DEFAULT_VIDEO_MODEL` | `grok-imagine-1.0-video` | Grok 默认模型 |
| `DEFAULT_GPT_IMAGE_BASE_URL` | `DEFAULT_BASE_URL` | GPT 生图默认接口地址 |
| `DEFAULT_GPT_IMAGE_MODEL` | `gpt-image-2-flatfee` | GPT 生图默认模型 |
| `DEFAULT_REQUEST_TIMEOUT` | `60` | 普通请求超时时间，单位秒 |

## Session 和数据安全

API Key、提示词、参考图草稿、任务记录和结果历史会按浏览器 session 保存到 `sessions/`。这能保证刷新页面后不丢状态，但也意味着 API Key 会以明文写入本地 session JSON。

部署建议：

- 不要把 `sessions/`、`output/`、`uploads/` 提交到 Git。
- 如果部署到公网，请限制访问、保护挂载目录权限。
- 多人共用时，建议定期清理过期 session。

## 开发验证

```bash
python -m py_compile server.py
```

如果本机安装了 Node.js，也可以解析页面脚本：

```bash
node -e "const fs=require('fs'),vm=require('vm'); for (const f of ['templates/gemini_app.html','templates/grok_app.html','templates/gpt_image_app.html']) { const h=fs.readFileSync(f,'utf8'); [...h.matchAll(/<script\\b[^>]*>([\\s\\S]*?)<\\/script>/gi)].forEach((m,i)=>new vm.Script(m[1],{filename:f+':script'+i})); console.log('ok', f); }"
```

## 备注

- `server.py` 是当前维护的 Flask 主入口。
- 运行态文件都应该留在本地或 Docker volume 中。
