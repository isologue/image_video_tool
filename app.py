#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini 图片生成与编辑工具
支持文生图和图生图功能
"""

import base64
import requests
from pathlib import Path
import gradio as gr

# 默认配置（支持环境变量）
import os
DEFAULT_BASE_URL = os.getenv("DEFAULT_BASE_URL", "https://moai.wiki")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemini-2.5-flash-image")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/app/output")

# 全局变量：存储最后一张生成的图片路径
last_generated_image = None

def encode_image_to_base64(image_path, max_size=384, jpeg_quality=60):
    """将图片编码为 base64，自动压缩到最大边长 max_size，使用 JPEG 压缩
    
    优化参数减少 413 错误：
    - max_size: 512 → 384 (减小 39% 面积)
    - jpeg_quality: 75 → 60 (减小约 30% 体积)
    """
    from PIL import Image
    img = Image.open(image_path)
    
    # 转为 RGB（去除 alpha 通道，避免 PNG 体积大）
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGB')
    
    # 如果图片太大，进行缩放
    if max(img.size) > max_size:
        ratio = max_size / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
    
    # 保存到内存并编码为 JPEG
    import io
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")

def get_image_mime_type(image_path):
    """获取图片的 MIME 类型"""
    ext = Path(image_path).suffix.lower()
    mime_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif"
    }
    return mime_types.get(ext, "image/png")

def fetch_models(api_key, base_url):
    """获取模型列表 - 调用 [baseurl]/v1beta/models"""
    if not api_key or not api_key.strip():
        return [], "⚠️ 请先填写 API Key"
    
    try:
        base_url = base_url.rstrip("/")
        url = f"{base_url}/v1beta/models"
        
        headers = {"X-Goog-Api-Key": api_key.strip()}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        data = response.json()
        models = []
        
        if "models" in data:
            for m in data["models"]:
                model_id = m.get("name", "")
                # 提取纯模型 ID（去掉 models/ 前缀）
                if model_id.startswith("models/"):
                    model_id = model_id[7:]
                if model_id:
                    models.append(model_id)
        
        if models:
            return gr.Dropdown(choices=models, value=models[0]), f"✅ 获取成功！找到 {len(models)} 个模型"
        else:
            return gr.Dropdown(choices=[DEFAULT_MODEL], value=DEFAULT_MODEL), "⚠️ 未找到模型，请手动输入"
        
    except requests.exceptions.RequestException as e:
        return gr.Dropdown(choices=[DEFAULT_MODEL], value=DEFAULT_MODEL), f"❌ 请求错误：{str(e)}"
    except Exception as e:
        return gr.Dropdown(choices=[DEFAULT_MODEL], value=DEFAULT_MODEL), f"❌ 错误：{str(e)}"

def text_to_image(api_key, base_url, model_id, prompt, negative_prompt, image_count, resolution, aspect_ratio, use_last_image):
    """文生图功能 - 调用 [baseurl]/v1beta/models/[modelid]:generateContent"""
    global last_generated_image
    
    if not api_key or not api_key.strip():
        return None, "❌ 错误：请填写 API Key"
    
    if not model_id or not model_id.strip():
        return None, "❌ 错误：请填写或选择模型 ID"
    
    try:
        base_url = base_url.rstrip("/")
        url = f"{base_url}/v1beta/models/{model_id.strip()}:generateContent"
        
        # 构建 Gemini generateContent 格式的请求
        system_instruction = ""
        if negative_prompt and negative_prompt.strip():
            system_instruction = f"不要生成包含以下内容的内容：{negative_prompt.strip()}"
        
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key.strip()
        }
        
        output_paths = []
        
        # 如果勾选了"使用上一张图"且有上一张图，则使用图生图模式
        if use_last_image and last_generated_image is not None:
            base64_image = encode_image_to_base64(last_generated_image, max_size=512, jpeg_quality=75)
            mime_type = get_image_mime_type(last_generated_image)
        
        # 循环生成多张图片
        for i in range(int(image_count)):
            if use_last_image and last_generated_image is not None:
                # 图生图模式：带图片请求
                payload = {
                    "contents": [{
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": base64_image
                                }
                            },
                            {
                                "text": prompt
                            }
                        ]
                    }],
                    "generationConfig": {
                        "responseModalities": ["IMAGE"],
                    }
                }
            else:
                # 纯文生图模式
                payload = {
                    "contents": [{
                        "parts": [{
                            "text": prompt
                        }]
                    }],
                    "generationConfig": {
                        "responseModalities": ["IMAGE"],
                    }
                }
            
            if system_instruction:
                payload["systemInstruction"] = {
                    "parts": [{"text": system_instruction}]
                }
            
            response = requests.post(url, json=payload, headers=headers, timeout=90)
            response.raise_for_status()
            
            result = response.json()
            
            # 解析 generateContent 返回的图片
            if "candidates" in result and len(result["candidates"]) > 0:
                candidate = result["candidates"][0]
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                
                for part in parts:
                    # 方式 1: inlineData 格式
                    if "inlineData" in part:
                        inline_data = part["inlineData"]
                        mime_type = inline_data.get("mimeType", "image/png")
                        if mime_type.startswith("image/"):
                            image_data = base64.b64decode(inline_data["data"])
                            
                            import io
                            from PIL import Image
                            image = Image.open(io.BytesIO(image_data))
                            
                            output_path = Path(f"{OUTPUT_DIR}/text_to_image_output_{i+1}.png")
                            output_path.parent.mkdir(parents=True, exist_ok=True)
                            image.save(output_path)
                            output_paths.append(str(output_path))
                            break
                    
                    # 方式 2: Markdown base64 格式 ![image](data:image/png;base64,...)
                    if "text" in part:
                        import re
                        text = part["text"]
                        md_pattern = r'!\[.*?\]\(data:(image/[a-z]+);base64,([A-Za-z0-9+/=]+)\)'
                        match = re.search(md_pattern, text)
                        if match:
                            mime_type = match.group(1)
                            image_data = base64.b64decode(match.group(2))
                            
                            import io
                            from PIL import Image
                            image = Image.open(io.BytesIO(image_data))
                            
                            output_path = Path(f"{OUTPUT_DIR}/text_to_image_output_{i+1}.png")
                            output_path.parent.mkdir(parents=True, exist_ok=True)
                            image.save(output_path)
                            output_paths.append(str(output_path))
                            break
                        
                        # 方式 3: Markdown 外部 URL 格式 ![Image](https://...)
                        url_pattern = r'!\[.*?\]\((https?://[^\s\)]+\.(png|jpg|jpeg|webp|gif))\)'
                        url_match = re.search(url_pattern, text, re.IGNORECASE)
                        if url_match:
                            image_url = url_match.group(1)
                            
                            # 下载图片
                            img_response = requests.get(image_url, timeout=30)
                            img_response.raise_for_status()
                            
                            import io
                            from PIL import Image
                            image = Image.open(io.BytesIO(img_response.content))
                            
                            output_path = Path(f"{OUTPUT_DIR}/text_to_image_output_{i+1}.png")
                            output_path.parent.mkdir(parents=True, exist_ok=True)
                            image.save(output_path)
                            output_paths.append(str(output_path))
                            break
        
        if output_paths:
            # 更新全局变量：最后一张生成的图片
            last_generated_image = output_paths[-1]
            
            msg = f"✅ 生成成功 {len(output_paths)} 张！模型：{model_id}\n文件：{', '.join(output_paths)}"
            return output_paths[0], msg
        
        return None, "❌ 生成失败：未找到图片数据"
        
    except requests.exceptions.RequestException as e:
        return None, f"❌ 请求错误：{str(e)}"
    except Exception as e:
        return None, f"❌ 错误：{str(e)}"

def image_to_image(api_key, base_url, model_id, input_image, prompt, negative_prompt, image_count, resolution, aspect_ratio, strength):
    """图生图功能 - 调用 [baseurl]/v1beta/models/[modelid]:generateContent"""
    if not api_key or not api_key.strip():
        return None, "❌ 错误：请填写 API Key"
    
    if not model_id or not model_id.strip():
        return None, "❌ 错误：请填写或选择模型 ID"
    
    if input_image is None:
        return None, "❌ 错误：请先上传参考图片"
    
    try:
        base_url = base_url.rstrip("/")
        url = f"{base_url}/v1beta/models/{model_id.strip()}:generateContent"
        
        image_path = input_image if isinstance(input_image, str) else input_image
        base64_image = encode_image_to_base64(image_path, max_size=512, jpeg_quality=75)
        mime_type = get_image_mime_type(image_path)
        
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key.strip()
        }
        
        output_paths = []
        
        # 循环生成多张图片
        for i in range(int(image_count)):
            # 构建带图片的请求
            payload = {
                "contents": [{
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": base64_image
                            }
                        },
                        {
                            "text": prompt
                        }
                    ]
                }],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                }
            }
            
            if negative_prompt and negative_prompt.strip():
                payload["systemInstruction"] = {
                    "parts": [{"text": f"不要生成包含以下内容的内容：{negative_prompt.strip()}"}]
                }
            
            response = requests.post(url, json=payload, headers=headers, timeout=90)
            response.raise_for_status()
            
            result = response.json()
            
            # 解析返回的图片
            if "candidates" in result and len(result["candidates"]) > 0:
                candidate = result["candidates"][0]
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                
                for part in parts:
                    # 方式 1: inlineData 格式
                    if "inlineData" in part:
                        inline_data = part["inlineData"]
                        mime_type = inline_data.get("mimeType", "image/png")
                        if mime_type.startswith("image/"):
                            image_data = base64.b64decode(inline_data["data"])
                            
                            import io
                            from PIL import Image
                            image = Image.open(io.BytesIO(image_data))
                            
                            output_path = Path(f"{OUTPUT_DIR}/image_to_image_output_{i+1}.png")
                            output_path.parent.mkdir(parents=True, exist_ok=True)
                            image.save(output_path)
                            output_paths.append(str(output_path))
                            break
                    
                    # 方式 2: Markdown base64 格式 ![image](data:image/png;base64,...)
                    if "text" in part:
                        import re
                        text = part["text"]
                        md_pattern = r'!\[.*?\]\(data:(image/[a-z]+);base64,([A-Za-z0-9+/=]+)\)'
                        match = re.search(md_pattern, text)
                        if match:
                            mime_type = match.group(1)
                            image_data = base64.b64decode(match.group(2))
                            
                            import io
                            from PIL import Image
                            image = Image.open(io.BytesIO(image_data))
                            
                            output_path = Path(f"{OUTPUT_DIR}/image_to_image_output_{i+1}.png")
                            output_path.parent.mkdir(parents=True, exist_ok=True)
                            image.save(output_path)
                            output_paths.append(str(output_path))
                            break
                        
                        # 方式 3: Markdown 外部 URL 格式 ![Image](https://...)
                        url_pattern = r'!\[.*?\]\((https?://[^\s\)]+\.(png|jpg|jpeg|webp|gif))\)'
                        url_match = re.search(url_pattern, text, re.IGNORECASE)
                        if url_match:
                            image_url = url_match.group(1)
                            
                            # 下载图片
                            img_response = requests.get(image_url, timeout=30)
                            img_response.raise_for_status()
                            
                            import io
                            from PIL import Image
                            image = Image.open(io.BytesIO(img_response.content))
                            
                            output_path = Path(f"{OUTPUT_DIR}/image_to_image_output_{i+1}.png")
                            output_path.parent.mkdir(parents=True, exist_ok=True)
                            image.save(output_path)
                            output_paths.append(str(output_path))
                            break
        
        if output_paths:
            msg = f"✅ 生成成功 {len(output_paths)} 张！模型：{model_id}\n文件：{', '.join(output_paths)}"
            return output_paths[0], msg
        
        return None, "❌ 生成失败：未找到图片数据"
        
    except requests.exceptions.RequestException as e:
        return None, f"❌ 请求错误：{str(e)}"
    except Exception as e:
        return None, f"❌ 错误：{str(e)}"

def check_connection(api_key, base_url):
    """检查 API 连接状态"""
    if not api_key or not api_key.strip():
        return "⚠️ 请先填写 API Key"
    try:
        base_url = base_url.rstrip("/")
        test_url = f"{base_url}/v1beta/models"
        headers = {"X-Goog-Api-Key": api_key.strip()}
        response = requests.get(test_url, headers=headers, timeout=10)
        if response.status_code == 200:
            return f"✅ 连接成功！Base URL: {base_url}"
        else:
            return f"⚠️ 连接失败：HTTP {response.status_code}"
    except Exception as e:
        return f"❌ 连接错误：{str(e)}"

# 创建 Gradio 界面
with gr.Blocks(
    title="Gemini 图片工具",
    css="""
    footer, .footer, .footer-links, .api-settings, .gradio-footer { display: none !important; }
    .contain { padding-bottom: 0 !important; }
    """
) as demo:
    gr.Markdown("# 🎨 Gemini 图片生成与编辑工具")
    gr.Markdown("支持文生图和图生图功能")
    
    # 隐藏页脚
    demo.load(js="""
    () => {
        setTimeout(() => {
            document.querySelectorAll('footer, .footer, .footer-links, .api-settings').forEach(el => el.remove());
        }, 500);
    }
    """)
    
    # API 配置
    with gr.Accordion("🔑 API 配置", open=True):
        with gr.Row():
            api_key_input = gr.Textbox(
                label="API Key",
                placeholder="填入你的中转站 API Key",
                type="password",
                scale=2
            )
            base_url_input = gr.Textbox(
                label="Base URL (中转站地址)",
                value=DEFAULT_BASE_URL,
                placeholder="例如：https://your-proxy.com/v1beta",
                scale=3
            )
        
        with gr.Row():
            check_btn = gr.Button("🔌 测试连接", variant="secondary", scale=1)
            connection_status = gr.Textbox(label="连接状态", interactive=False, scale=2)
        
        check_btn.click(
            fn=check_connection,
            inputs=[api_key_input, base_url_input],
            outputs=[connection_status]
        )
    
    # 模型选择
    with gr.Accordion("🤖 模型选择", open=True):
        with gr.Row():
            fetch_models_btn = gr.Button("📋 获取模型列表", variant="secondary", scale=1)
            model_status = gr.Textbox(label="获取状态", interactive=False, scale=2)
        
        with gr.Row():
            model_dropdown = gr.Dropdown(
                label="选择模型（或手动输入自定义模型 ID）",
                choices=[DEFAULT_MODEL],
                value=DEFAULT_MODEL,
                allow_custom_value=True,
                scale=3
            )
            refresh_model_btn = gr.Button("🔄 刷新", variant="secondary", scale=1)
        
        fetch_models_btn.click(
            fn=fetch_models,
            inputs=[api_key_input, base_url_input],
            outputs=[model_dropdown, model_status]
        )
        
        refresh_model_btn.click(
            fn=fetch_models,
            inputs=[api_key_input, base_url_input],
            outputs=[model_dropdown, model_status]
        )
    
    with gr.Tab("📝 文生图"):
        with gr.Row():
            with gr.Column():
                txt_prompt = gr.Textbox(
                    label="提示词",
                    placeholder="描述你想要生成的图片，例如：一只可爱的猫咪在草地上晒太阳",
                    lines=3
                )
                txt_negative_prompt = gr.Textbox(
                    label="负面提示词（可选）",
                    placeholder="描述你不想要的内容，例如：模糊、低质量",
                    lines=2
                )
                txt_use_last = gr.Checkbox(label="🔄 基于上一张图编辑（连续对话）", value=False)
                txt_image_count = gr.Slider(1, 4, value=1, step=1, label="生成数量")
                txt_resolution = gr.Dropdown(
                    choices=["1K (1024x1024)", "2K (2048x2048)", "4K (4096x4096)"],
                    value="1K (1024x1024)",
                    label="分辨率"
                )
                txt_aspect_ratio = gr.Dropdown(
                    choices=["1:1", "16:9", "9:16", "4:3", "3:4"],
                    value="1:1",
                    label="图片比例"
                )
                txt_generate_btn = gr.Button("🎨 生成图片", variant="primary")
            
            with gr.Column():
                txt_output_image = gr.Image(label="生成的图片", type="filepath")
                txt_output_status = gr.Textbox(label="状态", interactive=False)
        
        def start_generation():
            return gr.Button(interactive=False, value="⏳ 生图中，请耐心等待")
        
        def end_generation():
            return gr.Button(interactive=True, value="🎨 生成图片")
        
        txt_generate_btn.click(
            fn=start_generation,
            inputs=[],
            outputs=[txt_generate_btn],
            queue=False
        ).then(
            fn=text_to_image,
            inputs=[api_key_input, base_url_input, model_dropdown, txt_prompt, txt_negative_prompt, txt_image_count, txt_resolution, txt_aspect_ratio, txt_use_last],
            outputs=[txt_output_image, txt_output_status]
        ).then(
            fn=end_generation,
            inputs=[],
            outputs=[txt_generate_btn],
            queue=False
        )
    
    with gr.Tab("🖼️ 图生图"):
        with gr.Row():
            with gr.Column():
                img_input = gr.Image(label="参考图片", type="filepath")
                img_prompt = gr.Textbox(
                    label="提示词",
                    placeholder="描述你想要如何修改这张图片",
                    lines=3
                )
                img_negative_prompt = gr.Textbox(
                    label="负面提示词（可选）",
                    placeholder="描述你不想要的内容",
                    lines=2
                )
                img_image_count = gr.Slider(1, 4, value=1, step=1, label="生成数量")
                img_resolution = gr.Dropdown(
                    choices=["1K (1024x1024)", "2K (2048x2048)", "4K (4096x4096)"],
                    value="1K (1024x1024)",
                    label="分辨率"
                )
                img_aspect_ratio = gr.Dropdown(
                    choices=["1:1", "16:9", "9:16", "4:3", "3:4"],
                    value="1:1",
                    label="图片比例"
                )
                img_strength = gr.Slider(0.1, 1.0, value=0.7, step=0.1, label="重绘强度（越高变化越大）")
                img_generate_btn = gr.Button("🎨 生成图片", variant="primary")
            
            with gr.Column():
                img_output_image = gr.Image(label="生成的图片", type="filepath")
                img_output_status = gr.Textbox(label="状态", interactive=False)
        
        def start_img_generation():
            return gr.Button(interactive=False, value="⏳ 生图中，请耐心等待")
        
        def end_img_generation():
            return gr.Button(interactive=True, value="🎨 生成图片")
        
        img_generate_btn.click(
            fn=start_img_generation,
            inputs=[],
            outputs=[img_generate_btn],
            queue=False
        ).then(
            fn=image_to_image,
            inputs=[api_key_input, base_url_input, model_dropdown, img_input, img_prompt, img_negative_prompt, img_image_count, img_resolution, img_aspect_ratio, img_strength],
            outputs=[img_output_image, img_output_status]
        ).then(
            fn=end_img_generation,
            inputs=[],
            outputs=[img_generate_btn],
            queue=False
        )
    
    gr.Markdown("---")
    gr.Markdown("Made with 🐾 by 老五 | 基于 Gemini API")

if __name__ == "__main__":
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print("Starting Gemini Image Tool (Docker Version)...")
    print(f"Working directory: /app")
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Default Base URL: {DEFAULT_BASE_URL}")
    print(f"Default Model: {DEFAULT_MODEL}")
    print("Open http://localhost:7860 in your browser")
    
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860
    )
