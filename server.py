#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini 图片生成与编辑工具 - Flask 版本 (多用户隔离版)
纯 HTML/CSS/JS 前端，Flask 后端 API
支持完整的 Session 隔离，防止多用户数据混淆
"""

import os
import base64
import copy
import requests
import time
import uuid
import json
import logging
import sys
import hashlib
import shutil
import html as html_lib
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
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemini-3.1-flash-image-preview-2k")
GEMINI_RESPONSE_MODALITIES = [
    item.strip()
    for item in os.getenv("GEMINI_RESPONSE_MODALITIES", "TEXT,IMAGE").split(",")
    if item.strip()
]
SESSION_COOKIE_NAME = "gemini_session_id"
SESSION_COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7 天
VIDEO_SESSION_COOKIE_NAME = "video_tool_session_id"
IMAGE_SESSION_COOKIE_NAME = "image_session_id"
DEFAULT_VIDEO_BASE_URL = os.getenv("DEFAULT_VIDEO_BASE_URL", DEFAULT_BASE_URL)
DEFAULT_VIDEO_MODEL = os.getenv("DEFAULT_VIDEO_MODEL", "grok-imagine-1.0-video")
DEFAULT_GPT_IMAGE_BASE_URL = os.getenv("DEFAULT_GPT_IMAGE_BASE_URL", DEFAULT_BASE_URL)
DEFAULT_GPT_IMAGE_MODEL = os.getenv("DEFAULT_GPT_IMAGE_MODEL", "gpt-image-2-flatfee")
DEFAULT_REQUEST_TIMEOUT = int(os.getenv("DEFAULT_REQUEST_TIMEOUT", "60"))
GEMINI_OUTPUT_SUBDIR = "gemini"
IMAGE_SESSION_SUBDIR = "image"
GPT_IMAGE_OUTPUT_SUBDIR = "gpt"
GPT_IMAGE_SESSION_SUBDIR = "gpt"
GPT_IMAGE_SESSION_COOKIE_NAME = "gpt_image_session_id"


# 确保目录存在
Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
Path(SESSIONS_DIR).mkdir(parents=True, exist_ok=True)

# 线程安全的 Session 状态存储
session_states = {}  # {session_id: {"last_image": str, "created_at": float}}
session_lock = threading.Lock()
file_lock = threading.Lock()
gemini_tasks = {}
gemini_task_lock = threading.Lock()
gpt_image_tasks = {}
gpt_image_task_lock = threading.Lock()
image_tasks = {}
image_task_lock = threading.Lock()


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
    """Return the session output directory."""
    session_dir = Path(OUTPUT_DIR) / GEMINI_OUTPUT_SUBDIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def get_session_state_path(session_id):
    """Return the session state file path."""
    return Path(SESSIONS_DIR) / f"{session_id}.json"


def load_session_state(session_id):
    """Load session state from memory or disk."""
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
            "request_count": 0,
            "history": [],
            "draft": {}
        }
        session_states[session_id] = state
        return state


def save_session_state(session_id, state):
    """Persist session state to disk."""
    with session_lock:
        session_states[session_id] = state
        state_path = get_session_state_path(session_id)
        try:
            with open(state_path, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logging.error(f"保存 Session 状态失败：{e}")


def create_gemini_task(session_id, mode, payload):
    task_id = str(uuid.uuid4())
    now = utc_now_iso()
    task = {
        'task_id': task_id,
        'session_id': session_id,
        'mode': mode,
        'status': 'queued',
        'success': False,
        'message': 'Task queued',
        'created_at': now,
        'updated_at': now,
        'result': None,
        'error': None,
    }
    with gemini_task_lock:
        gemini_tasks[task_id] = task

    worker = threading.Thread(
        target=run_gemini_task,
        args=(task_id, session_id, mode, copy.deepcopy(payload)),
        daemon=True,
    )
    worker.start()
    return task


def update_gemini_task(task_id, **updates):
    updates['updated_at'] = utc_now_iso()
    with gemini_task_lock:
        task = gemini_tasks.get(task_id)
        if not task:
            return None
        task.update(updates)
        return dict(task)


def get_gemini_task(task_id):
    with gemini_task_lock:
        task = gemini_tasks.get(task_id)
        return dict(task) if task else None


def create_gpt_image_task(session_id, mode, payload):
    task_id = str(uuid.uuid4())
    now = utc_now_iso()
    task = {
        'task_id': task_id,
        'session_id': session_id,
        'mode': mode,
        'status': 'queued',
        'success': False,
        'message': 'Task queued',
        'created_at': now,
        'updated_at': now,
        'result': None,
        'error': None,
    }
    with gpt_image_task_lock:
        gpt_image_tasks[task_id] = task

    worker = threading.Thread(
        target=run_gpt_image_task,
        args=(task_id, session_id, mode, copy.deepcopy(payload)),
        daemon=True,
    )
    worker.start()
    return task


def update_gpt_image_task(task_id, **updates):
    updates['updated_at'] = utc_now_iso()
    with gpt_image_task_lock:
        task = gpt_image_tasks.get(task_id)
        if not task:
            return None
        task.update(updates)
        return dict(task)


def get_gpt_image_task(task_id):
    with gpt_image_task_lock:
        task = gpt_image_tasks.get(task_id)
        return dict(task) if task else None


def create_image_task(session_id, mode, payload):
    task_id = str(uuid.uuid4())
    now = utc_now_iso()
    task = {
        'task_id': task_id,
        'session_id': session_id,
        'mode': mode,
        'provider': '',
        'status': 'queued',
        'success': False,
        'message': 'Task queued',
        'created_at': now,
        'updated_at': now,
        'result': None,
        'error': None,
    }
    with image_task_lock:
        image_tasks[task_id] = task

    worker = threading.Thread(
        target=run_image_task,
        args=(task_id, session_id, mode, copy.deepcopy(payload)),
        daemon=True,
    )
    worker.start()
    return task


def update_image_task(task_id, **updates):
    updates['updated_at'] = utc_now_iso()
    with image_task_lock:
        task = image_tasks.get(task_id)
        if not task:
            return None
        task.update(updates)
        return dict(task)


def get_image_task(task_id):
    with image_task_lock:
        task = image_tasks.get(task_id)
        return dict(task) if task else None


def normalize_unified_base_size(base_size, resolved_size=''):
    value = (base_size or '').strip()
    if value in {'1024x1024', '2048x2048', '3840x2160'}:
        return value
    dims = parse_image_size(resolved_size)
    if not dims:
        return '1024x1024'
    max_edge = max(dims)
    if max_edge >= 3000:
        return '3840x2160'
    if max_edge >= 1800:
        return '2048x2048'
    return '1024x1024'


def map_unified_base_size_to_gemini_resolution(base_size):
    mapping = {
        '1024x1024': '1K',
        '2048x2048': '2K',
        '3840x2160': '4K',
    }
    return mapping.get(normalize_unified_base_size(base_size), '1K')


def build_data_url_from_bytes(content, filename):
    mime_type = get_image_mime_type(filename or 'reference.png')
    encoded = base64.b64encode(content).decode('utf-8')
    return f'data:{mime_type};base64,{encoded}'


def run_unified_gpt_image_task(session_id, mode, payload):
    data = dict(payload.get('data') or {})
    effective_auth = build_effective_auth(data)
    data['api_key'] = effective_auth['api_key']
    data['base_url'] = effective_auth['base_url'] or data.get('base_url') or DEFAULT_GPT_IMAGE_BASE_URL
    cookie_header = f'{GPT_IMAGE_SESSION_COOKIE_NAME}={session_id}'
    if mode == 'generate':
        with app.test_request_context(
            '/api/gpt-image/generate',
            method='POST',
            json=data,
            headers={'Cookie': cookie_header},
            base_url=payload.get('origin') or None,
        ):
            return normalize_flask_result(gpt_image_generate())

    multipart_data = dict(data)
    has_reference_files = any((item.get('field_name') or '').startswith('image') for item in payload.get('files') or [])
    if str(multipart_data.get('use_last_image') or '').lower() == 'true' and not has_reference_files:
        last_image_path = find_reusable_image_path_for_unified_session(session_id)
        if not last_image_path:
            return 400, {'success': False, 'message': 'No reusable image found in the current unified image session'}
        with Path(last_image_path).open('rb') as f:
            image_bytes = f.read()
        payload.setdefault('files', []).append({
            'field_name': 'image',
            'filename': Path(last_image_path).name,
            'content': image_bytes,
            'mimetype': get_image_mime_type(last_image_path),
        })
        multipart_data['use_last_image'] = 'false'

    for item in payload.get('files') or []:
        field_name = item.get('field_name') or 'image[]'
        current = multipart_data.get(field_name)
        file_tuple = (BytesIO(item['content']), item.get('filename') or 'reference.jpg')
        if current is None:
            multipart_data[field_name] = [file_tuple]
        elif isinstance(current, list):
            current.append(file_tuple)
        else:
            multipart_data[field_name] = [current, file_tuple]

    with app.test_request_context(
        '/api/gpt-image/edit',
        method='POST',
        data=multipart_data,
        content_type='multipart/form-data',
        headers={'Cookie': cookie_header},
        base_url=payload.get('origin') or None,
    ):
        return normalize_flask_result(gpt_image_edit())


def run_unified_gemini_image_task(session_id, mode, payload):
    data = dict(payload.get('data') or {})
    effective_auth = build_effective_auth(data)
    data['api_key'] = effective_auth['api_key']
    data['base_url'] = effective_auth['base_url'] or data.get('base_url') or DEFAULT_BASE_URL
    aspect_ratio_raw = (data.get('aspect_ratio') or '').strip()
    aspect_ratio = normalize_gemini_aspect_ratio(aspect_ratio_raw)
    if aspect_ratio_raw and not aspect_ratio:
        return 400, {'success': False, 'message': f'Gemini provider does not support aspect ratio `{aspect_ratio_raw}`'}

    base_size = normalize_unified_base_size(data.get('base_size') or data.get('size_base'), data.get('size') or '')
    request_payload = {
        'api_key': data.get('api_key', ''),
        'base_url': data.get('base_url', DEFAULT_BASE_URL),
        'model_id': data.get('model') or data.get('model_id') or DEFAULT_MODEL,
        'prompt': data.get('prompt', ''),
        'negative_prompt': data.get('negative_prompt', ''),
        'image_count': int(data.get('image_count', 1) or 1),
        'resolution': map_unified_base_size_to_gemini_resolution(base_size),
        'aspect_ratio': aspect_ratio or '',
    }
    cookie_header = f'{SESSION_COOKIE_NAME}={session_id}'

    if mode == 'generate':
        with app.test_request_context('/api/text-to-image', method='POST', json=request_payload, headers={'Cookie': cookie_header}):
            return normalize_flask_result(text_to_image())

    image_data_list = []
    for item in payload.get('files') or []:
        if not (item.get('field_name') or '').startswith('image'):
            continue
        image_data_list.append(build_data_url_from_bytes(item['content'], item.get('filename') or 'reference.png'))

    if str(data.get('use_last_image') or '').lower() == 'true' and not image_data_list:
        last_image_path = find_reusable_image_path_for_unified_session(session_id)
        if not last_image_path:
            return 400, {'success': False, 'message': 'No reusable image found in the current unified image session'}
        with Path(last_image_path).open('rb') as f:
            image_data_list.append(build_data_url_from_bytes(f.read(), Path(last_image_path).name))

    request_payload['image_data_list'] = image_data_list
    with app.test_request_context('/api/image-to-image', method='POST', json=request_payload, headers={'Cookie': cookie_header}):
        return normalize_flask_result(image_to_image())


def run_image_task(task_id, session_id, mode, payload):
    update_image_task(task_id, status='running', message='Task running')
    data = payload.get('data') or {}
    model_name = data.get('model') or data.get('model_id') or ''
    provider = resolve_image_provider(model_name, data.get('base_url') or '')
    effective_auth = build_effective_auth(data)
    prompt = (data.get('prompt') or '').strip()
    if not provider:
        update_image_task(
            task_id,
            provider='',
            status='failed',
            success=False,
            message=f'Unable to resolve image provider from model `{model_name}`',
            error={'success': False, 'message': f'Unable to resolve image provider from model `{model_name}`'},
            finished_at=utc_now_iso(),
        )
        return

    try:
        if provider == 'gpt':
            status_code, result = run_unified_gpt_image_task(session_id, mode, payload)
        else:
            status_code, result = run_unified_gemini_image_task(session_id, mode, payload)

        success = bool(result.get('success')) and status_code < 400
        if success:
            sync_image_session_from_result(session_id, provider, mode, prompt, result)
        update_image_task(
            task_id,
            provider=provider,
            status='succeeded' if success else 'failed',
            success=success,
            message=result.get('message') or result.get('error') or ('Task completed' if success else 'Task failed'),
            result=result if success else None,
            error=None if success else result,
            finished_at=utc_now_iso(),
        )
    except Exception as exc:
        logging.exception('Unified image async task failed: %s', task_id)
        update_image_task(
            task_id,
            provider=provider,
            status='failed',
            success=False,
            message=f'Error: {exc}',
            error={'success': False, 'message': str(exc)},
            finished_at=utc_now_iso(),
        )


def run_gpt_image_task(task_id, session_id, mode, payload):
    update_gpt_image_task(task_id, status='running', message='Task running')
    try:
        cookie_header = f'{GPT_IMAGE_SESSION_COOKIE_NAME}={session_id}'
        if mode == 'generate':
            with app.test_request_context(
                '/api/gpt-image/generate',
                method='POST',
                json=payload.get('data') or {},
                headers={'Cookie': cookie_header},
                base_url=payload.get('origin') or None,
            ):
                status_code, data = normalize_flask_result(gpt_image_generate())
        else:
            form_data = dict(payload.get('data') or {})
            file_items = []
            for item in payload.get('files') or []:
                file_items.append((BytesIO(item['content']), item.get('filename') or 'reference.jpg'))
            if file_items:
                form_data['image[]'] = file_items
            with app.test_request_context(
                '/api/gpt-image/edit',
                method='POST',
                data=form_data,
                content_type='multipart/form-data',
                headers={'Cookie': cookie_header},
                base_url=payload.get('origin') or None,
            ):
                status_code, data = normalize_flask_result(gpt_image_edit())

        success = bool(data.get('success')) and status_code < 400
        update_gpt_image_task(
            task_id,
            status='succeeded' if success else 'failed',
            success=success,
            message=data.get('message') or data.get('error') or ('Task completed' if success else 'Task failed'),
            result=data if success else None,
            error=None if success else data,
            finished_at=utc_now_iso(),
        )
    except Exception as exc:
        logging.exception('GPT image async task failed: %s', task_id)
        update_gpt_image_task(
            task_id,
            status='failed',
            success=False,
            message=f'Error: {exc}',
            error={'success': False, 'message': str(exc)},
            finished_at=utc_now_iso(),
        )


def normalize_flask_result(result):
    status_code = 200
    response = result
    if isinstance(result, tuple):
        response = result[0]
        if len(result) > 1 and isinstance(result[1], int):
            status_code = result[1]
    if hasattr(response, 'status_code'):
        status_code = response.status_code
    if hasattr(response, 'get_json'):
        data = response.get_json(silent=True)
        if data is not None:
            return status_code, data
    if hasattr(response, 'get_data'):
        return status_code, {'success': False, 'message': response.get_data(as_text=True)[:500]}
    return status_code, {'success': False, 'message': str(response)[:500]}


def run_gemini_task(task_id, session_id, mode, payload):
    update_gemini_task(task_id, status='running', message='Task running')
    endpoint = '/api/text-to-image' if mode == 'text' else '/api/image-to-image'
    view_func = text_to_image if mode == 'text' else image_to_image
    try:
        cookie_header = f'{SESSION_COOKIE_NAME}={session_id}'
        with app.test_request_context(endpoint, method='POST', json=payload, headers={'Cookie': cookie_header}):
            status_code, data = normalize_flask_result(view_func())

        success = bool(data.get('success')) and status_code < 400
        update_gemini_task(
            task_id,
            status='succeeded' if success else 'failed',
            success=success,
            message=data.get('message') or data.get('error') or ('Task completed' if success else 'Task failed'),
            result=data if success else None,
            error=None if success else data,
            finished_at=utc_now_iso(),
        )
    except Exception as exc:
        logging.exception('Gemini async task failed: %s', task_id)
        update_gemini_task(
            task_id,
            status='failed',
            success=False,
            message=f'Error: {exc}',
            error={'success': False, 'message': str(exc)},
            finished_at=utc_now_iso(),
        )


def update_session_last_image(session_id, image_path):
    """Update the latest image for a session."""
    state = load_session_state(session_id)
    state["last_image"] = str(image_path)
    state["request_count"] = state.get("request_count", 0) + 1
    state["last_accessed"] = time.time()
    save_session_state(session_id, state)


def build_gemini_session_payload(session_id):
    state = load_session_state(session_id)
    history = state.get('history', [])
    return {
        'session_id': session_id,
        'created_at': state.get('created_at'),
        'request_count': state.get('request_count', 0),
        'has_last_image': state.get('last_image') is not None,
        'history': history if isinstance(history, list) else [],
        'last_image_url': f"/output/{session_id}/{Path(state['last_image']).name}" if state.get('last_image') else None,
        'draft': state.get('draft') or {},
    }


def append_gemini_history(session_id, mode, prompt, image_names):
    state = load_session_state(session_id)
    history = state.get('history', [])
    if not isinstance(history, list):
        history = []
    entry = {
        'mode': mode,
        'prompt': prompt,
        'images': [f'/output/{session_id}/{name}' for name in image_names],
        'created_at': utc_now_iso(),
    }
    history.insert(0, entry)
    state['history'] = history[:20]
    state['last_accessed'] = time.time()
    save_session_state(session_id, state)
    return entry


def rebuild_gemini_last_image_state(state, session_id):
    history = state.get('history', [])
    session_dir = get_session_dir(session_id)

    for entry in history:
        if not isinstance(entry, dict):
            continue
        images = entry.get('images')
        if not isinstance(images, list) or not images:
            continue
        first_image_url = images[0]
        filename = Path(first_image_url).name
        image_path = session_dir / filename
        if image_path.exists():
            state['last_image'] = str(image_path)
            return state

    state['last_image'] = None
    return state


def delete_gemini_history_image(session_id, image_url):
    image_name = Path((image_url or '').strip()).name
    if not image_name:
        return False, 'Invalid image reference'

    session_dir = get_session_dir(session_id)
    target_path = session_dir / image_name
    if not target_path.exists():
        return False, 'Image not found'

    state = load_session_state(session_id)
    history = state.get('history', [])
    updated_history = []
    removed = False

    for entry in history:
        if not isinstance(entry, dict):
            continue
        images = entry.get('images')
        if not isinstance(images, list):
            continue
        remaining_images = []
        for item in images:
            if Path(str(item)).name == image_name:
                removed = True
                continue
            remaining_images.append(item)
        if remaining_images:
            next_entry = dict(entry)
            next_entry['images'] = remaining_images
            updated_history.append(next_entry)

    if not removed:
        return False, 'Image not found in history'

    target_path.unlink(missing_ok=True)
    state['history'] = updated_history[:20]
    state['last_accessed'] = time.time()
    rebuild_gemini_last_image_state(state, session_id)
    save_session_state(session_id, state)
    return True, None


def save_gemini_draft(session_id, payload):
    state = load_session_state(session_id)
    draft = {
        'api_key': payload.get('api_key', ''),
        'base_url': payload.get('base_url', DEFAULT_BASE_URL),
        'model_id': payload.get('model_id', DEFAULT_MODEL),
        'txt_prompt': payload.get('txt_prompt', ''),
        'txt_negative': payload.get('txt_negative', ''),
        'txt_resolution': payload.get('txt_resolution', ''),
        'txt_aspect_ratio': payload.get('txt_aspect_ratio', ''),
        'txt_use_last': bool(payload.get('txt_use_last', False)),
        'img_prompt': payload.get('img_prompt', ''),
        'img_negative': payload.get('img_negative', ''),
        'img_resolution': payload.get('img_resolution', ''),
        'img_aspect_ratio': payload.get('img_aspect_ratio', ''),
        'generate_count': str(payload.get('generate_count', '1')),
        'uploaded_images': payload.get('uploaded_images', [])[:4],
        'updated_at': utc_now_iso(),
    }
    state['draft'] = draft
    state['last_accessed'] = time.time()
    save_session_state(session_id, state)
    return draft


def encode_image_to_base64(image_path, max_size=384, jpeg_quality=60):
    """将图片编码为 base64，自动压缩。
    
    优化参数减少 413 错误：
    - max_size: 512 -> 384 (减小 39% 面积)
    - jpeg_quality: 75 -> 60 (减小约 30% 体积)
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


