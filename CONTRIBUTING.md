# Contributing to 团购视频生成器

感谢您对本项目的关注！我们欢迎任何形式的贡献。

## 如何贡献

### 报告 Bug

1. 确保 Bug 尚未在 [Issues](../../issues) 中报告
2. 创建一个新的 Issue，包含：
   - 清晰的标题和描述
   - 重现步骤
   - 期望行为 vs 实际行为
   - 系统环境信息
   - 相关日志或截图

### 提交功能请求

1. 在 [Issues](../../issues) 中搜索是否已有类似请求
2. 创建一个新的 Issue，包含：
   - 功能描述
   - 使用场景
   - 实现建议（可选）

### 提交代码

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/AmazingFeature`
3. 提交更改：`git commit -m 'Add some AmazingFeature'`
4. 推送到分支：`git push origin feature/AmazingFeature`
5. 创建 Pull Request

## 开发环境设置

### 1. 克隆仓库

```bash
git clone https://github.com/kaka2022/video-maker.git
cd video-maker
```

### 2. 创建虚拟环境

```bash
python -m venv venv
source venv/bin/activate  # macOS/Linux
# 或
venv\Scripts\activate  # Windows
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt  # 开发依赖
```

### 4. 配置环境

```bash
cp config.toml.example config.toml
# 编辑 config.toml 填入你的 API Key
```

## 代码规范

### Python 代码风格

- 遵循 [PEP 8](https://www.python.org/dev/peps/pep-0008/) 规范
- 使用类型注解（Type Hints）
- 编写文档字符串（Docstrings）
- 保持函数简洁，单一职责

### 提交信息规范

使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
<type>(<scope>): <subject>

<body>

<footer>
```

**类型（type）**：
- `feat`: 新功能
- `fix`: Bug 修复
- `docs`: 文档更新
- `style`: 代码格式调整
- `refactor`: 代码重构
- `test`: 测试相关
- `chore`: 构建/工具相关

**示例**：
```
feat(tts): 添加 MiMo TTS 引擎支持

- 集成小米 MiMo TTS API
- 支持多种语音角色
- 添加重试机制

Closes #123
```

## 测试

### 运行测试

```bash
# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_video_maker.py

# 运行带覆盖率的测试
pytest --cov=video_maker
```

### 编写测试

- 为新功能编写单元测试
- 确保测试覆盖率不低于 80%
- 测试文件放在 `tests/` 目录

## 文档

### 更新文档

- 更新 README.md（如果需要）
- 更新 PROJECT.md（如果需要）
- 更新 CHANGELOG.md（必须）
- 添加代码注释（如果需要）

### 文档规范

- 使用中文编写
- 保持简洁清晰
- 包含代码示例
- 添加必要的截图

## Pull Request 流程

### 提交前检查

- [ ] 代码符合 PEP 8 规范
- [ ] 添加了类型注解
- [ ] 编写了文档字符串
- [ ] 更新了 CHANGELOG.md
- [ ] 添加了测试（如果适用）
- [ ] 测试全部通过

### PR 描述模板

```markdown
## 描述

简要描述此 PR 的更改内容。

## 更改类型

- [ ] 新功能
- [ ] Bug 修复
- [ ] 文档更新
- [ ] 代码重构
- [ ] 其他

## 测试

描述如何测试这些更改。

## 截图（如果适用）

添加相关截图。

## 相关 Issue

Closes #123
```

## 发布流程

### 版本号规则

遵循 [Semantic Versioning](https://semver.org/)：

- **Major (X.0.0)**: 不兼容的 API 更改
- **Minor (0.X.0)**: 向后兼容的功能添加
- **Patch (0.0.X)**: 向后兼容的 Bug 修复

### 发布步骤

1. 更新版本号
2. 更新 CHANGELOG.md
3. 创建 Git Tag
4. 推送到 GitHub
5. 创建 Release

## 行为准则

### 我们的承诺

为了营造一个开放和友好的环境，我们承诺：

- 使用友好和包容的语言
- 尊重不同的观点和经验
- 优雅地接受建设性批评
- 关注对社区最有利的事情
- 对其他社区成员表示同理心

### 我们的标准

积极行为包括：

- 使用友好和包容的语言
- 尊重不同的观点和经验
- 优雅地接受建设性批评
- 关注对社区最有利的事情
- 对其他社区成员表示同理心

不可接受的行为包括：

- 使用性暗示的语言或图像
- 恶意评论或人身攻击
- 公开或私下的骚扰
- 未经许可发布他人的私人信息
- 其他不道德或不专业的行为

## 联系方式

如有问题，请通过以下方式联系我们：

- 提交 [Issue](../../issues)
- 发送邮件到项目维护者

## 许可证

参与本项目即表示您同意您的贡献将在 [MIT License](LICENSE) 下发布。
