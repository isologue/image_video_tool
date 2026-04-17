#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini 图片生成与编辑工具 - Flask 版本 (多用户隔离版)
纯 HTML/CSS/JS 前端，Flask 后端 API
支持完整的 Session 隔离，防止多用户数据混淆
"""

import os
import base64
import requests
import time
import uuid
import json
import logging
import sys
import hashlib
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory, make_response
from PIL import Image
import io
import re
import threading
from io import BytesIO
from datetime import datetime, timezone

# 配置日志输出到 stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(message)s',
    stream=sys.stdout,
    force=True
)

# API 超时配置
API_TIMEOUT = 360  # 6 分钟超时

app = Flask(__name__)

# 配置
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/output")
SESSIONS_DIR = os.getenv("SESSIONS_DIR", "/app/sessions")
DEFAULT_BASE_URL = os.getenv("DEFAULT_BASE_URL", "https://moai.wiki")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemini-2.5-flash-image")
SESSION_COOKIE_NAME = "gemini_session_id"
SESSION_COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7 天
VIDEO_SESSION_COOKIE_NAME = "video_tool_session_id"
DEFAULT_VIDEO_BASE_URL = os.getenv("DEFAULT_VIDEO_BASE_URL", DEFAULT_BASE_URL)
DEFAULT_VIDEO_MODEL = os.getenv("DEFAULT_VIDEO_MODEL", "grok-imagine-1.0-video")
DEFAULT_REQUEST_TIMEOUT = int(os.getenv("DEFAULT_REQUEST_TIMEOUT", "60"))


# 确保目录存在
Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
Path(SESSIONS_DIR).mkdir(parents=True, exist_ok=True)

# 线程安全的 Session 状态存储
session_states = {}  # {session_id: {"last_image": str, "created_at": float}}
session_lock = threading.Lock()
file_lock = threading.Lock()


def get_session_id():
    """从 Cookie 获取或生成 Session ID"""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    
    # 验证 session_id 格式（必须是有效的 UUID）
    if session_id:
        try:
            uuid.UUID(session_id)
            return session_id
        except ValueError:
            pass  # 无效的 UUID，生成新的
    
    # 生成新的 Session ID
    session_id = str(uuid.uuid4())
    return session_id


def set_session_cookie(response, session_id):
    """设置 Session Cookie"""
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite='Lax'
    )
    return response


def get_session_dir(session_id):
    """获取 Session 的输出目录"""
    return Path(OUTPUT_DIR) / session_id


def get_session_state_path(session_id):
    """获取 Session 状态文件路径"""
    return Path(SESSIONS_DIR) / f"{session_id}.json"


def load_session_state(session_id):
    """加载 Session 状态（从内存或文件）"""
    with session_lock:
        # 先检查内存
        if session_id in session_states:
            return session_states[session_id]
        
        # 从文件加载
        state_path = get_session_state_path(session_id)
        if state_path.exists():
            try:
                with open(state_path, 'r') as f:
                    state = json.load(f)
                session_states[session_id] = state
                return state
            except Exception as e:
                logging.warning(f"加载 Session 状态失败：{e}")
        
        # 创建新状态
        state = {
            "last_image": None,
            "created_at": time.time(),
            "request_count": 0
        }
        session_states[session_id] = state
        return state


def save_session_state(session_id, state):
    """保存 Session 状态到文件和内存"""
    with session_lock:
        session_states[session_id] = state
        state_path = get_session_state_path(session_id)
        try:
            with open(state_path, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logging.error(f"保存 Session 状态失败：{e}")


def update_session_last_image(session_id, image_path):
    """更新 Session 的最后一张图片"""
    state = load_session_state(session_id)
    state["last_image"] = str(image_path)
    state["request_count"] = state.get("request_count", 0) + 1
    state["last_accessed"] = time.time()
    save_session_state(session_id, state)


def encode_image_to_base64(image_path, max_size=384, jpeg_quality=60):
    """将图片编码为 base64，自动压缩
    
    优化参数减少 413 错误：
    - max_size: 512 → 384 (减小 39% 面积)
    - jpeg_quality: 75 → 60 (减小约 30% 体积)
    """
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


def get_image_mime_type(image_path):
    """获取图片 MIME 类型"""
    ext = Path(image_path).suffix.lower()
    mime_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif"
    }
    return mime_types.get(ext, "image/png")




def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_video_session_id():
    session_id = request.cookies.get(VIDEO_SESSION_COOKIE_NAME)
    if session_id:
        try:
            uuid.UUID(session_id)
            return session_id
        except ValueError:
            pass
    return str(uuid.uuid4())


def set_video_session_cookie(response, session_id):
    response.set_cookie(
        VIDEO_SESSION_COOKIE_NAME,
        session_id,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite='Lax'
    )
    return response


def get_video_session_dir(session_id):
    session_dir = Path(SESSIONS_DIR) / 'video' / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def get_tasks_path(session_id):
    return get_video_session_dir(session_id) / 'video_tasks.json'


def get_draft_path(session_id):
    return get_video_session_dir(session_id) / 'draft.json'


def load_tasks(session_id):
    tasks_path = get_tasks_path(session_id)
    if not tasks_path.exists():
        return []
    with file_lock:
        try:
            with tasks_path.open('r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as exc:
            logging.warning(f'Failed to load video tasks for {session_id}: {exc}')
            return []


def save_tasks(session_id, tasks):
    tasks_path = get_tasks_path(session_id)
    with file_lock:
        with tasks_path.open('w', encoding='utf-8') as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)


def load_draft(session_id):
    draft_path = get_draft_path(session_id)
    if not draft_path.exists():
        return {}
    with file_lock:
        try:
            with draft_path.open('r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logging.warning(f'Failed to load video draft for {session_id}: {exc}')
            return {}


def save_draft(session_id, draft):
    draft_path = get_draft_path(session_id)
    with file_lock:
        with draft_path.open('w', encoding='utf-8') as f:
            json.dump(draft, f, ensure_ascii=False, indent=2)


def clear_video_session_data(session_id):
    session_dir = get_video_session_dir(session_id)
    if session_dir.exists():
        shutil.rmtree(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)


def upsert_task(session_id, task):
    tasks = load_tasks(session_id)
    updated = False
    for index, item in enumerate(tasks):
        if item.get('video_id') == task.get('video_id'):
            tasks[index] = {**item, **task, 'updated_at': utc_now_iso()}
            updated = True
            break
    if not updated:
        task['updated_at'] = utc_now_iso()
        tasks.append(task)
    tasks.sort(key=lambda item: item.get('created_at', ''), reverse=True)
    save_tasks(session_id, tasks)
    return task


def find_task(session_id, task_id=None, video_id=None):
    for task in load_tasks(session_id):
        if task_id and task.get('task_id') == task_id:
            return task
        if video_id and task.get('video_id') == video_id:
            return task
    return None


def sanitize_base_url(base_url):
    return (base_url or DEFAULT_VIDEO_BASE_URL).strip().rstrip('/')


def build_headers(api_key):
    return {'Authorization': f'Bearer {api_key.strip()}'}


def build_task_record(session_id, payload, response_data):
    video_id = response_data.get('id')
    created_at = utc_now_iso()
    return {
        'session_id': session_id,
        'task_id': video_id,
        'video_id': video_id,
        'status': response_data.get('status', 'queued'),
        'progress': response_data.get('progress', 0),
        'created_at': created_at,
        'prompt': payload.get('prompt', ''),
        'model': payload.get('model', ''),
        'size': payload.get('size', ''),
        'seconds': payload.get('seconds'),
        'quality': payload.get('quality', ''),
        'video_url': response_data.get('video_url'),
        'error': response_data.get('error'),
    }


def compress_uploaded_image(uploaded_file, max_size_kb=10240, max_dimension=1920):
    image = Image.open(uploaded_file.stream)
    if image.mode not in ('RGB', 'L'):
        image = image.convert('RGB')

    width, height = image.size
    if max(width, height) > max_dimension:
        ratio = max_dimension / max(width, height)
        width = max(1, int(width * ratio))
        height = max(1, int(height * ratio))
        image = image.resize((width, height), Image.Resampling.LANCZOS)

    quality = 90
    output = BytesIO()
    image.save(output, format='JPEG', quality=quality, optimize=True)
    while output.tell() / 1024 > max_size_kb and quality > 20:
        quality -= 10
        output = BytesIO()
        image.save(output, format='JPEG', quality=quality, optimize=True)

    output.seek(0)
    return output, f"{Path(uploaded_file.filename or 'reference').stem}.jpg"


def fetch_video_models(api_key, base_url):
    candidates = [
        ('/v1/models', 'GET'),
        ('/v1/video/models', 'GET'),
        ('/v1/videos/models', 'GET'),
    ]
    last_error = None
    for path, method in candidates:
        url = f'{base_url}{path}'
        try:
            response = requests.request(
                method,
                url,
                headers=build_headers(api_key),
                timeout=15,
            )
            if response.status_code >= 400:
                last_error = f'{response.status_code} {response.text[:200]}'
                continue
            data = response.json()
            models = data.get('data') or data.get('models') or data.get('items') or []
            parsed = []
            for item in models:
                if isinstance(item, str):
                    parsed.append(item)
                elif isinstance(item, dict):
                    parsed.append(item.get('id') or item.get('name'))
            parsed = [item for item in parsed if item]
            if parsed:
                return parsed, None
        except Exception as exc:
            last_error = str(exc)
    return [DEFAULT_VIDEO_MODEL], last_error


def forward_error_response(response):
    try:
        detail = response.json()
    except Exception:
        detail = response.text[:500]
    return jsonify(
        {
            'success': False,
            'message': '????????',
            'status_code': response.status_code,
            'detail': detail,
        }
    ), response.status_code


@app.route('/')
def index():
    session_id = get_session_id()
    video_session_id = get_video_session_id()
    response = make_response(render_template('index.html'))
    response = set_session_cookie(response, session_id)
    return set_video_session_cookie(response, video_session_id)


@app.route('/gemini-app')
def gemini_app():
    session_id = get_session_id()
    response = make_response(render_template(
        'gemini_app.html',
        session_id=session_id,
        default_base_url=DEFAULT_BASE_URL,
        default_model=DEFAULT_MODEL,
    ))
    return set_session_cookie(response, session_id)


@app.route('/grok-app')
def grok_app():
    video_session_id = get_video_session_id()
    response = make_response(render_template(
        'grok_app.html',
        session_id=video_session_id,
        default_base_url=DEFAULT_VIDEO_BASE_URL,
        default_model=DEFAULT_VIDEO_MODEL,
        default_video_base_url=DEFAULT_VIDEO_BASE_URL,
        default_video_model=DEFAULT_VIDEO_MODEL,
    ))
    return set_video_session_cookie(response, video_session_id)


@app.route('/output/<session_id>/<filename>')
def serve_output(session_id, filename):
    """提供 Session 隔离的图片访问"""
    # 安全校验：session_id 必须是有效 UUID
    try:
        uuid.UUID(session_id)
    except ValueError:
        return jsonify({'error': 'Invalid session ID'}), 400
    
    session_dir = get_session_dir(session_id)
    
    # 防止目录遍历攻击
    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid filename'}), 400
    
    # 校验文件存在
    file_path = session_dir / filename
    if not file_path.exists():
        return jsonify({'error': 'File not found'}), 404
    
    return send_from_directory(str(session_dir), filename)


@app.route('/api/session', methods=['GET'])
def get_session_info():
    """获取当前 Session 信息"""
    session_id = get_session_id()
    state = load_session_state(session_id)
    response = make_response(jsonify({
        'session_id': session_id,
        'created_at': state.get('created_at'),
        'request_count': state.get('request_count', 0),
        'has_last_image': state.get('last_image') is not None
    }))
    return set_session_cookie(response, session_id)


@app.route('/api/session/clear', methods=['POST'])
def clear_session():
    """清除当前 Session 的历史数据"""
    session_id = get_session_id()
    
    # 清除内存状态
    with session_lock:
        if session_id in session_states:
            del session_states[session_id]
    
    # 清除文件状态
    state_path = get_session_state_path(session_id)
    if state_path.exists():
        try:
            state_path.unlink()
        except Exception as e:
            logging.error(f"删除 Session 状态文件失败：{e}")
    
    # 清除图片目录
    session_dir = get_session_dir(session_id)
    if session_dir.exists():
        try:
            import shutil
            shutil.rmtree(session_dir)
        except Exception as e:
            logging.error(f"删除 Session 图片目录失败：{e}")
    
    # 生成新的 Session ID
    new_session_id = str(uuid.uuid4())
    response = make_response(jsonify({
        'success': True,
        'message': 'Session 已清除',
        'new_session_id': new_session_id
    }))
    return set_session_cookie(response, new_session_id)


@app.route('/api/models', methods=['POST'])
def get_models():
    """获取模型列表"""
    data = request.json
    api_key = data.get('api_key', '')
    base_url = data.get('base_url', DEFAULT_BASE_URL)
    
    if not api_key:
        return jsonify({'error': '请填写 API Key'})
    
    try:
        url = f"{base_url.rstrip('/')}/v1beta/models"
        headers = {"X-Goog-Api-Key": api_key.strip()}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        models = []
        if "models" in data:
            for m in data["models"]:
                model_id = m.get("name", "")
                if model_id.startswith("models/"):
                    model_id = model_id[7:]
                if model_id:
                    models.append(model_id)
        return jsonify({'models': models if models else [DEFAULT_MODEL]})
    except Exception as e:
        return jsonify({'error': str(e), 'models': [DEFAULT_MODEL]})


@app.route('/api/test', methods=['POST'])
def test_connection():
    """测试连接"""
    data = request.json
    api_key = data.get('api_key', '')
    base_url = data.get('base_url', DEFAULT_BASE_URL)
    
    if not api_key:
        return jsonify({'success': False, 'message': '请填写 API Key'})
    
    try:
        url = f"{base_url.rstrip('/')}/v1beta/models"
        headers = {"X-Goog-Api-Key": api_key.strip()}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return jsonify({'success': True, 'message': f'连接成功！{base_url}'})
        return jsonify({'success': False, 'message': f'HTTP {response.status_code}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/text-to-image', methods=['POST'])
def text_to_image():
    """文生图（支持 Session 隔离的连续对话）"""
    session_id = get_session_id()
    
    # 加载 Session 状态
    state = load_session_state(session_id)
    last_image = state.get("last_image")
    
    data = request.json
    api_key = data.get('api_key', '')
    base_url = data.get('base_url', DEFAULT_BASE_URL)
    model_id = data.get('model_id', DEFAULT_MODEL)
    prompt = data.get('prompt', '')
    negative_prompt = data.get('negative_prompt', '')
    image_count = int(data.get('image_count', 1))
    resolution = data.get('resolution', '1K (1024x1024)')
    aspect_ratio = data.get('aspect_ratio', '1:1')
    use_last_image = data.get('use_last_image', False)
    
    # 转换分辨率格式： "1K (1024x1024)" -> "1K"
    resolution_map = {
        '512 (512x512)': '512',
        '1K (1024x1024)': '1K',
        '2K (2048x2048)': '2K',
        '4K (4096x4096)': '4K'
    }
    image_size = resolution_map.get(resolution, '1K')
    
    # 调试日志
    logging.info("=" * 50)
    logging.info(f"[DEBUG] 收到文生图请求")
    logging.info(f"[DEBUG] Session ID: {session_id[:8]}...")
    logging.info(f"[DEBUG] image_size: {image_size}")
    logging.info(f"[DEBUG] aspect_ratio: {aspect_ratio}")
    logging.info(f"[DEBUG] prompt: {prompt[:50]}...")
    logging.info("=" * 50)
    
    if not api_key:
        return jsonify({'error': '请填写 API Key'})
    if not model_id:
        return jsonify({'error': '请填写模型 ID'})
    if not prompt:
        return jsonify({'error': '请填写提示词'})
    
    # 确保 Session 输出目录存在
    session_dir = get_session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        logging.info(f"[DEBUG] 开始调用 API...")
        url = f"{base_url.rstrip('/')}/v1beta/models/{model_id.strip()}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key.strip()
        }
        
        output_paths = []
        
        for i in range(image_count):
            logging.info(f"[DEBUG] 开始第 {i+1} 次循环...")
            
            # 构建 payload 后打印调试
            logging.info(f"[DEBUG] 准备发送 payload...")
            
            # 如果使用上一张图（连续对话）
            if use_last_image and last_image and Path(last_image).exists():
                logging.info(f"[DEBUG] 🔄 使用连续对话模式，参考图：{last_image}")
                base64_image = encode_image_to_base64(last_image, max_size=512, jpeg_quality=75)
                mime_type = get_image_mime_type(last_image)
                
                payload = {
                    "contents": [{
                        "parts": [
                            {"inlineData": {"mimeType": mime_type, "data": base64_image}},
                            {"text": prompt}
                        ]
                    }],
                    "generationConfig": {
                        "responseModalities": ["IMAGE"],
                        "imageConfig": {
                            "aspectRatio": aspect_ratio,
                            "imageSize": image_size
                        }
                    }
                }
            else:
                logging.info(f"[DEBUG] 📝 使用纯文生图模式")
                # 纯文生图模式
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseModalities": ["IMAGE"],
                        "imageConfig": {
                            "aspectRatio": aspect_ratio,
                            "imageSize": image_size
                        }
                    }
                }
            
            if negative_prompt:
                payload["systemInstruction"] = {"parts": [{"text": f"不要生成包含以下内容的内容：{negative_prompt}"}]}
            
            # 打印 payload 用于调试
            import json
            logging.info(f"[DEBUG] Payload: {json.dumps(payload, ensure_ascii=False)[:800]}")
            
            # 直接调用 API（不重试）
            try:
                logging.info(f"[DEBUG] 发送请求到：{url}")
                response = requests.post(url, json=payload, headers=headers, timeout=API_TIMEOUT)
                logging.info(f"[DEBUG] 响应状态码：{response.status_code}")
                logging.info(f"[DEBUG] 响应头：{dict(response.headers)}")
                if response.status_code >= 400:
                    logging.error(f"[DEBUG] 响应体：{response.text[:500]}")
            except requests.exceptions.Timeout:
                logging.error(f"[TIMEOUT] 请求超时（{API_TIMEOUT}秒）")
                return jsonify({'success': False, 'message': '⚠️ 上游可能已成功，但传输超时，请检查上游是否已生成图片'})
            except requests.exceptions.RequestException as e:
                logging.error(f"[ERROR] 请求失败：{e}")
                logging.error(f"[ERROR] 异常类型：{type(e).__name__}")
                return jsonify({'success': False, 'message': f'⚠️ 上游可能已成功，但传输失败：{str(e)}'})
            
            # 处理特殊错误码
            if response.status_code == 413:
                logging.error(f"[413] 请求太大 - 上游可能已成功处理，但响应无法返回")
                return jsonify({'success': False, 'message': '⚠️ 请求太大 (413) - 上游可能已成功生成图片，建议检查上游日志'})
            
            if response.status_code == 502:
                logging.error(f"[502] Bad Gateway - Gemini API 返回空响应或连接断开")
                logging.error(f"[502] 可能原因：1)Gemini 限流 2)Gemini 服务波动 3)请求体太大被切断")
                return jsonify({'success': False, 'message': '⚠️ 网关错误 (502) - Gemini API 返回空响应，可能是限流或服务波动，请稍后重试'})
            
            if response.status_code == 504:
                logging.error(f"[504] 网关超时 - 上游可能仍在处理或已成功")
                return jsonify({'success': False, 'message': '⚠️ 网关超时 (504) - 上游可能已成功，建议检查上游日志'})
            
            if response.status_code == 500:
                logging.error(f"[500] 服务器错误 - 可能是 HTTP/2 stream 错误")
                return jsonify({'success': False, 'message': '⚠️ 服务器错误 (500) - 上游可能已成功，建议检查上游日志'})
            
            response.raise_for_status()
            result = response.json()
            
            logging.info(f"[DEBUG] API 返回完整结果：{json.dumps(result, ensure_ascii=False)[:1000]}")
            
            if "candidates" in result and len(result["candidates"]) > 0:
                candidate = result["candidates"][0]
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                
                for part in parts:
                    if "inlineData" in part:
                        inline_data = part["inlineData"]
                        mime_type = inline_data.get("mimeType", "image/png")
                        if mime_type.startswith("image/"):
                            image_data = base64.b64decode(inline_data["data"])
                            image = Image.open(io.BytesIO(image_data))
                            
                            output_path = session_dir / f"output_{i+1}.png"
                            image.save(output_path)
                            output_paths.append(f"output_{i+1}.png")
                            
                            # 更新 Session 的最后一张图（只记录第一张）
                            if i == 0:
                                update_session_last_image(session_id, str(output_path))
                                logging.info(f"[DEBUG] ✅ 已更新 last_image: {output_path} (尺寸：{image.size})")
                            break
                    
                    if "text" in part:
                        text = part["text"]
                        
                        # 方式 1: Markdown base64 格式 ![image](data:image/png;base64,...)
                        md_pattern = r'!\[.*?\]\(data:(image/[a-z]+);base64,([A-Za-z0-9+/=]+)\)'
                        match = re.search(md_pattern, text)
                        if match:
                            image_data = base64.b64decode(match.group(2))
                            image = Image.open(io.BytesIO(image_data))
                            
                            output_path = session_dir / f"output_{i+1}.png"
                            image.save(output_path)
                            output_paths.append(f"output_{i+1}.png")
                            
                            if i == 0:
                                update_session_last_image(session_id, str(output_path))
                                logging.info(f"[DEBUG] ✅ 已更新 last_image: {output_path} (尺寸：{image.size})")
                            break
                        
                        # 方式 2: Markdown 外部 URL 格式 ![Image](https://...)
                        url_pattern = r'!\[.*?\]\((https?://[^\s\)]+\.(png|jpg|jpeg|webp|gif))\)'
                        url_match = re.search(url_pattern, text, re.IGNORECASE)
                        if url_match:
                            image_url = url_match.group(1)
                            logging.info(f"[DEBUG] 🖼️ 发现外部图片 URL: {image_url}")
                            
                            # 下载图片
                            img_response = requests.get(image_url, timeout=30)
                            img_response.raise_for_status()
                            image = Image.open(io.BytesIO(img_response.content))
                            
                            output_path = session_dir / f"output_{i+1}.png"
                            image.save(output_path)
                            output_paths.append(f"output_{i+1}.png")
                            
                            if i == 0:
                                update_session_last_image(session_id, str(output_path))
                                logging.info(f"[DEBUG] ✅ 已更新 last_image: {output_path} (尺寸：{image.size})")
                            break
        
        if output_paths:
            return jsonify({
                'success': True,
                'images': [f'/output/{session_id}/{p}' for p in output_paths],
                'message': f'生成成功 {len(output_paths)} 张！'
            })
        
        return jsonify({'success': False, 'message': '未找到图片数据'})
        
    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'message': f'请求错误：{str(e)}'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'错误：{str(e)}'})


@app.route('/api/image-to-image', methods=['POST'])
def image_to_image():
    """图生图（Session 隔离，支持多参考图）"""
    session_id = get_session_id()
    
    data = request.json
    api_key = data.get('api_key', '')
    base_url = data.get('base_url', DEFAULT_BASE_URL)
    model_id = data.get('model_id', DEFAULT_MODEL)
    prompt = data.get('prompt', '')
    negative_prompt = data.get('negative_prompt', '')
    image_count = int(data.get('image_count', 1))
    resolution = data.get('resolution', '1K (1024x1024)')
    aspect_ratio = data.get('aspect_ratio', '1:1')
    image_data_list = data.get('image_data_list', [])  # base64 列表，支持多张
    
    # 转换分辨率格式： "1K (1024x1024)" -> "1K"
    resolution_map = {
        '512 (512x512)': '512',
        '1K (1024x1024)': '1K',
        '2K (2048x2048)': '2K',
        '4K (4096x4096)': '4K'
    }
    image_size = resolution_map.get(resolution, '1K')
    
    # 调试日志
    logging.info(f"[DEBUG] 图生图：image_size={image_size}, aspect_ratio={aspect_ratio}, 参考图数量={len(image_data_list)}")
    
    if not api_key:
        return jsonify({'error': '请填写 API Key'})
    if not model_id:
        return jsonify({'error': '请填写模型 ID'})
    if not prompt:
        return jsonify({'error': '请填写提示词'})
    if not image_data_list or len(image_data_list) == 0:
        return jsonify({'error': '请至少上传 1 张参考图片'})
    if len(image_data_list) > 4:
        return jsonify({'error': '最多支持 4 张参考图片'})
    
    # 检查图片总大小
    total_size = sum(len(img.split(',')[1]) if ',' in img else len(img) for img in image_data_list)
    total_size_mb = total_size * 3 / 4 / 1024 / 1024  # base64 转回原始大小估算
    logging.info(f"[DEBUG] 参考图总大小估算：{total_size_mb:.2f}MB")
    if total_size_mb > 5:
        return jsonify({'error': f'参考图总大小过大 ({total_size_mb:.2f}MB)，建议每张图不超过 2MB，或在前端使用压缩功能'})
    
    # 确保 Session 输出目录存在
    session_dir = get_session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        url = f"{base_url.rstrip('/')}/v1beta/models/{model_id.strip()}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key.strip()
        }
        
        # 解析上传的所有图片，编码为 base64
        encoded_images = []
        for img_data in image_data_list:
            if ',' in img_data:
                img_data = img_data.split(',')[1]
            img = Image.open(io.BytesIO(base64.b64decode(img_data)))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=75, optimize=True)
            encoded_images.append(base64.b64encode(buffer.getvalue()).decode("utf-8"))
        
        output_paths = []
        
        for i in range(image_count):
            # 构建 parts 列表：所有参考图 + 提示词
            parts = []
            for encoded_img in encoded_images:
                parts.append({"inlineData": {"mimeType": "image/jpeg", "data": encoded_img}})
            parts.append({"text": prompt})
            
            payload = {
                "contents": [{
                    "parts": parts
                }],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {
                        "aspectRatio": aspect_ratio,
                        "imageSize": image_size
                    }
                }
            }
            
            if negative_prompt:
                payload["systemInstruction"] = {"parts": [{"text": f"不要生成包含以下内容的内容：{negative_prompt}"}]}
            
            # 直接调用 API（不重试）
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=API_TIMEOUT)
            except requests.exceptions.Timeout:
                logging.error(f"[TIMEOUT] 请求超时")
                return jsonify({'success': False, 'message': '⚠️ 上游可能已成功，但传输超时，请检查上游是否已生成图片'})
            except requests.exceptions.RequestException as e:
                logging.error(f"[ERROR] 请求失败：{e}")
                return jsonify({'success': False, 'message': f'⚠️ 上游可能已成功，但传输失败：{str(e)}'})
            
            # 打印响应日志
            logging.info(f"[DEBUG] 响应状态码：{response.status_code}")
            if response.status_code >= 400:
                logging.error(f"[DEBUG] 响应体：{response.text[:500]}")
            
            # 处理特殊错误码
            if response.status_code == 413:
                logging.error(f"[413] 请求太大")
                return jsonify({'success': False, 'message': '⚠️ 请求太大 (413) - 上游可能已成功，建议检查上游日志'})
            
            if response.status_code == 502:
                logging.error(f"[502] Bad Gateway - Gemini API 返回空响应或连接断开")
                logging.error(f"[502] 可能原因：1)Gemini 限流 2)Gemini 服务波动 3)请求体太大被切断")
                return jsonify({'success': False, 'message': '⚠️ 网关错误 (502) - Gemini API 返回空响应，可能是限流或服务波动，请稍后重试'})
            
            if response.status_code == 504:
                logging.error(f"[504] 网关超时")
                return jsonify({'success': False, 'message': '⚠️ 网关超时 (504) - 上游可能已成功，建议检查上游日志'})
            
            if response.status_code == 500:
                logging.error(f"[500] 服务器错误")
                return jsonify({'success': False, 'message': '⚠️ 服务器错误 (500) - 上游可能已成功，建议检查上游日志'})
            
            response.raise_for_status()
            result = response.json()
            
            if "candidates" in result and len(result["candidates"]) > 0:
                candidate = result["candidates"][0]
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                
                for part in parts:
                    if "inlineData" in part:
                        inline_data = part["inlineData"]
                        mime_type = inline_data.get("mimeType", "image/png")
                        if mime_type.startswith("image/"):
                            image_data = base64.b64decode(inline_data["data"])
                            image = Image.open(io.BytesIO(image_data))
                            
                            output_path = session_dir / f"img2img_{i+1}.png"
                            image.save(output_path)
                            output_paths.append(f"img2img_{i+1}.png")
                            
                            # 更新 Session 的最后一张图
                            if i == 0:
                                update_session_last_image(session_id, str(output_path))
                                logging.info(f"[DEBUG] ✅ 已更新 last_image: {output_path} (尺寸：{image.size})")
                            break
                    
                    if "text" in part:
                        text = part["text"]
                        
                        # 方式 1: Markdown base64 格式
                        md_pattern = r'!\[.*?\]\(data:(image/[a-z]+);base64,([A-Za-z0-9+/=]+)\)'
                        match = re.search(md_pattern, text)
                        if match:
                            image_data = base64.b64decode(match.group(2))
                            image = Image.open(io.BytesIO(image_data))
                            
                            output_path = session_dir / f"img2img_{i+1}.png"
                            image.save(output_path)
                            output_paths.append(f"img2img_{i+1}.png")
                            
                            if i == 0:
                                update_session_last_image(session_id, str(output_path))
                                logging.info(f"[DEBUG] ✅ 已更新 last_image: {output_path} (尺寸：{image.size})")
                            break
                        
                        # 方式 2: Markdown 外部 URL 格式
                        url_pattern = r'!\[.*?\]\((https?://[^\s\)]+\.(png|jpg|jpeg|webp|gif))\)'
                        url_match = re.search(url_pattern, text, re.IGNORECASE)
                        if url_match:
                            image_url = url_match.group(1)
                            logging.info(f"[DEBUG] 🖼️ 发现外部图片 URL: {image_url}")
                            
                            # 下载图片
                            img_response = requests.get(image_url, timeout=30)
                            img_response.raise_for_status()
                            image = Image.open(io.BytesIO(img_response.content))
                            
                            output_path = session_dir / f"img2img_{i+1}.png"
                            image.save(output_path)
                            output_paths.append(f"img2img_{i+1}.png")
                            
                            if i == 0:
                                update_session_last_image(session_id, str(output_path))
                                logging.info(f"[DEBUG] ✅ 已更新 last_image: {output_path} (尺寸：{image.size})")
                            break
        
        if output_paths:
            return jsonify({
                'success': True,
                'images': [f'/output/{session_id}/{p}' for p in output_paths],
                'message': f'生成成功 {len(output_paths)} 张！'
            })
        
        return jsonify({'success': False, 'message': '未找到图片数据'})
        
    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'message': f'请求错误：{str(e)}'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'错误：{str(e)}'})




@app.route('/api/video/generate', methods=['POST', 'OPTIONS'])
def generate_video():
    if request.method == 'OPTIONS':
        return ('', 204)

    session_id = get_video_session_id()
    api_key = (request.form.get('api_key') or '').strip()
    base_url = sanitize_base_url(request.form.get('base_url'))
    model = (request.form.get('model') or DEFAULT_VIDEO_MODEL).strip()
    prompt = (request.form.get('prompt') or '').strip()
    size = (request.form.get('size') or '').strip()
    quality = (request.form.get('quality') or '').strip()

    seconds_raw = (request.form.get('seconds') or '').strip()
    seconds = None
    if seconds_raw:
        try:
            seconds = int(seconds_raw)
        except ValueError:
            return jsonify({'success': False, 'message': '????????'}), 400

    if not api_key:
        return jsonify({'success': False, 'message': '??? API Key'}), 400
    if not prompt:
        return jsonify({'success': False, 'message': '??????'}), 400
    if not size:
        return jsonify({'success': False, 'message': '???????'}), 400
    if quality not in {'high', 'standard'}:
        return jsonify({'success': False, 'message': '??????????'}), 400
    if seconds is not None and seconds not in {6, 10, 15}:
        return jsonify({'success': False, 'message': '??????? 6 / 10 / 15 ?'}), 400

    files = request.files.getlist('input_reference')
    if len(files) > 4:
        return jsonify({'success': False, 'message': '???? 4 ????'}), 400

    multipart_data = {
        'model': model,
        'prompt': prompt,
        'size': size,
        'quality': quality,
    }
    if seconds is not None:
        multipart_data['seconds'] = str(seconds)

    outbound_files = []
    try:
        for file in files:
            if not file or not file.filename:
                continue
            compressed_stream, filename = compress_uploaded_image(file)
            outbound_files.append(
                ('input_reference', (filename, compressed_stream, 'image/jpeg'))
            )

        response = requests.post(
            f"{base_url}/v1/videos",
            data=multipart_data,
            files=outbound_files,
            headers=build_headers(api_key),
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return jsonify({'success': False, 'message': f'????: {exc}'}), 502
    finally:
        for _, file_tuple in outbound_files:
            file_tuple[1].close()

    if response.status_code >= 400:
        return forward_error_response(response)

    data = response.json()
    task = build_task_record(session_id, multipart_data, data)
    upsert_task(session_id, task)

    result = jsonify({
        'success': True,
        'task_id': task['task_id'],
        'video_id': task['video_id'],
        'status': task['status'],
        'message': '???????',
        'task': task,
    })
    return set_video_session_cookie(result, session_id)


@app.route('/api/video/query', methods=['GET', 'OPTIONS'])
def query_video():
    if request.method == 'OPTIONS':
        return ('', 204)

    session_id = get_video_session_id()
    api_key = (request.args.get('api_key') or '').strip()
    task_id = (request.args.get('task_id') or '').strip()
    video_id = (request.args.get('video_id') or '').strip()
    base_url = sanitize_base_url(request.args.get('base_url'))

    if not api_key:
        return jsonify({'success': False, 'message': '??? API Key'}), 400
    lookup_id = video_id or task_id
    if not lookup_id:
        return jsonify({'success': False, 'message': '??? task_id ? video_id'}), 400

    local_task = find_task(session_id, task_id=task_id, video_id=video_id)
    if local_task and not video_id:
        lookup_id = local_task.get('video_id') or lookup_id

    try:
        response = requests.get(
            f"{base_url}/v1/videos/{lookup_id}",
            headers=build_headers(api_key),
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return jsonify({'success': False, 'message': f'????: {exc}'}), 502

    if response.status_code >= 400:
        return forward_error_response(response)

    remote = response.json()
    merged = {
        **(local_task or {}),
        'session_id': session_id,
        'task_id': (local_task or {}).get('task_id') or remote.get('id'),
        'video_id': remote.get('id') or lookup_id,
        'status': remote.get('status', (local_task or {}).get('status', 'queued')),
        'progress': remote.get('progress', (local_task or {}).get('progress', 0)),
        'prompt': remote.get('prompt', (local_task or {}).get('prompt', '')),
        'model': remote.get('model', (local_task or {}).get('model', '')),
        'size': remote.get('size', (local_task or {}).get('size', '')),
        'seconds': remote.get('seconds', (local_task or {}).get('seconds')),
        'quality': remote.get('quality', (local_task or {}).get('quality', '')),
        'video_url': remote.get('video_url', (local_task or {}).get('video_url')),
        'error': remote.get('error', (local_task or {}).get('error')),
        'created_at': (local_task or {}).get('created_at', utc_now_iso()),
    }
    upsert_task(session_id, merged)

    result = jsonify({'success': True, 'task': merged})
    return set_video_session_cookie(result, session_id)


@app.route('/api/video/list', methods=['GET', 'OPTIONS'])
def list_videos():
    if request.method == 'OPTIONS':
        return ('', 204)

    session_id = get_video_session_id()
    response = jsonify({'success': True, 'session_id': session_id, 'tasks': load_tasks(session_id)})
    return set_video_session_cookie(response, session_id)


@app.route('/api/session/state', methods=['GET', 'POST', 'DELETE', 'OPTIONS'])
def session_state():
    if request.method == 'OPTIONS':
        return ('', 204)

    session_id = get_video_session_id()

    if request.method == 'GET':
        response = jsonify({
            'success': True,
            'session_id': session_id,
            'draft': load_draft(session_id),
            'tasks': load_tasks(session_id),
        })
        return set_video_session_cookie(response, session_id)

    if request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        draft = {
            'api_key': payload.get('api_key', ''),
            'base_url': payload.get('base_url', DEFAULT_VIDEO_BASE_URL),
            'prompt': payload.get('prompt', ''),
            'size': payload.get('size', ''),
            'quality': payload.get('quality', ''),
            'model': payload.get('model', ''),
            'seconds': payload.get('seconds', ''),
            'enable_seconds': bool(payload.get('enable_seconds', False)),
            'references': payload.get('references', []),
            'updated_at': utc_now_iso(),
        }
        save_draft(session_id, draft)
        response = jsonify({'success': True, 'session_id': session_id, 'draft': draft})
        return set_video_session_cookie(response, session_id)

    clear_video_session_data(session_id)
    response = jsonify({'success': True, 'session_id': session_id, 'message': '?????????'})
    return set_video_session_cookie(response, session_id)


@app.route('/api/video/models', methods=['POST', 'OPTIONS'])
def list_video_models():
    if request.method == 'OPTIONS':
        return ('', 204)

    data = request.get_json(silent=True) or {}
    api_key = (data.get('api_key') or '').strip()
    base_url = sanitize_base_url(data.get('base_url'))

    if not api_key:
        return jsonify({'success': False, 'message': '??? API Key'}), 400

    models, error = fetch_video_models(api_key, base_url)
    return jsonify({'success': True, 'models': models, 'fallback': error is not None, 'error': error})


@app.route('/api/cleanup', methods=['POST'])
def cleanup_sessions():
    """清理过期 Session（可选的管理接口）"""
    data = request.json or {}
    max_age_hours = int(data.get('max_age_hours', 24))
    
    now = time.time()
    cutoff = now - (max_age_hours * 3600)
    cleaned = 0
    
    import shutil
    
    for state_file in Path(SESSIONS_DIR).glob("*.json"):
        try:
            with open(state_file, 'r') as f:
                state = json.load(f)
            
            last_accessed = state.get('last_accessed', state.get('created_at', 0))
            if last_accessed < cutoff:
                session_id = state_file.stem
                
                # 删除状态文件
                state_file.unlink()
                
                # 删除图片目录
                session_dir = get_session_dir(session_id)
                if session_dir.exists():
                    shutil.rmtree(session_dir)
                
                # 清除内存
                with session_lock:
                    if session_id in session_states:
                        del session_states[session_id]
                
                cleaned += 1
                logging.info(f"[CLEANUP] 清理过期 Session: {session_id[:8]}...")
        except Exception as e:
            logging.error(f"[CLEANUP] 清理 Session 失败：{e}")
    
    return jsonify({
        'success': True,
        'cleaned_count': cleaned,
        'max_age_hours': max_age_hours
    })


if __name__ == "__main__":
    print("Starting Gemini Image Tool (Flask Version) - Multi-User Isolated...")
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Sessions directory: {SESSIONS_DIR}")
    print(f"Default Base URL: {DEFAULT_BASE_URL}")
    print(f"Default Model: {DEFAULT_MODEL}")
    print("Open http://localhost:7860 in your browser")
    app.run(host="0.0.0.0", port=7860, debug=False)
