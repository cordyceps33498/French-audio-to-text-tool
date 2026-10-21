# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的基本格式，
版本号遵循语义化版本。

## [Unreleased]

### Added

- `asr` 轻量包模式：只识别法语原文，不加载翻译模型（`-Mode asr`、`--no-translate`）；
- 开源仓库配套：安全政策、贡献指南、行为准则、第三方声明、Issue/PR 模板、CodeQL、Dependabot。

### Security

- 日志不再记录识别/翻译正文，只记录耗时与字符数；
- `.gitignore` 增加密钥、证书、压缩包等规则。

## [0.1.0] - 2026-10-21

### Added

- 阶段 1：loopback 系统音频捕获、silero-vad 切句、faster-whisper CPU int8、
  OPUS-MT 翻译、中文异常提示、设备选择、用户配置；
- 阶段 2：Python 3.10 embeddable 便携包，`run.bat`、`--local`、离线模型加载、
  lite/full 构建脚本与模型预下载；
- 阶段 3：GitHub Actions 构建/Release、README、验收步骤；
- 阶段 4：PyQt6 悬浮字幕（可选）。

### Fixed

- 中文路径下 sentencepiece `LoadFromFile` 失败：改为字节加载 `LoadFromSerializedProto`；
- ASR/翻译阻塞录音导致漏句：改为录制线程 + VAD + 识别队列；
- 切句过激进：静音 700ms、前后 padding 300ms、单句上限 20s，并加入上一句上下文提示。
