#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini 鍥剧墖鐢熸垚涓庣紪杈戝伐鍏?- Flask 鐗堟湰 (澶氱敤鎴烽殧绂荤増)
绾?HTML/CSS/JS 鍓嶇锛孎lask 鍚庣 API
鏀寔瀹屾暣鐨?Session 闅旂锛岄槻姝㈠鐢ㄦ埛鏁版嵁娣锋穯
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
import shutil
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory, make_response
from PIL import Image
import io
import re
import threading
from io import BytesIO
from datetime import datetime, timezone

# 閰嶇疆鏃ュ織杈撳嚭鍒?stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(message)s',
    stream=sys.stdout,
    force=True
)

# API 瓒呮椂閰嶇疆
API_TIMEOUT = 360  # 6 鍒嗛挓瓒呮椂

app = Flask(__name__)

# 閰嶇疆
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/output")
SESSIONS_DIR = os.getenv("SESSIONS_DIR", "/app/sessions")
DEFAULT_BASE_URL = os.getenv("DEFAULT_BASE_URL", "https://moai.wiki")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemini-2.5-flash-image")
SESSION_COOKIE_NAME = "gemini_session_id"
SESSION_COOKIE_MAX_AGE = 7 * 24 * 60 * 60  # 7 澶?
VIDEO_SESSION_COOKIE_NAME = "video_tool_session_id"
DEFAULT_VIDEO_BASE_URL = os.getenv("DEFAULT_VIDEO_BASE_URL", DEFAULT_BASE_URL)
DEFAULT_VIDEO_MODEL = os.getenv("DEFAULT_VIDEO_MODEL", "grok-imagine-1.0-video")
DEFAULT_GPT_IMAGE_BASE_URL = os.getenv("DEFAULT_GPT_IMAGE_BASE_URL", DEFAULT_BASE_URL)
DEFAULT_GPT_IMAGE_MODEL = os.getenv("DEFAULT_GPT_IMAGE_MODEL", "gpt-image-2-flatfee")
DEFAULT_REQUEST_TIMEOUT = int(os.getenv("DEFAULT_REQUEST_TIMEOUT", "60"))
GEMINI_OUTPUT_SUBDIR = "gemini"
GPT_IMAGE_OUTPUT_SUBDIR = "gpt"
GPT_IMAGE_SESSION_SUBDIR = "gpt"
GPT_IMAGE_SESSION_COOKIE_NAME = "gpt_image_session_id"


# 纭繚鐩綍瀛樺湪
Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
Path(SESSIONS_DIR).mkdir(parents=True, exist_ok=True)

# 绾跨▼瀹夊叏鐨?Session 鐘舵€佸瓨鍌?
session_states = {}  # {session_id: {"last_image": str, "created_at": float}}
session_lock = threading.Lock()
file_lock = threading.Lock()


def get_session_id():
    """浠?Cookie 鑾峰彇鎴栫敓鎴?Session ID"""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    
    # 楠岃瘉 session_id 鏍煎紡锛堝繀椤绘槸鏈夋晥鐨?UUID锛?
    if session_id:
        try:
            uuid.UUID(session_id)
            return session_id
        except ValueError:
            pass  # 鏃犳晥鐨?UUID锛岀敓鎴愭柊鐨?
    
    # 鐢熸垚鏂扮殑 Session ID
    session_id = str(uuid.uuid4())
    return session_id


def set_session_cookie(response, session_id):
    """璁剧疆 Session Cookie"""
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
        # 鍏堟鏌ュ唴瀛?
        if session_id in session_states:
            return session_states[session_id]
        
        # 浠庢枃浠跺姞杞?
        state_path = get_session_state_path(session_id)
        if state_path.exists():
            try:
                with open(state_path, 'r') as f:
                    state = json.load(f)
                session_states[session_id] = state
                return state
            except Exception as e:
                logging.warning(f"鍔犺浇 Session 鐘舵€佸け璐ワ細{e}")
        
        # 鍒涘缓鏂扮姸鎬?
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
            logging.error(f"淇濆瓨 Session 鐘舵€佸け璐ワ細{e}")


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
        'txt_resolution': payload.get('txt_resolution', '1K (1024x1024)'),
        'txt_aspect_ratio': payload.get('txt_aspect_ratio', '1:1'),
        'txt_use_last': bool(payload.get('txt_use_last', False)),
        'img_prompt': payload.get('img_prompt', ''),
        'img_negative': payload.get('img_negative', ''),
        'img_resolution': payload.get('img_resolution', '1K (1024x1024)'),
        'img_aspect_ratio': payload.get('img_aspect_ratio', '1:1'),
        'generate_count': str(payload.get('generate_count', '1')),
        'uploaded_images': payload.get('uploaded_images', [])[:4],
        'updated_at': utc_now_iso(),
    }
    state['draft'] = draft
    state['last_accessed'] = time.time()
    save_session_state(session_id, state)
    return draft


