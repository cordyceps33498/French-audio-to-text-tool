"""可选依赖兼容层。

便携包为了避开 PyAV/FFmpeg 的许可证与体积，不包含 ``av``。
但 faster-whisper 在包导入时会 ``import av``（即使我们只传 numpy 音频、
从不调用 decode_audio）。这里用一个带合法 ``__spec__`` 的占位模块满足导入，
避免 transformers 的 ``find_spec("av")`` 报错。
"""

from __future__ import annotations

import importlib.machinery
import sys
import types


def ensure_av_stub() -> None:
    """如果没有安装 PyAV，则注册一个最小占位模块。"""

    if "av" in sys.modules:
        return
    try:
        import av  # noqa: F401
    except ImportError:
        module = types.ModuleType("av")
        module.__spec__ = importlib.machinery.ModuleSpec("av", loader=None)
        module.__version__ = "0.0.0-stub"
        sys.modules["av"] = module
