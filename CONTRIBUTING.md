# Contributing / 贡献指南

感谢你愿意改进 fr-translator。提交前请确认：

## 开发环境

- Windows 10/11 x64；
- Python **3.10**（64 位）；
- 不建议在仓库里提交 `models/`、`dist/`、`portable/`、`.venv/`，它们已在 `.gitignore` 中。

```bat
py -3.10 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
run-dev.bat
```

跑测试：

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 代码约定

- 面向用户的提示一律中文；异常必须是 `fr_translator.errors.FrTranslatorError` 的子类，
  不要直接把堆栈甩给用户；
- 不要硬编码绝对路径，模型/日志路径从 `AppPaths` 解析；
- 音频只在内存处理，不新增上传、埋点或音频落盘；
- 日志不要记录识别/翻译正文（只记录耗时、字符数、模型路径）；
- 新增配置项要同步 `config.example.yaml` 和 README，并加校验；
- 修改 VAD/ASR/队列行为时补单元测试；
- 不引入 PyInstaller/Nuitka、不要把 venv 打进便携包。

## 提交 PR

1. Fork 并创建分支；
2. 保持改动聚焦，说明“为什么”而不只是“改了什么”；
3. 确认 `python -m unittest discover -s tests` 通过；
4. 涉及便携包/构建脚本时，运行一次：

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 -Mode lite
   ```

5. 在 PR 描述中勾选安全检查（见 PR 模板）。

## 许可证

提交代码即表示你同意以本项目的 MIT License 发布，并确认你有权提交相关内容。
新增第三方依赖/模型时，请同时更新 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，
并确认许可证与 MIT 项目兼容（Apache-2.0、BSD、MIT、MPL-2.0 等允许；GPL/AGPL 需先讨论）。

## 安全问题

不要公开提交安全漏洞，请按 [SECURITY.md](SECURITY.md) 私密报告。
