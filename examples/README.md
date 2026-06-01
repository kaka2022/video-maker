# 示例文件

本目录包含团购视频生成器的使用示例。

## 文件说明

- `batch_tasks.json` - 批量任务示例
- `template_example.yaml` - 商家模板示例
- `config_example.toml` - 配置文件示例

## 使用方法

### 1. 批量任务

```bash
python video_maker.py --batch examples/batch_tasks.json
```

### 2. 商家模板

```bash
python video_maker.py --template examples/template_example.yaml --topic "你的主题"
```

### 3. 配置文件

```bash
cp examples/config_example.toml config.toml
# 编辑 config.toml 填入你的 API Key
python video_maker.py --config config.toml --topic "你的主题"
```
