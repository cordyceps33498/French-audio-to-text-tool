# 合规性与安全检查报告

- 项目：fr-translator
- 检查日期：2026-10-22
- 检查范围：git 仓库（不含 `dist/`、`models/`、`.cache/`）、源码、构建脚本、CI 配置、便携包依赖与模型
- 结论：**未发现阻塞开源的合规问题或硬编码密钥**；已补齐许可证声明、安全政策、CI/CodeQL/Dependabot 与隐私修复。

> 本报告是工程自查，不构成法律意见或渗透测试报告。

## 1. 仓库与供应链

| 检查项 | 结果 |
| --- | --- |
| git 工作区 | 干净，提交历史 8 个提交 |
| 仓库体积 | `.git` 约 732 KB，无大文件历史 |
| 提交的大文件（>200KB） | 无 |
| 模型/构建产物 | `models/`、`dist/`、`portable/`、`.cache/` 已在 `.gitignore` |
| 敏感信息 | 未发现 token、私钥、密码、`ghp_`/`sk-`/`AKIA` 等模式 |
| 历史提交敏感信息 | `git log -p` 扫描无命中 |
| 硬编码绝对路径 | 源码/脚本中无；文档示例中的 `D:\...` 仅为演示 |

## 2. 依赖许可证

全部直接依赖与传递依赖的许可证均为宽松/弱 copyleft，可与 MIT 项目一起分发：

- MIT / MIT-0 / BSD-2 / BSD-3 / Apache-2.0 / PSF-2.0 / CNRI-Python：允许商业/开源分发；
- **MPL-2.0**：`certifi`、`tqdm`（文件级弱 copyleft，未修改，源码可从 PyPI 获取）；
- 未发现 GPL/AGPL/LGPL 依赖。

> **PyAV/FFmpeg**：`av` wheel 自带 FFmpeg（含 GPL 的 x264/x265），仅在构建/开发环境安装。
> 构建脚本 `Step-Trim` 会在打包前删除 `av`、`av.libs` 与 dist-info；发布 zip 不包含
> PyAV/FFmpeg，程序用标准库 `wave` 解码调试 WAV，并用占位模块满足 faster-whisper 导入。
> 详见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

详见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) 与 [LICENSES/](../LICENSES/)。

已处理：

- 增加第三方组件/模型清单与完整许可证文本；
- `full`/`asr` 包包含模型 weights，已在清单中标注模型来源与许可证；
- 构建脚本会把 `THIRD_PARTY_NOTICES.md` 与 `LICENSES/` 复制进便携包（已实现）。

## 3. 模型许可证

| 模型 | 许可证 | 结论 |
| --- | --- | --- |
| Systran/faster-whisper-small | MIT | 可分发，保留声明 |
| Helsinki-NLP/opus-mt-fr-en | Apache-2.0 | 可分发，保留声明与出处 |
| Helsinki-NLP/opus-mt-en-zh | Apache-2.0 | 可分发，保留声明与出处 |
| Silero VAD | MIT | 可分发 |

模型权重不进入 git 仓库；发布 zip 才包含。

## 4. 安全代码审查

| 风险点 | 结果/处理 |
| --- | --- |
| 命令注入 | 无 `os.system`/`shell=True`；`run.bat` 只把参数传给 Python |
| 任意代码执行 | 无 `eval`/`exec`；`yaml.safe_load` |
| 不安全反序列化 | 只加载固定 HF 仓库的模型；不加载用户指定 `.bin`；`.bin` 属信任边界，已在 SECURITY 说明 |
| 网络 | 仅模型下载；HTTPS；无遥测；无音频上传 |
| 日志隐私 | **已修复**：不再记录识别/翻译正文，只记录耗时/字符数 |
| PyAV/FFmpeg | **已移除**：构建打包前删除 `av`/`av.libs`；调试 WAV 改用标准库 `wave`；发布包不含 GPL FFmpeg |
| 文件路径 | 不硬编码绝对路径；模型/日志从根目录解析 |
| 设备 | 不自动切换默认输出设备，不修改系统音频设置 |
| 依赖漏洞 | 版本较旧（torch 2.4.1、transformers 4.44.2）；已加 Dependabot + CodeQL，建议发布前评估升级 |
| Python 运行时 | embeddable 3.10.11 偏旧；建议用 `-PythonVersion 3.10.14+` 构建并回归测试 |
| CI 权限 | workflow 仅 `contents: write`（Release 需要）；构建/测试不接触 secrets |
| 第三方 Action | 使用官方/主流 Action；Dependabot 跟踪；建议长期固定到 commit SHA |

## 5. 本次修复/新增

1. 新增 `SECURITY.md`、`CONTRIBUTING.md`、`CODE_OF_CONDUCT.md`、`CHANGELOG.md`；
2. 新增 `THIRD_PARTY_NOTICES.md`、`LICENSES/`（Apache-2.0、BSD-2/3、MPL-2.0、PSF、PyTorch）；
3. 新增 Issue/PR 模板、Dependabot、CI、CodeQL workflow；
4. `.gitignore` 增加 `.env`、证书/私钥、`*.zip` 等规则；
5. 日志去除识别/翻译正文；
6. 便携包构建增加合规文件复制，并在 `full`/`asr` 包中保留模型许可证声明。

## 6. 尚未完成/建议

- CodeQL 与 CI 工作流已写入仓库，但本机没有 GitHub remote，未实际触发；
- 未做第三方渗透测试；
- 未建立模型 SHA-256 清单；如担心模型供应链，可在 `prepare_models.py` 中固定 commit + 校验；
- Python 3.10 已进入生命周期后段，长期建议评估迁移到 3.11/3.12 并重新验证依赖；
- `dist/` 下的历史 zip 是本地构建产物，正式发布请以 CI 重新构建的 Release 为准。

## 7. 复现检查的命令

```bash
git status --short
git ls-files
git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)'
git log -p --all | grep -EI "(ghp_|github_pat_|sk-|AKIA|PRIVATE KEY)"

grep -RInE "(eval|exec|pickle\.loads|yaml\.load|subprocess\.|os\.system|shell=True|verify=False)" src scripts tests
grep -RInE "(ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|PRIVATE KEY)" .

python -m unittest discover -s tests -v
```
