"""把音频捕获、VAD 切句、语音识别、翻译串成一条流水线。"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import numpy as np

from .asr import WhisperASR
from .audio_capture import AudioSource
from .config import AppConfig, AppPaths
from .errors import AsrError, TranslationError
from .translate import MarianTranslator
from .vad import SileroSegmenter, load_silero_model

logger = logging.getLogger("fr_translator.pipeline")


class EventSink(Protocol):
    """控制台 / GUI 都实现这三个方法。"""

    def status(self, message: str, level: str = "info") -> None: ...

    def caption(self, french: str, chinese: str) -> None: ...

    def translation_failed(self, french: str, reason: str = "") -> None: ...


@dataclass(frozen=True)
class Caption:
    french: str
    chinese: str
    timestamp: datetime


class TranslationEngine:
    def __init__(
        self,
        config: AppConfig,
        paths: AppPaths,
        source: AudioSource,
        sink: EventSink,
        *,
        offline: bool,
        edition: str,
    ) -> None:
        self.config = config
        self.paths = paths
        self.source = source
        self.sink = sink
        self.offline = offline
        self.edition = edition

        self._asr = WhisperASR(config.asr, paths, offline=offline, edition=edition)
        self._translator = MarianTranslator(
            config.translate, paths, offline=offline, edition=edition
        )
        self._segmenter: SileroSegmenter | None = None

    # -- 初始化 ------------------------------------------------------------
    def load(self) -> None:
        """加载全部模型；失败时抛出面向用户的中文异常。"""

        if self._segmenter is not None:
            return

        self.sink.status("正在加载 silero-vad 切句模型 ...")
        vad_model = load_silero_model(self.paths)
        self._segmenter = SileroSegmenter(
            vad_model,
            self.config.vad,
            samplerate=self.config.audio.samplerate,
        )

        self.sink.status(
            f"正在加载语音识别模型（faster-whisper {self.config.asr.model}，"
            f"CPU {self.config.asr.compute_type}）..."
        )
        self._asr.load()

        self.sink.status(f"正在加载翻译模型（{self.config.translate.model}）...")
        self._translator.load()
        self.sink.status(f"翻译模型已就绪：{self._translator.describe()}")

        self.sink.status("模型加载完成，开始监听系统音频。")

    # -- 主循环 ------------------------------------------------------------
    def run(self, stop_event: threading.Event | None = None) -> None:
        self.load()
        assert self._segmenter is not None

        with self.source as source:
            self.sink.status(
                f"正在监听{source.describe()}。请播放法语音频，按 Ctrl+C 停止。"
            )
            for block in source.frames(stop_event):
                for utterance in self._segmenter.push(block):
                    self._handle_utterance(utterance)
                if stop_event is not None and stop_event.is_set():
                    break

            # 用户停止时，把最后一句也处理掉
            leftover = self._segmenter.flush()
            if leftover is not None:
                self._handle_utterance(leftover)

        self.sink.status("已停止监听。")

    # -- 单句处理 ----------------------------------------------------------
    def _handle_utterance(self, audio: np.ndarray) -> None:
        started = time.perf_counter()
        try:
            french = self._asr.transcribe(audio)
        except AsrError as exc:
            logger.warning("识别失败：%s", exc)
            self.sink.status(str(exc), "warning")
            return
        except Exception as exc:  # 防御性兜底：单句失败不拖垮整个程序
            logger.exception("语音识别出现未预期错误")
            self.sink.status(f"语音识别出现异常，已跳过本句：{exc}", "warning")
            return

        if not french:
            logger.debug("识别结果为空，跳过本句")
            return

        try:
            chinese = self._translator.translate(french)
        except TranslationError as exc:
            logger.warning("翻译失败：%s", exc)
            self.sink.translation_failed(french, str(exc))
            return
        except Exception as exc:
            logger.exception("翻译出现未预期错误")
            self.sink.translation_failed(french, f"翻译出现异常：{exc}")
            return

        if not chinese:
            logger.warning("翻译结果为空：%s", french)
            self.sink.translation_failed(french, "翻译结果为空")
            return

        elapsed = time.perf_counter() - started
        logger.info("耗时 %.2fs | %s", elapsed, french)
        self.sink.caption(french, chinese)
