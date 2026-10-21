# Third-Party Notices / 第三方组件与模型声明

本文件列出 fr-translator 源码与便携包（Release zip）中包含的第三方组件、模型及其许可证。
项目本身以 [MIT License](LICENSE) 发布。

> 本文件不是法律意见。发布前建议由维护者再核对一次实际构建产物中的 `.dist-info` 与模型卡片。

## 1. 运行时与 Python

| 组件 | 版本 | 许可证 | 说明 |
| --- | --- | --- | --- |
| Python embeddable | 3.10.11 | PSF-2.0 | 便携包内的 Python 解释器，见 `LICENSES/Python-3.10.txt` |
| PyTorch (`torch`) | 2.4.1+cpu | BSD-3-Clause | 含第三方声明，见 `LICENSES/PyTorch.txt` |
| torchaudio | 2.4.1+cpu | BSD-2-Clause | 见 `LICENSES/BSD-2-Clause.txt` |

## 2. Python 依赖

以下版本与 `requirements*.txt` 锁定一致；便携包内还包含它们的传递依赖。

| 包 | 版本 | 许可证 |
| --- | --- | --- |
| certifi | 2026.10.19 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| cloudpickle | 3.1.2 | BSD-3-Clause |
| colorama | 0.4.6 | BSD-3-Clause |
| coloredlogs | 15.0.1 | MIT |
| ctranslate2 | 4.4.0 | MIT |
| faster-whisper | 1.0.3 | MIT |
| filelock | 4.1.0 | MIT |
| flatbuffers | 25.12.19 | Apache-2.0 |
| fsspec | 2026.9.0 | BSD-3-Clause |
| huggingface-hub | 0.25.1 | Apache-2.0 |
| humanfriendly | 10.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| Jinja2 | 3.1.6 | BSD-3-Clause |
| joblib | 1.6.0 | BSD-3-Clause |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| mpmath | 1.3.0 | BSD-3-Clause |
| networkx | 3.4.2 | BSD-3-Clause |
| numpy | 1.26.4 | BSD-3-Clause |
| onnxruntime | 1.19.2 | MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pip（仅构建/便携包内） | 24.2 | MIT |
| protobuf | 4.25.5 | BSD-3-Clause |
| pycparser | 3.11 | BSD-3-Clause |
| pyreadline3 | 3.5.6 | BSD-3-Clause |
| PyYAML | 6.0.2 | MIT |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python |
| requests | 2.34.2 | Apache-2.0 |
| sacremoses | 0.1.1 | MIT |
| safetensors | 0.4.5 | Apache-2.0 |
| sentencepiece | 0.2.0 | Apache-2.0 |
| setuptools | 75.8.0 | MIT |
| silero-vad | 5.1.2 | MIT |
| SoundCard | 0.4.3 | BSD-3-Clause |
| sympy | 1.14.0 | BSD-3-Clause |
| tokenizers | 0.19.1 | Apache-2.0 |
| tqdm | 4.70.1 | MPL-2.0 AND MIT（本项目按 MIT 使用） |
| transformers | 4.44.2 | Apache-2.0 |
| typing_extensions | 4.16.0 | PSF-2.0 |
| urllib3 | 2.8.0 | MIT |

## 3. 模型

模型权重不在 git 仓库中（`models/` 被忽略），但 **full / asr 便携包**会包含它们：

| 模型 | 来源 | 许可证 | 用途 |
| --- | --- | --- | --- |
| faster-whisper small | [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small) | MIT | 法语语音识别 |
| OPUS-MT fr-en | [Helsinki-NLP/opus-mt-fr-en](https://huggingface.co/Helsinki-NLP/opus-mt-fr-en) | Apache-2.0 | 两段式翻译第一段 |
| OPUS-MT en-zh | [Helsinki-NLP/opus-mt-en-zh](https://huggingface.co/Helsinki-NLP/opus-mt-en-zh) | Apache-2.0 | 两段式翻译第二段 |
| Silero VAD ONNX | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) | MIT | 语音活动检测/切句 |

说明：

- `Systran/faster-whisper-small` 是 OpenAI Whisper（MIT）的 CTranslate2 转换版本；
- Helsinki-NLP OPUS-MT 模型卡片标注为 Apache-2.0，使用时保留出处与许可证声明；
- Silero VAD 的 Python 包与 ONNX 模型均为 MIT；
- 模型仅从上述固定仓库下载，程序不会加载用户路径下的任意 `.bin` 模型。

## 4. 许可证文本

- [MIT License](LICENSE)（本项目）
- [Apache-2.0](LICENSES/Apache-2.0.txt)
- [BSD-3-Clause](LICENSES/BSD-3-Clause.txt)
- [BSD-2-Clause](LICENSES/BSD-2-Clause.txt)
- [MPL-2.0](LICENSES/MPL-2.0.txt)
- [PSF-2.0 / Python](LICENSES/Python-3.10.txt)
- [PyTorch 及其第三方声明](LICENSES/PyTorch.txt)

其余组件的完整许可证文本随对应 wheel 一起位于 `python/Lib/site-packages/*.dist-info/`。
如果你重新分发便携包，请保留该目录下的 LICENSE/COPYING 文件与本声明。

### PyAV / FFmpeg（不随发布包分发）

构建时为了满足 `faster-whisper` 的导入会安装 `av`（PyAV）。PyPI 的 Windows wheel
自带 FFmpeg 动态库，其中可能包含 GPL 的 x264/x265 等组件。**构建脚本会在打包前删除
`av`、`av.libs` 与对应 dist-info**；程序只处理 numpy 音频，不调用 `decode_audio`，
并用占位模块满足导入。因此发布 zip 不包含 PyAV/FFmpeg 二进制。

源码开发者执行 `pip install -r requirements.txt` 时仍会安装 `av`；它只在本机开发环境，
不会进入 Release 包。如果你要自行分发开发环境，请单独评估 FFmpeg 的许可证。

## 5. MPL-2.0 说明

`certifi` 与 `tqdm` 含 MPL-2.0 代码。MPL-2.0 是文件级弱 copyleft：本项目未修改这些文件，
仅以未修改形式随依赖分发。对应源码可从 PyPI 获取：

- https://pypi.org/project/certifi/
- https://pypi.org/project/tqdm/

完整 MPL-2.0 文本见 `LICENSES/MPL-2.0.txt`。
