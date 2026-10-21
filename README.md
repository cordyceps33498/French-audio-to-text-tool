# fr-translator · Windows 法语系统音频实时翻译

把电脑正在播放的**法语声音**实时转成中文字幕：系统音频（loopback）→ silero-vad 切句 →
faster-whisper 识别 → OPUS-MT 翻译 → 控制台两行输出（可选 PyQt6 悬浮字幕）。

- 只捕获**系统播放的声音**，不碰麦克风，不修改系统音频设置。
- 纯 CPU 运行（int8），无需显卡。
- 两种形态共用一份代码：**便携包**（目标机器不需要 Python）和**源码运行**（开发调试）。
- 不收集、不上传任何音频或文本。

> 目标平台：Windows 10 / 11 x64。macOS / Linux 不在支持范围内。

---

## 1. 效果示意

播放一段法语视频后，控制台输出：

```text
[21:03:12] fr-translator 0.1.0（根目录：D:\FrenchTranslatorPortable）
[21:03:12] 运行模式：离线（不联网），版本标识：full
[21:03:13] 正在加载 silero-vad 切句模型 ...
[21:03:14] 正在加载语音识别模型（faster-whisper small，CPU int8）...
[21:03:18] 正在加载翻译模型（Helsinki-NLP/opus-mt-fr-zh）...
[21:03:22] 翻译模型已就绪：Helsinki-NLP/opus-mt-fr-en → Helsinki-NLP/opus-mt-en-zh（两段式）
[21:03:22] 模型加载完成，开始监听系统音频。
[21:03:22] 正在监听系统音频：扬声器（Realtek(R) Audio）。请播放法语音频，按 Ctrl+C 停止。
[21:03:27] 法语：Bonjour à toutes et à tous, bienvenue dans ce documentaire.
[21:03:27] 中文：大家好，欢迎收看这部纪录片。
[21:03:33] 法语：Aujourd'hui, nous allons parler de la cuisine française.
[21:03:33] 中文：今天我们要聊的是法国美食。
```

悬浮字幕模式（`python -m fr_translator.gui`）是一个无边框、半透明、可拖动的置顶小窗：

```text
┌──────────────────────────────────────────────────────────┐
│  Bonjour à toutes et à tous, bienvenue...                │
│  大家好，欢迎收看这部纪录片。                              │
│  正在监听：扬声器（Realtek(R) Audio）                      │
└──────────────────────────────────────────────────────────┘
```

首次识别会有几秒延迟（模型冷启动 + 第一句要等静音切句），之后进入稳定节奏。

---

## 2. 便携版使用（推荐）

### 2.1 下载哪个包

| 包 | 大小（约） | 是否含模型 | 适合谁 |
| --- | --- | --- | --- |
| `FrenchTranslatorPortable-lite.zip` | 327 MB | 否，首次运行联网下载 | 网络正常、想快速下载 |
| `FrenchTranslatorPortable-full.zip` | 1.27 GB | 是（faster-whisper small + 两段式 OPUS-MT） | 需要中文字幕、纯离线 |
| `FrenchTranslatorPortable-asr.zip` | 754 MB | 是（仅 faster-whisper small） | 只要法语原文、想更轻量 |

lite/full 会输出“法语 + 中文”两行；**asr 包只输出法语原文**，不加载翻译模型，体积更小、
启动更快。三个包都支持 `--local`。

### 2.2 三步启动

1. 解压 zip（建议解压到本地磁盘，不要直接在压缩包里双击）。
2. 双击 `run.bat`。
3. 首次运行会列出可用的输出设备，输入编号并回车：

```text
[21:00:01] 检测到多个可用的系统音频输出设备：
[21:00:01]   1. 扬声器 (Realtek(R) Audio)（扬声器 (Realtek(R) Audio)）
[21:00:01]   2. 耳机 (USB Audio)（耳机 (USB Audio)）
[21:00:01] 请选择要监听的设备编号（1-2，直接回车选择 1）：
```

选择结果会写入 `%LOCALAPPDATA%\fr-translator\config.yaml`，**不会写 U 盘**；
换电脑、换扬声器时会重新提示。想重新选择，删除该文件即可。

### 2.3 USB 2.0：使用 `--local`

U 盘随机读取速度慢，直接加载 torch 和模型可能要等很久。把便携包镜像到本机临时目录再启动：

