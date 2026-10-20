"""silero-vad 流式切句。

为什么不用固定时长切句：固定窗口会把一个句子从中间截断，翻译质量会明显下降。
这里的做法是：

1. 每 512 个采样点（16kHz 下 32ms）计算一次语音概率；
2. 连续语音超过阈值 -> 开始累积，并带上 200ms 预录，避免句首被切；
3. 连续静音达到 ``min_silence_ms`` -> 结束一句；
4. 单句超过 ``max_utterance_s`` -> 强制切分，避免长句无限累积；
5. 有效语音短于 ``min_speech_ms`` -> 丢弃，过滤咳嗽、敲击声。

模型优先从 ``models/silero-vad/silero_vad.onnx`` 本地加载；找不到时使用
``silero-vad`` 包内置的 ONNX 模型（同样不联网）。jit 模型仅作为兜底，
由 ``scripts/prepare_models.py`` 预置到同一目录后可离线使用。
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

import numpy as np

from .config import AppPaths, VadConfig
from .errors import DependencyError, VadError

logger = logging.getLogger("fr_translator.vad")

# silero-vad v5 要求固定窗口：8k -> 256，16k -> 512
WINDOW_SAMPLES = {8000: 256, 16000: 512}


def _import_torch():
    try:
        import torch  # type: ignore import-not-found
    except ImportError as exc:  # pragma: no cover
        raise DependencyError(
            "缺少依赖 torch，无法运行语音活动检测。\n"
            "请执行 pip install -r requirements.txt 后重试。"
        ) from exc
    return torch


def _smoke_test(model, torch) -> None:
    window = WINDOW_SAMPLES[16000]
    with torch.no_grad():
        result = model(torch.zeros(window, dtype=torch.float32), 16000)
    float(result.reshape(-1)[0].item())


def load_silero_model(paths: AppPaths):
    """加载 silero-vad 模型，返回可调用的模型对象。"""

    torch = _import_torch()
    local_onnx = paths.silero_onnx()

    if local_onnx.is_file():
        try:
            from silero_vad.utils_vad import OnnxWrapper  # type: ignore import-not-found

            model = OnnxWrapper(str(local_onnx), force_onnx_cpu=True)
            _smoke_test(model, torch)
            logger.info("已加载本地 silero-vad 模型：%s", local_onnx)
            return model
        except Exception as exc:
            raise VadError(
                f"无法加载本地 silero-vad 模型：{local_onnx}\n"
                "文件可能损坏，请重新运行 scripts/prepare_models.py，或删除该文件让程序使用内置模型。\n"
                f"详细信息：{exc}"
            ) from exc

    try:
        from silero_vad import load_silero_vad  # type: ignore import-not-found

        model = load_silero_vad(onnx=True)
        _smoke_test(model, torch)
        logger.info("已加载 silero-vad 包内置 ONNX 模型")
        return model
    except Exception as exc:
        raise VadError(
            "silero-vad 模型加载失败。\n"
            "如果使用便携包，请确认 models/silero-vad/silero_vad.onnx 存在；"
            "缺失时重新运行 scripts/prepare_models.py 或重新下载 full 包。\n"
            "源码运行时可以执行：python scripts/prepare_models.py --root .\n"
            f"详细信息：{exc}"
        ) from exc


class SileroSegmenter:
    """把连续音频块切成一句话一句话的音频。"""

    def __init__(
        self,
        model,
        config: VadConfig,
        *,
        samplerate: int = 16000,
    ) -> None:
        if samplerate not in WINDOW_SAMPLES:
            raise VadError(
                f"silero-vad 目前只支持 8000Hz 或 16000Hz，当前为 {samplerate}Hz。"
            )
        self._model = model
        self._config = config
        self._samplerate = samplerate
        self._window = WINDOW_SAMPLES[samplerate]
        self._min_speech = _ms_to_samples(config.min_speech_ms, samplerate)
        self._min_silence = _ms_to_samples(config.min_silence_ms, samplerate)
        self._pad = _ms_to_samples(config.speech_pad_ms, samplerate)
        self._pre_max = _ms_to_samples(config.pre_roll_ms, samplerate)
        self._max_samples = max(1, int(config.max_utterance_s * samplerate))

        self._pending = np.zeros(0, dtype=np.float32)
        self._pre: deque[np.ndarray] = deque()
        self._pre_len = 0
        self._seg: list[np.ndarray] = []
        self._seg_len = 0
        self._active = False
        self._silence = 0
        self._speech = 0
        self._last_prob = 0.0

    # -- 只读状态 ----------------------------------------------------------
    @property
    def last_probability(self) -> float:
        return self._last_prob

    @property
    def is_speaking(self) -> bool:
        return self._active

    # -- 主接口 ------------------------------------------------------------
    def push(self, block: np.ndarray) -> list[np.ndarray]:
        """送入一个音频块，返回本次切出的完整句子（可能 0 个或多个）。"""

        block = np.asarray(block, dtype=np.float32).reshape(-1)
        if block.size == 0:
            return []

        self._append_pre_roll(block)
        if self._active:
            self._seg.append(block)
            self._seg_len += block.size

        if self._pending.size:
            self._pending = np.concatenate((self._pending, block))
        else:
            self._pending = block.copy()

        results: list[np.ndarray] = []
        while self._pending.size >= self._window:
            window = self._pending[: self._window]
            self._pending = self._pending[self._window :]
            probability = self._infer(window)
            closed = self._step(probability)
            if closed is not None:
                results.append(closed)
            if self._active and self._seg_len >= self._max_samples:
                forced = self._close(force=True)
                if forced is not None:
                    results.append(forced)
        return results

    def flush(self) -> np.ndarray | None:
        """停止捕获时把最后一句吐出来。"""

        if not self._active or self._seg_len == 0:
            self._reset_utterance()
            return None
        if self._speech < self._min_speech:
            self._reset_utterance()
            return None
        segment = np.concatenate(self._seg)
        self._reset_utterance()
        return segment

    def reset(self) -> None:
        self._pending = np.zeros(0, dtype=np.float32)
        self._reset_utterance()

    # -- 内部 --------------------------------------------------------------
    def _append_pre_roll(self, block: np.ndarray) -> None:
        if self._pre_max <= 0:
            return
        self._pre.append(block)
        self._pre_len += block.size
        while self._pre and self._pre_len - self._pre[0].size >= self._pre_max:
            self._pre_len -= self._pre.popleft().size

    def _step(self, probability: float) -> np.ndarray | None:
        self._last_prob = probability
        threshold = self._config.threshold

        if not self._active:
            if probability < threshold:
                return None
            # 触发：把预录音频放进句子开头
            self._active = True
            self._seg = list(self._pre)
            self._seg_len = self._pre_len
            self._speech = self._window
            self._silence = 0
            self._pre.clear()
            self._pre_len = 0
            logger.debug("检测到语音开始（p=%.2f）", probability)
            return None

        if probability >= threshold:
            self._speech += self._window
            self._silence = 0
            return None

        self._silence += self._window
        if self._silence >= self._min_silence:
            return self._close(force=False)
        return None

    def _close(self, *, force: bool) -> np.ndarray | None:
        if not self._active:
            return None

        segment = np.concatenate(self._seg) if self._seg else np.zeros(0, dtype=np.float32)
        speech = self._speech

        if speech < self._min_speech or segment.size < self._min_speech:
            logger.debug("丢弃过短的语音片段（%.0fms）", speech * 1000 / self._samplerate)
            self._reset_utterance()
            return None

        if not force:
            # 去掉句尾多余的静音，只保留 speech_pad_ms
            trim = max(0, self._silence - self._pad)
            if trim > 0 and trim < segment.size:
                segment = segment[: segment.size - trim]

        # 强制切分时保留一小段尾巴，保证连续说话时下一句的句首不被丢掉
        tail = segment[-self._pre_max :].copy() if force and self._pre_max > 0 else None
        keep_going = force and self._last_prob >= self._config.threshold and tail is not None

        self._reset_utterance()
        if keep_going and tail is not None and tail.size > 0:
            self._active = True
            self._seg = [tail]
            self._seg_len = tail.size
            self._speech = 0
            self._silence = 0

        if segment.size == 0:
            return None
        return segment

    def _reset_utterance(self) -> None:
        self._seg = []
        self._seg_len = 0
        self._active = False
        self._silence = 0
        self._speech = 0
        self._pre.clear()
        self._pre_len = 0
        try:
            self._model.reset_states()
        except Exception:  # pragma: no cover - 某些模型没有该方法
            pass

    def _infer(self, window: np.ndarray) -> float:
        torch = _import_torch()
        try:
            with torch.no_grad():
                tensor = torch.from_numpy(np.array(window, dtype=np.float32, copy=True))
                result = self._model(tensor, self._samplerate)
            value = result.reshape(-1)[0].item()
            return float(value)
        except Exception as exc:
            raise VadError(
                "语音活动检测推理失败。\n"
                "请确认 models/silero-vad/silero_vad.onnx 与当前 Python 环境匹配，"
                "或删除该文件后重试（程序会使用包内置模型）。\n"
                f"详细信息：{exc}"
            ) from exc


def _ms_to_samples(milliseconds: float, samplerate: int) -> int:
    return max(0, int(round(milliseconds * samplerate / 1000.0)))