IMAGE_SIZE_PRESETS = {
    '512': {
        '1:1': (512, 512),
        '16:9': (896, 512),
        '9:16': (512, 896),
        '4:3': (640, 480),
        '3:4': (480, 640),
    },
    '1K': {
        '1:1': (1024, 1024),
        '2:3': (768, 1152),
        '3:2': (1152, 768),
        '16:9': (1280, 720),
        '9:16': (720, 1280),
        '4:3': (1024, 768),
        '3:4': (768, 1024),
    },
    '2K': {
        '1:1': (2048, 2048),
        '2:3': (1456, 2176),
        '3:2': (2176, 1456),
        '16:9': (2048, 1152),
        '9:16': (1152, 2048),
        '4:3': (2048, 1536),
        '3:4': (1536, 2048),
    },
    '4K': {
        '1:1': (2048, 2048),
        '2:3': (2176, 3264),
        '3:2': (3264, 2176),
        '16:9': (3840, 2160),
        '9:16': (2160, 3840),
        '4:3': (3264, 2448),
        '3:4': (2448, 3264),
    },
}

GPT_UPSCALE_SIZE_PRESETS = {
    '2K': {
        '1:1': (2048, 2048),
        '2:3': (1456, 2176),
        '3:2': (2176, 1456),
        '16:9': (1920, 1088),
        '9:16': (1088, 1920),
        '4:3': (2048, 1536),
        '3:4': (1536, 2048),
    },
    '4K': {
        '1:1': (2880, 2880),
        '2:3': (2176, 3264),
        '3:2': (3264, 2176),
        '16:9': (3840, 2160),
        '9:16': (2160, 3840),
        '4:3': (3264, 2448),
        '3:4': (2448, 3264),
    },
}