```bat
run.bat --local
```

行为：

- 用 `robocopy /E` 把便携包**增量复制**到 `%TEMP%\fr-translator-portable\`（只复制新增/变化的文件，不会每次全量重拷，也不会删除 lite 包已下载的模型）；
- 从临时目录启动，之后再次 `--local` 只同步变化；
- `logs\` 不会被镜像覆盖；
- 退出后临时目录保留，下次启动更快；不需要时可手动删除。

### 2.4 杀毒软件误报

便携包内置了 Python 解释器和大量 `.pyd/.dll`（torch、ctranslate2、onnxruntime 等）。
部分杀软会对“U 盘里带 Python 的可执行文件”敏感，可能出现误报或首次启动扫描很慢。
处理方式：把整个目录加入杀软白名单，或改用 full 包并放在本地磁盘运行。
本项目不包含任何下载器/混淆壳，所有文件都可以在 GitHub 上核对。

### 2.5 便携包目录

```text
FrenchTranslatorPortable/
├── run.bat                 # 主入口
├── run-gui.bat             # 可选：构建时加 -WithGui 才有
├── config.yaml             # 随包默认配置（device: auto）
├── README.md
├── python/                 # Python 3.10 embeddable
│   ├── python.exe
│   ├── python310._pth      # 已启用 import site + ..\app + Lib\site-packages
│   └── Lib/site-packages/  # 依赖
├── app/fr_translator/      # 应用代码
├── models/
│   ├── faster-whisper/small/
│   ├── hub/                # OPUS-MT 翻译模型的 HF 缓存（asr 包没有这一层）
│   └── silero-vad/silero_vad.onnx
└── logs/
```

### 2.6 只识别法语原文（asr 轻量包）

`FrenchTranslatorPortable-asr.zip` 只保留“系统音频 → VAD 切句 → faster-whisper 法语识别”，
不下载也不加载任何翻译模型，控制台只输出一行：

```text
[21:03:27] 法语：Bonjour à toutes et à tous, bienvenue dans ce documentaire.
```

它适合：只要法语听写/字幕原文、机器内存较小、想更快启动的场景。

源码运行时等价于：

```bat
.venv\Scripts\python.exe -m fr_translator --no-translate
```

或者在 `config.yaml` 里设置：

```yaml
translate:
  enabled: false
```

asr 包的 `run.bat` 已经自动设置 `FR_TRANSLATOR_DISABLE_TRANSLATE=1`，配置文件里也是
`enabled: false`，不需要手工改。如果要重新开启翻译，删除该环境变量并准备翻译模型即可
（但 asr 包本身不包含翻译模型）。

---

## 3. 源码运行（开发者）

环境：Windows 10/11 x64 + **Python 3.10**（64 位）。

```bat
py -3.10 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
run-dev.bat
```

`run-dev.bat` 会自动 `chcp 65001`、设置 `PYTHONIOENCODING=utf-8` 和 `FR_TRANSLATOR_ROOT`，
然后执行 `python -m fr_translator`。如果 `.venv` 不存在，会询问是否自动创建并安装依赖。

也可以直接：

```bat
set PYTHONPATH=%CD%\src
.venv\Scripts\python.exe -m fr_translator --list-devices
.venv\Scripts\python.exe -m fr_translator
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--list-devices` | 列出可录制的系统音频设备后退出 |
| `--device NAME_OR_ID` | 指定输出设备（名称或 soundcard 设备 id） |
| `--offline` | 强制离线，缺模型直接报错 |
| `--online` | 允许联网补全缺失模型 |
| `--no-translate` | 只识别法语原文，不加载翻译模型（asr 模式） |
| `--config FILE` | 额外配置文件（优先级最高，可多次指定） |
| `--wav FILE` | 开发调试：从 PCM WAV 文件读取（16k 单声道最佳），不捕获系统音频 |
| `--debug` | 输出调试日志和完整异常堆栈 |

模型默认下载到仓库的 `models/`（与便携包布局一致）：

```bat
set HF_ENDPOINT=https://hf-mirror.com
.venv\Scripts\python.exe scripts\prepare_models.py --root .
```

---

## 4. 配置

加载顺序（后者覆盖前者）：

1. 程序内置默认值；
2. 便携包根目录 `config.yaml`（源码模式是仓库根目录）；
3. `%LOCALAPPDATA%\fr-translator\config.yaml`（用户配置，设备选择/GUI 位置写在这里）；
4. `--config` 指定的文件。

完整示例见 [`config.example.yaml`](config.example.yaml)。最常改的几项：

```yaml
audio:
  device: auto        # 输出设备；auto 表示首次运行时交互选择
  block_ms: 24        # 每次读取 24ms
