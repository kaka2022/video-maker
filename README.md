# 🎬 团购视频生成器

> **一句话生成抖音/小红书团购引流短视频** — AI 文案 + TTS 配音 + 自动剪辑

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-required-green.svg)](https://ffmpeg.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 🎯 这是什么？

**团购视频生成器**是一个自动化短视频制作工具，专为**本地生活商家**设计。

只需输入一个主题关键词，AI 就能：
1. 📝 自动生成 8 句口语化带货文案
2. 🎤 逐句 TTS 语音合成（支持多种引擎）
3. 🎬 自动匹配视频/图片素材
4. ✨ 添加 Ken Burns 特效 + 智能转场
5. 🎵 匹配背景音乐
6. 📱 输出适配抖音/小红书的竖版视频

**适用场景**：
- 🏪 实体店团购引流
- 🍜 餐饮店推广
- 🏨 酒店/民宿宣传
- 💇 美容美发引流
- 🐾 宠物店推广
- 🏋️ 健身房招生
- ...任何需要短视频引流的本地商家

---

## ✨ 核心功能

### 🤖 AI 智能文案
- 输入主题关键词，自动生成 8 句口语化文案
- 支持商家模板，自动融入价格、服务、卖点
- 语言自然流畅，适合口播

### 🎤 多引擎 TTS
| 引擎 | 特点 | 费用 |
|------|------|------|
| **MiMo TTS** | 高质量中文语音 | 需要 API Key |
| **Edge TTS** | 微软免费语音 | 免费 |

### 🎨 专业视频效果
- **Ken Burns 特效**：静态图片自动缩放平移
- **智能转场**：xfade 平滑过渡
- **字幕动画**：10+ 种字幕样式
- **背景音乐**：根据内容风格自动匹配

### 📦 商家模板系统
```yaml
merchant:
  name: "你的店铺"
  address: "你的地址"
  phone: "你的电话"

services:
  服务1:
    - name: "基础服务"
      price: 100
      unit: "元/次"

selling_points:
  - "环境优雅"
  - "价格实惠"
  - "服务周到"
```

### 🚀 批量生成
```json
[
  {"topic": "周末特惠", "template": "templates/shop.yaml"},
  {"topic": "新品上市", "template": "templates/shop.yaml"},
  {"topic": "会员福利", "template": "templates/shop.yaml"}
]
```

### 🌐 Web 管理界面
- FastAPI 后端 + 前端页面
- 实时进度推送（WebSocket）
- 任务队列管理
- 视频预览和下载

---

## 🚀 快速开始

### 1. 安装依赖

```bash
# Python 依赖
pip install edge-tts pycapsule requests toml pyyaml httpx pillow

# FFmpeg（必需）
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg

# Windows
# 下载 https://ffmpeg.org/download.html 并添加到 PATH
```

### 2. 配置

```bash
# 复制配置模板
cp config.toml.example config.toml

# 编辑配置文件
nano config.toml
```

**配置示例**：
```toml
[api]
# 小米 MiMo API（用于 AI 文案 + TTS）
xiaomi_api_key = "YOUR_API_KEY"
xiaomi_base_url = "https://token-plan-cn.xiaomimimo.com/v1"

[pexels]
# Pexels 素材 API（可选，用于自动下载视频）
api_key = "YOUR_PEXELS_KEY"

[video]
# 视频参数
width = 1080
height = 1920
fps = 30

[audio]
# 音频参数
tts_engine = "edge_tts"  # 或 "mimo_tts"
bgm_volume = 0.12
```

### 3. 生成视频

```bash
# 基本用法（使用本地图片）
python video_maker.py --topic "你的主题" --images 素材/

# 使用商家模板
python video_maker.py --template templates/example.yaml --topic "你的主题"

# 使用 Pexels 自动下载素材
python video_maker.py --topic "宠物寄养"

# 批量生成
python video_maker.py --batch batch_tasks.json
```

### 4. Web 界面（可选）

```bash
# 安装 Web 依赖
pip install fastapi uvicorn

# 启动服务
python web/server.py

# 访问 http://localhost:8000
```

---

## 📁 项目结构

```
video-maker/
├── video_maker.py      # 🎬 核心生成引擎
├── bgm.py              # 🎵 背景音乐匹配模块
├── pexels.py           # 📸 Pexels 素材下载模块
├── ai_filter.py        # 🤖 AI 内容过滤模块
├── config.toml.example # ⚙️ 配置模板
├── batch_tasks.json    # 📋 批量任务示例
├── web/
│   ├── server.py       # 🌐 FastAPI 后端
│   ├── index.html      # 🖥️ 前端页面
│   └── publish_utils.py # 📤 发布工具
├── templates/
│   └── example.yaml    # 📝 商家模板示例
├── 脚本/
│   ├── README.md       # 📚 文案说明
│   └── 示例-*.txt      # 📝 文案示例
├── 素材库/
│   └── 文案资料/       # 📚 文案素材
└── 音乐/
    └── bgm.mp3         # 🎵 背景音乐
```

---

## 🎬 视频效果预览

### 输出规格
| 参数 | 值 |
|------|-----|
| 分辨率 | 1080x1920（竖版） |
| 帧率 | 30fps |
| 时长 | 30-60 秒 |
| 格式 | MP4 (H.264) |
| 音频 | AAC |

### 视频特点
- ✅ 适配抖音、小红书、快手
- ✅ 专业转场效果
- ✅ AI 配音自然流畅
- ✅ 字幕清晰易读
- ✅ 背景音乐匹配内容风格

---

## ⚙️ 配置详解

### 视频配置
```toml
[video]
width = 1080              # 视频宽度
height = 1920             # 视频高度（竖版）
fps = 30                  # 帧率
img_duration = 4          # 每张图片展示秒数
transition_duration = 0.5 # 转场时长
subtitle_style = 8        # 字幕样式（1-10）
encode_preset = "fast"    # 编码预设
crf = 23                  # 视频质量（18-28）
```

### 音频配置
```toml
[audio]
tts_engine = "edge_tts"      # TTS 引擎
edge_tts_voice = "zh-CN-XiaoxiaoNeural"  # 语音角色
bgm_volume = 0.12            # 背景音乐音量
voice_volume = 1.0           # 语音音量
silence_between = 0.15       # 句间静音时长
```

### 批量配置
```toml
[batch]
max_workers = 4              # 并行任务数
output_dir = "输出"          # 输出目录
```

---

## 📝 商家模板

### 完整示例
```yaml
merchant:
  name: "宠悦小狗工厂"
  full_name: "天津市滨海新区宠悦宠物服务工作室"
  address: "天津市滨海新区塘沽新城镇东庄房路"
  phone: "13110016634"
  contact: "刘志豪"
  hours: "10:00-22:00"

regions:
  - "天津"
  - "滨海新区"

services:
  寄养:
    - name: "中小间"
      price: 58
      unit: "元/天"
    - name: "大间"
      price: 98
      unit: "元/天"

  洗澡:
    - name: "小型犬"
      price: 39
    - name: "中型犬"
      price: 59

selling_points:
  - "24小时监控"
  - "专业护理"
  - "环境整洁"

hashtags:
  - "#宠物寄养"
  - "#天津宠物"
  - "#狗狗"
```

---

## 🔧 高级用法

### 自定义字幕样式
```toml
[video]
subtitle_style = 8  # 1-10 不同样式
```

### 使用本地图片
```bash
# 图片会自动应用 Ken Burns 特效
python video_maker.py --topic "主题" --images /path/to/images/
```

### 使用 Pexels 素材
```bash
# 配置 Pexels API Key 后
python video_maker.py --topic "宠物寄养"
# 自动下载匹配的竖版视频素材
```

### 批量任务
```json
[
  {
    "topic": "周末特惠",
    "template": "templates/shop.yaml",
    "images": "素材/"
  },
  {
    "topic": "新品上市",
    "template": "templates/shop.yaml"
  }
]
```

```bash
python video_maker.py --batch batch_tasks.json
```

---

## 📋 命令行参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `--topic` | 视频主题 | `--topic "宠物寄养"` |
| `--template` | 商家模板路径 | `--template templates/shop.yaml` |
| `--images` | 图片目录 | `--images 素材/` |
| `--batch` | 批量任务文件 | `--batch batch_tasks.json` |
| `--output` | 输出目录 | `--output 输出/` |
| `--config` | 配置文件路径 | `--config config.toml` |

---

## 🛠️ 技术栈

| 技术 | 用途 |
|------|------|
| **Python 3.8+** | 主语言 |
| **FFmpeg** | 视频处理 |
| **Edge TTS** | 免费语音合成 |
| **MiMo API** | AI 文案生成 |
| **Pexels API** | 素材视频下载 |
| **FastAPI** | Web 界面 |
| **WebSocket** | 实时进度推送 |
| **Pillow** | 图片处理 |
| **httpx** | HTTP 客户端 |

---

## 📊 性能指标

| 指标 | 值 |
|------|-----|
| 单视频生成时间 | 1-3 分钟 |
| 批量并行任务 | 4 个 |
| 支持图片格式 | JPG, PNG, WEBP |
| 支持视频格式 | MP4, MOV, AVI |
| 最大图片数量 | 20 张 |
| 最大视频时长 | 60 秒 |

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

### 贡献方式
1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

---

## 📄 许可证

MIT License - 详见 [LICENSE](LICENSE)

---

## 🙏 致谢

- [FFmpeg](https://ffmpeg.org/) - 视频处理
- [Edge TTS](https://github.com/rany2/edge-tts) - 语音合成
- [Pexels](https://www.pexels.com/) - 素材视频
- [FastAPI](https://fastapi.tiangolo.com/) - Web 框架

---

## 📧 联系方式

- 提交 [Issue](../../issues) 反馈问题
- 提交 [Pull Request](../../pulls) 贡献代码

---

## ⭐ Star History

如果这个项目对你有帮助，请给个 Star ⭐

[![Star History Chart](https://api.star-history.com/svg?repos=kaka2022/video-maker&type=Date)](https://star-history.com/#kaka2022/video-maker&Date)
