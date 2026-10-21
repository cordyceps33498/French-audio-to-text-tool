# Security Policy / 安全政策

## 支持范围

| 版本 | 支持状态 |
| --- | --- |
| 0.1.x | 安全修复 |

旧版便携包请优先升级到最新 Release。Release 页面会同时提供 `lite`、`full`、`asr` 三个 zip。

## 报告安全问题

**请不要在公开 Issue 中提交安全漏洞细节。**

请使用 GitHub 仓库的 **Security → Report a vulnerability**（Private Security Advisory）提交，
并尽量包含：

- 受影响版本/包（lite / full / asr）与 Windows 版本；
- 复现步骤、影响（例如本地提权、任意文件读取、模型加载 RCE）；
- 相关日志或 PoC（请先移除个人信息与音频内容）。

维护者会在 7 天内确认收到，并在修复发布后致谢（如果你愿意）。

## 安全模型与边界

fr-translator 是一个**本地桌面工具**，设计上：

- 只捕获 Windows 系统播放音频（loopback），不访问麦克风；
- 音频只在内存中处理，不落盘、不上传；
- 只有**模型下载**会访问网络（Hugging Face / `HF_ENDPOINT` 镜像）；已禁用 HF 遥测；
- 识别文本只打印到控制台；日志只记录耗时和字符数，不记录识别/翻译正文；
- 配置文件使用 `yaml.safe_load` 解析；不执行配置中的代码；
- 不修改系统音频设置，不自动切换默认输出设备。

## 已知风险与建议

1. **模型供应链**：程序只从固定仓库加载 faster-whisper / OPUS-MT / Silero VAD 模型。
   如果你使用自定义 `config.yaml` 指向其它模型，请自行确认来源可信。
   不要加载来源不明的 `.bin` / `.safetensors` / `.onnx` 文件。
2. **依赖版本**：为保证兼容性，`requirements*.txt` 锁定了 Python 3.10 时代的版本
   （Python 3.10.11、torch 2.4.1 等）。这些版本可能缺少后续安全补丁；发布前建议用
   `-PythonVersion` 指向较新的 3.10.x，并通过 Dependabot/CodeQL 关注依赖公告。
3. **torch 反序列化**：翻译模型的两段式模型中 `opus-mt-en-zh` 只提供 `pytorch_model.bin`，
   加载权重属于反序列化信任边界。只从本项目固定的 HF 仓库下载，不要替换成未知来源文件。
4. **便携包完整性**：从非官方渠道获得的 zip 可能被篡改。请从 GitHub Release 下载，并用
   发布页提供的 SHA-256 校验；放入 U 盘前也建议扫描。
5. **`--local` 模式**：会把便携包复制到 `%TEMP%\fr-translator-portable`。在多用户机器上，
   请确认该临时目录权限，退出后可手动删除。
6. **日志**：正常日志不含识别正文；但控制台输出、用户自行重定向的输出文件可能包含音频内容，
   分享日志前请先脱敏。

## 安全开发生命周期

- GitHub Actions 运行单元测试与 CodeQL（`.github/workflows/ci.yml`、`codeql.yml`）；
- Dependabot 每周检查 pip 与 GitHub Actions 依赖；
- 发布构建使用 `scripts/build_portable.ps1`，构建产物带 SHA-256；
- 合并 PR 前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
