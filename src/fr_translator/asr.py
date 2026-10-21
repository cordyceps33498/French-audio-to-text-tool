"""faster-whisper 语音识别封装（CPU / int8）。"""

from __future__ import annotations

import logging
import re

import numpy as np

from ._compat import ensure_av_stub
from .config import AsrConfig, AppPaths, model_missing_message
from .errors import AsrError, DependencyError, ModelLoadError, ModelMissingError

logger = logging.getLogger("fr_translator.asr")

# 静音/音乐时 whisper 偶尔会“幻听”出这些字幕组署名，直接丢弃。
_HALLUCINATION_HINTS = (
    "sous-titres réalisés par",
    "sous-titres realises par",
    "amara.org",
    "sous-titrage société radio-canada",
    "sous-titrage societe radio-canada",
    "merci d'avoir regardé",
    "merci d'avoir regarde",
)

_WHITESPACE = re.compile(r"\s+")


class WhisperASR:
    def __init__(
        self,
        config: AsrConfig,
        paths: AppPaths,
        *,
        offline: bool,
        edition: str,
    ) -> None:
        self.config = config
        self.paths = paths
        self.offline = offline
        self.edition = edition
        self._model = None
        # 上一句识别文本，作为下一句的 initial_prompt；只保留末尾一小段
        self._context = ""

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        ensure_av_stub()
        try:
            from faster_whisper import WhisperModel  # type: ignore import-not-found
        except ImportError as exc:  # pragma: no cover
            raise DependencyError(
                "缺少依赖 faster-whisper，无法进行语音识别。\n"
                "请执行 pip install -r requirements.txt 后重试。"
            ) from exc

        local_dir = self.paths.whisper_dir(self.config.model)
        if (local_dir / "model.bin").is_file():
            model_arg = str(local_dir)
            download_root = None
            local_only = True
            source = f"本地模型 {local_dir}"
        else:
            if self.offline:
                raise ModelMissingError(
                    model_missing_message(self.edition, f"语音识别模型（{self.config.model}）")
                )
            model_arg = self.config.model
            download_root = str(self.paths.whisper)
            local_only = False
            source = f"在线下载 {self.config.model} -> {download_root}"

        logger.info("加载 faster-whisper：%s", source)
        try:
            self._model = WhisperModel(
                model_arg,
                device="cpu",
                compute_type=self.config.compute_type,
                cpu_threads=max(0, self.config.cpu_threads),
                num_workers=1,
                download_root=download_root,
                local_files_only=local_only,
            )
        except Exception as exc:
            if self.offline:
                raise ModelMissingError(
                    model_missing_message(self.edition, f"语音识别模型（{self.config.model}）")
                    + f"\n详细信息：{exc}"
                ) from exc
            raise ModelLoadError(
                "语音识别模型加载失败。\n"
                "请检查网络是否可用，或删除 models/faster-whisper 后重新下载。\n"
                f"详细信息：{exc}"
            ) from exc

    def transcribe(self, audio: np.ndarray) -> str:
        """识别一段 16kHz 单声道 float32 音频，返回法语文本（可能为空）。"""

        if self._model is None:
            raise AsrError("语音识别模型尚未加载。")
        if audio.size < 1600:  # 少于 0.1 秒，whisper 处理没有意义
            return ""

        try:
            segments, _info = self._model.transcribe(
                np.ascontiguousarray(audio, dtype=np.float32),
                language=self.config.language,
                task="transcribe",
                beam_size=self.config.beam_size,
                vad_filter=self.config.vad_filter,
                # 我们的 silero-vad 已经切好句子，这里只做一次温和的二次过滤，
                # 不要把句尾轻声再削掉。
                vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 300},
                condition_on_previous_text=False,
                initial_prompt=self._context or None,
                without_timestamps=True,
                no_speech_threshold=0.8,
                log_prob_threshold=-2.0,
            )
            parts: list[str] = []
            for segment in segments:
                text = (segment.text or "").strip()
                if not text:
                    continue
                if _is_hallucination(text):
                    logger.debug("丢弃疑似幻听片段（%d 字符）", len(text))
                    continue
                # 高 no_speech 概率 + 低置信度的片段通常是音乐/噪声
                if getattr(segment, "no_speech_prob", 0.0) > 0.9 and getattr(segment, "avg_logprob", 0.0) < -1.5:
                    logger.debug("丢弃低置信度片段（%d 字符）", len(text))
                    continue
                parts.append(text)
        except Exception as exc:
            raise AsrError(f"语音识别失败：{exc}") from exc

        result = _WHITESPACE.sub(" ", " ".join(parts)).strip()
        if result and self.config.use_context:
            # 只保留最后 ~160 个字符，避免 prompt 无限增长
            self._context = (self._context + " " + result).strip()[-160:]
        return result


def _is_hallucination(text: str) -> bool:
    lowered = text.casefold()
    return any(hint in lowered for hint in _HALLUCINATION_HINTS)
