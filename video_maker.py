#!/usr/bin/env python3
"""
团购视频生成器 v3 — 修复 + 优化 + 多引擎 TTS + 批量调度

主要改进（vs v2）:
  - 修复 xfade 转场 offset 计算 Bug
  - 逐句 TTS + 精确时长对齐
  - API 重试机制
  - 多 TTS 引擎（Edge TTS 免费 + MiMo TTS）
  - 商家模板 YAML 外置
  - 配置文件 TOML 外置
  - logging 替代 print
  - FFmpeg 优化（减少重编码、并行 Ken Burns）
  - 批量任务调度

用法:
  python video_maker.py --topic "宠物寄养" --images 素材/ --output 输出/
  python video_maker.py --template templates/example.yaml --topic "宠物寄养" --images 素材/
  python video_maker.py --batch batch_tasks.json --images 素材/
"""
import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from functools import wraps
from pathlib import Path
from typing import Optional

import httpx
import yaml
from PIL import Image, ImageDraw, ImageFont, ImageFile

# 新增：Pexels 素材 + 背景音乐自动匹配
try:
    from pexels import fetch_pexels_videos
except ImportError:
    fetch_pexels_videos = None


def _get_ffmpeg() -> str:
    """获取 ffmpeg 可执行文件路径，确保在 PATH 中可找到"""
    path = shutil.which("ffmpeg")
    if path:
        return path
    # 回退到 imageio-ffmpeg 内置的 ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, Exception):
        pass
    # 最后回退到裸命令（可能 FileNotFoundError）
    return "ffmpeg"


# 全局 ffmpeg 路径（启动时解析一次）
_FFMPEG = _get_ffmpeg()

try:
    from bgm import select_bgm, auto_match_bgm, BGMSelector
except ImportError:
    select_bgm = None
    auto_match_bgm = None
    BGMSelector = None

ImageFile.LOAD_TRUNCATED_IMAGES = True

# ── 日志 ──────────────────────────────────────────────
logger = logging.getLogger("video_maker")
logger.debug(f"ffmpeg path: {_FFMPEG}")


def setup_logging(level=logging.INFO, log_file: str = None):
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