def encode_image_to_base64(image_path, max_size=384, jpeg_quality=60):
    """灏嗗浘鐗囩紪鐮佷负 base64锛岃嚜鍔ㄥ帇缂?
    
    浼樺寲鍙傛暟鍑忓皯 413 閿欒锛?
    - max_size: 512 鈫?384 (鍑忓皬 39% 闈㈢Н)
    - jpeg_quality: 75 鈫?60 (鍑忓皬绾?30% 浣撶Н)
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
    """鑾峰彇鍥剧墖 MIME 绫诲瀷"""
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


def get_gpt_interface_mode(payload, default='native'):
    mode = ''
    if isinstance(payload, dict):
        mode = (payload.get('interface_mode') or default).strip().lower()
    return mode if mode in {'native', 'compatible'} else default


def normalize_gpt_image_quality(quality):
    value = (quality or 'auto').strip().lower()
    return value if value in {'auto', 'low', 'medium', 'high'} else 'auto'


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
    return value if value in {'1:1', '16:9', '9:16', '4:3', '3:4'} else '1:1'


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


def summarize_upstream_error(response):
    content_type = (response.headers.get('Content-Type') or '').lower()
    if 'text/html' in content_type:
        match = re.search(r'<title>([^<]+)</title>', response.text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return response.text[:200]
    try:
        return response.json()
    except Exception:
        return response.text[:500]


def build_gpt_compatible_text_payload(model, prompt):
    return {
        'stream': False,
        'model': model,
        'messages': [
            {
                'role': 'user',
                'content': prompt,
            }
        ],
    }


def build_gpt_compatible_edit_payload(model, prompt, image_urls):
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    content = [{'type': 'text', 'text': prompt}]
    for image_url in image_urls or []:
        content.append({'type': 'image_url', 'image_url': {'url': image_url}})
    return {
        'stream': False,
        'model': model,
        'messages': [
            {
                'role': 'user',
                'content': content,
            }
        ],
    }


def parse_gpt_compatible_image_urls(response_data):
    choices = response_data.get('choices') if isinstance(response_data, dict) else None
    if not isinstance(choices, list):
        return []
    urls = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get('message') or {}
        content = message.get('content')
        if isinstance(content, str):
            urls.extend(parse_markdown_image_urls(content))
    return list(dict.fromkeys(urls))


def fetch_gpt_models(api_key, base_url, interface_mode):
    candidates = []
    if interface_mode == 'compatible':
        candidates = [('/v1/models', 'GET')]
    else:
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


def save_gpt_image_items(session_id, image_items, prefix, prompt):
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
    detail = summarize_upstream_error(response)
    if response.status_code == 524:
        message = 'Upstream image request timed out (524). Try a smaller size or a shorter prompt.'
    elif response.status_code in {401, 403}:
        message = 'Upstream authentication failed. Check the API key and Authorization format.'
    elif response.status_code == 413:
        message = 'Upstream rejected the request as too large. Try a smaller image size or a shorter prompt.'
    else:
        message = f'Upstream request failed (HTTP {response.status_code}).'
    return jsonify(
        {
            'success': False,
            'message': message,
            'status_code': response.status_code,
            'detail': detail,
        }
    ), response.status_code


@app.route('/')
def index():
    session_id = get_session_id()
    video_session_id = get_video_session_id()
    gpt_session_id = get_gpt_session_id()
    response = make_response(render_template('index.html'))
    response = set_session_cookie(response, session_id)
    response = set_video_session_cookie(response, video_session_id)
    return set_gpt_session_cookie(response, gpt_session_id)


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


@app.route('/gpt-image-app')
def gpt_image_app():
    session_id = get_gpt_session_id()
    response = make_response(render_template(
        'gpt_image_app.html',
        session_id=session_id,
        default_base_url=DEFAULT_GPT_IMAGE_BASE_URL,
        default_model=DEFAULT_GPT_IMAGE_MODEL,
    ))
    return set_gpt_session_cookie(response, session_id)


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


@app.route('/api/session', methods=['GET', 'POST'])
def get_session_info():
    """鑾峰彇褰撳墠 Session 淇℃伅"""
    session_id = get_session_id()

    if request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        draft = save_gemini_draft(session_id, payload)
        response = make_response(jsonify({
            'success': True,
            'session_id': session_id,
            'draft': draft,
        }))
        return set_session_cookie(response, session_id)

    response = make_response(jsonify(build_gemini_session_payload(session_id)))
    return set_session_cookie(response, session_id)


@app.route('/api/session/clear', methods=['POST'])
def clear_session():
    """Clear the current session state."""
    session_id = get_session_id()

    # 娓呴櫎鍐呭瓨鐘舵€?
    with session_lock:
        if session_id in session_states:
            del session_states[session_id]

    # 娓呴櫎鏂囦欢鐘舵€?
    state_path = get_session_state_path(session_id)
    if state_path.exists():
        try:
            state_path.unlink()
        except Exception as e:
            logging.error(f"鍒犻櫎 Session 鐘舵€佹枃浠跺け璐ワ細{e}")

    # 娓呴櫎鍥剧墖鐩綍
    session_dir = get_session_dir(session_id)
    if session_dir.exists():
        try:
            shutil.rmtree(session_dir)
        except Exception as e:
            logging.error(f"Failed to remove session image directory: {e}")

    # 鐢熸垚鏂扮殑 Session ID
    new_session_id = str(uuid.uuid4())
    response = make_response(jsonify({
        'success': True,
        'message': 'Session cleared',
        'new_session_id': new_session_id
    }))
    return set_session_cookie(response, new_session_id)


@app.route('/api/gemini-image/result', methods=['DELETE'])
def delete_gemini_image_result():
    session_id = get_session_id()
    payload = request.get_json(silent=True) or {}
    image_url = (payload.get('image_url') or '').strip()

    if not image_url:
        return jsonify({'success': False, 'message': '请提供要删除的图片'}), 400

    success, error = delete_gemini_history_image(session_id, image_url)
    if not success:
        status_code = 404 if error == 'Image not found' or error == 'Image not found in history' else 400
        return jsonify({'success': False, 'message': error}), status_code

    response = jsonify({'success': True, 'message': '图片已删除', 'session': build_gemini_session_payload(session_id)})
    return set_session_cookie(response, session_id)


@app.route('/api/gpt-image/session', methods=['GET', 'POST'])
def get_gpt_image_session():
    session_id = get_gpt_session_id()

    if request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        state = load_gpt_session_state(session_id)
        state['draft'] = {
            'api_key': payload.get('api_key', ''),
            'base_url': payload.get('base_url', DEFAULT_GPT_IMAGE_BASE_URL),
            'model': payload.get('model', DEFAULT_GPT_IMAGE_MODEL),
            'interface_mode': get_gpt_interface_mode(payload),
            'generate_prompt': payload.get('generate_prompt', ''),
            'generate_size': payload.get('generate_size', '1024x1024'),
            'generate_aspect_ratio': payload.get('generate_aspect_ratio', '1:1'),
            'generate_count': payload.get('generate_count', '1'),
            'generate_quality': payload.get('generate_quality', 'auto'),
            'edit_prompt': payload.get('edit_prompt', ''),
            'edit_size': payload.get('edit_size', '1024x1024'),
            'edit_aspect_ratio': payload.get('edit_aspect_ratio', '1:1'),
            'edit_count': payload.get('edit_count', '1'),
            'edit_quality': payload.get('edit_quality', 'auto'),
            'use_last_image': bool(payload.get('use_last_image', False)),
            'edit_references': payload.get('edit_references', [])[:4],
            'updated_at': utc_now_iso(),
        }
        state['last_accessed'] = time.time()
        save_gpt_session_state(session_id, state)
        response = jsonify({'success': True, **build_gpt_session_payload(session_id)})
        return set_gpt_session_cookie(response, session_id)

    response = jsonify({'success': True, **build_gpt_session_payload(session_id)})
    return set_gpt_session_cookie(response, session_id)


@app.route('/api/gpt-image/session/clear', methods=['POST'])
def clear_gpt_image_session():
    session_id = get_gpt_session_id()
    clear_gpt_session_data(session_id)
    new_session_id = str(uuid.uuid4())
    response = jsonify({
        'success': True,
        'message': 'GPT image session cleared',
        'new_session_id': new_session_id,
    })
    return set_gpt_session_cookie(response, new_session_id)


@app.route('/api/gpt-image/result', methods=['DELETE'])
def delete_gpt_image_result():
    session_id = get_gpt_session_id()
    payload = request.get_json(silent=True) or {}
    image_url = (payload.get('image_url') or '').strip()

    if not image_url:
        return jsonify({'success': False, 'message': '请提供要删除的图片'}), 400

    success, error = delete_gpt_history_image(session_id, image_url)
    if not success:
        status_code = 404 if error == 'Image not found' or error == 'Image not found in history' else 400
        return jsonify({'success': False, 'message': error}), status_code

    response = jsonify({'success': True, 'message': '图片已删除', 'session': build_gpt_session_payload(session_id)})
    return set_gpt_session_cookie(response, session_id)


@app.route('/api/models', methods=['POST'])
def get_models():
    """鑾峰彇妯″瀷鍒楄〃"""
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
    """娴嬭瘯杩炴帴"""
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
            return jsonify({'success': True, 'message': f'Connection successful: {base_url}'})
        return jsonify({'success': False, 'message': f'HTTP {response.status_code}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/gpt-image/models', methods=['POST'])
def list_gpt_image_models():
    data = request.get_json(silent=True) or {}
    api_key = (data.get('api_key') or '').strip()
    base_url = (data.get('base_url') or DEFAULT_GPT_IMAGE_BASE_URL).strip().rstrip('/')
    interface_mode = get_gpt_interface_mode(data)

    if not api_key:
        return jsonify({'success': False, 'message': '请填写 API Key'}), 400

    models, error = fetch_gpt_models(api_key, base_url, interface_mode)
    return jsonify({'success': True, 'models': models, 'fallback': error is not None, 'error': error})


@app.route('/api/gpt-image/test', methods=['POST'])
def test_gpt_image_connection():
    data = request.get_json(silent=True) or {}
    api_key = (data.get('api_key') or '').strip()
    base_url = (data.get('base_url') or DEFAULT_GPT_IMAGE_BASE_URL).strip().rstrip('/')
    model = (data.get('model') or DEFAULT_GPT_IMAGE_MODEL).strip()

    if not api_key:
        return jsonify({'success': False, 'message': '请填写 API Key'})
    if not model:
        return jsonify({'success': False, 'message': '请填写模型 ID'})

    try:
        response = send_gpt_request(
            'POST',
            f'{base_url}/v1/images/generations',
            api_key,
            json={
                'model': model,
                'prompt': 'test',
                'n': 1,
                'size': '1024x1024',
            },
            timeout=15,
        )
        if response.status_code < 500:
            return jsonify({'success': True, 'message': f'GPT 生图接口可访问：{base_url}'})
        return jsonify({'success': False, 'message': f'HTTP {response.status_code}'})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)})


@app.route('/api/text-to-image', methods=['POST'])
def text_to_image():
    """鏂囩敓鍥撅紙鏀寔 Session 闅旂鐨勮繛缁璇濓級"""
    session_id = get_session_id()
    
    # 鍔犺浇 Session 鐘舵€?
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
    
    # 杞崲鍒嗚鲸鐜囨牸寮忥細 "1K (1024x1024)" -> "1K"
    resolution_map = {
        '512 (512x512)': '512',
        '1K (1024x1024)': '1K',
        '2K (2048x2048)': '2K',
        '4K (4096x4096)': '4K'
    }
    image_size = resolution_map.get(resolution, '1K')
    
    # 璋冭瘯鏃ュ織
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
    
    # 纭繚 Session 杈撳嚭鐩綍瀛樺湪
    session_dir = get_session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        logging.info("[DEBUG] Calling upstream API")
        url = f"{base_url.rstrip('/')}/v1beta/models/{model_id.strip()}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key.strip()
        }
        
        output_paths = []
        generation_stamp = int(time.time() * 1000)
        
        for i in range(image_count):
            logging.info(f"[DEBUG] Starting generation loop {i+1}")
            
            # 鏋勫缓 payload 鍚庢墦鍗拌皟璇?
            logging.info("[DEBUG] Preparing payload")
            
            # 濡傛灉浣跨敤涓婁竴寮犲浘锛堣繛缁璇濓級
            if use_last_image and last_image and Path(last_image).exists():
                logging.info(f"[DEBUG] Using previous image: {last_image}")
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
                logging.info("[DEBUG] Using text-only generation mode")
                # 绾枃鐢熷浘妯″紡
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
            
            # 鎵撳嵃 payload 鐢ㄤ簬璋冭瘯
            import json
            logging.info(f"[DEBUG] Payload: {json.dumps(payload, ensure_ascii=False)[:800]}")
            
            # 鐩存帴璋冪敤 API锛堜笉閲嶈瘯锛?
            try:
                logging.info(f"[DEBUG] Sending request to {url}")
                response = requests.post(url, json=payload, headers=headers, timeout=API_TIMEOUT)
                logging.info(f"[DEBUG] Response status: {response.status_code}")
                logging.info(f"[DEBUG] 鍝嶅簲澶达細{dict(response.headers)}")
                if response.status_code >= 400:
                    logging.error(f"[DEBUG] 鍝嶅簲浣擄細{response.text[:500]}")
            except requests.exceptions.Timeout:
                logging.error(f"[TIMEOUT] Request timed out after {API_TIMEOUT} seconds")
                return jsonify({'success': False, 'message': '鈿狅笍 涓婃父鍙兘宸叉垚鍔燂紝浣嗕紶杈撹秴鏃讹紝璇锋鏌ヤ笂娓告槸鍚﹀凡鐢熸垚鍥剧墖'})
            except requests.exceptions.RequestException as e:
                logging.error(f"[ERROR] Request failed: {e}")
                logging.error(f"[ERROR] Exception type: {type(e).__name__}")
                return jsonify({'success': False, 'message': f'鈿狅笍 涓婃父鍙兘宸叉垚鍔燂紝浣嗕紶杈撳け璐ワ細{str(e)}'})
            
            # 澶勭悊鐗规畩閿欒鐮?
            if response.status_code == 413:
                logging.error("[413] Request too large")
                return jsonify({'success': False, 'message': 'Request too large (413)'})
            
            if response.status_code == 502:
                logging.error(f"[502] Bad Gateway - Gemini API 杩斿洖绌哄搷搴旀垨杩炴帴鏂紑")
                logging.error(f"[502] 鍙兘鍘熷洜锛?)Gemini 闄愭祦 2)Gemini 鏈嶅姟娉㈠姩 3)璇锋眰浣撳お澶ц鍒囨柇")
                return jsonify({'success': False, 'message': '鈿狅笍 缃戝叧閿欒 (502) - Gemini API 杩斿洖绌哄搷搴旓紝鍙兘鏄檺娴佹垨鏈嶅姟娉㈠姩锛岃绋嶅悗閲嶈瘯'})
            
            if response.status_code == 504:
                logging.error(f"[504] 缃戝叧瓒呮椂 - 涓婃父鍙兘浠嶅湪澶勭悊鎴栧凡鎴愬姛")
                return jsonify({'success': False, 'message': 'Gateway timeout (504)'})
            
            if response.status_code == 500:
                logging.error(f"[500] 鏈嶅姟鍣ㄩ敊璇?- 鍙兘鏄?HTTP/2 stream 閿欒")
                return jsonify({'success': False, 'message': 'Upstream server error (500)'})
            
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
                            
                            # 鏇存柊 Session 鐨勬渶鍚庝竴寮犲浘锛堝彧璁板綍绗竴寮狅級
                            if i == 0:
                                update_session_last_image(session_id, str(output_path))
                                logging.info(f"[DEBUG] Updated last_image: {output_path} size={image.size}")
                            break
                    
                    if "text" in part:
                        text = part["text"]
                        
                        # 鏂瑰紡 1: Markdown base64 鏍煎紡 ![image](data:image/png;base64,...)
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
                        
                        # 鏂瑰紡 2: Markdown 澶栭儴 URL 鏍煎紡 ![Image](https://...)
                        url_pattern = r'!\[.*?\]\((https?://[^\s\)]+\.(png|jpg|jpeg|webp|gif))\)'
                        url_match = re.search(url_pattern, text, re.IGNORECASE)
                        if url_match:
                            image_url = url_match.group(1)
                            logging.info(f"[DEBUG] 馃柤锔?鍙戠幇澶栭儴鍥剧墖 URL: {image_url}")
                            
                            # 涓嬭浇鍥剧墖
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
            return jsonify({
                'success': True,
                'images': [f'/output/{session_id}/{p}' for p in output_paths],
                'message': f'生成成功 {len(output_paths)} 张！'
            })
        
        return jsonify({'success': False, 'message': 'No image data found'})
        
    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'message': f'Request error: {str(e)}'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})


@app.route('/api/image-to-image', methods=['POST'])
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
    resolution = data.get('resolution', '1K (1024x1024)')
    aspect_ratio = data.get('aspect_ratio', '1:1')
    image_data_list = data.get('image_data_list', [])  # base64 鍒楄〃锛屾敮鎸佸寮?

    # 杞崲鍒嗚鲸鐜囨牸寮忥細 "1K (1024x1024)" -> "1K"
    resolution_map = {
        '512 (512x512)': '512',
        '1K (1024x1024)': '1K',
        '2K (2048x2048)': '2K',
        '4K (4096x4096)': '4K'
    }
    image_size = resolution_map.get(resolution, '1K')

    # 璋冭瘯鏃ュ織
    logging.info(f"[DEBUG] 鍥剧敓鍥撅細image_size={image_size}, aspect_ratio={aspect_ratio}, 鍙傝€冨浘鏁伴噺={len(image_data_list)}")

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

    # 妫€鏌ュ浘鐗囨€诲ぇ灏?
    total_size = sum(len(img.split(',')[1]) if ',' in img else len(img) for img in image_data_list)
    total_size_mb = total_size * 3 / 4 / 1024 / 1024  # base64 杞洖鍘熷澶у皬浼扮畻
    logging.info(f"[DEBUG] 鍙傝€冨浘鎬诲ぇ灏忎及绠楋細{total_size_mb:.2f}MB")
    if total_size_mb > 5:
        return jsonify({'error': f'Reference images are too large ({total_size_mb:.2f}MB total)'})

    # 纭繚 Session 杈撳嚭鐩綍瀛樺湪
    session_dir = get_session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    try:
        url = f"{base_url.rstrip('/')}/v1beta/models/{model_id.strip()}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key.strip()
        }

        # 瑙ｆ瀽涓婁紶鐨勬墍鏈夊浘鐗囷紝缂栫爜涓?base64
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
            # 鏋勫缓 parts 鍒楄〃锛氭墍鏈夊弬鑰冨浘 + 鎻愮ず璇?
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

            # 鐩存帴璋冪敤 API锛堜笉閲嶈瘯锛?
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=API_TIMEOUT)
            except requests.exceptions.Timeout:
                logging.error(f"[TIMEOUT] 璇锋眰瓒呮椂")
                return jsonify({'success': False, 'message': '鈿狅笍 涓婃父鍙兘宸叉垚鍔燂紝浣嗕紶杈撹秴鏃讹紝璇锋鏌ヤ笂娓告槸鍚﹀凡鐢熸垚鍥剧墖'})
            except requests.exceptions.RequestException as e:
                logging.error(f"[ERROR] Request failed: {e}")
                return jsonify({'success': False, 'message': f'鈿狅笍 涓婃父鍙兘宸叉垚鍔燂紝浣嗕紶杈撳け璐ワ細{str(e)}'})

            # 鎵撳嵃鍝嶅簲鏃ュ織
            logging.info(f"[DEBUG] Response status: {response.status_code}")
            if response.status_code >= 400:
                logging.error(f"[DEBUG] 鍝嶅簲浣擄細{response.text[:500]}")

            # 澶勭悊鐗规畩閿欒鐮?
            if response.status_code == 413:
                logging.error(f"[413] 璇锋眰澶ぇ")
                return jsonify({'success': False, 'message': 'Request too large (413)'})

            if response.status_code == 502:
                logging.error(f"[502] Bad Gateway - Gemini API 杩斿洖绌哄搷搴旀垨杩炴帴鏂紑")
                logging.error(f"[502] 鍙兘鍘熷洜锛?)Gemini 闄愭祦 2)Gemini 鏈嶅姟娉㈠姩 3)璇锋眰浣撳お澶ц鍒囨柇")
                return jsonify({'success': False, 'message': '鈿狅笍 缃戝叧閿欒 (502) - Gemini API 杩斿洖绌哄搷搴旓紝鍙兘鏄檺娴佹垨鏈嶅姟娉㈠姩锛岃绋嶅悗閲嶈瘯'})

            if response.status_code == 504:
                logging.error(f"[504] 缃戝叧瓒呮椂")
                return jsonify({'success': False, 'message': 'Gateway timeout (504)'})

            if response.status_code == 500:
                logging.error("[500] Upstream server error")
                return jsonify({'success': False, 'message': 'Upstream server error (500)'})

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

                            # 鏇存柊 Session 鐨勬渶鍚庝竴寮犲浘
                            if i == 0:
                                update_session_last_image(session_id, str(output_path))
                                logging.info(f"[DEBUG] Updated last_image: {output_path} size={image.size}")
                            break

                    if "text" in part:
                        text = part["text"]

                        # 鏂瑰紡 1: Markdown base64 鏍煎紡
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

                        # 鏂瑰紡 2: Markdown 澶栭儴 URL 鏍煎紡
                        url_pattern = r'!\[.*?\]\((https?://[^\s\)]+\.(png|jpg|jpeg|webp|gif))\)'
                        url_match = re.search(url_pattern, text, re.IGNORECASE)
                        if url_match:
                            image_url = url_match.group(1)
                            logging.info(f"[DEBUG] 馃柤锔?鍙戠幇澶栭儴鍥剧墖 URL: {image_url}")

                            # 涓嬭浇鍥剧墖
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
            return jsonify({
                'success': True,
                'images': [f'/output/{session_id}/{p}' for p in output_paths],
                'message': f'生成成功 {len(output_paths)} 张！'
            })

        return jsonify({'success': False, 'message': 'No image data found'})

    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'message': f'Request error: {str(e)}'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})


@app.route('/api/gpt-image/generate', methods=['POST'])
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
    interface_mode = get_gpt_interface_mode(data)
    prompt_with_layout = apply_gpt_layout_instruction(prompt, size, aspect_ratio)

    if not api_key:
        return jsonify({'success': False, 'message': '请填写 API Key'}), 400
    if not model:
        return jsonify({'success': False, 'message': '请填写模型 ID'}), 400
    if not prompt:
        return jsonify({'success': False, 'message': '请填写提示词'}), 400
    if image_count < 1 or image_count > 4:
        return jsonify({'success': False, 'message': 'Image count must be between 1 and 4'}), 400
    if interface_mode == 'compatible' and image_count != 1:
        return jsonify({'success': False, 'message': 'Compatible mode only supports 1 image'}), 400

    try:
        if interface_mode == 'compatible':
            response = send_gpt_request(
                'POST',
                f'{base_url}/v1/chat/completions',
                api_key,
                json=build_gpt_compatible_text_payload(model, prompt_with_layout),
                timeout=API_TIMEOUT,
            )
            if response.status_code >= 400:
                return forward_error_response(response)
            payload = response.json()
            image_items = [{'type': 'url', 'value': url} for url in parse_gpt_compatible_image_urls(payload)]
        else:
            response = send_gpt_request(
                'POST',
                f'{base_url}/v1/images/generations',
                api_key,
                json={
                    'model': model,
                    'prompt': prompt_with_layout,
                    'n': image_count,
                    'size': size,
                    'quality': quality,
                },
                timeout=API_TIMEOUT,
            )
            if response.status_code >= 400:
                return forward_error_response(response)
            payload = response.json()
            image_items = parse_gpt_image_items(payload)
        if not image_items:
            return jsonify({'success': False, 'message': 'Upstream did not return image data'}), 502
        saved_files = save_gpt_image_items(session_id, image_items, 'generate', prompt)
        result = jsonify({
            'success': True,
            'images': [f'/output/gpt/{session_id}/{name}' for name in saved_files],
            'message': f'生成成功 {len(saved_files)} 张！',
            'session': build_gpt_session_payload(session_id),
        })
        return set_gpt_session_cookie(result, session_id)
    except requests.RequestException as exc:
        return jsonify({'success': False, 'message': f'Request error: {exc}'}), 502
    except Exception as exc:
        return jsonify({'success': False, 'message': f'Error: {exc}'}), 500


@app.route('/api/gpt-image/edit', methods=['POST'])
def gpt_image_edit():
    session_id = get_gpt_session_id()
    api_key = (request.form.get('api_key') or '').strip()
    base_url = (request.form.get('base_url') or DEFAULT_GPT_IMAGE_BASE_URL).strip().rstrip('/')
    model = (request.form.get('model') or DEFAULT_GPT_IMAGE_MODEL).strip()
    prompt = (request.form.get('prompt') or '').strip()
    size = normalize_gpt_image_size(request.form.get('size') or '1024x1024')
    aspect_ratio = normalize_gpt_aspect_ratio(request.form.get('aspect_ratio'))
    quality = normalize_gpt_image_quality(request.form.get('quality'))
    image_count = int(request.form.get('image_count', 1) or 1)
    use_last = (request.form.get('use_last_image') or '').lower() == 'true'
    uploaded_files = get_uploaded_image_files(request.files)
    interface_mode = get_gpt_interface_mode(request.form)
    prompt_with_layout = apply_gpt_layout_instruction(prompt, size, aspect_ratio)

    if not api_key:
        return jsonify({'success': False, 'message': '请填写 API Key'}), 400
    if not model:
        return jsonify({'success': False, 'message': '请填写模型 ID'}), 400
    if not prompt:
        return jsonify({'success': False, 'message': '请填写提示词'}), 400
    if image_count < 1 or image_count > 4:
        return jsonify({'success': False, 'message': 'Image count must be between 1 and 4'}), 400
    if interface_mode == 'compatible' and image_count != 1:
        return jsonify({'success': False, 'message': 'Compatible mode only supports 1 image'}), 400

    if len(uploaded_files) > 4:
        return jsonify({'success': False, 'message': '最多支持 4 张参考图'}), 400

    outbound_streams = []
    local_reference_paths = []
    try:
        if use_last and not uploaded_files:
            state = load_gpt_session_state(session_id)
            last_image = state.get('last_image')
            if not last_image or not Path(last_image).exists():
                return jsonify({'success': False, 'message': 'No reusable image found in the current session'}), 400
            local_reference_paths = [Path(last_image)]
            if interface_mode == 'compatible':
                image_urls = [f'/output/gpt/{session_id}/{local_reference_paths[0].name}']
            else:
                outbound_stream = open(last_image, 'rb')
                outbound_streams.append(outbound_stream)
                outbound_files = [('image[]', (local_reference_paths[0].name, outbound_stream, get_image_mime_type(last_image)))]
        else:
            if not uploaded_files:
                return jsonify({'success': False, 'message': 'Please upload at least 1 reference image'}), 400
            outbound_files = []
            for index, uploaded_file in enumerate(uploaded_files, start=1):
                compressed_stream, filename = compress_uploaded_image(uploaded_file)
                outbound_streams.append(compressed_stream)
                outbound_files.append(('image[]', (filename, compressed_stream, 'image/jpeg')))
                if interface_mode == 'compatible':
                    reference_dir = get_gpt_session_dir(session_id)
                    reference_path = reference_dir / f'reference_{int(time.time())}_{index}.jpg'
                    with reference_path.open('wb') as f:
                        f.write(compressed_stream.getvalue())
                    local_reference_paths.append(reference_path)

        if interface_mode == 'compatible':
            if not local_reference_paths:
                return jsonify({'success': False, 'message': 'Compatible mode requires a reference image'}), 400
            origin = request.host_url.rstrip('/')
            reference_urls = [f'{origin}/output/gpt/{session_id}/{path.name}' for path in local_reference_paths]
            response = send_gpt_request(
                'POST',
                f'{base_url}/v1/chat/completions',
                api_key,
                json=build_gpt_compatible_edit_payload(model, prompt_with_layout, reference_urls),
                timeout=API_TIMEOUT,
            )
            if response.status_code >= 400:
                return forward_error_response(response)
            payload = response.json()
            image_items = [{'type': 'url', 'value': url} for url in parse_gpt_compatible_image_urls(payload)]
        else:
            response = send_gpt_request(
                'POST',
                f'{base_url}/v1/images/edits',
                api_key,
                data={
                    'model': model,
                    'prompt': prompt_with_layout,
                    'n': str(image_count),
                    'size': size,
                    'quality': quality,
                },
                files=outbound_files,
                timeout=API_TIMEOUT,
            )
            if response.status_code >= 400:
                return forward_error_response(response)
            payload = response.json()
            image_items = parse_gpt_image_items(payload)

        if not image_items:
            return jsonify({'success': False, 'message': 'Upstream did not return image data'}), 502
        saved_files = save_gpt_image_items(session_id, image_items, 'edit', prompt)
        result = jsonify({
            'success': True,
            'images': [f'/output/gpt/{session_id}/{name}' for name in saved_files],
            'message': f'编辑成功 {len(saved_files)} 张！',
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
        lookup_id = local_task.get('video_id') or lookup_id

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
                
                # 鍒犻櫎鐘舵€佹枃浠?
                state_file.unlink()
                
                # 鍒犻櫎鍥剧墖鐩綍
                session_dir = get_session_dir(session_id)
                if session_dir.exists():
                    shutil.rmtree(session_dir)
                
                # 娓呴櫎鍐呭瓨
                with session_lock:
                    if session_id in session_states:
                        del session_states[session_id]
                
                cleaned += 1
                logging.info(f"[CLEANUP] 娓呯悊杩囨湡 Session: {session_id[:8]}...")
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