vad:
  min_silence_ms: 700 # 静音超过 700ms 才切句，避免逗号处截断
  max_utterance_s: 20 # 单句最长 20s
asr:
  model: small
  compute_type: int8
translate:
  model: Helsinki-NLP/opus-mt-fr-zh
  fallback: true
  pivot_model: Helsinki-NLP/opus-mt-fr-en
  pivot_target_model: Helsinki-NLP/opus-mt-en-zh
logging:
  level: INFO
  file: logs/fr-translator.log
```

校验规则（写错会得到中文提示，不会抛堆栈）：采样率只能是 16000、单声道；阈值 0.1~0.95；
`max_utterance_s` 必须大于 `min_silence_ms + 0.5s`；`asr.language` 固定为 `fr`。

### 关于翻译模型（重要）

需求指定的 `Helsinki-NLP/opus-mt-fr-zh` 在 Hugging Face 上**并不存在**：
Helsinki-NLP 的 OPUS-MT 官方模型里没有法语→中文直译对（2026-10 核实，
`https://huggingface.co/api/models/Helsinki-NLP/opus-mt-fr-zh` 返回 404/401）。

因此默认行为是：

1. 本地/远端若存在 `opus-mt-fr-zh`，优先直译；
2. 否则自动回退到两段式 `opus-mt-fr-en`（法→英）+ `opus-mt-en-zh`（英→中），
   两者都是 OPUS-MT / MarianMT，同样完全离线；
3. 控制台会明确打印当前用的是哪一种，不会静默换模型。

如果你找到了可用的法语→中文直译模型（例如自己微调的 Marian 模型），把
`translate.model` 改成它的 id、把 `fallback` 设为 `false` 即可。
两段式会多一次推理，CPU 上大约多 0.3~1 秒延迟。

### 模型路径与离线

- 识别模型：`models/faster-whisper/small/`（`download_root` 显式指向这里）；
- 翻译模型：`models/hub/Helsinki-NLP--opus-mt-fr-en/` 与 `...--opus-mt-en-zh/`
  （`snapshot_download(local_dir=...)` 的平铺目录，不依赖 HF 符号链接缓存；
  也兼容旧的 `models/hub/models--*/snapshots/*` 布局）；
- silero-vad：优先 `models/silero-vad/silero_vad.onnx`，缺失时用 silero-vad 包内置 ONNX；
- full 包模型齐全时自动离线；lite 包首次运行允许联网，下载完成后自动转为离线。

> 为什么不用默认 HF 缓存：Windows 未开启“开发者模式”时普通用户没有创建符号链接的权限，
> HF 缓存会退化为复制甚至留下 0 字节文件。平铺目录没有这个问题，复制到 U 盘也更直观。
> 程序路径含中文也没问题：sentencepiece 的窄字符文件 API 打不开中文路径，代码改为
> 用 Python 读取 `.spm` 字节并调用 `LoadFromSerializedProto`，不再依赖临时目录。

---

## 5. 国内网络

Hugging Face 下载慢或失败时，设置镜像后重试：

```bat
set HF_ENDPOINT=https://hf-mirror.com
```

- 源码运行：设置后再执行 `python -m fr_translator` 或 `scripts\prepare_models.py`；
- 构建便携包：

  ```powershell
  .\scripts\build_portable.ps1 -Mode full -HfEndpoint https://hf-mirror.com
  ```

- CI：手动触发 workflow 时填写 `hf_endpoint`，或在仓库 Variables 里设置 `HF_ENDPOINT`。

lite 包遇到模型下载失败时会提示：
`模型缺失：请联网后重试，或设置 HF_ENDPOINT=https://hf-mirror.com 使用镜像。`

---

## 6. 常见问题

### 6.1 没有声音 / 选错设备

