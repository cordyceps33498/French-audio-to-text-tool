"""面向用户的异常类型。

约定：这些异常的 ``str()`` 必须是完整的中文人话提示，可以直接打印给用户看；
程序入口会捕获它们并退出，不输出 Python 堆栈（``--debug`` 时才输出）。
"""

from __future__ import annotations


class FrTranslatorError(Exception):
    """所有可预期错误的基类。"""


class ConfigError(FrTranslatorError):
    """配置文件缺失、格式错误或取值非法。"""


class DependencyError(FrTranslatorError):
    """Python 依赖缺失或版本不兼容。"""


class DeviceError(FrTranslatorError):
    """音频设备枚举 / 选择失败。"""


class AudioCaptureError(FrTranslatorError):
    """打开或读取系统音频（loopback）失败。"""


class ModelMissingError(FrTranslatorError):
    """模型文件不存在，且当前不允许联网下载。"""


class ModelLoadError(FrTranslatorError):
    """模型文件存在但加载失败。"""


class VadError(FrTranslatorError):
    """silero-vad 初始化或推理失败。"""


class AsrError(FrTranslatorError):
    """语音识别失败。"""


class TranslationError(FrTranslatorError):
    """翻译失败。"""
