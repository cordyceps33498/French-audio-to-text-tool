"""控制台输出。

所有面向用户的文字都是中文；法语原文照原样输出（保留重音符号）。
输出格式固定为两行：

    [HH:MM:SS] 法语：<识别文本>
    [HH:MM:SS] 中文：<翻译文本>

时间戳写在行首，既方便调试，也不会破坏“法语/中文”两行的结构。
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import TextIO


class ConsoleUI:
    """控制台事件接收器，同时提供少量交互方法。"""

    def __init__(self, stream: TextIO | None = None, error_stream: TextIO | None = None) -> None:
        self._out = stream if stream is not None else sys.stdout
        self._err = error_stream if error_stream is not None else sys.stderr

    # -- 基础输出 ----------------------------------------------------------
    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _print(self, text: str, *, error: bool = False) -> None:
        stream = self._err if error else self._out
        if stream is None:  # pythonw / GUI 场景没有控制台
            return
        try:
            print(text, file=stream, flush=True)
        except (OSError, ValueError, UnicodeEncodeError):
            # 控制台编码异常不应该让主流程崩溃
            pass

    # -- 事件接口（pipeline 调用） -----------------------------------------
    def status(self, message: str, level: str = "info") -> None:
        prefix = {
            "info": "",
            "warning": "警告：",
            "error": "错误：",
        }.get(level, "")
        self._print(f"[{self._now()}] {prefix}{message}", error=(level == "error"))

    def error(self, message: str) -> None:
        self.status(message, "error")

    def caption(self, french: str, chinese: str) -> None:
        stamp = self._now()
        self._print(f"[{stamp}] 法语：{french}")
        self._print(f"[{stamp}] 中文：{chinese}")

    def french_only(self, french: str) -> None:
        """asr 轻量模式：只输出法语原文。"""

        self._print(f"[{self._now()}] 法语：{french}")

    def translation_failed(self, french: str, reason: str = "") -> None:
        stamp = self._now()
        self._print(f"[{stamp}] 法语：{french}")
        detail = f"（{reason}）" if reason else ""
        self._print(f"[{stamp}] 中文：（翻译失败，已跳过本句）{detail}")

    # -- 交互 --------------------------------------------------------------
    def ask(self, prompt: str) -> str:
        """读取一行输入；stdin 不可用时返回空串（调用方使用默认值）。"""

        if self._out is not None:
            try:
                print(f"[{self._now()}] {prompt}", end="", flush=True)
            except (OSError, ValueError, UnicodeEncodeError):
                pass
        try:
            return input()
        except (EOFError, OSError):
            if self._out is not None:
                self._print("")
            return ""

    def confirm(self, prompt: str) -> bool:
        """回车 / y 表示确认，其它字符表示取消。"""

        answer = self.ask(prompt).strip().lower()
        return answer in {"", "y", "yes", "是"}

    def blank_line(self) -> None:
        self._print("")