def get_target_dimensions(size_label, aspect_ratio):
    return IMAGE_SIZE_PRESETS.get(size_label, {}).get(aspect_ratio)


def normalize_gemini_image_size(resolution):
    resolution_map = {
        '512 (512x512)': '512',
        '1K (1024x1024)': '1K',
        '2K (2048x2048)': '2K',
        '4K (4096x4096)': '4K',
    }
    value = (resolution or '').strip()
    if not value or value.lower() in {'auto', 'none', 'default'}:
        return None
    return resolution_map.get(value, value if value in IMAGE_SIZE_PRESETS else None)


def normalize_gemini_aspect_ratio(aspect_ratio):
    value = (aspect_ratio or '').strip()
    if not value or value.lower() in {'auto', 'none', 'default'}:
        return None
    return value if value in {'1:1', '16:9', '9:16', '4:3', '3:4'} else None


def build_gemini_generation_config(image_size=None, aspect_ratio=None):
    config = {'responseModalities': GEMINI_RESPONSE_MODALITIES or ['TEXT', 'IMAGE']}
    image_config = {}
    if aspect_ratio:
        image_config['aspectRatio'] = aspect_ratio
    if image_size:
        image_config['imageSize'] = image_size
    if image_config:
        config['imageConfig'] = image_config
    return config


def build_gemini_payload(parts, image_size=None, aspect_ratio=None):
    return {
        'contents': [{
            'role': 'user',
            'parts': parts,
        }],
        'generationConfig': build_gemini_generation_config(image_size, aspect_ratio),
    }