1. 先确认视频本身有声音，Windows 音量合成器里没有被静音；
2. 运行 `--list-devices`，确认目标设备出现在列表里；
3. 删除 `%LOCALAPPDATA%\fr-translator\config.yaml`，重新运行会再次提示选择；
4. 蓝牙耳机切换后设备 id 会变化，重新选择即可；
5. 报“没有找到回环设备（loopback）”时，按提示检查输出设备是否被禁用、是否在远程桌面/虚拟机里。

### 6.2 提示模型缺失

- **full 包**：提示“模型文件不完整，请重新下载 full 包”——重新解压，或确认
  `models\faster-whisper\small\model.bin` 与 `models\hub\` 下的 OPUS-MT 模型目录存在；
  也可以加 `--online` 联网补全。
- **lite 包**：提示“请联网或设置 HF_ENDPOINT 镜像后重试”——首次运行必须联网下载约 1.1GB 模型。
  下载中断会留下不完整目录，删除 `models\faster-whisper\small` 或 `models\hub` 后重试。

### 6.3 控制台中文乱码

`run.bat` 已经执行 `chcp 65001` 并设置 `PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`。
如果仍然乱码：

- 使用 Windows Terminal，或在 `cmd.exe` 里手动执行 `chcp 65001`；
- 不要用“以 ANSI 编码保存”的第三方终端包装器；
- 源码运行时确认没有在代码里覆盖 `sys.stdout` 编码。

### 6.4 USB 2.0 启动很慢

使用 `run.bat --local`。首次镜像会花一点时间（取决于 U 盘速度），之后启动从本地磁盘读取，
通常能从几分钟降到十几秒。也可以直接把整个目录拷到本地磁盘使用。

### 6.5 首次识别延迟 / 字幕比声音慢

CPU 模式下整体延迟约 **2~5 秒**，来源：

1. 第一句要等 700ms 静音才切句；
2. whisper small 在 CPU 上推理需要时间；
3. 翻译模型再花几百毫秒。

可以调小 `vad.min_silence_ms`（例如 500）让切句更快，但句子可能被切碎、翻译质量下降。
想要更低延迟需要 GPU 或更小的模型（本工具默认不提供）。

### 6.5.1 识别漏词、句子被从中间截断

2026-10-21 的版本做了两项针对性修复：

1. **录音与识别解耦**：以前 ASR/翻译（2~5 秒）直接阻塞录音循环，loopback 缓冲区溢出会丢掉
   整段音频；现在录音线程只做 VAD，切好的句子进队列，由独立识别线程处理，录音不中断。
2. **更宽容的切句**：静音阈值从 500ms 调到 700ms，前后保留 300ms，单句上限 20s，
   并把上一句文本作为 `initial_prompt`，减少句首/句尾丢词。

如果仍然觉得切得太碎，可以在 `config.yaml` 里调大：

```yaml
vad:
  min_silence_ms: 900   # 句子更长，但字幕延迟增加
  speech_pad_ms: 400
  pre_roll_ms: 400
