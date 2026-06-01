# 团购视频生成器

> 一句话生成抖音/小红书团购引流短视频 — AI 文案 + TTS 配音 + 自动剪辑

## ✨ 功能特点

- **AI 文案生成**：输入主题关键词，自动生成 8 句口语化带货文案
- **TTS 语音合成**：支持 MiMo TTS / Edge TTS（免费），逐句配音
- **素材自动匹配**：根据关键词从 Pexels 自动下载竖版视频素材
- **Ken Burns 特效**：静态图片自动添加缩放平移动效
- **智能转场**：xfade 转场 + 字幕叠加
- **背景音乐**：根据内容风格自动匹配 BGM
- **商家模板**：YAML 模板管理价格、服务、卖点等信息
- **批量任务**：JSON 配置批量生成多个视频
- **Web 界面**：FastAPI 后端 + 前端页面，支持实时进度推送

## 📁 目录结构

```
video-maker/
├── video_maker.py      # 核心生成引擎
├── bgm.py              # 背景音乐匹配模块
├── pexels.py           # Pexels 素材下载模块
├── ai_filter.py        # AI 内容过滤模块
├── config.toml.example # 配置模板（复制为 config.toml 后填入密钥）
├── batch_tasks.json    # 批量任务示例
├── web/
│   ├── server.py       # FastAPI 后端
│   └── index.html      # 前端页面
├── templates/
│   └── example.yaml    # 商家模板示例
├── 脚本/               # 短视频文案库
├── 素材库/             # 素材管理
│   └── 文案资料/       # 文案素材
└── 音乐/               # 背景音乐
```

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install edge-tts pycapsule requests toml pyyaml
# FFmpeg 需要单独安装
brew install ffmpeg  # macOS
# 或
sudo apt install ffmpeg  # Ubuntu/Debian
```

### 2. 配置

```bash
cp config.toml.example config.toml
# 编辑 config.toml，填入你的 API Key
```

**需要的 API：**

| 服务 | 用途 | 是否必须 |
|------|------|----------|
| [小米 MiMo](https://mimo.xiaomi.com) | AI 文案 + TTS 配音 | 是 |
| [Pexels](https://www.pexels.com/api/) | 自动下载素材视频 | 否（可用本地图片代替） |

### 3. 生成视频

```bash
# 基本用法（纯本地图片）
python video_maker.py --topic "你的主题" --images 素材/

# 使用商家模板
python video_maker.py --template templates/example.yaml --topic "你的主题" --images 素材/

# 使用 Pexels 自动下载素材
python video_maker.py --topic "你的主题"

# 批量生成
python video_maker.py --batch batch_tasks.json --images 素材/
```

### 4. Web 界面（可选）

```bash
pip install fastapi uvicorn
python web/server.py
# 访问 http://localhost:8000
```

## ⚙️ 配置说明

`config.toml` 主要配置项：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `video.width` / `height` | 视频分辨率 | 1080x1920（竖版） |
| `video.fps` | 帧率 | 30 |
| `video.img_duration` | 每张图片展示秒数 | 4 |
| `video.transition_duration` | 转场时长 | 0.5s |
| `audio.bgm_volume` | 背景音乐音量 | 0.12 |
| `audio.tts_engine` | TTS 引擎 | mimo_tts |
| `batch.max_workers` | 批量并行数 | 4 |

## 📝 商家模板

创建 `templates/your_shop.yaml`，格式参考 `templates/example.yaml`：

```yaml
merchant:
  name: "你的店铺名称"
  full_name: "你的店铺全称"
  address: "你的店铺地址"
  phone: "你的联系电话"
  contact: "联系人姓名"
  hours: "10:00-22:00"

regions:
  - "你的城市"
  - "你的区域"

services:
  服务1:
    - name: "基础服务"
      price: 100
      unit: "元/次"
    - name: "高级服务"
      price: 200
      unit: "元/次"

selling_points:
  - "你的卖点1"
  - "你的卖点2"
  - "你的卖点3"

hashtags:
  - "#你的行业标签"
  - "#你的地区标签"
  - "#你的品牌标签"
```

## 📚 文案脚本

在 `脚本/` 目录放置短视频文案脚本，每个 `.txt` 文件对应一个主题。

**文件格式**：每行一句文案，用于 TTS 语音合成。建议 8-12 句，每句 15-25 字。

**示例**（`脚本/示例-团购引流.txt`）：

```
还在为找不到靠谱的店铺发愁吗
今天给大家推荐一家超赞的店
环境干净整洁服务超级贴心
价格透明没有任何隐形消费
老客户都说好新客户更满意
专业团队持证上岗让您放心
现在下单还有超值优惠活动
赶紧点击下方链接体验吧
```

## 🎬 视频效果

- **分辨率**：1080x1920（竖版，适配抖音/小红书）
- **时长**：约 30-60 秒
- **配音**：AI 语音，自然流畅
- **字幕**：逐句显示，多种样式可选
- **转场**：平滑过渡，专业感强
- **背景音乐**：自动匹配内容风格

## 🔧 高级用法

### 批量任务

创建 `batch_tasks.json`：

```json
[
  {
    "topic": "主题1",
    "template": "templates/example.yaml",
    "images": "素材/"
  },
  {
    "topic": "主题2",
    "template": "templates/example.yaml"
  }
]
```

运行批量任务：

```bash
python video_maker.py --batch batch_tasks.json
```

### 自定义字幕样式

修改 `config.toml` 中的 `video.subtitle_style`：

```toml
[video]
subtitle_style = 8  # 1-10 不同样式
```

### 使用本地图片

```bash
# 指定图片目录
python video_maker.py --topic "主题" --images /path/to/images/

# 图片会自动应用 Ken Burns 特效（缩放平移）
```

### 使用 Pexels 素材

```bash
# 配置 Pexels API Key
# config.toml 中填入 pexels.api_key

# 自动下载匹配素材
python video_maker.py --topic "宠物寄养"
```

## 📋 命令行参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `--topic` | 视频主题 | `--topic "宠物寄养"` |
| `--template` | 商家模板路径 | `--template templates/example.yaml` |
| `--images` | 图片目录 | `--images 素材/` |
| `--batch` | 批量任务文件 | `--batch batch_tasks.json` |
| `--output` | 输出目录 | `--output 输出/` |

## 🛠️ 技术栈

- **Python 3.8+**
- **FFmpeg**：视频处理
- **Edge TTS**：免费语音合成
- **MiMo API**：AI 文案生成（可选）
- **Pexels API**：素材视频下载（可选）
- **FastAPI**：Web 界面（可选）

## 📄 许可证

MIT License

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📧 联系方式

如有问题，请提交 [Issue](../../issues)。