# ── 配置管理 ──────────────────────────────────────────
DEFAULT_CONFIG = {
    "api": {
        "xiaomi_api_key": "",
        "xiaomi_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "llm_model": "mimo-v2.5-pro",
        "tts_model": "mimo-v2.5-tts",
        "tts_voice": "mimo_default",
        "timeout": 120,
        "max_retries": 3,
        "retry_delay": 2,
    },
    "video": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "img_duration": 4,
        "transition_duration": 0.5,
        "subtitle_style": 8,
        "encode_preset": "fast",
        "crf": 23,
    },
    "audio": {
        "bgm_volume": 0.12,
        "voice_volume": 1.0,
        "silence_between": 0.15,
        "tts_engine": "edge_tts",
        "edge_tts_voice": "zh-CN-XiaoxiaoNeural",
        "bgm_auto_match": True,
        "music_dir": "音乐",
    },
    "pexels": {
        "api_key": "",
        "download_dir": "素材/pexels",
        "count_per_keyword": 2,
        "orientation": "portrait",
        "min_duration": 5,
        "max_duration": 30,
    },
    "batch": {
        "max_workers": 4,
        "output_dir": "输出",
        "temp_dir": "",
    },
    "tts": {
        "concurrency": 5,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并字典，override 覆盖 base"""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_dotenv():
    """加载 .env 文件到 os.environ（不覆盖已有值）"""
    env_paths = [
        Path(__file__).parent / ".env",
        Path.cwd() / ".env",
        Path.home() / ".hermes" / ".env",  # 兼容 hermes 工具链
    ]
    for env_path in env_paths:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val
            # 不 break，继续扫描所有路径（后续路径不覆盖已有值）


def _resolve_env_vars(value):
    """解析 ${ENV_VAR} 或 ${ENV_VAR|default_value} 格式的环境变量，支持嵌套 dict/list"""
    if isinstance(value, str):
        # 支持 ${VAR|default} 语法：如果环境变量不存在则使用默认值
        pattern = re.compile(r'\$\{(\w+)(?:\|([^}]*))?\}')
        def replacer(m):
            var_name = m.group(1)
            default_val = m.group(2) or ""
            return os.environ.get(var_name, default_val)
        return pattern.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


def load_config(config_path: str = None) -> dict:
    """加载配置，优先级：config.toml > 环境变量 > 默认值"""
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        import tomli as tomllib  # Python 3.9 fallback

    # 先加载 .env 文件（如果存在），将环境变量注入 os.environ
    _load_dotenv()

    # 使用深拷贝防止嵌套 dict 修改污染全局默认值
    import copy
    config = copy.deepcopy(DEFAULT_CONFIG)

    # 尝试加载 config.toml
    search_paths = [
        config_path,
        "config.toml",
        Path(__file__).parent / "config.toml",
    ]

    for path in search_paths:
        if path and Path(path).exists():
            try:
                with open(path, "rb") as f:
                    file_config = tomllib.load(f)
                config = _deep_merge(config, file_config)
                logger.debug(f"加载配置: {path}")
                break
            except Exception as e:
                logger.warning(f"加载配置失败 {path}: {e}")

    # 环境变量覆盖 API Key
    env_key = os.environ.get("XIAOMI_API_KEY", "")
    if env_key:
        config["api"]["xiaomi_api_key"] = env_key

    # 解析环境变量引用
    config = _resolve_env_vars(config)

    return config


# ── 字体 ──────────────────────────────────────────────
# 优先使用 .ttf，.ttc 作为备选（需要 index 参数）
FONT_PATHS = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",  # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Linux
]

_font_cache = {}


def get_font(size: int = 52) -> str:
    """获取字体路径，带缓存"""
    if size in _font_cache:
        return _font_cache[size]
    for fp in FONT_PATHS:
        if os.path.exists(fp):
            _font_cache[size] = fp
            return fp
    raise FileNotFoundError("找不到中文字体，请安装字体或修改 FONT_PATHS")


def _load_font(font_path: str, size: int):
    """加载字体，兼容 .ttc 字体集合文件"""
    try:
        return ImageFont.truetype(font_path, size)
    except OSError:
        # .ttc 文件在某些 Pillow 版本需要指定 index
        if font_path.endswith(".ttc"):
            try:
                return ImageFont.truetype(font_path, size, index=0)
            except (OSError, TypeError):
                pass
        raise


# ── 字幕样式 ──────────────────────────────────────────
SUBTITLE_STYLES = {
    1: {
        "name": "经典白字黑底",
        "font_size": 52,
        "color": (255, 255, 255),
        "shadow": (0, 0, 0),
        "bg": (0, 0, 0, 140),
        "position": "bottom",
        "margin": 120,
        "highlight_words": [],
        "highlight_color": (255, 230, 0),
    },
    2: {
        "name": "活力黄字+关键词高亮",
        "font_size": 54,
        "color": (255, 230, 0),
        "shadow": (0, 0, 0),
        "bg": (0, 0, 0, 170),
        "position": "bottom",
        "margin": 100,
        "highlight_words": ["58元", "38元", "24小时", "免费", "优惠", "团购", "限时", "快"],
        "highlight_color": (255, 80, 80),
    },
    3: {
        "name": "清新绿字",
        "font_size": 50,
        "color": (100, 255, 100),
        "shadow": (0, 60, 0),
        "bg": (0, 0, 0, 130),
        "position": "bottom",
        "margin": 130,
        "highlight_words": [],
        "highlight_color": (255, 255, 255),
    },
    4: {
        "name": "热情红字+价格高亮",
        "font_size": 54,
        "color": (255, 70, 70),
        "shadow": (255, 255, 255),
        "bg": (0, 0, 0, 180),
        "position": "bottom",
        "margin": 110,
        "highlight_words": ["元", "折", "优惠", "抢", "限时"],
        "highlight_color": (255, 255, 0),
    },
    5: {
        "name": "居中大字",
        "font_size": 64,
        "color": (255, 255, 255),
        "shadow": (0, 0, 0),
        "bg": (0, 0, 0, 190),
        "position": "center",
        "margin": 0,
        "highlight_words": [],
        "highlight_color": (255, 230, 0),
    },
    6: {
        "name": "顶部字幕",
        "font_size": 48,
        "color": (255, 255, 255),
        "shadow": (0, 0, 0),
        "bg": (0, 0, 0, 160),
        "position": "top",
        "margin": 80,
        "highlight_words": [],
        "highlight_color": (255, 230, 0),
    },
    7: {
        "name": "渐变蓝字",
        "font_size": 52,
        "color": (100, 200, 255),
        "shadow": (0, 0, 80),
        "bg": (0, 0, 0, 150),
        "position": "bottom",
        "margin": 120,
        "highlight_words": [],
        "highlight_color": (255, 255, 255),
    },
    8: {
        "name": "抖音风（黄字+红色高亮）",
        "font_size": 56,
        "color": (255, 230, 0),
        "shadow": (0, 0, 0),
        "bg": (0, 0, 0, 180),
        "position": "bottom",
        "margin": 100,
        "highlight_words": ["元", "折", "免费", "优惠", "团购", "限时", "抢", "快", "送"],
        "highlight_color": (255, 50, 50),
    },
}


# ── 封面图生成 ──────────────────────────────────────────

def generate_cover(title: str, subtitle: str = "", w: int = 1080, h: int = 1920, brand: str = "") -> str:
    """生成封面图，返回临时文件路径"""
    import tempfile
    img = Image.new("RGB", (w, h), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 渐变背景
    for y in range(h):
        r = int(15 + 25 * (y / h))
        g = int(15 + 15 * (y / h))
        b = int(35 + 40 * (y / h))
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    # 标题
    try:
        title_font = _load_font(get_font(72), 72)
        sub_font = _load_font(get_font(36), 36)
        brand_font = _load_font(get_font(28), 28)
    except Exception:
        title_font = ImageFont.load_default()
        sub_font = ImageFont.load_default()
        brand_font = ImageFont.load_default()

    # 标题居中
    bbox = draw.textbbox((0, 0), title, font=title_font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (w - tw) // 2
    ty = (h - th) // 2 - 60

    # 标题阴影
    draw.text((tx + 3, ty + 3), title, fill=(0, 0, 0), font=title_font)
    draw.text((tx, ty), title, fill=(255, 255, 255), font=title_font)

    # 副标题
    if subtitle:
        bbox2 = draw.textbbox((0, 0), subtitle, font=sub_font)
        sw = bbox2[2] - bbox2[0]
        sx = (w - sw) // 2
        sy = ty + th + 30
        draw.text((sx + 2, sy + 2), subtitle, fill=(0, 0, 0), font=sub_font)
        draw.text((sx, sy), subtitle, fill=(200, 200, 200), font=sub_font)

    # 品牌
    if brand:
        bbox3 = draw.textbbox((0, 0), brand, font=brand_font)
        bw = bbox3[2] - bbox3[0]
        bx = (w - bw) // 2
        by = h - 150
        draw.text((bx, by), brand, fill=(150, 150, 150), font=brand_font)

    # 使用固定临时目录，避免 NamedTemporaryFile 的文件锁和泄漏问题
    tmp_dir = Path(tempfile.gettempdir())
    cover_path = str(tmp_dir / f"cover_{hashlib.md5(title.encode()).hexdigest()[:8]}.jpg")
    img.save(cover_path, quality=95)
    return cover_path


# ── PIL 字幕动画 ──────────────────────────────────────────

def _get_subtitle_geometry(text: str, style: dict, w: int, h: int, font) -> tuple:
    """计算字幕文字布局：返回 (lines, line_height, bar_top, bar_bottom)"""
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    max_width = w - 80
    lines = []
    current_line = ""
    for char in text:
        test_line = current_line + char
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] > max_width:
            lines.append(current_line)
            current_line = char
        else:
            current_line = test_line
    if current_line:
        lines.append(current_line)

    line_height = style["font_size"] + 14
    total_height = line_height * len(lines)

    if style["position"] == "top":
        bar_top = style["margin"]
    elif style["position"] == "center":
        bar_top = (h - total_height) // 2
    else:
        bar_top = h - style["margin"] - total_height - 24

    bar_bottom = bar_top + total_height + 24
    return lines, line_height, bar_top, bar_bottom


def _render_subtitle_frame(base_img: Image.Image, text: str, style_id: int,
                           alpha: float = 1.0, y_offset: int = 0,
                           typewriter_chars: int = -1,
                           position_override: str = None,
                           margin_override: int = None) -> Image.Image:
    """渲染单帧字幕（支持 alpha/偏移/打字机）

    Args:
        base_img: 基础图片（无字幕）
        text: 字幕文本
        style_id: 样式 ID
        alpha: 字幕透明度 0.0~1.0
        y_offset: Y 轴偏移（正值向下，用于 slide 动画）
        typewriter_chars: 打字机模式显示的字符数，-1 表示全部显示
        position_override: 覆盖样式中的 position（top/center/bottom）
        margin_override: 覆盖样式中的 margin（像素）
    """
    style = dict(SUBTITLE_STYLES.get(style_id, SUBTITLE_STYLES[1]))  # shallow copy
    if position_override:
        style["position"] = position_override
    if margin_override is not None:
        style["margin"] = margin_override
    w, h = base_img.size
    font = _load_font(get_font(style["font_size"]), style["font_size"])

    lines, line_height, bar_top, bar_bottom = _get_subtitle_geometry(text, style, w, h, font)

    # 整体偏移
    bar_top += y_offset
    bar_bottom += y_offset

    img = base_img.copy().convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)

    # 背景条（带 alpha）
    bg = style["bg"]
    bg_alpha = int(bg[3] * alpha) if len(bg) > 3 else int(180 * alpha)
    overlay_draw.rectangle([(0, bar_top), (w, bar_bottom)], fill=(*bg[:3], bg_alpha))

    # 画文字
    y = bar_top + 12
    char_count = 0
    for line in lines:
        bbox = overlay_draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        x = (w - line_width) // 2

        cx = x
        i = 0
        while i < len(line):
            # 打字机模式：超过显示字符数就停
            if typewriter_chars >= 0 and char_count >= typewriter_chars:
                break

            matched = False
            for hw in style.get("highlight_words", []):
                if line[i:i + len(hw)] == hw:
                    for ch in hw:
                        if typewriter_chars >= 0 and char_count >= typewriter_chars:
                            break
                        overlay_draw.text((cx + 2, y + 2), ch, font=font,
                                         fill=(*style["shadow"][:3], int(255 * alpha)))
                        overlay_draw.text((cx, y), ch, font=font,
                                         fill=(*style["highlight_color"][:3], int(255 * alpha)))
                        b = overlay_draw.textbbox((0, 0), ch, font=font)
                        cx += b[2] - b[0]
                        char_count += 1
                    i += len(hw)
                    matched = True
                    break
            if not matched:
                ch = line[i]
                overlay_draw.text((cx + 2, y + 2), ch, font=font,
                                 fill=(*style["shadow"][:3], int(255 * alpha)))
                overlay_draw.text((cx, y), ch, font=font,
                                 fill=(*style["color"][:3], int(255 * alpha)))
                b = overlay_draw.textbbox((0, 0), ch, font=font)
                cx += b[2] - b[0]
                i += 1
                char_count += 1

        y += line_height
        if typewriter_chars >= 0 and char_count >= typewriter_chars:
            break

    img = Image.alpha_composite(img, overlay).convert("RGB")
    return img


def generate_animated_frames(base_img: Image.Image, text: str, style_id: int,
                             animation: str, fps: int, anim_duration: float,
                             output_dir: str, prefix: str,
                             position_override: str = None,
                             margin_override: int = None) -> list:
    """生成字幕动画帧序列

    Args:
        base_img: 基础图片（无字幕）
        text: 字幕文本
        style_id: 样式 ID
        animation: 动画类型 (none/fade/slide/typewriter)
        fps: 帧率
        anim_duration: 动画持续时间（秒）
        output_dir: 输出目录
        prefix: 文件名前缀
    Returns:
        帧文件路径列表
    """
    if animation == "none":
        # 无动画：只生成一帧带字幕的图
        frame = _render_subtitle_frame(base_img, text, style_id,
                                       position_override=position_override,
                                       margin_override=margin_override)
        path = os.path.join(output_dir, f"{prefix}_sub.jpg")
        frame.save(path, quality=95)
        return [path]

    n_frames = max(int(anim_duration * fps), 2)
    frames = []
    style = dict(SUBTITLE_STYLES.get(style_id, SUBTITLE_STYLES[1]))
    if position_override:
        style["position"] = position_override
    if margin_override is not None:
        style["margin"] = margin_override
    w, h = base_img.size
    font = _load_font(get_font(style["font_size"]), style["font_size"])
    lines, line_height, bar_top, bar_bottom = _get_subtitle_geometry(text, style, w, h, font)
    total_chars = sum(len(line) for line in lines)

    for f_idx in range(n_frames):
        progress = f_idx / (n_frames - 1)  # 0.0 → 1.0
        path = os.path.join(output_dir, f"{prefix}_sub_{f_idx:03d}.jpg")

        if animation == "fade":
            # 淡入：alpha 从 0 → 1
            alpha = progress
            frame = _render_subtitle_frame(base_img, text, style_id, alpha=alpha,
                                           position_override=position_override,
                                           margin_override=margin_override)

        elif animation == "slide":
            # 滑入：从画面底部滑到目标位置
            slide_distance = style["margin"] + 100  # 从更下方滑入
            y_offset = int(slide_distance * (1 - progress))
            frame = _render_subtitle_frame(base_img, text, style_id, y_offset=y_offset,
                                           position_override=position_override,
                                           margin_override=margin_override)

        elif animation == "typewriter":
            # 打字机：逐字出现
            chars_to_show = int(total_chars * progress) + 1
            frame = _render_subtitle_frame(base_img, text, style_id, typewriter_chars=chars_to_show,
                                           position_override=position_override,
                                           margin_override=margin_override)

        else:
            frame = _render_subtitle_frame(base_img, text, style_id)

        frame.save(path, quality=95)
        frames.append(path)

    return frames


# ── 商家模板 ──────────────────────────────────────────
def load_template(template_path: str) -> dict:
    """加载商家模板 YAML"""
    if not template_path or not Path(template_path).exists():
        return {}
    with open(template_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def template_to_prompt_context(template: dict) -> str:
    """将商家模板转换为 prompt 上下文"""
    if not template:
        return ""

    merchant = template.get("merchant", {})
    services = template.get("services", {})
    highlights = template.get("highlights", [])
    trust = template.get("trust", [])
    membership = template.get("membership", [])
    rules = template.get("script_rules", {})
    regions = template.get("regions", [])

    parts = []
    parts.append(f"品牌信息：")
    parts.append(f"- 品牌名：{merchant.get('name', '')}")
    parts.append(f"- 全称：{merchant.get('full_name', '')}")
    parts.append(f"- 地址：{merchant.get('address', '')}")
    parts.append(f"- 电话：{merchant.get('phone', '')}")
    parts.append(f"- 联系人：{merchant.get('contact', '')}")
    parts.append(f"- 营业时间：{merchant.get('hours', '')}")

    if regions:
        parts.append(f"\n地域关键词：{'、'.join(regions)}")

    for svc_name, svc_items in services.items():
        parts.append(f"\n{svc_name}服务：")
        for item in svc_items:
            if "note" in item:
                parts.append(f"- {item['note']}")
            else:
                price_info = f"{item.get('price', '')}{item.get('unit', '元')}" if item.get("price") else ""
                cond = item.get("condition", item.get("name", ""))
                parts.append(f"- {cond}：{price_info}")

    if membership:
        parts.append("\n会员储值：")
        for m in membership:
            parts.append(f"- 充{m['topup']}送{m['bonus']}，寄养{m['discount']}")

    if highlights:
        parts.append("\n核心卖点：")
        for h in highlights:
            parts.append(f"- {h}")

    if trust:
        parts.append("\n信任背书：")
        for t in trust:
            parts.append(f"- {t}")

    return "\n".join(parts)


# ── API 重试机制 ──────────────────────────────────────
class APIError(Exception):
    """API 调用异常"""
    def __init__(self, message: str, status_code: int = 0, retry_after: int = 0):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


async def api_call_with_retry(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    payload: dict,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    timeout: float = 120,
) -> dict:
    """带重试的 API 调用"""
    last_error = None
    for attempt in range(max_retries):
        try:
            resp = await client.post(url, headers=headers, json=payload, timeout=timeout)

            if resp.status_code == 429:
                # 限流：等待后重试
                retry_after = int(resp.headers.get("Retry-After", retry_delay * (attempt + 1)))
                logger.warning(f"API 限流，等待 {retry_after}s 后重试 ({attempt + 1}/{max_retries})")
                await asyncio.sleep(retry_after)
                continue

            if resp.status_code >= 500:
                # 服务器错误：指数退避重试
                delay = retry_delay * (2 ** attempt)
                logger.warning(f"API 服务器错误 {resp.status_code}，{delay}s 后重试 ({attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
                continue

            resp.raise_for_status()
            return resp.json()

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            delay = retry_delay * (2 ** attempt)
            logger.warning(f"API 网络错误: {e}，{delay}s 后重试 ({attempt + 1}/{max_retries})")
            last_error = e
            await asyncio.sleep(delay)

        except httpx.HTTPStatusError as e:
            if e.response.status_code < 500:
                # 4xx 错误不重试
                raise APIError(f"API 错误 {e.response.status_code}: {e.response.text}",
                             status_code=e.response.status_code)
            delay = retry_delay * (2 ** attempt)
            logger.warning(f"API HTTP 错误 {e.response.status_code}，{delay}s 后重试 ({attempt + 1}/{max_retries})")
            last_error = e
            await asyncio.sleep(delay)

    raise APIError(f"API 调用失败，已重试 {max_retries} 次: {last_error}")


# ── 图片处理 ──────────────────────────────────────────
_yolo_model = None
def get_yolo_model():
    global _yolo_model
    if _yolo_model is None:
        try:
            from ultralytics import YOLO
            import logging
            logging.getLogger("ultralytics").setLevel(logging.WARNING)
            _yolo_model = YOLO("yolov8n.pt")
        except Exception as e:
            logger.warning(f"YOLO 模型加载失败: {e}")
            _yolo_model = False
    return _yolo_model

def smart_crop_resize(img_path: str, w: int, h: int) -> Image.Image:
    """智能裁剪：利用 YOLOv8 AI 模型识别宠物主体并以此为中心进行裁剪"""
    img = Image.open(img_path).convert("RGB")
    src_w, src_h = img.size
    target_ratio = w / h
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        new_h = h
        new_w = int(src_w * (h / src_h))
    else:
        new_w = w
        new_h = int(src_h * (w / src_w))

    resized_img = img.resize((new_w, new_h), Image.LANCZOS)
    
    # 默认居中坐标
    left = (new_w - w) // 2
    top = (new_h - h) // 2

    model = get_yolo_model()
    dog_box = None
    if model:
        try:
            results = model(resized_img, verbose=False)
            max_area = 0
            for result in results:
                for box in result.boxes:
                    cls = int(box.cls[0])
                    if cls in [15, 16]:  # cat or dog
                        x1, y1, x2, y2 = box.xyxy[0].tolist()
                        area = (x2 - x1) * (y2 - y1)
                        if area > max_area:
                            max_area = area
                            dog_box = (x1, y1, x2, y2)
        except Exception as e:
            logger.warning(f"YOLO 裁剪推理失败: {e}")

    use_contain = False
    if dog_box:
        x1, y1, x2, y2 = dog_box
        box_w = x2 - x1
        box_h = y2 - y1
        
        # 如果狗占据了目标画面极大部分（大于85%），说明画面太满，缺乏留白，触发包含模式
        if box_w > w * 0.85 or box_h > h * 0.85:
            use_contain = True
            
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        left = int(center_x - w / 2)
        top = int(center_y - h / 2 - (y2 - y1) * 0.1)
        left = max(0, min(left, new_w - w))
        top = max(0, min(top, new_h - h))
    else:
        # 没找到狗的情况下，如果原图比例和目标比例差距极大（比如横图配竖屏），也触发包含模式
        if max(src_ratio/target_ratio, target_ratio/src_ratio) > 2.0:
            use_contain = True

    if use_contain:
        from PIL import ImageFilter
        # 1. 制作高斯模糊背景（用 cover 裁切后的结果）
        bg = resized_img.crop((left, top, left + w, top + h))
        bg = bg.filter(ImageFilter.GaussianBlur(radius=30))
        
        # 2. 制作含留白的前景
        if src_ratio > target_ratio:
            fg_w = w
            fg_h = int(src_h * (w / src_w))
        else:
            fg_h = h
            fg_w = int(src_w * (h / src_h))
            
        # 强制留出 10% 的安全边距 (留白)
        margin_scale = 0.9
        fg_w = max(1, int(fg_w * margin_scale))
        fg_h = max(1, int(fg_h * margin_scale))
        
        fg = img.resize((fg_w, fg_h), Image.LANCZOS)
        
        # 将前景居中贴在模糊背景上
        fg_left = (w - fg_w) // 2
        fg_top = (h - fg_h) // 2
        bg.paste(fg, (fg_left, fg_top))
        return bg

    return resized_img.crop((left, top, left + w, top + h))


def make_collage(image_paths: list, w: int, h: int) -> Image.Image:
    """多图拼贴"""
    count = len(image_paths)
    canvas = Image.new("RGB", (w, h), (30, 30, 30))

    if count == 1:
        return smart_crop_resize(image_paths[0], w, h)
    elif count == 2:
        # 上下分屏，避免左右分屏导致画面极度狭窄被截断
        half_h = h // 2
        gap = 4
        for i, path in enumerate(image_paths):
            img = smart_crop_resize(path, w, half_h - gap // 2)
            canvas.paste(img, (0, i * (half_h + gap // 2)))
    elif count == 3:
        # 上半屏全宽1张，下半屏左右2张，避免左右分屏出现细长条
        half_w = w // 2
        half_h = h // 2
        gap = 4
        # 上面一张
        canvas.paste(smart_crop_resize(image_paths[0], w, half_h - gap // 2), (0, 0))
        # 下面两张
        for i in range(2):
            img = smart_crop_resize(image_paths[i + 1], half_w - gap // 2, half_h - gap // 2)
            canvas.paste(img, (i * (half_w + gap // 2), half_h + gap // 2))
    else:
        # 4格拼图
        half_w = w // 2
        half_h = h // 2
        gap = 4
        positions = [
            (0, 0), (half_w + gap // 2, 0),
            (0, half_h + gap // 2), (half_w + gap // 2, half_h + gap // 2),
        ]
        for i in range(4):
            img = smart_crop_resize(image_paths[i % count], half_w - gap // 2, half_h - gap // 2)
            canvas.paste(img, positions[i])

    return canvas


def add_subtitle(img: Image.Image, text: str, style_id: int = 1,
                  position_override: str = None, margin_override: int = None) -> Image.Image:
    """添加字幕（支持关键词高亮）"""
    style = dict(SUBTITLE_STYLES.get(style_id, SUBTITLE_STYLES[1]))
    if position_override:
        style["position"] = position_override
    if margin_override is not None:
        style["margin"] = margin_override
    w, h = img.size
    font = _load_font(get_font(style["font_size"]), style["font_size"])
    draw = ImageDraw.Draw(img)

    # 自动换行
    max_width = w - 80
    lines = []
    current_line = ""
    for char in text:
        test_line = current_line + char
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] > max_width:
            lines.append(current_line)
            current_line = char
        else:
            current_line = test_line
    if current_line:
        lines.append(current_line)

    line_height = style["font_size"] + 14
    total_height = line_height * len(lines)

    # 位置
    if style["position"] == "top":
        bar_top = style["margin"]
    elif style["position"] == "center":
        bar_top = (h - total_height) // 2
    else:
        bar_top = h - style["margin"] - total_height - 24

    bar_bottom = bar_top + total_height + 24

    # 背景条
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle([(0, bar_top), (w, bar_bottom)], fill=style["bg"])
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # 画文字（支持关键词高亮）
    y = bar_top + 12
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        x = (w - line_width) // 2

        cx = x
        i = 0
        while i < len(line):
            matched = False
            for hw in style.get("highlight_words", []):
                if line[i:i + len(hw)] == hw:
                    for ch in hw:
                        draw.text((cx + 2, y + 2), ch, font=font, fill=style["shadow"])
                        draw.text((cx, y), ch, font=font, fill=style["highlight_color"])
                        bbox = draw.textbbox((0, 0), ch, font=font)
                        cx += bbox[2] - bbox[0]
                    i += len(hw)
                    matched = True
                    break
            if not matched:
                ch = line[i]
                draw.text((cx + 2, y + 2), ch, font=font, fill=style["shadow"])
                draw.text((cx, y), ch, font=font, fill=style["color"])
                bbox = draw.textbbox((0, 0), ch, font=font)
                cx += bbox[2] - bbox[0]
                i += 1

        y += line_height

    return img


# ── AI 文案生成 ───────────────────────────────────────
async def generate_script(
    topic: str,
    config: dict,
    style: str = "团购种草",
    count: int = 8,
    template: dict = None,
    batch_index: int = None,
    batch_total: int = None,
) -> list:
    """用 MiMo 生成文案，支持商家模板

    Args:
        batch_index: 批量生成时的序号（从1开始），用于确保每条视频文案不同
        batch_total: 批量生成总数
    """
    api_key = config["api"]["xiaomi_api_key"]
    if not api_key:
        raise ValueError("未配置 XIAOMI_API_KEY，请设置环境变量或在 config.toml 中配置")

    # 构建 prompt 上下文
    if template:
        merchant_info = template_to_prompt_context(template)
        rules = template.get("script_rules", {})
        style_desc = rules.get("style", style)
        sent_count = rules.get("sentence_count", count)
        sent_length = rules.get("sentence_length", "10-20字")
        opening = rules.get("opening", "用痛点/场景切入")
        closing = rules.get("closing", "行动号召（评论扣1 / 点击团购 / 私信咨询）")
        emphasis = rules.get("emphasis", [])
        persona = rules.get("persona", "第三人称")
    else:
        # 无模板时使用默认商家信息（向后兼容）
        merchant_info = """品牌信息：
- 品牌名：你的店铺名称
- 全称：你的店铺全称
- 地址：你的店铺地址
- 电话：你的联系电话
- 联系人：联系人姓名
- 营业时间：10:00-22:00

寄养服务：
- 中小间：58元/天（0-25KG）
- 大间：98元/天（25-50KG）
- 超大间：148元/天（无体重限制）
- 同一房间多只狗狗，总价优惠20%

日托服务：
- 25KG以下：38元/天
- 25KG以上：78元/天

洗澡服务：
- 0-5KG：39元 | 5-15KG：59元 | 15-25KG：99元 | >25KG：159元

游泳服务：
- ≤15KG：38元/次 | >15KG：68元/次

会员储值：
- 充1000送188，寄养9折
- 充5000送388，寄养8.5折
- 充10000送588，寄养8折

核心卖点：
- 独立房间，非笼养（单次笼养≤4小时，单日≤8小时）
- 空调恒温 + 新风系统
- 24小时监控直连手机，主人随时查看
- 每天紫外线消毒 + 消毒液拖地擦拭
- 高频接触面每日消毒不少于2次
- 每日至少2小时户外活动
- 按体型、性格科学分组管理
- 每日上传狗狗视频至微信群聊
- 采用正向行为管理，严禁惩罚式饲养
- 寄养10公里内免费接送
- 日托5公里内免费接送

信任背书：
- 营业执照齐全
- 服务依据：《民法典》保管合同第888-903条
- 环境标准：GB/T 45204-2025
- 笼舍标准：USDA 9 CFR § 3.6
- 行为管理：IAABC LIMA原则"""
        style_desc = style
        sent_count = count
        sent_length = "10-20个字"
        opening = "痛点/场景切入"
        closing = "行动号召（评论扣1 / 点击团购 / 私信咨询）"
        emphasis = ["突出具体价格数字（58元、38元等）", "突出安全性（监控、消毒、独立房间）"]
        persona = "第三人称（'这家''这里'），不要出现'我们'"

    emphasis_text = "\n".join(f"  {i+1}. {e}" for i, e in enumerate(emphasis)) if emphasis else ""

    # 批量生成时添加差异化提示，确保每条视频文案不重复
    batch_hint = ""
    if batch_index is not None and batch_total is not None and batch_total > 1:
        batch_hint = f"""

【重要】这是批量生成的第 {batch_index}/{batch_total} 条视频的文案。
请确保与其他视频的文案完全不同，使用不同的切入角度、不同的表述方式和不同的侧重点。
可以从以下角度中选择：情感共鸣、价格优势、安全保障、服务细节、用户体验、对比反差等。"""

    prompt = f"""你是一个专业的短视频文案策划师，专门为本地生活商家撰写抖音团购视频文案。

请为以下主题生成一段{style_desc}风格的短视频口播文案：

主题：{topic}

【商家完整资料】
{merchant_info}

要求：
1. {style_desc}，适合抖音配音
2. 每句{sent_length}
3. 共{sent_count}句
4. 开头{opening}，结尾有{closing}
{emphasis_text}
5. 不要编号，每句一行
6. 用{persona}
{batch_hint}
请直接输出文案："""

    base_url = config["api"]["xiaomi_base_url"]
    model = config["api"]["llm_model"]
    max_retries = config["api"]["max_retries"]
    retry_delay = config["api"]["retry_delay"]

    async with httpx.AsyncClient(timeout=config["api"]["timeout"]) as client:
        result = await api_call_with_retry(
            client,
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.8,
                "max_tokens": 1000,
            },
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            raise ValueError("AI 返回空文案，请检查 API 配置或稍后重试")

    sentences = []
    for line in content.strip().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            line = re.sub(r'^[\d]+[.、)）]\s*', '', line)
            if line:
                sentences.append(line)
    return sentences


# ── TTS 引擎 ──────────────────────────────────────────
async def generate_tts_mimo(
    text: str,
    output_path: str,
    config: dict,
    voice: str = None,
):
    """MiMo TTS 合成"""
    api_key = config["api"]["xiaomi_api_key"]
    if not api_key:
        raise ValueError("未配置 XIAOMI_API_KEY，MiMo TTS 不可用")

    base_url = config["api"]["xiaomi_base_url"]
    model = config["api"]["tts_model"]
    tts_voice = voice or config["api"]["tts_voice"]
    max_retries = config["api"]["max_retries"]
    retry_delay = config["api"]["retry_delay"]

    async with httpx.AsyncClient(timeout=config["api"]["timeout"]) as client:
        result = await api_call_with_retry(
            client,
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload={
                "model": model,
                "messages": [
                    {"role": "user", "content": ""},
                    {"role": "assistant", "content": text},
                ],
                "audio": {"format": "wav", "voice": tts_voice},
            },
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        audio_b64 = result.get("choices", [{}])[0].get("message", {}).get("audio", {}).get("data")
        if not audio_b64:
            logger.error(f"❌ MiMo TTS 未返回音频数据 (model={model}, voice={tts_voice})")
            raise ValueError("MiMo TTS 未返回音频数据，请检查 API 配置")

    audio_bytes = base64.b64decode(audio_b64)

    # 校验音频数据有效性
    if len(audio_bytes) < 1024:
        logger.error(f"❌ MiMo TTS 返回音频数据过小（{len(audio_bytes)} 字节）")
        raise ValueError(f"MiMo TTS 返回音频数据过小（{len(audio_bytes)} 字节），可能生成失败")

    # 简单校验 WAV 头部（RIFF....WAVE）
    if not audio_bytes[:4] == b"RIFF":
        logger.warning(f"MiMo TTS 返回的音频数据不是标准 WAV 格式，头部: {audio_bytes[:8].hex()}")

    Path(output_path).write_bytes(audio_bytes)
    logger.debug(f"MiMo TTS 音频已保存: {output_path} ({len(audio_bytes)} 字节)")


async def generate_tts_edge(
    text: str,
    output_path: str,
    config: dict,
    voice: str = None,
):
    """Edge TTS 合成（免费）"""
    try:
        import edge_tts
    except ImportError:
        logger.error("❌ edge-tts 未安装，请运行: pip install edge-tts")
        raise RuntimeError("edge-tts 未安装，请运行: pip install edge-tts")

    tts_voice = voice or config["audio"].get("edge_tts_voice", "zh-CN-XiaoxiaoNeural")
    default_voice = "zh-CN-XiaoxiaoNeural"

    # Edge TTS 免费版支持的中文语音白名单
    # 注意：Azure 付费版有更多语音，但免费 edge-tts 仅支持以下
    _EDGE_TTS_ZH_VOICES = {
        "zh-CN-XiaoxiaoNeural",
        "zh-CN-XiaoyiNeural",
        "zh-CN-YunjianNeural",
        "zh-CN-YunxiNeural",
        "zh-CN-YunxiaNeural",
        "zh-CN-YunyangNeural",
        "zh-CN-liaoning-XiaobeiNeural",
        "zh-CN-shaanxi-XiaoniNeural",
    }

    # 验证语音是否可用，无效时回退到默认语音
    if tts_voice not in _EDGE_TTS_ZH_VOICES:
        logger.warning(
            f"⚠️ Edge TTS 不支持语音 '{tts_voice}'（仅免费版语音可用），"
            f"自动回退到默认语音 '{default_voice}'"
        )
        tts_voice = default_voice

    try:
        communicate = edge_tts.Communicate(text, tts_voice)
        await communicate.save(output_path)
        # 验证输出文件
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
            raise RuntimeError(f"Edge TTS 输出文件异常: {output_path}")
    except Exception as e:
        logger.error(f"❌ Edge TTS 失败 (voice={tts_voice}): {e}")
        raise


async def generate_tts_single(
    text: str,
    output_path: str,
    config: dict,
    voice: str = None,
):
    """单句 TTS 调度，根据配置选择引擎"""
    engine = config["audio"].get("tts_engine", "edge_tts")
    if engine == "mimo_tts":
        await generate_tts_mimo(text, output_path, config, voice)
    else:
        await generate_tts_edge(text, output_path, config, voice)


async def generate_tts_per_sentence(
    sentences: list,
    output_dir: Path,
    config: dict,
    voice: str = None,
) -> list:
    """逐句 TTS + 精确时长，返回 [(audio_path, duration), ...]"""
    # 确保 output_dir 是 Path（在创建任务前转换，防止内部拼接出错）
    if isinstance(output_dir, str):
        output_dir = Path(output_dir)

    concurrency = config["tts"].get("concurrency", 5)
    semaphore = asyncio.Semaphore(concurrency)

    async def _synthesize(idx: int, text: str) -> tuple:
        path = str(output_dir / f"sentence_{idx:03d}.wav")
        async with semaphore:
            await generate_tts_single(text, path, config, voice)
        dur = get_audio_duration(path)
        logger.debug(f"  TTS [{idx+1}/{len(sentences)}]: {dur:.2f}s - {text[:20]}...")
        return (path, dur)

    tasks = [_synthesize(i, s) for i, s in enumerate(sentences)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    timed = []
    failed_count = 0
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.error(f"  TTS 第 {i+1} 句失败: {r}")
            failed_count += 1
            # 降级：生成静音
            silence_path = str(output_dir / f"sentence_{i:03d}.wav")
            _generate_silence(silence_path, config["video"]["img_duration"])
            dur = config["video"]["img_duration"]
            timed.append((silence_path, dur))
        else:
            timed.append(r)

    # 如果所有 TTS 都失败，抛出异常而不是静默返回全静音
    if failed_count == len(sentences):
        engine = config["audio"].get("tts_engine", "edge_tts")
        raise RuntimeError(
            f"所有 {len(sentences)} 句 TTS 全部失败（引擎: {engine}），"
            f"请检查 TTS 配置或网络连接。"
            f"如果使用 edge_tts，请确保已安装: pip install edge-tts"
        )

    if failed_count > 0:
        logger.warning(f"⚠️ TTS 有 {failed_count}/{len(sentences)} 句失败，已用静音替代")

    return timed


def _generate_silence(output_path: str, duration: float):
    """生成静音 WAV 文件"""
    subprocess.run([
        _FFMPEG, "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono",
        "-t", str(duration), "-c:a", "pcm_s16le", output_path
    ], capture_output=True, check=True)


def get_audio_duration(audio_path: str) -> float:
    """获取音频时长（优先用 ffprobe，不可用时用 ffmpeg）"""
    # 方案1: ffprobe
    import shutil
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-show_entries", "format=duration", "-of", "json", audio_path],
            capture_output=True, text=True
        )
        try:
            return float(json.loads(result.stdout)["format"]["duration"])
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    # 方案2: 用 ffmpeg 解码获取时长
    ffmpeg = shutil.which("ffmpeg") or _FFMPEG
    if ffmpeg:
        result = subprocess.run(
            [ffmpeg, "-i", audio_path, "-f", "null", "-"],
            capture_output=True, text=True
        )
        # 从 stderr 中提取时长，支持两种格式：
        # "time=4.52" (纯秒) 或 "time=00:00:16.28" (HH:MM:SS.ms)
        import re
        time_match = re.search(r'time=(\d+:\d+:\d+\.?\d*|\d+\.?\d*)', result.stderr)
        if time_match:
            time_str = time_match.group(1)
            if ':' in time_str:
                # HH:MM:SS.ms 格式
                parts = time_str.split(':')
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = float(parts[2])
                return hours * 3600 + minutes * 60 + seconds
            else:
                return float(time_str)

    return 4.0  # 默认 4 秒


def concat_audio_files(timed: list, output_path: str, silence_between: float = 0.15) -> float:
    """拼接所有单句音频为完整音频，返回总时长"""
    if not timed:
        return 0.0

    if len(timed) == 1:
        # 单句直接复制
        shutil.copy2(timed[0][0], output_path)
        return timed[0][1]

    # 使用 FFmpeg concat 拼接
    temp_dir = Path(output_path).parent / "_concat_tmp"
    temp_dir.mkdir(exist_ok=True)

    try:
        # 生成静音间隔
        silence_path = str(temp_dir / "silence.wav")
        subprocess.run([
            _FFMPEG, "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono",
            "-t", str(silence_between), "-c:a", "pcm_s16le", silence_path
        ], capture_output=True, check=True)

        # 生成 concat 列表（转义路径中的单引号，防止 FFmpeg 解析错误）
        concat_list = str(temp_dir / "concat.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            for i, (audio_path, _) in enumerate(timed):
                safe_path = os.path.abspath(audio_path).replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")
                if i < len(timed) - 1 and silence_between > 0:
                    safe_silence = os.path.abspath(silence_path).replace("'", "'\\''")
                    f.write(f"file '{safe_silence}'\n")

        subprocess.run([
            _FFMPEG, "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list, "-c:a", "pcm_s16le", output_path
        ], capture_output=True, check=True)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return get_audio_duration(output_path)


# ── 文案处理 ──────────────────────────────────────────
def load_script(path: str) -> list:
    lines = Path(path).read_text(encoding="utf-8").strip().splitlines()
    sentences = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            for p in re.split(r'[。！？；\n]+', line):
                p = p.strip()
                if p:
                    sentences.append(p)
    return sentences


def split_sentences(text: str) -> list:
    return [p.strip() for p in re.split(r'[。！？；,，\n]+', text) if p.strip()]


class MaterialPool:
    def __init__(self, folder: str, shuffle: bool = True):
        self.folder = folder
        self.exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".mp4", ".mov", ".avi", ".webm"}
        folder_path = Path(folder)
        
        # 递归扫描所有子目录
        raw_materials = sorted([str(p) for p in folder_path.rglob("*") if p.suffix.lower() in self.exts and p.is_file()])
        
        # 1. 使用轻量级 AI (CLIP) 过滤不安全/受伤宠物的图片
        self.all_materials = []
        try:
            from ai_filter import get_clip, is_image_safe
            model, _ = get_clip()
            if model:  # 只有模型实际可用时才遍历筛查，避免无效遍历 4700+ 张图片
                logger.info("开始进行 AI 素材安全筛查...")
                for mat in raw_materials:
                    if mat.lower().endswith((".jpg", ".png", ".jpeg", ".webp", ".bmp")):
                        if is_image_safe(mat):
                            self.all_materials.append(mat)
                    else:
                        self.all_materials.append(mat)
                logger.info(f"AI 筛查完成: 保留 {len(self.all_materials)} 个可用素材 (过滤了 {len(raw_materials) - len(self.all_materials)} 个)")
            else:
                logger.info("CLIP 模型不可用，跳过 AI 筛查")
                self.all_materials = raw_materials
        except Exception as e:
            logger.warning(f"AI 安全筛查异常: {e}")
            self.all_materials = raw_materials

        # 2. 如果本地可用素材过少，调用 Pexels API 自动补充
        EXPECTED_MIN_MATERIALS = 20
        if len(self.all_materials) < EXPECTED_MIN_MATERIALS:
            logger.info(f"可用素材仅 {len(self.all_materials)} 个，准备从 Pexels 补充...")
            try:
                from pexels import fetch_pexels_photos_sync
                import os
                import toml
                
                c = toml.load(Path(__file__).parent / "config.toml")
                api_key = c.get("pexels", {}).get("api_key", "")
                if not api_key:
                    api_key = os.environ.get("PEXELS_API_KEY", "")
                    
                if api_key:
                    keywords = ["healthy dog", "cute pet dog", "happy dog playing", "dog running"]
                    need_count = EXPECTED_MIN_MATERIALS - len(self.all_materials)
                    count_per = max(1, need_count // len(keywords) + 1)
                    
                    logger.info(f"正在从 Pexels 自动下载约 {count_per * len(keywords)} 张补充图片...")
                    new_photos = fetch_pexels_photos_sync(
                        keywords=keywords,
                        count_per_keyword=count_per,
                        orientation="portrait",
                        api_key=api_key,
                        download_dir=str(folder_path / "pexels_auto")
                    )
                    self.all_materials.extend(new_photos)
                    logger.info(f"Pexels 补充完成，当前总可用素材数: {len(self.all_materials)}")
                else:
                    logger.warning("未配置有效的 Pexels API Key，跳过自动补充")
            except Exception as e:
                logger.warning(f"自动补充素材失败: {e}")

        if not self.all_materials:
            raise FileNotFoundError(f"素材文件夹 {folder} 中没有有效图片或视频")
            
        self.available = self.all_materials.copy()
        self.shuffle = shuffle
        if self.shuffle and len(self.available) > 1:
            import random
            random.shuffle(self.available)
            
    def get_materials(self, count: int) -> list:
        groups = []
        import random
        for i in range(count):
            if len(self.all_materials) <= 4:
                n = random.choice([1, 2]) if len(self.all_materials) >= 2 else 1
            else:
                n = random.choice([1, 1, 1, 2, 2, 3, 4])
                
            n = min(n, len(self.all_materials))
            
            group = []
            for _ in range(n):
                if not self.available:
                    self.available = self.all_materials.copy()
                    if self.shuffle and len(self.available) > 1:
                        random.shuffle(self.available)
                
                # Find a material not in group
                mat = None
                for idx, cand in enumerate(self.available):
                    if cand not in group:
                        mat = cand
                        self.available.pop(idx)
                        break
                
                if mat is None:
                    mat = self.available.pop(0)
                    
                group.append(mat)
                
            groups.append(group)
            
        logger.info(f"📷 {count} 个场景，共 {sum(len(g) for g in groups)} 个素材（从 {len(self.all_materials)} 个中选取）")
        return groups


# ── 视频合成（Ken Burns + 转场）─────────────────────────
TRANSITIONS = ["fade", "slideleft", "slideright", "slideup", "wipeleft", "wiperight", "circleopen", "fadeblack"]

# Ken Burns 效果预设
KB_EFFECTS = {
    "zoom_in": "zoompan=z='min(zoom+0.0008,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s={w}x{h}:fps={fps}",
    "zoom_out": "zoompan=z='if(eq(on,1),1.15,max(zoom-0.0008,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s={w}x{h}:fps={fps}",
    "pan_left": "zoompan=z='1.08':x='if(eq(on,1),0,min(x+2,iw))':y='ih/2-(ih/zoom/2)':d={d}:s={w}x{h}:fps={fps}",
    "pan_right": "zoompan=z='1.08':x='if(eq(on,1),iw/10,max(x-2,0))':y='ih/2-(ih/zoom/2)':d={d}:s={w}x{h}:fps={fps}",
    "pan_up": "zoompan=z='1.08':x='iw/2-(iw/zoom/2)':y='if(eq(on,1),ih/10,max(y-2,0))':d={d}:s={w}x{h}:fps={fps}",
    # 新增效果
    "pan_down": "zoompan=z='1.08':x='iw/2-(iw/zoom/2)':y='if(eq(on,1),0,min(y+2,ih))':d={d}:s={w}x{h}:fps={fps}",
    "zoom_in_slow": "zoompan=z='min(zoom+0.0004,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s={w}x{h}:fps={fps}",
    "static": "zoompan=z='1.0':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s={w}x{h}:fps={fps}",
}


def _generate_video_clip_segment(video_path: str, subtitle_path: str, seg_path: str, dur: float, config: dict, fade_in: float, fade_out: float):
    w = config["video"]["width"]
    h = config["video"]["height"]
    fps = config["video"]["fps"]
    preset = config["video"]["encode_preset"]
    crf = config["video"]["crf"]
    
    # 尝试获取视频时长
    try:
        dur_result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", video_path
        ], capture_output=True, text=True, check=True)
        orig_dur = float(dur_result.stdout.strip())
    except:
        orig_dur = dur
        
    loop_arg = ["-stream_loop", "-1"] if orig_dur < dur else []
    
    # 构建滤镜: scale -> crop -> setpts -> overlay -> fade
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setpts=PTS-STARTPTS[vbg]"
    if subtitle_path:
        vf += f";[vbg][1:v]overlay=0:0[vout]"
    else:
        vf += f";[vbg]copy[vout]"
        
    fade_filters = []
    if fade_in > 0:
        fade_filters.append(f"fade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        fade_filters.append(f"fade=t=out:st={dur - fade_out}:d={fade_out}")
        
    if fade_filters:
        vf += f";[vout]{','.join(fade_filters)}[vout_f]"
        out_map = "[vout_f]"
    else:
        out_map = "[vout]"
        
    cmd = [
        _FFMPEG, "-y",
        *loop_arg, "-i", video_path
    ]
    if subtitle_path:
        cmd.extend(["-i", subtitle_path])
        
    cmd.extend([
        "-filter_complex", vf,
        "-map", out_map,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-t", str(dur),
        seg_path
    ])
    subprocess.run(cmd, capture_output=True, check=True)


def _generate_ken_burns_segment(frame_path: str, seg_path: str, dur: float, config: dict,
                                fade_in: float = 0, fade_out: float = 0):
    """生成单个 Ken Burns 视频段（用于并行执行）

    Args:
        fade_in: 淡入时长（秒），0 表示不淡入
        fade_out: 淡出时长（秒），0 表示不淡出
    """
    w = config["video"]["width"]
    h = config["video"]["height"]
    fps = config["video"]["fps"]
    preset = config["video"]["encode_preset"]
    crf = config["video"]["crf"]
    dur_frames = int(dur * fps)

    effect_name = random.choice(list(KB_EFFECTS.keys()))
    vf_template = KB_EFFECTS[effect_name]
    vf = vf_template.format(d=dur_frames, w=w, h=h, fps=fps)

    # 添加淡入淡出滤镜
    fade_filters = []
    if fade_in > 0:
        fade_filters.append(f"fade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        fade_filters.append(f"fade=t=out:st={dur - fade_out}:d={fade_out}")
    if fade_filters:
        vf = f"{vf},{','.join(fade_filters)}"

    result = subprocess.run([
        _FFMPEG, "-y", "-loop", "1", "-i", frame_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-t", str(dur),
        seg_path
    ], capture_output=True, text=True)

    if result.returncode != 0:
        logger.warning(f"Ken Burns 生成失败 ({effect_name})，降级为静态: {result.stderr[:200]}")
        vf_static = KB_EFFECTS["static"].format(d=dur_frames, w=w, h=h, fps=fps)
        if fade_filters:
            vf_static = f"{vf_static},{','.join(fade_filters)}"
        subprocess.run([
            _FFMPEG, "-y", "-loop", "1", "-i", frame_path,
            "-vf", vf_static,
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-t", str(dur),
            seg_path
        ], capture_output=True, check=True)


def compose_video(
    image_groups: list,
    sentences: list,
    output_path: str,
    durations: list,
    config: dict,
    bgm_path: str = None,
    cover_image_path: str = None,
    cover_text: str = "",
    cover_subtitle: str = "",
):
    """视频合成主函数

    流程：封面图(可选) → PIL帧(含字幕) → Ken Burns + fade动画 → xfade转场 → 合并音频

    Args:
        cover_image_path: 封面图路径（PIL生成），为 None 则不加封面
        cover_text: 封面标题文字
        cover_subtitle: 封面副标题
    """
    w = config["video"]["width"]
    h = config["video"]["height"]
    fps = config["video"]["fps"]
    subtitle_style = config["video"]["subtitle_style"]
    transition_duration = config["video"]["transition_duration"]
    cover_duration = config["video"].get("cover_duration", 3.0)
    animation = config["video"].get("subtitle_animation", "none")
    subtitle_position = config["video"].get("subtitle_position")
    subtitle_margin = config["video"].get("subtitle_margin")
    preset = config["video"]["encode_preset"]
    crf = config["video"]["crf"]
    voice_volume = config["audio"]["voice_volume"]
    bgm_volume = config["audio"]["bgm_volume"]

    # 动画参数
    anim_fade_in = 0.5 if animation in ("fade", "slide", "typewriter") else 0
    anim_fade_out = 0.3 if animation == "fade" else 0

    temp_dir = Path(tempfile.mkdtemp(prefix="video_maker_"))

    try:
        # ── 1. 准备帧图片 ──
        frames = []
        frame_durations = []

        # 封面帧（如果有）
        if cover_image_path and os.path.exists(cover_image_path):
            cover_frame = str(temp_dir / "frame_cover.jpg")
            shutil.copy2(cover_image_path, cover_frame)
            frames.append({"type": "image", "path": cover_frame})
            frame_durations.append(cover_duration)
            logger.info(f"📌 封面图已插入，时长 {cover_duration}s")

        # 内容帧
        for i, group in enumerate(image_groups):
            # 检查是否有视频素材
            video_files = [p for p in group if Path(p).suffix.lower() in {".mp4", ".mov", ".avi", ".webm"}]
            if video_files:
                # 若有视频，取第一个作为画面，不合并
                video_path = video_files[0]
                # 生成透明背景字幕图
                subtitle_img = Image.new("RGBA", (w, h), (0,0,0,0))
                subtitle_img = add_subtitle(subtitle_img, sentences[i] if i < len(sentences) else "", subtitle_style,
                                           position_override=subtitle_position, margin_override=subtitle_margin)
                subtitle_path = str(temp_dir / f"subtitle_{i:03d}.png")
                subtitle_img.save(subtitle_path)
                
                frames.append({
                    "type": "video",
                    "path": video_path,
                    "subtitle": subtitle_path
                })
            else:
                # 纯图片
                frame_path = str(temp_dir / f"frame_{i:03d}.jpg")
                if len(group) == 1:
                    img = smart_crop_resize(group[0], w, h)
                else:
                    img = make_collage(group, w, h)
                img = add_subtitle(img, sentences[i] if i < len(sentences) else "", subtitle_style,
                                   position_override=subtitle_position,
                                   margin_override=subtitle_margin)
                img.save(frame_path, quality=95)
                frames.append({
                    "type": "image",
                    "path": frame_path
                })
                
            frame_durations.append(durations[i] if i < len(durations) else 4.0)

        logger.info(f"生成 {len(frames)} 帧图片完成（含封面: {cover_image_path is not None}）")

        # ── 2. 计算 adjusted_durations（补偿转场重叠）──
        n_total = len(frames)
        adjusted_durations = []
        for i, dur in enumerate(frame_durations):
            extra = 0.0
            if n_total > 1:
                if i == 0 or i == n_total - 1:
                    extra = transition_duration / 2
                else:
                    extra = transition_duration
            adjusted_durations.append(dur + extra)

        # ── 3. Ken Burns 效果（带 fade 动画）──
        segments = []
        kb_tasks = []
        for i, (frame, dur) in enumerate(zip(frames, adjusted_durations)):
            seg_path = str(temp_dir / f"seg_{i:03d}.mp4")
            segments.append(seg_path)
            # 封面段用 fade_in，内容段根据 animation 设置
            if i == 0 and cover_image_path:
                kb_tasks.append((frame, seg_path, dur, 0.5, 0.3))
            else:
                kb_tasks.append((frame, seg_path, dur, anim_fade_in, anim_fade_out))

        try:
            with ProcessPoolExecutor(max_workers=min(config["batch"].get("max_workers", 4), len(kb_tasks))) as executor:
                futures = []
                for frame, seg_path, dur, fi, fo in kb_tasks:
                    if frame["type"] == "video":
                        future = executor.submit(
                            _generate_video_clip_segment,
                            frame["path"], frame["subtitle"], seg_path, dur, config, fi, fo
                        )
                    else:
                        future = executor.submit(
                            _generate_ken_burns_segment,
                            frame["path"], seg_path, dur, config, fi, fo
                        )
                    futures.append(future)

                for future in futures:
                    try:
                        future.result(timeout=120)
                    except Exception as e:
                        logger.error(f"Ken Burns 段生成失败: {e}")
                        raise
        except (PermissionError, OSError) as e:
            # sandbox 环境下 ProcessPoolExecutor 可能不可用，回退到顺序执行
            logger.warning(f"ProcessPoolExecutor 不可用({e})，改为顺序生成 Ken Burns 段")
            for frame, seg_path, dur, fi, fo in kb_tasks:
                if frame["type"] == "video":
                    _generate_video_clip_segment(frame["path"], frame["subtitle"], seg_path, dur, config, fi, fo)
                else:
                    _generate_ken_burns_segment(frame["path"], seg_path, dur, config, fi, fo)

        logger.info(f"Ken Burns {len(segments)} 段生成完成")

        # ── 4. 拼接（带 xfade 转场）──
        if len(segments) == 1:
            video_no_audio = segments[0]
        else:
            video_no_audio = str(temp_dir / "video_no_audio.mp4")

            inputs = []
            for seg in segments:
                inputs.extend(["-i", seg])

            filter_parts = []
            n = len(segments)

            for i in range(n - 1):
                offset = sum(adjusted_durations[:i+1]) - (i + 1) * transition_duration
                if offset < 0:
                    offset = 0

                transition = random.choice(TRANSITIONS)

                if i == 0:
                    filter_parts.append(
                        f"[0:v][1:v]xfade=transition={transition}:duration={transition_duration}:offset={offset:.3f}[v{i}]"
                    )
                elif i < n - 2:
                    filter_parts.append(
                        f"[v{i-1}][{i+1}:v]xfade=transition={transition}:duration={transition_duration}:offset={offset:.3f}[v{i}]"
                    )
                else:
                    filter_parts.append(
                        f"[v{i-1}][{i+1}:v]xfade=transition={transition}:duration={transition_duration}:offset={offset:.3f}[vout]"
                    )

            filter_str = ";".join(filter_parts)

            cmd = [_FFMPEG, "-y"] + inputs + [
                "-filter_complex", filter_str,
                "-map", "[vout]",
                "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-pix_fmt", "yuv420p",
                video_no_audio
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.warning(f"转场失败，使用简单拼接: {result.stderr[:200]}")
                concat_list = str(temp_dir / "concat.txt")
                with open(concat_list, "w", encoding="utf-8") as f:
                    for seg in segments:
                        safe_path = os.path.abspath(seg).replace("'", "'\\''")
                        f.write(f"file '{safe_path}'\n")
                subprocess.run([
                    _FFMPEG, "-y", "-f", "concat", "-safe", "0",
                    "-i", concat_list,
                    "-c", "copy",
                    video_no_audio
                ], capture_output=True, check=True)

        # ── 5. 合并音频 ──
        voice_path = str(Path(output_path).parent / f"{Path(output_path).stem}_voice.wav")

        # 检查配音文件是否存在
        if not os.path.exists(voice_path):
            logger.error(f"❌ 配音文件不存在: {voice_path}，TTS 可能全部失败")
            # 尝试仅合并 BGM
            if bgm_path and os.path.exists(bgm_path):
                subprocess.run([
                    _FFMPEG, "-y", "-i", video_no_audio, "-i", bgm_path,
                    "-filter_complex",
                    f"[1:a]volume={bgm_volume}[music]",
                    "-map", "0:v", "-map", "[music]",
                    "-c:v", "copy", "-c:a", "aac", "-shortest",
                    output_path
                ], capture_output=True, check=True)
                logger.warning("⚠️ 仅添加背景音乐（无配音）")
            else:
                # 无配音也无 BGM，直接复制视频
                shutil.copy2(video_no_audio, output_path)
                logger.warning("⚠️ 无音频轨道")
        elif bgm_path and os.path.exists(bgm_path):
            subprocess.run([
                _FFMPEG, "-y", "-i", video_no_audio, "-i", voice_path, "-i", bgm_path,
                "-filter_complex",
                f"[1:a]volume={voice_volume}[voice];[2:a]volume={bgm_volume}[music];[voice][music]amix=inputs=2:duration=first[aout]",
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-shortest",
                output_path
            ], capture_output=True, check=True)
        else:
            subprocess.run([
                _FFMPEG, "-y", "-i", video_no_audio, "-i", voice_path,
                "-c:v", "copy", "-c:a", "aac", "-shortest",
                output_path
            ], capture_output=True, check=True)

        logger.info(f"视频合成完成: {output_path}")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ── 抖音发布指南生成 ──────────────────────────────────
async def generate_publish_guide(
    sentences: list,
    title: str,
    topic: str,
    config: dict,
    template: dict = None,
) -> str:
    """用 MiMo 生成抖音发布指南"""
    api_key = config["api"]["xiaomi_api_key"]
    if not api_key:
        return _fallback_publish_guide(sentences, title, topic, template)

    script_text = "\n".join(sentences)

    # 从模板获取地域关键词
    if template and template.get("regions"):
        regions = "、".join(template["regions"])
        region_req = f'所有内容都要包含地域关键词"{regions}"'
    else:
        region_req = '所有内容都要包含地域关键词"天津""滨海新区""塘沽"'

    prompt = f"""你是一个抖音本地生活运营专家。根据以下视频文案，生成一套完整的抖音发布指南。

视频主题：{topic}
视频文案：
{script_text}

请生成以下内容，用 === 分隔每部分：

===视频标题===
（20-30字，包含核心卖点+价格+地域关键词，适合抖音搜索推荐）

===视频描述===
（100-150字，包含：①痛点切入 ②核心卖点3-4个 ③价格信息 ④行动号召 ⑤联系/地址信息）

===话题标签===
（8-12个话题标签，包含：地域词+行业词+长尾词+热门词）

===置顶评论===
（引导用户互动的话术，包含：①价格表引导 ②预约方式 ③福利钩子）

===小红书标题===
（3个版本，适合小红书风格）

===小红书正文===
（200-300字，包含：①真实体验口吻 ②具体细节 ③价格透明 ④地址电话 ⑤话题标签）

要求：
1. {region_req}
2. 标题要吸引点击，用数字、痛点、好奇心
3. 话题标签要覆盖搜索词+长尾词
4. 评论区话术要引导"扣1""私信""点击团购"
5. 小红书要用第一人称真实体验风格"""

    base_url = config["api"]["xiaomi_base_url"]
    model = config["api"]["llm_model"]
    max_retries = config["api"]["max_retries"]
    retry_delay = config["api"]["retry_delay"]

    try:
        async with httpx.AsyncClient(timeout=config["api"]["timeout"]) as client:
            result = await api_call_with_retry(
                client,
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                payload={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 2000,
                },
                max_retries=max_retries,
                retry_delay=retry_delay,
            )
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                raise ValueError("AI 返回空内容")
            return content
    except Exception as e:
        logger.warning(f"发布指南 AI 生成失败，使用降级方案: {e}")
        return _fallback_publish_guide(sentences, title, topic, template)


def _fallback_publish_guide(sentences: list, title: str, topic: str, template: dict = None) -> str:
    """降级方案：基于模板生成发布指南"""
    merchant = template.get("merchant", {}) if template else {}
    m_name = merchant.get("name", "你的店铺名称")
    m_addr = merchant.get("address", "你的店铺地址")
    m_phone = merchant.get("phone", "你的联系电话")
    regions = template.get("regions", ["天津", "滨海新区", "塘沽"]) if template else ["天津", "滨海新区", "塘沽"]
    region_tags = " ".join(f"#{r}宠物寄养" for r in regions)

    return f"""===视频标题===
{regions[0]}{m_name}｜58元/天起 独立房间24h监控 每日遛弯消毒

===视频描述===
{chr(10).join(sentences[:4])}

💰 价格透明：
中小间58元/天 | 大间98元/天 | 超大间148元/天
日托38元起 | 洗澡39元起 | 游泳38元起

📍 地址：{m_addr}
📞 电话：{m_phone}
🚗 10公里内免费接送

{region_tags}

===话题标签===
{region_tags} #{m_name} #宠物寄养推荐 #宠物日托 #宠物寄养价格

===置顶评论===
📋 价目表：
中小间58/天 | 大间98/天 | 超大间148/天
日托38元起 | 洗澡39元起 | 游泳38元起
储值1000送188，最高享8折

👇 想了解更多：
1️⃣ 评论区扣"1"发你完整价目表
2️⃣ 私信预约参观
3️⃣ 点击左下角团购链接直接下单

🚗 10公里内免费接送，预约电话：{m_phone}

===小红书标题===
A: {regions[0]}{m_name}｜58元起有监控有空调的真实体验
B: {regions[0]}狗狗寄养避坑｜我跑了5家选了这家
C: {regions[1]}宠物寄养推荐｜24h监控+每日遛狗+独立房间

===小红书正文===
{regions[0]}{regions[1]}找寄养的姐妹看过来👀

选寄养一定要看这几点👇

✅ 独立房间还是笼养？独立房间，空调恒温新风。
✅ 24小时监控？每个房间都有，直连手机随时看。
✅ 每天遛多久？至少2小时户外活动。
✅ 消毒做不做？每天紫外线消毒+消毒液拖地。

💰 价格透明：中小间58/天 大间98/天 超大间148/天
🚗 10公里内免费接送

📍 {m_addr}
📞 {m_phone}

{region_tags}"""


# ── SRT 生成 ──────────────────────────────────────────
def _generate_srt(sentences, durations, output_path, silence_between=0.15):
    """生成 SRT 字幕文件，基于精确时长"""
    srt_lines = []
    t = 0.0
    for i, (s, d) in enumerate(zip(sentences, durations), 1):
        srt_lines.append(f"{i}")
        srt_lines.append(f"{_fmt(t)} --> {_fmt(t + d)}")
        srt_lines.append(s)
        srt_lines.append("")
        t += d + silence_between
    Path(output_path).write_text("\n".join(srt_lines), encoding="utf-8")


def _fmt(sec):
    h, m, s, ms = int(sec // 3600), int((sec % 3600) // 60), int(sec % 60), int((sec % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ── 主流程 ────────────────────────────────────────────
def _extract_keywords(text: str) -> list:
    """从文本中提取关键词用于 Pexels 搜索"""
    keyword_map = {
        "宠物": "pet", "狗": "dog", "猫": "cat", "寄养": "pet boarding",
        "游泳": "dog swimming", "遛狗": "walking dog", "洗澡": "dog grooming",
        "可爱": "cute pet", "温馨": "warm pet", "家庭": "happy family",
        "儿童": "children playing", "节日": "holiday celebration",
        "夏天": "summer outdoor", "冬天": "cozy winter",
        "天津": "tianjin city", "滨海": "coastal city",
    }
    keywords = []
    for cn, en in keyword_map.items():
        if cn in text:
            keywords.append(en)
    if not keywords:
        keywords = ["cute dog", "pet care", "happy pet"]
    seen, unique = set(), []
    for k in keywords:
        if k not in seen:
            seen.add(k)
            unique.append(k)
    return unique[:5]


async def generate_single_video(
    topic: str = None,
    script_path: str = None,
    text: str = None,
    images_dir: str = None,
    output_dir: str = "输出",
    bgm_path: str = None,
    title: str = None,
    subtitle_style: int = None,
    voice: str = None,
    style: str = "团购种草",
    template_path: str = None,
    config: dict = None,
    cover: bool = False,
    cover_text: str = None,
    cover_subtitle: str = "",
    count: int = 8,
    progress_callback=None,
    batch_index: int = None,
    batch_total: int = None,
    material_pool=None,
) -> dict:
    """生成单个视频的完整流程

    Args:
        progress_callback: 进度回调函数，签名 async callback(step: str, percent: int)
                           step 为当前步骤描述，percent 为 0-100 的进度值
        batch_index: 批量生成时的序号（从1开始），用于确保每条视频文案不同
        batch_total: 批量生成总数
    """
    config = config or load_config()
    # 防止并发修改，确保每个任务使用独立的 config 副本
    import copy
    config = copy.deepcopy(config)

    start_time = time.time()

    # 加载商家模板
    template = load_template(template_path) if template_path else None

    async def _progress(step: str, percent: int):
        """安全调用进度回调"""
        if progress_callback:
            try:
                await progress_callback(step, percent)
            except Exception:
                pass

    # 加载文案
    await _progress("生成文案", 5)
    if topic:
        logger.info(f"AI 生成文案: {topic}" + (f" (批量 {batch_index}/{batch_total})" if batch_index else ""))
        sentences = await generate_script(topic, config, style, count=count, template=template,
                                          batch_index=batch_index, batch_total=batch_total)
    elif script_path:
        sentences = load_script(script_path)
    elif text:
        sentences = split_sentences(text)
    else:
        raise ValueError("请提供 topic、script 或 text")

    if not sentences:
        raise ValueError("文案为空")

    await _progress("文案生成完成", 15)

    logger.info(f"📝 文案: {len(sentences)} 句")
    for i, s in enumerate(sentences, 1):
        logger.info(f"   {i}. {s}")

    # Pexels 视频素材自动抓取
    await _progress("获取视频素材", 20)
    pexels_config = config.get("pexels", {})
    pexels_key = pexels_config.get("api_key", "")
    use_pexels = pexels_key and fetch_pexels_videos is not None and images_dir is None
    if use_pexels:
        logger.info("🎬 Pexels 自动抓取视频素材...")
        keywords = _extract_keywords(topic or " ".join(sentences))
        try:
            pexels_videos = await fetch_pexels_videos(
                keywords=keywords,
                count_per_keyword=pexels_config.get("count_per_keyword", 2),
                orientation=pexels_config.get("orientation", "portrait"),
                api_key=pexels_key,
                download_dir=pexels_config.get("download_dir", "素材/pexels"),
            )
            if pexels_videos:
                logger.info(f"✅ 下载了 {len(pexels_videos)} 个视频素材")
        except Exception as e:
            logger.warning(f"Pexels 抓取失败: {e}")

    # 背景音乐自动匹配
    if not bgm_path and config.get("audio", {}).get("bgm_auto_match", False) and auto_match_bgm:
        music_dir = config.get("audio", {}).get("music_dir", "音乐")
        logger.info(f"🎵 自动匹配背景音乐 (目录: {music_dir})...")
        matched = auto_match_bgm(topic or "", music_dir)
        if matched:
            bgm_path = matched
            logger.info(f"✅ 背景音乐: {Path(bgm_path).name}")

    # 字幕样式
    if subtitle_style is not None:
        config["video"]["subtitle_style"] = subtitle_style
    style_name = SUBTITLE_STYLES.get(config["video"]["subtitle_style"], {}).get("name", "默认")
    logger.info(f"🎨 字幕样式: {style_name}")

    # 加载图片/视频素材
    await _progress("加载图片/视频素材", 30)
    if material_pool is not None:
        image_groups = material_pool.get_materials(len(sentences))
    else:
        temp_pool = MaterialPool(images_dir)
        image_groups = temp_pool.get_materials(len(sentences))

    # 输出目录
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    video_title = title or topic or "团购视频"

    # TTS：逐句合成
    tts_dir = out_dir / f"_{video_title}_tts"
    tts_dir.mkdir(exist_ok=True)

    try:
        await _progress("TTS 语音合成", 40)
        engine = config["audio"].get("tts_engine", "edge_tts")
        logger.info(f"🎙️ 逐句 TTS 合成... (引擎: {engine})")
        timed = await generate_tts_per_sentence(sentences, tts_dir, config, voice)

        # 精确时长
        durations = [d for _, d in timed]
        total_audio_duration = sum(durations)

        # 检查是否全部为静音降级（TTS 全部失败）
        silence_duration = config["video"]["img_duration"]
        all_silence = all(abs(d - silence_duration) < 0.01 for d in durations)
        if all_silence and len(durations) > 0:
            logger.warning(f"⚠️ 所有 TTS 均为静音降级，视频将无配音！请检查 TTS 引擎配置")

        logger.info(f"⏱️ 精确时长: {total_audio_duration:.1f}s (逐句: {[f'{d:.2f}s' for d in durations]})")

        # 拼接完整音频
        audio_path = str(out_dir / f"{video_title}_voice.wav")
        silence_between = config["audio"]["silence_between"]
        concat_duration = concat_audio_files(timed, audio_path, silence_between)
        logger.info(f"✅ 语音: {audio_path} ({concat_duration:.1f}s)")
    finally:
        # 清理 TTS 临时文件
        if tts_dir.exists():
            shutil.rmtree(tts_dir, ignore_errors=True)

    await _progress("视频合成中", 55)

    # SRT 字幕（基于精确时长）
    srt_path = str(out_dir / f"{video_title}.srt")
    _generate_srt(sentences, durations, srt_path, silence_between)
    logger.info(f"✅ 字幕: {srt_path}")

    # 文案
    script_file = str(out_dir / f"{video_title}_文案.txt")
    Path(script_file).write_text("\n".join(sentences), encoding="utf-8")

    # 视频合成
    video_path = str(out_dir / f"{video_title}.mp4")

    # 封面图生成（如果启用）
    cover_image_path = None
    if cover:
        await _progress("生成封面图", 50)
        cover_title = cover_text or video_title
        cover_image_path = generate_cover(
            title=cover_title,
            subtitle=cover_subtitle,
            w=config["video"]["width"],
            h=config["video"]["height"],
            brand=template.get("merchant", {}).get("name", "") if template else "",
        )
        logger.info(f"📌 封面图生成: {cover_image_path}")

    compose_video(
        image_groups, sentences, video_path, durations,
        config=config, bgm_path=bgm_path,
        cover_image_path=cover_image_path,
        cover_text=cover_text or video_title,
        cover_subtitle=cover_subtitle,
    )

    await _progress("生成发布指南", 90)

    # 清理封面临时文件
    if cover_image_path and os.path.exists(cover_image_path):
        try:
            os.unlink(cover_image_path)
        except OSError:
            pass

    # 发布指南
    publish_path = str(out_dir / f"{video_title}_发布指南.txt")
    publish_guide = await generate_publish_guide(sentences, video_title, topic or "", config, template)
    Path(publish_path).write_text(publish_guide, encoding="utf-8")
    logger.info(f"✅ 发布指南: {publish_path}")

    elapsed = time.time() - start_time
    logger.info(f"🎬 完成! 耗时 {elapsed:.1f}s")
    logger.info(f"   视频: {video_path}")
    logger.info(f"   时长: {total_audio_duration:.1f}秒 | 分辨率: {config['video']['width']}x{config['video']['height']}")

    return {
        "title": video_title,
        "video_path": video_path,
        "duration": total_audio_duration,
        "elapsed": elapsed,
        "sentences": len(sentences),
    }


async def batch_generate(tasks: list, config: dict = None):
    """批量生成视频"""
    config = config or load_config()
    max_workers = config["batch"]["max_workers"]
    semaphore = asyncio.Semaphore(max_workers)

    results = {"success": [], "failed": []}
    total = len(tasks)
    
    pools = {}

    async def _run_task(idx: int, task: dict):
        async with semaphore:
            logger.info(f"📦 [{idx+1}/{total}] 开始: {task.get('title', task.get('topic', '未知'))}")
            try:
                img_dir = task.get("images_dir")
                if img_dir:
                    if img_dir not in pools:
                        pools[img_dir] = MaterialPool(img_dir)
                    task["material_pool"] = pools[img_dir]
                result = await generate_single_video(config=config, **task)
                results["success"].append(result)
                logger.info(f"✅ [{idx+1}/{total}] 完成: {result['title']}")
            except Exception as e:
                logger.error(f"❌ [{idx+1}/{total}] 失败: {e}")
                results["failed"].append({"task": task, "error": str(e)})

    await asyncio.gather(*[_run_task(i, t) for i, t in enumerate(tasks)])

    logger.info(f"\n{'='*50}")
    logger.info(f"批量生成完成: {len(results['success'])} 成功 / {len(results['failed'])} 失败")
    if results["failed"]:
        for f in results["failed"]:
            logger.info(f"  ❌ {f['task'].get('title', '未知')}: {f['error']}")

    return results


async def main():
    parser = argparse.ArgumentParser(description="团购视频生成器 v3")
    parser.add_argument("--topic", help="主题，AI 自动生成文案")
    parser.add_argument("--style", default="团购种草", help="文案风格")
    parser.add_argument("--script", "-s", help="文案脚本文件")
    parser.add_argument("--text", "-t", help="直接输入文案")
    parser.add_argument("--images", "-i", help="素材图片文件夹")
    parser.add_argument("--output", "-o", default="输出", help="输出目录")
    parser.add_argument("--voice", help="TTS 音色（覆盖配置）")
    parser.add_argument("--bgm", help="背景音乐文件路径")
    parser.add_argument("--title", help="视频标题")
    parser.add_argument("--subtitle-style", type=int, choices=range(1, 9), help="字幕样式 1-8")
    parser.add_argument("--list-styles", action="store_true", help="列出字幕样式")
    parser.add_argument("--template", help="商家模板 YAML 文件路径")
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("--batch", help="批量任务 JSON 文件路径")
    parser.add_argument("--log-file", help="日志文件路径")
    parser.add_argument("--tts-engine", choices=["edge_tts", "mimo_tts"], help="TTS 引擎")
    parser.add_argument("--cover", action="store_true", help="生成封面图")
    parser.add_argument("--cover-text", help="封面标题文字")
    parser.add_argument("--cover-subtitle", default="", help="封面副标题")
    parser.add_argument("--cover-duration", type=float, default=3.0, help="封面展示时长（秒）")
    parser.add_argument("--subtitle-animation", choices=["none", "fade", "slide", "typewriter"], help="字幕入场动画")

    args = parser.parse_args()

    # 日志设置
    setup_logging(log_file=args.log_file)

    if args.list_styles:
        print("📝 字幕样式：")
        for sid, s in SUBTITLE_STYLES.items():
            print(f"   {sid}. {s['name']}")
        return

    # 加载配置
    config = load_config(args.config)

    # CLI 参数覆盖配置
    if args.tts_engine:
        config["audio"]["tts_engine"] = args.tts_engine
    if args.output:
        config["batch"]["output_dir"] = args.output
    if args.subtitle_animation:
        config["video"]["subtitle_animation"] = args.subtitle_animation
    if args.cover_duration:
        config["video"]["cover_duration"] = args.cover_duration

    # 批量模式
    if args.batch:
        batch_path = Path(args.batch)
        if not batch_path.exists():
            logger.error(f"批量任务文件不存在: {args.batch}")
            sys.exit(1)
        tasks = json.loads(batch_path.read_text(encoding="utf-8"))
        # 为每个任务补充 images 参数
        for task in tasks:
            if "images_dir" not in task:
                task["images_dir"] = args.images
            if "output_dir" not in task:
                task["output_dir"] = args.output
        await batch_generate(tasks, config)
        return

    # 单视频模式
    await generate_single_video(
        topic=args.topic,
        script_path=args.script,
        text=args.text,
        images_dir=args.images,
        output_dir=args.output,
        bgm_path=args.bgm,
        title=args.title,
        subtitle_style=args.subtitle_style,
        voice=args.voice,
        style=args.style,
        template_path=args.template,
        config=config,
        cover=args.cover,
        cover_text=args.cover_text,
        cover_subtitle=args.cover_subtitle,
    )


if __name__ == "__main__":
    asyncio.run(main())