```

如果识别速度跟不上（控制台出现“识别速度跟不上，已丢弃最早的一句”），可降低
`asr.beam_size`（如 3）或改用更小的模型；正常情况下不会触发。

### 6.6 播放纯音乐时输出乱码/幻听

程序有两层过滤：silero-vad 只把“有人声”的片段送进 ASR，faster-whisper 内置 VAD 再过滤一次，
并对常见的字幕组幻听文本做了丢弃。纯音乐通常不会输出任何内容；偶发一两句属于模型正常误差。
如果音乐里持续出现人声，仍会正常翻译。

### 6.7 程序启动就退出

看 `logs\fr-translator.log` 和 `run.bat` 窗口最后的提示；加 `--debug` 会打印完整异常堆栈。
常见原因是缺少 VC++ 运行库（Windows 10/11 一般自带）或被杀软拦截了 `.pyd` 文件。

### 6.8 日志里出现 `sentencepiece ... Not found: ...source.spm`

这是旧版包的已知问题：程序路径含中文时，sentencepiece 的窄字符文件 API 打不开
`source.spm`，即使文件存在也会报 Not found。**2026-10-21 15:40 之后的包已修复**
（改为读取字节后 `LoadFromSerializedProto`）。请重新下载 full/lite 包；如果暂时不想重下，
也可以只把新版 `app\fr_translator\translate.py` 覆盖到旧包对应位置。

---

## 7. 已知限制

- 仅支持 **Windows 10/11 x64**；
- 仅捕获**系统输出音频**，不能选麦克风（需要麦克风请改 `audio_capture.py` 的设备来源）；
- 仅支持**法语 → 中文**；
- 纯 CPU、`small` 模型，端到端延迟约 2~5 秒，不适合实时会议同传；
- 不支持多路音频混音，一次只能监听一个输出设备；
- 需要约 3GB 磁盘空间（full 包解压后）和 4GB 以上内存；
- 首次加载模型需要十几秒，期间没有字幕是正常现象。

---

## 8. 项目结构

```text
fr-translator/
├── README.md
├── LICENSE
├── .gitignore
├── pyproject.toml
├── requirements.txt              # 开发用
├── requirements-portable.txt     # 便携包用（torch CPU 版）
├── requirements-portable-asr.txt # asr 轻量包用（不含 transformers）
├── requirements-gui.txt          # 可选 GUI 依赖
├── config.example.yaml
├── run-dev.bat
├── src/fr_translator/
│   ├── __init__.py
│   ├── __main__.py               # python -m fr_translator
│   ├── config.py                 # 配置加载、路径解析、离线环境
│   ├── devices.py                # loopback 设备枚举与选择
│   ├── audio_capture.py          # 统一捕获接口（loopback / 文件）
│   ├── vad.py                    # silero-vad 流式切句
│   ├── asr.py                    # faster-whisper 封装
│   ├── translate.py              # OPUS-MT 翻译封装（含两段式回退）
│   ├── pipeline.py               # 流水线编排
│   ├── console_ui.py             # 控制台输出
│   ├── gui.py                    # 可选 PyQt6 悬浮字幕
│   └── errors.py                 # 面向用户的中文异常
├── scripts/
│   ├── prepare_models.py         # 预下载模型
│   ├── build_portable.ps1        # 一键构建便携包
│   ├── check_env.py              # 环境自检
│   └── check_repo.py             # 仓库密钥/大文件/合规检查
├── tests/                        # unittest，无需额外依赖
├── docs/
│   ├── ARCHITECTURE.md           # 架构与线程模型
│   ├── PRIVACY.md                # 隐私说明
│   └── COMPLIANCE_SECURITY_REPORT.md
├── SECURITY.md                   # 漏洞报告与安全边界
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── CHANGELOG.md
├── THIRD_PARTY_NOTICES.md        # 依赖/模型许可证清单
├── LICENSES/                     # Apache/BSD/MPL/PSF 许可证文本
├── portable/                     # 构建 staging（.gitignore）
└── .github/
    ├── workflows/build-portable.yml
    ├── workflows/ci.yml
    ├── workflows/codeql.yml
    ├── ISSUE_TEMPLATE/
    └── dependabot.yml
```

---

## 9. 构建便携包

```powershell
# lite：无模型，约 327MB
powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -Mode lite

# full：含模型，约 1.27GB，纯离线
powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -Mode full

# asr：只识别法语原文，约 754MB
powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -Mode asr

# 可选：带 PyQt6 悬浮字幕
powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -Mode full -WithGui