def parse_image_size(size):
    match = re.fullmatch(r'(\d+)x(\d+)', (size or '').strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def resize_image_to_target(image, target_size):
    if not target_size or image.size == target_size:
        return image
    if image.mode not in ('RGB', 'RGBA'):
        image = image.convert('RGB')
    return image.resize(target_size, Image.Resampling.LANCZOS)


def resize_saved_image_to_target(image_path, target_size):
    if not target_size:
        return
    image = Image.open(image_path)
    image = resize_image_to_target(image, target_size)
    image.save(image_path)


def summarize_html_error(text):
    if not text:
        return ''

    for pattern in (
        r'<title[^>]*>(.*?)</title>',
        r'<h1[^>]*>(.*?)</h1>',
    ):
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            summary = re.sub(r'<[^>]+>', ' ', match.group(1))
            summary = html_lib.unescape(re.sub(r'\s+', ' ', summary)).strip()
            if summary:
                return summary[:200]

    stripped = re.sub(r'<[^>]+>', ' ', text)
    stripped = html_lib.unescape(re.sub(r'\s+', ' ', stripped)).strip()
    return stripped[:200]


def forward_raw_upstream_error(response):
    try:
        payload = response.json()
        if isinstance(payload, dict):
            payload = dict(payload)
            payload.setdefault('success', False)
            if not payload.get('message'):
                payload['message'] = response.text[:500] or f'HTTP {response.status_code}'
            return jsonify(payload), response.status_code
        return jsonify({
            'success': False,
            'message': response.text[:500] or f'HTTP {response.status_code}',
            'detail': payload,
        }), response.status_code
    except Exception:
        text = response.text or ''
        return jsonify({
            'success': False,
            'message': text[:500] or f'HTTP {response.status_code}',
            'detail': text[:2000] or '',
        }), response.status_code


def iter_gemini_auth_headers(api_key):
    token = (api_key or '').strip()
    if not token:
        return []

    plain_token = token[7:].strip() if token.lower().startswith('bearer ') else token
    candidates = []

    def add(headers):
        if headers and headers not in candidates:
            candidates.append(headers)

    add({'Authorization': token if token.lower().startswith('bearer ') else f'Bearer {token}'})
    add({'X-Goog-Api-Key': plain_token})
    return candidates


def send_gemini_request(method, url, api_key, **kwargs):
    base_headers = dict(kwargs.pop('headers', {}) or {})
    auth_header_candidates = iter_gemini_auth_headers(api_key)

    last_response = None
    last_exception = None
    for index, auth_headers in enumerate(auth_header_candidates):
        try:
            response = requests.request(
                method,
                url,
                headers={**base_headers, **auth_headers},
                **kwargs,
            )
        except requests.RequestException as exc:
            last_exception = exc
            if index == len(auth_header_candidates) - 1:
                raise
            continue

        last_response = response
        if response.status_code not in {401, 403} or index == len(auth_header_candidates) - 1:
            return response

    if last_response is not None:
        return last_response
    if last_exception is not None:
        raise last_exception
    raise requests.RequestException('Gemini request failed without a response')


def post_gemini_request(url, payload, api_key):
    headers = {"Content-Type": "application/json"}
    return send_gemini_request('POST', url, api_key, json=payload, headers=headers, timeout=API_TIMEOUT)



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


def get_image_session_id():
    session_id = request.cookies.get(IMAGE_SESSION_COOKIE_NAME)
    if session_id:
        try:
            uuid.UUID(session_id)
            return session_id
        except ValueError:
            pass
    return str(uuid.uuid4())


def set_image_session_cookie(response, session_id):
    response.set_cookie(
        IMAGE_SESSION_COOKIE_NAME,
        session_id,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite='Lax'
    )
    return response


def get_image_session_dir(session_id):
    session_dir = Path(OUTPUT_DIR) / IMAGE_SESSION_SUBDIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def get_image_session_state_path(session_id):
    state_dir = Path(SESSIONS_DIR) / IMAGE_SESSION_SUBDIR
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / f'{session_id}.json'


def load_image_session_state(session_id):
    state_path = get_image_session_state_path(session_id)
    if state_path.exists():
        try:
            with state_path.open('r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logging.warning(f'Failed to load image session state for {session_id}: {exc}')
    return {
        'provider': '',
        'last_image': None,
        'created_at': time.time(),
        'last_accessed': time.time(),
        'request_count': 0,
        'last_prompt': '',
        'last_mode': '',
        'history': [],
        'draft': {},
    }


def save_image_session_state(session_id, state):
    state_path = get_image_session_state_path(session_id)
    with file_lock:
        with state_path.open('w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)


def get_gpt_session_id():
    session_id = request.cookies.get(GPT_IMAGE_SESSION_COOKIE_NAME)
    if session_id:
        try:
            uuid.UUID(session_id)
            return session_id
        except ValueError:
            pass
    return str(uuid.uuid4())


def set_gpt_session_cookie(response, session_id):
    response.set_cookie(
        GPT_IMAGE_SESSION_COOKIE_NAME,
        session_id,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite='Lax'
    )
    return response


def get_gpt_session_dir(session_id):
    session_dir = Path(OUTPUT_DIR) / GPT_IMAGE_OUTPUT_SUBDIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def get_gpt_session_state_path(session_id):
    state_dir = Path(SESSIONS_DIR) / GPT_IMAGE_SESSION_SUBDIR
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / f'{session_id}.json'


def load_gpt_session_state(session_id):
    state_path = get_gpt_session_state_path(session_id)
    if state_path.exists():
        try:
            with state_path.open('r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logging.warning(f'Failed to load GPT image session state for {session_id}: {exc}')
    return {
        'last_image': None,
        'created_at': time.time(),
        'last_accessed': time.time(),
        'request_count': 0,
        'last_prompt': '',
        'last_mode': '',
        'history': [],
        'draft': {},
    }


def save_gpt_session_state(session_id, state):
    state_path = get_gpt_session_state_path(session_id)
    with file_lock:
        with state_path.open('w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)


def update_gpt_session_last_image(session_id, image_path, prompt='', mode=''):
    state = load_gpt_session_state(session_id)
    state['last_image'] = str(image_path)
    state['last_prompt'] = prompt
    state['last_mode'] = mode
    state['last_accessed'] = time.time()
    state['request_count'] = state.get('request_count', 0) + 1
    save_gpt_session_state(session_id, state)


def append_gpt_history(session_id, entry):
    state = load_gpt_session_state(session_id)
    history = state.get('history', [])
    history.insert(0, entry)
    state['history'] = history[:20]
    state['last_accessed'] = time.time()
    save_gpt_session_state(session_id, state)


def rebuild_gpt_last_image_state(state, session_id):
    history = state.get('history', [])
    session_dir = get_gpt_session_dir(session_id)

    for entry in history:
        if not isinstance(entry, dict):
            continue
        images = entry.get('images')
        if not isinstance(images, list) or not images:
            continue
        first_image_url = images[0]
        filename = Path(first_image_url).name
        image_path = session_dir / filename
        if image_path.exists():
            state['last_image'] = str(image_path)
            state['last_prompt'] = entry.get('prompt', '')
            state['last_mode'] = entry.get('mode', '')
            return state

    state['last_image'] = None
    state['last_prompt'] = ''
    state['last_mode'] = ''
    return state


def delete_gpt_history_image(session_id, image_url):
    image_name = Path((image_url or '').strip()).name
    if not image_name:
        return False, 'Invalid image reference'

    session_dir = get_gpt_session_dir(session_id)
    target_path = session_dir / image_name
    if not target_path.exists():
        return False, 'Image not found'

    state = load_gpt_session_state(session_id)
    history = state.get('history', [])
    updated_history = []
    removed = False

    for entry in history:
        if not isinstance(entry, dict):
            continue
        images = entry.get('images')
        if not isinstance(images, list):
            continue
        remaining_images = []
        for item in images:
            if Path(str(item)).name == image_name:
                removed = True
                continue
            remaining_images.append(item)
        if remaining_images:
            next_entry = dict(entry)
            next_entry['images'] = remaining_images
            updated_history.append(next_entry)

    if not removed:
        return False, 'Image not found in history'

    target_path.unlink(missing_ok=True)
    state['history'] = updated_history[:20]
    state['last_accessed'] = time.time()
    rebuild_gpt_last_image_state(state, session_id)
    save_gpt_session_state(session_id, state)
    return True, None


def clear_gpt_session_data(session_id):
    state_path = get_gpt_session_state_path(session_id)
    if state_path.exists():
        state_path.unlink()
    session_dir = get_gpt_session_dir(session_id)
    if session_dir.exists():
        shutil.rmtree(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)


def resolve_local_image_path_from_url(image_url):
    raw = (image_url or '').strip()
    if not raw:
        return None
    path = raw.split('?', 1)[0]
    if path.startswith('/output/gpt/'):
        parts = path.split('/')
        if len(parts) >= 5:
            return Path(OUTPUT_DIR) / GPT_IMAGE_OUTPUT_SUBDIR / parts[3] / parts[4]
    if path.startswith('/output/'):
        parts = path.split('/')
        if len(parts) >= 4:
            session_id = parts[2]
            filename = parts[3]
            gemini_path = Path(OUTPUT_DIR) / GEMINI_OUTPUT_SUBDIR / session_id / filename
            if gemini_path.exists():
                return gemini_path
            legacy_path = Path(OUTPUT_DIR) / session_id / filename
            if legacy_path.exists():
                return legacy_path
            return gemini_path
    return None


def update_image_session_last_image(session_id, image_url, prompt='', mode='', provider='', image_path=None):
    state = load_image_session_state(session_id)
    resolved_path = Path(image_path) if image_path else resolve_local_image_path_from_url(image_url)
    state['provider'] = provider
    state['last_image'] = str(resolved_path) if resolved_path else None
    state['last_image_url'] = image_url
    state['last_prompt'] = prompt
    state['last_mode'] = mode
    state['last_accessed'] = time.time()
    state['request_count'] = state.get('request_count', 0) + 1
    save_image_session_state(session_id, state)


def append_image_history(session_id, entry):
    state = load_image_session_state(session_id)
    history = state.get('history', [])
    if not isinstance(history, list):
        history = []
    history.insert(0, entry)
    state['history'] = history[:20]
    state['last_accessed'] = time.time()
    save_image_session_state(session_id, state)


def build_image_session_payload(session_id):
    state = load_image_session_state(session_id)
    history = state.get('history', [])
    return {
        'success': True,
        'session_id': session_id,
        'provider': state.get('provider', ''),
        'created_at': state.get('created_at'),
        'request_count': state.get('request_count', 0),
        'has_last_image': bool(state.get('last_image')) or bool(state.get('last_image_url')),
        'last_prompt': state.get('last_prompt', ''),
        'last_mode': state.get('last_mode', ''),
        'history': history if isinstance(history, list) else [],
        'draft': state.get('draft') or {},
        'last_image_url': state.get('last_image_url') or '',
    }


def delete_image_history_image(session_id, image_url):
    state = load_image_session_state(session_id)
    image_name = Path((image_url or '').split('?', 1)[0]).name
    if not image_name:
        return False, 'Invalid image reference'

    history = state.get('history', [])
    if not isinstance(history, list):
        history = []

    removed = False
    updated_history = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        images = entry.get('images')
        if not isinstance(images, list):
            updated_history.append(entry)
            continue

        remaining_images = []
        for item in images:
            if Path(str(item).split('?', 1)[0]).name == image_name:
                removed = True
                continue
            remaining_images.append(item)
        if remaining_images:
            next_entry = dict(entry)
            next_entry['images'] = remaining_images
            updated_history.append(next_entry)

    if not removed:
        return False, 'Image not found in history'

    target_path = resolve_local_image_path_from_url(image_url)
    if target_path:
        try:
            target_path.unlink(missing_ok=True)
        except Exception:
            pass

    state['history'] = updated_history[:20]
    state['last_accessed'] = time.time()
    if Path(str(state.get('last_image_url', '')).split('?', 1)[0]).name == image_name:
        state['last_image'] = None
        state['last_image_url'] = ''
    save_image_session_state(session_id, state)
    return True, None


def clear_image_session_data(session_id):
    state = load_image_session_state(session_id)
    preserved_draft = state.get('draft') if isinstance(state.get('draft'), dict) else {}

    state.update({
        'last_image': None,
        'last_image_url': '',
        'last_prompt': '',
        'last_mode': '',
        'history': [],
        'request_count': 0,
        'last_accessed': time.time(),
        'draft': preserved_draft,
    })
    save_image_session_state(session_id, state)

    image_dir = get_image_session_dir(session_id)
    if image_dir.exists():
        shutil.rmtree(image_dir)
    image_dir.mkdir(parents=True, exist_ok=True)

    gemini_state_path = get_session_state_path(session_id)
    if gemini_state_path.exists():
        gemini_state_path.unlink()
    gemini_output_dir = get_session_dir(session_id)
    if gemini_output_dir.exists():
        shutil.rmtree(gemini_output_dir)
    gemini_output_dir.mkdir(parents=True, exist_ok=True)

    gpt_state_path = get_gpt_session_state_path(session_id)
    if gpt_state_path.exists():
        gpt_state_path.unlink()
    gpt_output_dir = get_gpt_session_dir(session_id)
    if gpt_output_dir.exists():
        shutil.rmtree(gpt_output_dir)
    gpt_output_dir.mkdir(parents=True, exist_ok=True)


def find_reusable_image_path_for_unified_session(session_id):
    state = load_image_session_state(session_id)

    candidates = []
    if state.get('last_image'):
        candidates.append(Path(str(state['last_image'])))

    if state.get('last_image_url'):
        resolved = resolve_local_image_path_from_url(state.get('last_image_url'))
        if resolved:
            candidates.append(resolved)

    history = state.get('history', [])
    if isinstance(history, list):
        for entry in history:
            if not isinstance(entry, dict):
                continue
            images = entry.get('images')
            if not isinstance(images, list):
                continue
            for item in images:
                resolved = resolve_local_image_path_from_url(item)
                if resolved:
                    candidates.append(resolved)

    for candidate in candidates:
        try:
            if candidate and Path(candidate).exists():
                return Path(candidate)
        except Exception:
            continue
    return None


def sync_image_session_from_result(session_id, provider, mode, prompt, result):
    images = result.get('images') if isinstance(result, dict) else None
    if not isinstance(images, list) or not images:
        return
    first_url = images[0]
    first_path = resolve_local_image_path_from_url(first_url)
    update_image_session_last_image(
        session_id,
        first_url,
        prompt=prompt,
        mode=mode,
        provider=provider,
        image_path=first_path,
    )
    append_image_history(session_id, {
        'created_at': utc_now_iso(),
        'provider': provider,
        'mode': mode,
        'prompt': prompt,
        'images': images,
    })


def download_image_to_session(image_url, output_path):
    response = requests.get(image_url, timeout=DEFAULT_REQUEST_TIMEOUT)
    response.raise_for_status()
    image = Image.open(io.BytesIO(response.content))
    image.save(output_path)
    return output_path


def save_base64_image_to_session(image_base64, output_path):
    image_bytes = base64.b64decode(image_base64)
    image = Image.open(io.BytesIO(image_bytes))
    image.save(output_path)
    return output_path


def build_gpt_image_headers(api_key):
    token = (api_key or '').strip()
    return {'Authorization': token}


def iter_gpt_image_auth_headers(api_key):
    token = (api_key or '').strip()
    if not token:
        return []

    candidates = []

    def add(value):
        if value and value not in candidates:
            candidates.append(value)

    if token.lower().startswith('bearer '):
        add(token)
        add(token[7:].strip())
    else:
        add(token)
        add(f'Bearer {token}')

    return [{'Authorization': value} for value in candidates]


def rewind_request_files(files):
    if not files:
        return
    for item in files:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        payload = item[1]
        if not isinstance(payload, tuple) or len(payload) < 2:
            continue
        file_obj = payload[1]
        if hasattr(file_obj, 'seek'):
            try:
                file_obj.seek(0)
            except Exception:
                pass


def send_gpt_request(method, url, api_key, **kwargs):
    base_headers = dict(kwargs.pop('headers', {}) or {})
    auth_header_candidates = iter_gpt_image_auth_headers(api_key) or [build_gpt_image_headers(api_key)]

    last_response = None
    last_exception = None
    for index, auth_headers in enumerate(auth_header_candidates):
        rewind_request_files(kwargs.get('files'))
        try:
            response = requests.request(
                method,
                url,
                headers={**base_headers, **auth_headers},
                **kwargs,
            )
        except requests.RequestException as exc:
            last_exception = exc
            if index == len(auth_header_candidates) - 1:
                raise
            continue

        last_response = response
        if response.status_code not in {401, 403} or index == len(auth_header_candidates) - 1:
            return response

    if last_response is not None:
        return last_response
    if last_exception is not None:
        raise last_exception
    raise requests.RequestException('GPT image request failed without a response')


def normalize_gpt_image_quality(quality):
    value = (quality or 'high').strip().lower()
    return value if value in {'auto', 'low', 'medium', 'high'} else 'high'


def normalize_gpt_image_size(size):
    match = re.fullmatch(r'(\d+)x(\d+)', (size or '').strip())
    if not match:
        return '1024x1024'
    width = int(match.group(1))
    height = int(match.group(2))
    width = max(16, ((width + 15) // 16) * 16)
    height = max(16, ((height + 15) // 16) * 16)

    max_edge = 3840
    min_pixels = 655_360
    max_pixels = 8_294_400

    long_edge = max(width, height)
    short_edge = min(width, height)
    if long_edge / short_edge > 3:
        if width >= height:
            height = ((int(width / 3) + 15) // 16) * 16
        else:
            width = ((int(height / 3) + 15) // 16) * 16

    if max(width, height) > max_edge:
        scale = max_edge / max(width, height)
        width = max(16, int(width * scale) // 16 * 16)
        height = max(16, int(height * scale) // 16 * 16)

    if width * height < min_pixels:
        scale = (min_pixels / (width * height)) ** 0.5
        width = max(16, ((int(width * scale) + 15) // 16) * 16)
        height = max(16, ((int(height * scale) + 15) // 16) * 16)

    if width * height > max_pixels:
        scale = (max_pixels / (width * height)) ** 0.5
        width = max(16, int(width * scale) // 16 * 16)
        height = max(16, int(height * scale) // 16 * 16)

    return f'{width}x{height}'


def normalize_gpt_aspect_ratio(aspect_ratio):
    value = (aspect_ratio or '1:1').strip()
    return value if value in {'1:1', '2:3', '3:2', '16:9', '9:16', '4:3', '3:4'} else '1:1'


def normalize_gpt_image_style(style):
    value = (style or '').strip().lower()
    return value if value in {'natural', 'vivid'} else ''


def normalize_gpt_background(background):
    value = (background or 'auto').strip().lower()
    return value if value in {'auto', 'opaque', 'transparent'} else 'auto'


def normalize_gpt_response_format(response_format):
    value = (response_format or 'b64_json').strip().lower()
    return value if value in {'url', 'b64_json'} else 'b64_json'


def normalize_gpt_upscale(upscale):
    value = (upscale or '').strip().lower()
    return value if value in {'2k', '4k'} else ''


def get_gpt_upscale_target(upscale, aspect_ratio):
    label = {'2k': '2K', '4k': '4K'}.get(normalize_gpt_upscale(upscale))
    if not label:
        return None
    return GPT_UPSCALE_SIZE_PRESETS.get(label, {}).get(aspect_ratio)


def choose_larger_target_size(*targets):
    valid_targets = [item for item in targets if item and len(item) == 2]
    if not valid_targets:
        return None
    return max(valid_targets, key=lambda item: item[0] * item[1])


def resolve_image_provider(model_name, base_url=''):
    model_value = (model_name or '').strip().lower()
    base_url_value = (base_url or '').strip().lower()
    if 'gemini' in model_value:
        return 'gemini'
    if 'gpt' in model_value or 'openai' in model_value:
        return 'gpt'
    if 'googleapis' in base_url_value or '/v1beta/models' in base_url_value:
        return 'gemini'
    if 'openai' in base_url_value or '/v1/images' in base_url_value or '/v1/models' in base_url_value:
        return 'gpt'
    return ''


def normalize_key_profiles(payload):
    profiles = payload.get('key_profiles') if isinstance(payload, dict) else None
    if isinstance(profiles, str):
        try:
            profiles = json.loads(profiles)
        except Exception:
            profiles = None
    if not isinstance(profiles, list):
        return []
    normalized = []
    for index, item in enumerate(profiles, start=1):
        if not isinstance(item, dict):
            continue
        api_key = (item.get('api_key') or '').strip()
        base_url = (item.get('base_url') or '').strip()
        if not api_key or not base_url:
            continue
        normalized.append({
            'id': (item.get('id') or f'key_{index}').strip(),
            'label': (item.get('label') or f'Key {index}').strip(),
            'api_key': api_key,
            'base_url': base_url.rstrip('/'),
        })
    return normalized


def build_effective_auth(payload):
    profiles = normalize_key_profiles(payload)
    payload_model = (payload.get('model') or payload.get('model_id') or '').strip() if isinstance(payload, dict) else ''
    model_key_map = payload.get('model_key_map') if isinstance(payload, dict) else {}
    if isinstance(model_key_map, str):
        try:
            model_key_map = json.loads(model_key_map)
        except Exception:
            model_key_map = {}
    if not isinstance(model_key_map, dict):
        model_key_map = {}
    selected_profile = None
    if payload_model and model_key_map:
        profile_id = (model_key_map.get(payload_model) or '').strip()
        if profile_id:
            selected_profile = next((item for item in profiles if item['id'] == profile_id), None)
    if not selected_profile and profiles:
        selected_profile = profiles[0]
    return {
        'api_key': (selected_profile or {}).get('api_key') or (payload.get('api_key') if isinstance(payload, dict) else '') or '',
        'base_url': (selected_profile or {}).get('base_url') or (payload.get('base_url') if isinstance(payload, dict) else '') or '',
        'profile_id': (selected_profile or {}).get('id') or '',
        'profile_label': (selected_profile or {}).get('label') or '',
        'profiles': profiles,
        'model_key_map': model_key_map if isinstance(model_key_map, dict) else {},
    }


def apply_gpt_layout_instruction(prompt, size, aspect_ratio):
    text = (prompt or '').strip()
    layout = f'画面比例必须为 {aspect_ratio}，输出方向和尺寸应为 {size}。'
    return f'{text}\n\n{layout}' if text else layout


def parse_markdown_image_urls(text):
    if not text:
        return []
    return re.findall(r'!\[[^\]]*\]\((https?://[^)]+)\)', str(text))


def get_uploaded_image_files(files):
    image_files = files.getlist('image[]')
    if image_files:
        return [item for item in image_files if getattr(item, 'filename', '')]
    image_files = files.getlist('image')
    return [item for item in image_files if getattr(item, 'filename', '')]


def get_uploaded_mask_file(files):
    mask_file = files.get('mask')
    if mask_file and getattr(mask_file, 'filename', ''):
        return mask_file
    return None


def summarize_upstream_error(response):
    content_type = (response.headers.get('Content-Type') or '').lower()
    text = response.text or ''
    if 'text/html' in content_type or '<html' in text[:500].lower():
        return summarize_html_error(text)
    try:
        return response.json()
    except Exception:
        return text[:500]


def fetch_gemini_models(api_key, base_url):
    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/v1beta/models"
    try:
        response = send_gemini_request('GET', url, api_key, timeout=15)
        response.raise_for_status()
        data = response.json()
        models = []
        if isinstance(data, dict) and isinstance(data.get('models'), list):
            for item in data['models']:
                if not isinstance(item, dict):
                    continue
                model_id = (item.get('name') or '').strip()
                if model_id.startswith('models/'):
                    model_id = model_id[7:]
                if model_id:
                    models.append(model_id)
        return sorted(dict.fromkeys(models)) if models else [DEFAULT_MODEL], None
    except Exception as exc:
        return [DEFAULT_MODEL], str(exc)


def fetch_gpt_models(api_key, base_url):
    candidates = [('/v1/models', 'GET')]
    last_error = None
    for path, method in candidates:
        url = f'{base_url}{path}'
        try:
            response = send_gpt_request(
                method,
                url,
                api_key,
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
                return sorted(dict.fromkeys(parsed)), None
        except Exception as exc:
            last_error = str(exc)
    return [DEFAULT_GPT_IMAGE_MODEL], last_error


def parse_gpt_image_urls(response_data):
    items = response_data.get('data') if isinstance(response_data, dict) else None
    if not isinstance(items, list):
        return []
    return [item.get('url') for item in items if isinstance(item, dict) and item.get('url')]


def parse_gpt_image_items(response_data):
    items = response_data.get('data') if isinstance(response_data, dict) else None
    if not isinstance(items, list):
        return []
    parsed = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get('url'):
            parsed.append({'type': 'url', 'value': item['url']})
        elif item.get('b64_json'):
            parsed.append({'type': 'b64_json', 'value': item['b64_json']})
    return parsed


def save_gpt_images(session_id, image_urls, prefix, prompt):
    session_dir = get_gpt_session_dir(session_id)
    saved_files = []
    timestamp = int(time.time())
    for index, image_url in enumerate(image_urls, start=1):
        output_path = session_dir / f'{prefix}_{timestamp}_{index}.png'
        download_image_to_session(image_url, output_path)
        saved_files.append(output_path.name)
    if saved_files:
        last_path = session_dir / saved_files[0]
        update_gpt_session_last_image(session_id, str(last_path), prompt=prompt, mode=prefix)
        append_gpt_history(session_id, {
            'created_at': utc_now_iso(),
            'mode': prefix,
            'prompt': prompt,
            'images': [f'/output/gpt/{session_id}/{name}' for name in saved_files],
        })
    return saved_files


def save_gpt_image_items(session_id, image_items, prefix, prompt, target_size=None):
    session_dir = get_gpt_session_dir(session_id)
    saved_files = []
    timestamp = int(time.time())
    for index, image_item in enumerate(image_items, start=1):
        output_path = session_dir / f'{prefix}_{timestamp}_{index}.png'
        if image_item['type'] == 'url':
            download_image_to_session(image_item['value'], output_path)
        elif image_item['type'] == 'b64_json':
            save_base64_image_to_session(image_item['value'], output_path)
        else:
            continue
        resize_saved_image_to_target(output_path, target_size)
        saved_files.append(output_path.name)
    if saved_files:
        last_path = session_dir / saved_files[0]
        update_gpt_session_last_image(session_id, str(last_path), prompt=prompt, mode=prefix)
        append_gpt_history(session_id, {
            'created_at': utc_now_iso(),
            'mode': prefix,
            'prompt': prompt,
            'images': [f'/output/gpt/{session_id}/{name}' for name in saved_files],
        })
    return saved_files


def build_gpt_session_payload(session_id):
    state = load_gpt_session_state(session_id)
    history = state.get('history', [])
    return {
        'session_id': session_id,
        'created_at': state.get('created_at'),
        'request_count': state.get('request_count', 0),
        'has_last_image': bool(state.get('last_image')),
        'last_prompt': state.get('last_prompt', ''),
        'last_mode': state.get('last_mode', ''),
        'history': history,
        'draft': state.get('draft') or {},
        'last_image_url': f"/output/gpt/{session_id}/{Path(state['last_image']).name}" if state.get('last_image') else None,
    }


def get_video_session_dir(session_id):
    session_dir = Path(SESSIONS_DIR) / 'video' / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def get_video_output_dir(session_id):
    output_dir = Path(OUTPUT_DIR) / 'video' / session_id
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


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
        same_video = item.get('video_id') and task.get('video_id') and item.get('video_id') == task.get('video_id')
        same_task = item.get('task_id') and task.get('task_id') and item.get('task_id') == task.get('task_id')
        if same_video or same_task:
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
        if task_id and task.get('video_id') == task_id:
            return task
        if video_id and task.get('video_id') == video_id:
            return task
        if video_id and task.get('task_id') == video_id:
            return task
    return None


def sanitize_base_url(base_url):
    return (base_url or DEFAULT_VIDEO_BASE_URL).strip().rstrip('/')


def build_headers(api_key):
    token = api_key.strip()
    return {'Authorization': token if token.lower().startswith('bearer ') else f'Bearer {token}'}


def first_present(mapping, keys):
    for key in keys:
        value = mapping.get(key)
        if value:
            return value
    return None


def extract_video_url(data):
    if not isinstance(data, dict):
        return None
    direct = first_present(data, ['video_url', 'videoUrl', 'url', 'content_url', 'contentUrl', 'download_url', 'downloadUrl'])
    if direct:
        return direct
    output = data.get('output') or data.get('result') or data.get('content')
    if isinstance(output, dict):
        return extract_video_url(output)
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict):
                url = extract_video_url(item)
                if url:
                    return url
            elif isinstance(item, str) and item.startswith(('http://', 'https://', '/')):
                return item
    data_items = data.get('data')
    if isinstance(data_items, list):
        for item in data_items:
            if isinstance(item, dict):
                url = extract_video_url(item)
                if url:
                    return url
    return None


def build_task_record(session_id, payload, response_data):
    task_id = first_present(response_data, ['task_id', 'taskId', 'task'])
    video_id = first_present(response_data, ['video_id', 'videoId', 'id'])
    if not task_id:
        task_id = video_id
    created_at = utc_now_iso()
    return {
        'session_id': session_id,
        'task_id': task_id,
        'video_id': video_id,
        'status': response_data.get('status', 'queued'),
        'progress': response_data.get('progress', 0),
        'created_at': created_at,
        'prompt': payload.get('prompt', ''),
        'model': payload.get('model', ''),
        'size': payload.get('size', ''),
        'seconds': payload.get('seconds'),
        'quality': payload.get('quality', ''),
        'video_url': extract_video_url(response_data),
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
    return forward_raw_upstream_error(response)


@app.route('/')
def index():
    video_session_id = get_video_session_id()
    image_session_id = get_image_session_id()
    response = make_response(render_template('index.html'))
    response = set_video_session_cookie(response, video_session_id)
    return set_image_session_cookie(response, image_session_id)


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


@app.route('/image-app')
def image_app():
    session_id = get_image_session_id()
    response = make_response(render_template(
        'image_app.html',
        session_id=session_id,
        default_base_url=DEFAULT_GPT_IMAGE_BASE_URL,
        default_model=DEFAULT_GPT_IMAGE_MODEL,
        api_prefix='/api/image',
        test_endpoint='/api/image/test',
        models_endpoint='/api/image/models',
        workspace_title='统一图片工作区',
        workspace_name='图片生成工作区',
        workspace_data_label='图片数据',
        session_results_hint='仅展示当前统一图片会话的本地图片结果。',
        session_scope_text='当前浏览器会话会统一保存图片生成记录，根据模型名称自动匹配 GPT 或 Gemini。',
        clear_confirm_text='确定要清空当前图片会话吗？这会删除本地结果和历史记录。',
        clear_success_text='已清空当前图片会话',
        file_name_prefix='image',
    ))
    return set_image_session_cookie(response, session_id)


@app.route('/output/<session_id>/<filename>')
def serve_output(session_id, filename):
    """Serve an output image for a session."""
    try:
        uuid.UUID(session_id)
    except ValueError:
        return jsonify({'error': 'Invalid session ID'}), 400

    session_dir = get_session_dir(session_id)

    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid filename'}), 400

    file_path = session_dir / filename
    if not file_path.exists():
        return jsonify({'error': 'File not found'}), 404

    return send_from_directory(str(session_dir), filename)


@app.route('/output/gpt/<session_id>/<filename>')
def serve_gpt_output(session_id, filename):
    try:
        uuid.UUID(session_id)
    except ValueError:
        return jsonify({'error': 'Invalid session ID'}), 400

    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid filename'}), 400

    session_dir = get_gpt_session_dir(session_id)
    file_path = session_dir / filename
    if not file_path.exists():
        return jsonify({'error': 'File not found'}), 404

    return send_from_directory(str(session_dir), filename)


@app.route('/output/video/<session_id>/<filename>')
def serve_video_output(session_id, filename):
    try:
        uuid.UUID(session_id)
    except ValueError:
        return jsonify({'error': 'Invalid session ID'}), 400

    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid filename'}), 400

    output_dir = get_video_output_dir(session_id)
    file_path = output_dir / filename
    if not file_path.exists():
        return jsonify({'error': 'File not found'}), 404

    return send_from_directory(str(output_dir), filename)


@app.route('/api/image/session', methods=['GET', 'POST'])
def get_image_session():
    session_id = get_image_session_id()

    if request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        state = load_image_session_state(session_id)
        state['draft'] = {
            'api_key': payload.get('api_key', ''),
            'base_url': payload.get('base_url', DEFAULT_GPT_IMAGE_BASE_URL),
            'model': payload.get('model', DEFAULT_GPT_IMAGE_MODEL),
            'key_profiles': normalize_key_profiles(payload),
            'model_key_map': payload.get('model_key_map', {}) if isinstance(payload.get('model_key_map'), dict) else {},
            'generate_prompt': payload.get('generate_prompt', ''),
            'generate_negative_prompt': payload.get('generate_negative_prompt', ''),
            'generate_size': payload.get('generate_size', '1024x1024'),
            'generate_aspect_ratio': payload.get('generate_aspect_ratio', '1:1'),
            'generate_count': payload.get('generate_count', '1'),
            'generate_quality': payload.get('generate_quality', 'high'),
            'generate_style': payload.get('generate_style', 'natural'),
            'generate_background': payload.get('generate_background', 'auto'),
            'generate_response_format': payload.get('generate_response_format', 'b64_json'),
            'generate_upscale': payload.get('generate_upscale', ''),
            'edit_prompt': payload.get('edit_prompt', ''),
            'edit_negative_prompt': payload.get('edit_negative_prompt', ''),
            'edit_size': payload.get('edit_size', '1024x1024'),
            'edit_aspect_ratio': payload.get('edit_aspect_ratio', '1:1'),
            'edit_count': payload.get('edit_count', '1'),
            'edit_quality': payload.get('edit_quality', 'high'),
            'edit_style': payload.get('edit_style', 'natural'),
            'edit_background': payload.get('edit_background', 'auto'),
            'edit_response_format': payload.get('edit_response_format', 'b64_json'),
            'edit_upscale': payload.get('edit_upscale', ''),
            'use_last_image': bool(payload.get('use_last_image', False)),
            'edit_references': payload.get('edit_references', [])[:4],
            'updated_at': utc_now_iso(),
        }
        state['last_accessed'] = time.time()
        save_image_session_state(session_id, state)
        response = jsonify(build_image_session_payload(session_id))
        return set_image_session_cookie(response, session_id)

    response = jsonify(build_image_session_payload(session_id))
    return set_image_session_cookie(response, session_id)


@app.route('/api/image/session/clear', methods=['POST'])
def clear_image_session():
    session_id = get_image_session_id()
    clear_image_session_data(session_id)
    response = jsonify({
        'success': True,
        'message': '当前图片历史已清空',
        'session': build_image_session_payload(session_id),
    })
    return set_image_session_cookie(response, session_id)


@app.route('/api/image/result', methods=['DELETE'])
def delete_image_result():
    session_id = get_image_session_id()
    payload = request.get_json(silent=True) or {}
    image_url = (payload.get('image_url') or '').strip()
    if not image_url:
        return jsonify({'success': False, 'message': '请提供要删除的图片'}), 400

    success, error = delete_image_history_image(session_id, image_url)
    if not success:
        status_code = 404 if error == 'Image not found in history' else 400
        return jsonify({'success': False, 'message': error}), status_code

    response = jsonify({
        'success': True,
        'message': '图片已删除',
        'session': build_image_session_payload(session_id),
    })
    return set_image_session_cookie(response, session_id)


@app.route('/api/image/tasks', methods=['POST'])
def create_image_task_endpoint():
    session_id = get_image_session_id()
    if request.content_type and request.content_type.startswith('multipart/form-data'):
        mode = (request.form.get('mode') or 'edit').strip().lower()
        payload = {'data': request.form.to_dict(), 'files': [], 'origin': request.host_url}
        for field_name, uploaded_file in request.files.items(multi=True):
            payload['files'].append({
                'field_name': field_name,
                'filename': uploaded_file.filename or 'reference.png',
                'content': uploaded_file.read(),
                'mimetype': getattr(uploaded_file, 'mimetype', None),
            })
    else:
        body = request.get_json(silent=True) or {}
        mode = (body.get('mode') or 'generate').strip().lower()
        payload_data = body.get('payload') if isinstance(body.get('payload'), dict) else body
        payload = {'data': payload_data or {}, 'files': [], 'origin': request.host_url}

    if mode not in {'generate', 'edit'}:
        return jsonify({'success': False, 'message': 'Invalid image task mode'}), 400

    data = payload.get('data') or {}
    model_name = (data.get('model') or data.get('model_id') or '').strip()
    provider = resolve_image_provider(model_name, data.get('base_url') or '')
    effective_auth = build_effective_auth(data)
    if not provider:
        return jsonify({'success': False, 'message': f'无法根据模型名识别 provider：{model_name}'}), 400
    if not effective_auth['api_key']:
        return jsonify({'success': False, 'message': '请填写 API Key'}), 400
    if not model_name:
        return jsonify({'success': False, 'message': '请填写模型 ID'}), 400
    if not data.get('prompt'):
        return jsonify({'success': False, 'message': '请填写提示词'}), 400
    if mode == 'edit':
        use_last = str(data.get('use_last_image') or '').lower() == 'true'
        has_reference_files = any((item.get('field_name') or '').startswith('image') for item in payload.get('files') or [])
        if not use_last and not has_reference_files:
            return jsonify({'success': False, 'message': 'Please upload at least 1 reference image'}), 400

    task = create_image_task(session_id, mode, payload)
    response = jsonify({
        'success': True,
        'task_id': task['task_id'],
        'provider': provider,
        'status': 'queued',
        'message': '任务已提交，后台正在生成。',
    })
    return set_image_session_cookie(response, session_id)


@app.route('/api/image/tasks/<task_id>', methods=['GET'])
def get_image_task_endpoint(task_id):
    task = get_image_task(task_id)
    if not task:
        return jsonify({'success': False, 'message': 'Task not found'}), 404
    session_id = get_image_session_id()
    if task.get('session_id') != session_id:
        return jsonify({'success': False, 'message': 'Task not found'}), 404
    task_payload = dict(task)
    task_payload['task_success'] = bool(task_payload.pop('success', False))
    response = jsonify({'success': True, **task_payload})
    return set_image_session_cookie(response, session_id)


@app.route('/api/image/models', methods=['POST'])
def list_image_models():
    data = request.get_json(silent=True) or {}
    profiles = normalize_key_profiles(data)
    if not profiles:
        api_key = (data.get('api_key') or '').strip()
        base_url = (data.get('base_url') or DEFAULT_GPT_IMAGE_BASE_URL).strip().rstrip('/')
        if api_key and base_url:
            profiles = [{
                'id': 'key_1',
                'label': 'Key 1',
                'api_key': api_key,
                'base_url': base_url,
            }]
    if not profiles:
        return jsonify({'success': False, 'message': '请至少配置 1 组 API Key 与 Base URL'}), 400

    merged_models = []
    model_key_map = {}
    errors = {}

    for profile in profiles:
        profile_errors = {}
        gpt_models, gpt_error = fetch_gpt_models(profile['api_key'], profile['base_url'])
        if gpt_error is None:
            for model in gpt_models:
                if not model:
                    continue
                merged_models.append(model)
                model_key_map.setdefault(model, profile['id'])
        else:
            profile_errors['gpt'] = gpt_error

        gemini_models, gemini_error = fetch_gemini_models(profile['api_key'], profile['base_url'])
        if gemini_error is None:
            for model in gemini_models:
                if not model:
                    continue
                merged_models.append(model)
                model_key_map.setdefault(model, profile['id'])
        else:
            profile_errors['gemini'] = gemini_error

        if profile_errors:
            errors[profile['id']] = profile_errors

    models = sorted(dict.fromkeys([item for item in merged_models if item]))
    if not models:
        return jsonify({
            'success': False,
            'message': '获取模型列表失败',
            'errors': errors,
        }), 502

    return jsonify({
        'success': True,
        'models': models,
        'model_key_map': model_key_map,
        'profiles': [{'id': item['id'], 'label': item['label'], 'base_url': item['base_url']} for item in profiles],
        'partial': bool(errors),
        'errors': errors,
    })


@app.route('/api/image/test', methods=['POST'])
def test_image_connection():
    data = request.get_json(silent=True) or {}
    api_key = (data.get('api_key') or '').strip()
    base_url = (data.get('base_url') or DEFAULT_GPT_IMAGE_BASE_URL).strip().rstrip('/')
    model_name = (data.get('model') or '').strip()
    if not api_key:
        return jsonify({'success': False, 'message': '请填写 API Key'}), 400

    provider = resolve_image_provider(model_name, base_url)
    errors = {}

    if provider == 'gemini':
        models, error = fetch_gemini_models(api_key, base_url)
        if error is None:
            return jsonify({'success': True, 'provider': 'gemini', 'message': f'Gemini connection successful: {base_url}', 'models': models})
        return jsonify({'success': False, 'provider': 'gemini', 'message': error}), 502

    if provider == 'gpt':
        models, error = fetch_gpt_models(api_key, base_url)
        if error is None:
            return jsonify({'success': True, 'provider': 'gpt', 'message': f'GPT connection successful: {base_url}', 'models': models})
        return jsonify({'success': False, 'provider': 'gpt', 'message': error}), 502

    gemini_models, gemini_error = fetch_gemini_models(api_key, base_url)
    if gemini_error is None:
        return jsonify({'success': True, 'provider': 'gemini', 'message': f'Gemini connection successful: {base_url}', 'models': gemini_models})
    errors['gemini'] = gemini_error

    gpt_models, gpt_error = fetch_gpt_models(api_key, base_url)
    if gpt_error is None:
        return jsonify({'success': True, 'provider': 'gpt', 'message': f'GPT connection successful: {base_url}', 'models': gpt_models})
    errors['gpt'] = gpt_error

    return jsonify({
        'success': False,
        'message': '无法确认该地址可用的图片 provider',
        'errors': errors,
    }), 502


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
    resolution = data.get('resolution', '')
    aspect_ratio = data.get('aspect_ratio', '')
    use_last_image = data.get('use_last_image', False)
    image_size = normalize_gemini_image_size(resolution)
    aspect_ratio = normalize_gemini_aspect_ratio(aspect_ratio)
    
    # 调试日志
    logging.info("=" * 50)
    logging.info("[DEBUG] Received text-to-image request")
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
        logging.info("[DEBUG] Calling upstream API")
        url = f"{base_url.rstrip('/')}/v1beta/models/{model_id.strip()}:generateContent"
        output_paths = []
        generation_stamp = int(time.time() * 1000)
        
        for i in range(image_count):
            logging.info(f"[DEBUG] Starting generation loop {i+1}")
            
            # 构建 payload 后打印调试日志
            logging.info("[DEBUG] Preparing payload")
            
            # 如果使用上一张图（连续对话）
            if use_last_image and last_image and Path(last_image).exists():
                logging.info(f"[DEBUG] Using previous image: {last_image}")
                base64_image = encode_image_to_base64(last_image, max_size=512, jpeg_quality=75)
                mime_type = get_image_mime_type(last_image)
                
                payload = build_gemini_payload(
                    [
                        {"inlineData": {"mimeType": mime_type, "data": base64_image}},
                        {"text": prompt},
                    ],
                    image_size=image_size,
                    aspect_ratio=aspect_ratio,
                )
            else:
                logging.info("[DEBUG] Using text-only generation mode")
                # 纯文生图模式
                payload = build_gemini_payload(
                    [{"text": prompt}],
                    image_size=image_size,
                    aspect_ratio=aspect_ratio,
                )
            
            if negative_prompt:
                payload["systemInstruction"] = {"parts": [{"text": f"不要生成包含以下内容的内容：{negative_prompt}"}]}
            
            # 打印 payload 用于调试
            import json
            logging.info(f"[DEBUG] Payload: {json.dumps(payload, ensure_ascii=False)[:800]}")
            
            # 直接调用 API（不重试）
            try:
                logging.info(f"[DEBUG] Sending request to {url}")
                response = post_gemini_request(url, payload, api_key)
                logging.info(f"[DEBUG] Response status: {response.status_code}")
                logging.info(f"[DEBUG] 响应头：{dict(response.headers)}")
                if response.status_code >= 400:
                    logging.error(f"[DEBUG] 响应体：{response.text[:500]}")
            except requests.exceptions.Timeout:
                logging.error(f"[TIMEOUT] Request timed out after {API_TIMEOUT} seconds")
                return jsonify({'success': False, 'message': '上游可能已成功，但传输超时，请检查上游是否已生成图片'})
            except requests.exceptions.RequestException as e:
                logging.error(f"[ERROR] Request failed: {e}")
                logging.error(f"[ERROR] Exception type: {type(e).__name__}")
                return jsonify({'success': False, 'message': f'上游可能已成功，但传输失败：{str(e)}'})
            
            if response.status_code >= 400:
                return forward_raw_upstream_error(response)

            response.raise_for_status()
            result = response.json()
            
            logging.info(f"[DEBUG] API response snippet: {json.dumps(result, ensure_ascii=False)[:1000]}")
            
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
                            output_path = session_dir / f"output_{generation_stamp}_{i+1}.png"
                            image.save(output_path)
                            output_paths.append(output_path.name)
                            
                            # 更新 Session 的最后一张图（只记录第一张）
                            if i == 0:
                                update_session_last_image(session_id, str(output_path))
                                logging.info(f"[DEBUG] Updated last_image: {output_path} size={image.size}")
                            break
                    
                    if "text" in part:
                        text = part["text"]
                        
                        # 方式 1: Markdown base64 格式 ![image](data:image/png;base64,...)
                        md_pattern = r'!\[.*?\]\(data:(image/[a-z]+);base64,([A-Za-z0-9+/=]+)\)'
                        match = re.search(md_pattern, text)
                        if match:
                            image_data = base64.b64decode(match.group(2))
                            image = Image.open(io.BytesIO(image_data))
                            output_path = session_dir / f"output_{generation_stamp}_{i+1}.png"
                            image.save(output_path)
                            output_paths.append(output_path.name)
                            
                            if i == 0:
                                update_session_last_image(session_id, str(output_path))
                                logging.info(f"[DEBUG] Updated last_image: {output_path} size={image.size}")
                            break
                        
                        # 方式 2: Markdown 外部 URL 格式 ![Image](https://...)
                        url_pattern = r'!\[.*?\]\((https?://[^\s\)]+\.(png|jpg|jpeg|webp|gif))\)'
                        url_match = re.search(url_pattern, text, re.IGNORECASE)
                        if url_match:
                            image_url = url_match.group(1)
                            logging.info(f"[DEBUG] 发现外部图片 URL: {image_url}")
                            
                            # 下载图片
                            img_response = requests.get(image_url, timeout=30)
                            img_response.raise_for_status()
                            image = Image.open(io.BytesIO(img_response.content))
                            output_path = session_dir / f"output_{generation_stamp}_{i+1}.png"
                            image.save(output_path)
                            output_paths.append(output_path.name)
                            
                            if i == 0:
                                update_session_last_image(session_id, str(output_path))
                                logging.info(f"[DEBUG] Updated last_image: {output_path} size={image.size}")
                            break
        
        if output_paths:
            append_gemini_history(session_id, 'text', prompt, output_paths)
            message = f'生成成功 {len(output_paths)} 张！'
            return jsonify({
                'success': True,
                'images': [f'/output/{session_id}/{p}' for p in output_paths],
                'message': message
            })
        
        return jsonify({'success': False, 'message': 'No image data found'})
        
    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'message': f'Request error: {str(e)}'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})


def image_to_image():
    """Image-to-image generation with session isolation."""
    session_id = get_session_id()

    data = request.json
    api_key = data.get('api_key', '')
    base_url = data.get('base_url', DEFAULT_BASE_URL)
    model_id = data.get('model_id', DEFAULT_MODEL)
    prompt = data.get('prompt', '')
    negative_prompt = data.get('negative_prompt', '')
    image_count = int(data.get('image_count', 1))
    resolution = data.get('resolution', '')
    aspect_ratio = data.get('aspect_ratio', '')
    image_data_list = data.get('image_data_list', [])  # base64 列表，支持多张

    image_size = normalize_gemini_image_size(resolution)
    aspect_ratio = normalize_gemini_aspect_ratio(aspect_ratio)

    # 调试日志
    logging.info(f"[DEBUG] 图生图：image_size={image_size}, aspect_ratio={aspect_ratio}, 参考图数量={len(image_data_list)}")

    if not api_key:
        return jsonify({'error': '请填写 API Key'})
    if not model_id:
        return jsonify({'error': '请填写模型 ID'})
    if not prompt:
        return jsonify({'error': '请填写提示词'})
    if not image_data_list or len(image_data_list) == 0:
        return jsonify({'error': 'Please upload at least 1 reference image'})
    if len(image_data_list) > 4:
        return jsonify({'error': 'At most 4 reference images are supported'})

    # 检查图片总大小
    total_size = sum(len(img.split(',')[1]) if ',' in img else len(img) for img in image_data_list)
    total_size_mb = total_size * 3 / 4 / 1024 / 1024  # base64 转回原始大小估算
    logging.info(f"[DEBUG] 参考图总大小估算：{total_size_mb:.2f}MB")
    if total_size_mb > 5:
        return jsonify({'error': f'Reference images are too large ({total_size_mb:.2f}MB total)'})

    # 确保 Session 输出目录存在
    session_dir = get_session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    try:
        url = f"{base_url.rstrip('/')}/v1beta/models/{model_id.strip()}:generateContent"
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
        generation_stamp = int(time.time() * 1000)

        for i in range(image_count):
            # 构建 parts 列表：所有参考图 + 提示词
            parts = []
            for encoded_img in encoded_images:
                parts.append({"inlineData": {"mimeType": "image/jpeg", "data": encoded_img}})
            parts.append({"text": prompt})

            payload = build_gemini_payload(
                parts,
                image_size=image_size,
                aspect_ratio=aspect_ratio,
            )

            if negative_prompt:
                payload["systemInstruction"] = {"parts": [{"text": f"不要生成包含以下内容的内容：{negative_prompt}"}]}

            # 直接调用 API（不重试）
            try:
                response = post_gemini_request(url, payload, api_key)
            except requests.exceptions.Timeout:
                logging.error(f"[TIMEOUT] 请求超时")
                return jsonify({'success': False, 'message': '上游可能已成功，但传输超时，请检查上游是否已生成图片'})
            except requests.exceptions.RequestException as e:
                logging.error(f"[ERROR] Request failed: {e}")
                return jsonify({'success': False, 'message': f'上游可能已成功，但传输失败：{str(e)}'})

            # 打印响应日志
            logging.info(f"[DEBUG] Response status: {response.status_code}")
            if response.status_code >= 400:
                logging.error(f"[DEBUG] 响应体：{response.text[:500]}")

            if response.status_code >= 400:
                return forward_raw_upstream_error(response)

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
                            output_path = session_dir / f"img2img_{generation_stamp}_{i+1}.png"
                            image.save(output_path)
                            output_paths.append(output_path.name)

                            # 更新 Session 的最后一张图
                            if i == 0:
                                update_session_last_image(session_id, str(output_path))
                                logging.info(f"[DEBUG] Updated last_image: {output_path} size={image.size}")
                            break

                    if "text" in part:
                        text = part["text"]

                        # 方式 1: Markdown base64 格式
                        md_pattern = r'!\[.*?\]\(data:(image/[a-z]+);base64,([A-Za-z0-9+/=]+)\)'
                        match = re.search(md_pattern, text)
                        if match:
                            image_data = base64.b64decode(match.group(2))
                            image = Image.open(io.BytesIO(image_data))
                            output_path = session_dir / f"img2img_{generation_stamp}_{i+1}.png"
                            image.save(output_path)
                            output_paths.append(output_path.name)

                            if i == 0:
                                update_session_last_image(session_id, str(output_path))
                                logging.info(f"[DEBUG] Updated last_image: {output_path} size={image.size}")
                            break

                        # 方式 2: Markdown 外部 URL 格式
                        url_pattern = r'!\[.*?\]\((https?://[^\s\)]+\.(png|jpg|jpeg|webp|gif))\)'
                        url_match = re.search(url_pattern, text, re.IGNORECASE)
                        if url_match:
                            image_url = url_match.group(1)
                            logging.info(f"[DEBUG] 发现外部图片 URL: {image_url}")

                            # 下载图片
                            img_response = requests.get(image_url, timeout=30)
                            img_response.raise_for_status()
                            image = Image.open(io.BytesIO(img_response.content))
                            output_path = session_dir / f"img2img_{generation_stamp}_{i+1}.png"
                            image.save(output_path)
                            output_paths.append(output_path.name)

                            if i == 0:
                                update_session_last_image(session_id, str(output_path))
                                logging.info(f"[DEBUG] Updated last_image: {output_path} size={image.size}")
                            break

        if output_paths:
            append_gemini_history(session_id, 'image', prompt, output_paths)
            message = f'生成成功 {len(output_paths)} 张！'
            return jsonify({
                'success': True,
                'images': [f'/output/{session_id}/{p}' for p in output_paths],
                'message': message
            })

        return jsonify({'success': False, 'message': 'No image data found'})

    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'message': f'Request error: {str(e)}'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})


def gpt_image_generate():
    session_id = get_gpt_session_id()
    data = request.get_json(silent=True) or {}
    api_key = (data.get('api_key') or '').strip()
    base_url = (data.get('base_url') or DEFAULT_GPT_IMAGE_BASE_URL).strip().rstrip('/')
    model = (data.get('model') or DEFAULT_GPT_IMAGE_MODEL).strip()
    prompt = (data.get('prompt') or '').strip()
    image_count = int(data.get('image_count', 1) or 1)
    size = normalize_gpt_image_size(data.get('size') or '1024x1024')
    aspect_ratio = normalize_gpt_aspect_ratio(data.get('aspect_ratio'))
    quality = normalize_gpt_image_quality(data.get('quality'))
    style = normalize_gpt_image_style(data.get('style'))
    background = normalize_gpt_background(data.get('background'))
    response_format = normalize_gpt_response_format(data.get('response_format'))
    upscale = normalize_gpt_upscale(data.get('upscale'))
    prompt_with_layout = apply_gpt_layout_instruction(prompt, size, aspect_ratio)

    if not api_key:
        return jsonify({'success': False, 'message': '请填写 API Key'}), 400
    if not model:
        return jsonify({'success': False, 'message': '请填写模型 ID'}), 400
    if not prompt:
        return jsonify({'success': False, 'message': '请填写提示词'}), 400
    if image_count < 1 or image_count > 4:
        return jsonify({'success': False, 'message': 'Image count must be between 1 and 4'}), 400

    try:
        resize_target = get_gpt_upscale_target(upscale, aspect_ratio)
        request_payload = {
            'model': model,
            'prompt': prompt_with_layout,
            'n': image_count,
            'size': size,
            'quality': quality,
            'style': style,
            'background': background,
            'response_format': response_format,
        }
        if upscale:
            request_payload['upscale'] = upscale
        response = send_gpt_request(
            'POST',
            f'{base_url}/v1/images/generations',
            api_key,
            json=request_payload,
            timeout=API_TIMEOUT,
        )
        if response.status_code >= 400:
            return forward_error_response(response)
        payload = response.json()
        image_items = parse_gpt_image_items(payload)
        if not image_items:
            return jsonify({'success': False, 'message': 'Upstream did not return image data'}), 502
        saved_files = save_gpt_image_items(session_id, image_items, 'generate', prompt, target_size=resize_target)
        message = f'生成成功 {len(saved_files)} 张！'
        result = jsonify({
            'success': True,
            'images': [f'/output/gpt/{session_id}/{name}' for name in saved_files],
            'message': message,
            'session': build_gpt_session_payload(session_id),
        })
        return set_gpt_session_cookie(result, session_id)
    except requests.RequestException as exc:
        return jsonify({'success': False, 'message': f'Request error: {exc}'}), 502
    except Exception as exc:
        return jsonify({'success': False, 'message': f'Error: {exc}'}), 500


def gpt_image_edit():
    session_id = get_gpt_session_id()
    api_key = (request.form.get('api_key') or '').strip()
    base_url = (request.form.get('base_url') or DEFAULT_GPT_IMAGE_BASE_URL).strip().rstrip('/')
    model = (request.form.get('model') or DEFAULT_GPT_IMAGE_MODEL).strip()
    prompt = (request.form.get('prompt') or '').strip()
    size = normalize_gpt_image_size(request.form.get('size') or '1024x1024')
    aspect_ratio = normalize_gpt_aspect_ratio(request.form.get('aspect_ratio'))
    quality = normalize_gpt_image_quality(request.form.get('quality'))
    style = normalize_gpt_image_style(request.form.get('style'))
    background = normalize_gpt_background(request.form.get('background'))
    response_format = normalize_gpt_response_format(request.form.get('response_format'))
    upscale = normalize_gpt_upscale(request.form.get('upscale'))
    image_count = int(request.form.get('image_count', 1) or 1)
    use_last = (request.form.get('use_last_image') or '').lower() == 'true'
    uploaded_files = get_uploaded_image_files(request.files)
    mask_file = get_uploaded_mask_file(request.files)
    prompt_with_layout = apply_gpt_layout_instruction(prompt, size, aspect_ratio)

    if not api_key:
        return jsonify({'success': False, 'message': '请填写 API Key'}), 400
    if not model:
        return jsonify({'success': False, 'message': '请填写模型 ID'}), 400
    if not prompt:
        return jsonify({'success': False, 'message': '请填写提示词'}), 400
    if image_count < 1 or image_count > 4:
        return jsonify({'success': False, 'message': 'Image count must be between 1 and 4'}), 400

    if len(uploaded_files) > 4:
        return jsonify({'success': False, 'message': '最多支持 4 张参考图'}), 400

    outbound_streams = []
    local_reference_paths = []
    try:
        resize_target = get_gpt_upscale_target(upscale, aspect_ratio)
        if use_last and not uploaded_files:
            state = load_gpt_session_state(session_id)
            last_image = state.get('last_image')
            if not last_image or not Path(last_image).exists():
                return jsonify({'success': False, 'message': 'No reusable image found in the current session'}), 400
            local_reference_paths = [Path(last_image)]
            outbound_stream = open(last_image, 'rb')
            outbound_streams.append(outbound_stream)
            outbound_files = [('image', (local_reference_paths[0].name, outbound_stream, get_image_mime_type(last_image)))]
        else:
            if not uploaded_files:
                return jsonify({'success': False, 'message': 'Please upload at least 1 reference image'}), 400
            outbound_files = []
            for index, uploaded_file in enumerate(uploaded_files, start=1):
                compressed_stream, filename = compress_uploaded_image(uploaded_file)
                outbound_streams.append(compressed_stream)
                field_name = 'image' if index == 1 else 'image[]'
                outbound_files.append((field_name, (filename, compressed_stream, 'image/jpeg')))
        request_data = {
            'model': model,
            'prompt': prompt_with_layout,
            'n': str(image_count),
            'size': size,
            'quality': quality,
            'style': style,
            'background': background,
            'response_format': response_format,
        }
        if upscale:
            request_data['upscale'] = upscale
        if mask_file:
            mask_bytes = mask_file.read()
            mask_stream = BytesIO(mask_bytes)
            mask_name = mask_file.filename or 'mask.png'
            outbound_streams.append(mask_stream)
            outbound_files.append(('mask', (mask_name, mask_stream, getattr(mask_file, 'mimetype', None) or 'image/png')))
        response = send_gpt_request(
            'POST',
            f'{base_url}/v1/images/edits',
            api_key,
            data=request_data,
            files=outbound_files,
            timeout=API_TIMEOUT,
        )
        if response.status_code >= 400:
            return forward_error_response(response)
        payload = response.json()
        image_items = parse_gpt_image_items(payload)

        if not image_items:
            return jsonify({'success': False, 'message': 'Upstream did not return image data'}), 502
        saved_files = save_gpt_image_items(session_id, image_items, 'edit', prompt, target_size=resize_target)
        message = f'编辑成功 {len(saved_files)} 张！'
        result = jsonify({
            'success': True,
            'images': [f'/output/gpt/{session_id}/{name}' for name in saved_files],
            'message': message,
            'session': build_gpt_session_payload(session_id),
        })
        return set_gpt_session_cookie(result, session_id)
    except requests.RequestException as exc:
        return jsonify({'success': False, 'message': f'Request error: {exc}'}), 502
    except Exception as exc:
        return jsonify({'success': False, 'message': f'Error: {exc}'}), 500
    finally:
        for outbound_stream in outbound_streams:
            outbound_stream.close()


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
            return jsonify({'success': False, 'message': '视频时长格式不正确'}), 400

    if not api_key:
        return jsonify({'success': False, 'message': '请填写 API Key'}), 400
    if not prompt:
        return jsonify({'success': False, 'message': '请填写提示词'}), 400
    if not size:
        return jsonify({'success': False, 'message': '请选择视频尺寸'}), 400
    if quality not in {'high', 'standard'}:
        return jsonify({'success': False, 'message': '请选择正确的视频质量'}), 400
    if seconds is not None and seconds not in {6, 10, 15}:
        return jsonify({'success': False, 'message': '视频时长只能是 6 / 10 / 15 秒'}), 400

    files = request.files.getlist('input_reference')
    if len(files) > 4:
        return jsonify({'success': False, 'message': '最多支持 4 张参考图'}), 400

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
        return jsonify({'success': False, 'message': f'请求失败：{exc}'}), 502
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
        'message': '提交成功',
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
        return jsonify({'success': False, 'message': '请填写 API Key'}), 400
    lookup_id = video_id or task_id
    if not lookup_id:
        return jsonify({'success': False, 'message': '请提供 task_id 或 video_id'}), 400

    local_task = find_task(session_id, task_id=task_id, video_id=video_id)
    if local_task and not video_id:
        lookup_id = local_task.get('task_id') or local_task.get('video_id') or lookup_id

    try:
        response = requests.get(
            f"{base_url}/v1/videos/{lookup_id}",
            headers=build_headers(api_key),
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return jsonify({'success': False, 'message': f'请求失败：{exc}'}), 502

    if response.status_code >= 400:
        return forward_error_response(response)

    remote = response.json()
    remote_task_id = first_present(remote, ['task_id', 'taskId', 'task'])
    remote_video_id = first_present(remote, ['video_id', 'videoId', 'id'])
    merged = {
        **(local_task or {}),
        'session_id': session_id,
        'task_id': (local_task or {}).get('task_id') or remote_task_id or lookup_id,
        'video_id': remote_video_id or (local_task or {}).get('video_id') or lookup_id,
        'status': remote.get('status', (local_task or {}).get('status', 'queued')),
        'progress': remote.get('progress', (local_task or {}).get('progress', 0)),
        'prompt': remote.get('prompt', (local_task or {}).get('prompt', '')),
        'model': remote.get('model', (local_task or {}).get('model', '')),
        'size': remote.get('size', (local_task or {}).get('size', '')),
        'seconds': remote.get('seconds', (local_task or {}).get('seconds')),
        'quality': remote.get('quality', (local_task or {}).get('quality', '')),
        'video_url': extract_video_url(remote) or (local_task or {}).get('video_url'),
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
    response = jsonify({'success': True, 'session_id': session_id, 'message': '当前会话数据已清空'})
    return set_video_session_cookie(response, session_id)


@app.route('/api/video/models', methods=['POST', 'OPTIONS'])
def list_video_models():
    if request.method == 'OPTIONS':
        return ('', 204)

    data = request.get_json(silent=True) or {}
    api_key = (data.get('api_key') or '').strip()
    base_url = sanitize_base_url(data.get('base_url'))

    if not api_key:
        return jsonify({'success': False, 'message': '请填写 API Key'}), 400

    models, error = fetch_video_models(api_key, base_url)
    return jsonify({'success': True, 'models': models, 'fallback': error is not None, 'error': error})


@app.route('/api/cleanup', methods=['POST'])
def cleanup_sessions():
    """Clean up expired sessions."""
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
            logging.error(f"[CLEANUP] Failed to clean session: {e}")
    
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
