# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2026-06-01

### Added
- 多 TTS 引擎支持（Edge TTS + MiMo TTS）
- 商家模板系统（YAML 配置）
- 批量任务调度
- Web 管理界面（FastAPI + WebSocket）
- Ken Burns 特效（图片缩放平移）
- 智能转场（xfade）
- 背景音乐自动匹配
- AI 内容过滤模块
- Pexels 素材自动下载
- 字幕动画（10+ 样式）
- 配置文件外置（TOML）
- 日志系统（logging）
- API 重试机制

### Fixed
- 修复 xfade 转场 offset 计算 Bug
- 修复 TTS 时长对齐问题
- 修复 FFmpeg 编码参数优化

### Changed
- 重构代码架构，模块化设计
- 优化视频合成流程
- 改进错误处理机制

## [2.0.0] - 2026-05-15

### Added
- Pexels 素材视频支持
- 背景音乐匹配功能
- FastAPI Web 界面
- WebSocket 实时进度推送
- 批量生成功能

### Changed
- 优化视频编码参数
- 改进字幕渲染效果

## [1.0.0] - 2026-05-01

### Added
- 初始版本发布
- AI 文案生成功能
- TTS 语音合成
- 基础视频剪辑
- 字幕叠加
- 命令行界面

---

## 版本说明

- **Major (X.0.0)**: 不兼容的 API 更改
- **Minor (0.X.0)**: 向后兼容的功能添加
- **Patch (0.0.X)**: 向后兼容的 Bug 修复