# 国内镜像
powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -Mode asr -HfEndpoint https://hf-mirror.com
```

参数：

| 参数 | 说明 |
| --- | --- |
| `-Mode lite\|full\|asr` | 必填；asr = 仅法语识别，不装翻译依赖和模型 |
| `-SkipModels` | 不下载模型（CI 调试用） |
| `-ModelsSource models` | 直接复制现成模型目录，跳过下载；asr 只复制 `faster-whisper/` 和 `silero-vad/` |
| `-PythonVersion 3.10.11` | 嵌入式 Python 版本 |
| `-OutputDir dist` | 输出目录 |
| `-HfEndpoint URL` | 传给 prepare_models 的镜像 |
| `-WithGui` | 安装 PyQt6 并生成 `run-gui.bat` |
| `-CompressionLevel Optimal\|Fastest\|NoCompression` | zip 压缩级别 |

构建流程：

1. 下载 `python-3.10.11-embed-amd64.zip`（缓存到 `portable\.cache`）；
2. 解压到 `python/`，修改 `python310._pth`（`import site` + `..\app` + `Lib\site-packages`）；
3. 用固定版本的 `pip-24.2` wheel 引导 pip（无系统 Python 时回退到 `get-pip.py`），
   再 `pip install --target python\Lib\site-packages -r requirements-portable.txt`
   （asr 模式使用 `requirements-portable-asr.txt`，不含 transformers/sentencepiece）；
4. 拷贝 `src/fr_translator` 到 `app/`；
5. full 模式用**嵌入式 Python** 运行 `scripts/prepare_models.py`（或 `-ModelsSource` 直接复制），
   模型落到 `models/`；asr 模式加 `--skip-translate`，只下载 faster-whisper + silero-vad；
6. 生成 `run.bat`、`config.yaml`、`logs/`（asr 的 run.bat 会设置 `FR_TRANSLATOR_DISABLE_TRANSLATE=1`）；
7. 打包到 `dist/FrenchTranslatorPortable-<Mode>.zip`。

> 说明：Python embeddable 没有 `ensurepip`，构建脚本用官方 `get-pip.py` / 固定 pip wheel 等价替代；
> `_pth` 中额外加入 `..\app` 是必需的——embeddable 是 isolated 模式，会忽略 `PYTHONPATH`。
> 构建时会把 `TEMP` 指到 `portable\.cache\tmp`，避免杀软/受控环境锁住系统临时目录导致 pip 解包失败。

### 发布

推送 `v*` tag 即可触发 GitHub Actions：

```bat
git tag v0.1.0
git push origin v0.1.0
```

workflow 会构建 lite、full、asr，上传 artifact，并把三个 zip 附加到 Release。
full 包模型下载较慢，job 超时 240 分钟、构建步骤 180 分钟，`prepare_models.py` 内置 3 次重试。

---

## 10. 环境自检

```bat
:: 源码
.venv\Scripts\python.exe scripts\check_env.py

