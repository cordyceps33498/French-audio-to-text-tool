# 架构说明

## 数据流

```text
Windows 播放设备
      │  WASAPI loopback (soundcard)
      ▼
LoopbackCapture (16 kHz / mono / float32, 24 ms 块)
      │
      ▼
SileroSegmenter (silero-vad ONNX, 512 采样窗口)
      │  静音 700ms 切句，前后保留 300ms，单句 ≤20s
      ▼
识别队列 (Queue, 最多 16 句)
      │
      ├── 识别线程：WhisperASR.transcribe()
      │        └── 失败：打印中文提示，继续下一句
      │
      └── 翻译线程（同一识别线程内串行）：MarianTranslator.translate()
               └── translate.enabled=false 时跳过
      ▼
ConsoleUI / PyQt6 Overlay
```

**关键点：录音只做 VAD，ASR 在独立线程消费队列。** 旧实现把 ASR 放在录音循环里，
识别 2~5 秒期间 loopback 缓冲区会溢出，导致漏句/截断。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `config.py` | 配置合并/校验、根目录发现、`%LOCALAPPDATA%` 用户配置、HF 环境变量、离线判定 |
| `devices.py` | loopback 设备枚举、名称/id 匹配、首次选择 |
| `audio_capture.py` | `LoopbackCapture` / `WavFileCapture`；统一 `frames()` 接口 |
| `vad.py` | silero-vad 模型加载、流式切句状态机、预录/补尾/强制切分 |
| `asr.py` | faster-whisper 加载与转写、上下文 prompt、幻听过滤 |
| `translate.py` | Marian 模型加载、直译/两段式回退、字节级 sentencepiece 加载 |
| `pipeline.py` | 线程编排、识别队列、单句异常隔离 |
| `console_ui.py` | 中文控制台输出、时间戳、设备选择交互 |
| `gui.py` | 可选 PyQt6 置顶悬浮窗，复用 pipeline |
| `__main__.py` | CLI、日志、错误边界、设备/模型初始化 |

## 配置优先级

```text
内置默认值
  < 便携包 config.yaml
  < %LOCALAPPDATA%\fr-translator\config.yaml
  < --config 指定的文件
  < 环境变量（FR_TRANSLATOR_*）与 --no-translate 等 CLI 覆盖
```

## 离线模型布局

```text
models/
├── faster-whisper/small/            # CTranslate2 模型（平铺目录）
├── hub/
│   ├── Helsinki-NLP--opus-mt-fr-en/ # 平铺目录，避免 Windows 符号链接问题
│   └── Helsinki-NLP--opus-mt-en-zh/
└── silero-vad/silero_vad.onnx
```

`snapshot_download(local_dir=...)` 生成平铺目录；兼容旧的
`models/hub/models--*/snapshots/*` 缓存布局。

## 线程与退出

- 主线程：读取音频、VAD、把句子放进队列；
- `fr-translator-asr`：消费队列，串行执行 ASR + 翻译；
- Ctrl+C / GUI 停止：关闭录音流 → 丢弃积压（最多等当前一句）→ 发送结束哨兵 → join；
- 识别线程是 daemon，即使异常也不会阻止进程退出。

## 失败隔离

- 单句 ASR 失败：中文提示 + 继续；
- 单句翻译失败：打印法语原文 + “翻译失败，已跳过本句”；
- 设备打开/读取失败：`AudioCaptureError`，控制台允许回车重试；
- 模型缺失：按 full/lite/asr 给出不同中文提示；
- 队列满：丢弃最早一句并警告，保证录音不中断。