:: 便携包（在便携包根目录）
python\python.exe ..\..\scripts\check_env.py --root .
```

检查 Python 版本/架构、依赖导入、torch 是否 CPU 版、模型文件、loopback 设备、日志目录权限。
输出 `[OK] / [警告] / [失败]`，退出码 0 表示通过。

---

## 11. 验收测试步骤

在一台**没有安装 Python** 的干净 Windows 11 上：

1. **full 包基本功能**：解压 `FrenchTranslatorPortable-full.zip`，双击 `run.bat`，选择扬声器，
   播放一段法语视频，控制台应出现成对的“法语：… / 中文：…”。
2. **断网离线**：拔掉网络后重复步骤 1，full 包必须仍能启动并翻译。
3. **U 盘便携**：把解压后的目录复制到 U 盘，在另一台 Windows 电脑上双击 `run.bat` 应能启动；
   再用 `run.bat --local` 验证临时目录模式（第二次启动应明显更快）。
4. **重新选择设备**：删除 `%LOCALAPPDATA%\fr-translator\config.yaml` 后重新运行，
   应重新列出设备并提示选择。
5. **无音频设备**：禁用/拔出所有音频输出设备后运行，应看到中文错误提示（不是 Python 堆栈），
   修复后按回车可以重试。
6. **纯音乐**：播放无人声的音乐，程序不应崩溃，最多输出空结果并继续监听。

补充检查：

- lite 包在断网、且 `models/` 为空时运行，应提示“请联网或设置 HF_ENDPOINT 镜像”，而不是崩溃；
- `run.bat --local` 第二次启动时 `%TEMP%\fr-translator-portable\` 只更新变化的文件；
- 删除日志目录后运行，日志应回退写到 `%LOCALAPPDATA%\fr-translator\logs`（U 盘只读场景）。

### 本地构建与测试记录（2026-10-21，Windows 11 x64 / CPU）

| 产物 | 大小 | SHA-256 |
| --- | --- | --- |
| `FrenchTranslatorPortable-lite.zip` | 326.7 MB（解压 1.51 GB） | `6f999101ba0ab68728c2cf0b65ae295c403ea26abdca127e9bb3e3e57cb2a320` |
| `FrenchTranslatorPortable-full.zip` | 1.27 GB（解压 2.54 GB） | `4fdc6933be8c8c3900d96b11901eef286274e1a1f43de8fde8c1135d672201df` |
| `FrenchTranslatorPortable-asr.zip` | 754 MB（解压 2.0 GB） | `1c703f8b420f4258ca0c54e1954c1d5168bb4e989aa66d5876bb57afde896407` |

已执行的验证：

- 35 个 `unittest` 全部通过（配置合并/校验、设备匹配、VAD 切句、识别队列、仅识别模式、WAV 读取、空识别、翻译失败降级、纯音乐）；
- 用 MMS-TTS 合成 4 句法语（23.1 秒，含一句 8 秒长句），`python -m fr_translator --wav`
  输出 4 组“法语/中文”，长句没有被从中间截断；
- 用 `winsound` 播放该 WAV，通过系统 loopback 捕获，实际输出字幕（非文件模拟）；
- full 包解压后断网运行 `run.bat --wav`，正常加载本地模型并翻译；
- asr 包解压后运行 `run.bat --wav`，只输出 4 行“法语：…”，不加载翻译模型；
- `run.bat --local` 成功把包复制到 `%TEMP%` 并从临时目录启动；
- 设备选择结果写入用户配置后，第二次启动不再提示，直接监听上次选择的设备；
- 纯音乐（6 秒和弦）运行无输出、无异常，退出码 0；
- 三个 zip 均通过 `ZipFile.testzip()` 完整性校验。

> **2026-10-21 23:20 修复**：录音与识别解耦——ASR/翻译不再阻塞 loopback 读取；
> 静音阈值 700ms、前后 padding 300ms、单句上限 20s，并加入上一句上下文提示。
> 这版专门解决“漏词/句子从中间截断”。

> **2026-10-21 15:40 修复**：中文路径下 `sentencepiece` 的窄字符文件 API 会报
> `Not found: ...source.spm`，导致翻译模型加载失败。现已改为 Python 读取 `.spm` 字节并
> 调用 `LoadFromSerializedProto`，不再依赖临时目录或 ASCII 路径；相关 zip 已重新打包。
> 如果你之前下载过旧包，请使用上表的新包（旧 SHA-256 已失效）。

> 本机的 DSH 沙箱禁止写 `%LOCALAPPDATA%`，所以“用户配置写入”是在把 `LOCALAPPDATA`
> 指到工作区临时目录后验证的；真实桌面上写入位置不变。无音频设备/设备被占用的提示
> 通过单元测试和代码路径检查覆盖，未在真机上禁用声卡（避免修改系统音频设置）。

---

## 12. 可选：PyQt6 悬浮字幕

设计目标：把字幕叠在视频上方，不抢焦点、可点击穿透。

```bat
.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
.venv\Scripts\python.exe -m fr_translator.gui
```

功能：

- 无边框、半透明、始终置顶，可拖动，位置和字号会记住；
- 两行显示：小字法语原文 + 大字中文翻译；
- 右键菜单：显示/隐藏法语、字号增减、点击穿透、隐藏到托盘、退出；
- 托盘图标双击恢复窗口；
- 识别与翻译在 `QThread` 中运行，UI 不卡顿；完全复用阶段 1 的模块。

便携包加 `-WithGui` 构建后会多出 `run-gui.bat`。

---

## 13. 隐私

- 音频只在本机内存中处理，不落盘、不发送；
- 识别和翻译全部本地推理；
- 只有**模型下载**会访问 Hugging Face（lite 首次运行或 `--online`），可用 `HF_ENDPOINT` 指向镜像；
- 用户配置只包含设备名、窗口位置等，不包含任何识别内容；
- 日志只记录耗时/字符数，不记录识别或翻译正文。

详见 [docs/PRIVACY.md](docs/PRIVACY.md)。

## 14. 安全与合规

- 漏洞报告：请走 GitHub Security Advisory，**不要公开 Issue**，见 [SECURITY.md](SECURITY.md)；
- 第三方依赖与模型许可证：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)、[LICENSES/](LICENSES/)；
- 自查报告：[docs/COMPLIANCE_SECURITY_REPORT.md](docs/COMPLIANCE_SECURITY_REPORT.md)；
- 仓库检查脚本：`python scripts/check_repo.py`（密钥模式、超大文件、禁止目录）；
- CI：`.github/workflows/ci.yml`（测试 + 仓库检查）、`codeql.yml`（CodeQL）、Dependabot。

下载 Release 后建议校验 SHA-256：

```powershell
Get-FileHash .\FrenchTranslatorPortable-full.zip -Algorithm SHA256
```

## 15. License

[MIT](LICENSE)
