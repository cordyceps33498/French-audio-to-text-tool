"""音频捕获：Windows loopback（系统播放声音）。

对上层暴露统一的“音频源”接口，便于以后扩展文件输入、麦克风输入等：

    with source:
        for block in source.frames(stop_event):
            ...  # float32, 单声道, 16kHz

soundcard 的 loopback 需要 Windows 10/11；请求 16kHz 单声道时由 Windows
音频引擎（AUTOCONVERTPCM）完成重采样。若设备不接受单声道，会退化为
双声道录制并在本地取平均。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Iterator, Protocol

import numpy as np

from .devices import CaptureDevice
from .errors import AudioCaptureError, DependencyError

logger = logging.getLogger("fr_translator.audio")


class AudioSource(Protocol):
    """音频源协议（供 pipeline 使用）。"""

    def __enter__(self) -> "AudioSource": ...

    def __exit__(self, exc_type, exc, tb) -> None: ...

    def frames(self, stop_event: threading.Event | None = None) -> Iterator[np.ndarray]: ...

    def describe(self) -> str: ...


def _as_mono_float32(data: object) -> np.ndarray:
    """把 soundcard 返回的任意形状数组规范成 [N] float32，并裁剪到 [-1, 1]。"""

    array = np.asarray(data, dtype=np.float32)
    if array.ndim == 2:
        if array.shape[1] == 1:
            array = array[:, 0]
        else:
            array = array.mean(axis=1)
    elif array.ndim > 2:
        array = array.reshape(-1)
    array = np.nan_to_num(array, nan=0.0, posinf=1.0, neginf=-1.0)
    np.clip(array, -1.0, 1.0, out=array)
    return np.ascontiguousarray(array, dtype=np.float32)


class LoopbackCapture:
    """录制某个播放设备的 loopback 音频。"""

    def __init__(
        self,
        device: CaptureDevice,
        *,
        samplerate: int = 16000,
        channels: int = 1,
        block_frames: int = 384,
    ) -> None:
        self.device = device
        self.samplerate = samplerate
        self.channels = channels
        self.block_frames = max(1, int(block_frames))
        # soundcard 内部按 blocksize 从设备取数据，取 1024 帧可减少系统调用；
        # record() 仍然按 block_frames 返回，不影响 VAD 的响应速度。
        self._blocksize = max(self.block_frames, 1024)
        self._recorder = None
        self._effective_channels = channels

    # -- 生命周期 ----------------------------------------------------------
    def __enter__(self) -> "LoopbackCapture":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        if self._recorder is not None:
            return
        try:
            import soundcard as sc  # type: ignore import-not-found

            # loopback 在设备切换/缓冲不足时会告警“data discontinuity”，
            # 不影响继续录制，屏蔽掉以免污染控制台输出。
            try:
                import warnings

                from soundcard import SoundcardRuntimeWarning  # type: ignore import-not-found

                warnings.filterwarnings("ignore", category=SoundcardRuntimeWarning)
            except Exception:
                pass
        except ImportError as exc:  # pragma: no cover
            raise DependencyError(
                "缺少依赖 soundcard，无法捕获系统音频。\n"
                "请执行 pip install -r requirements.txt 后重试。"
            ) from exc

        microphone = None
        try:
            microphone = sc.get_microphone(self.device.id, include_loopback=True)
        except Exception:
            # 某些系统上 loopback 设备只认名称
            try:
                microphone = sc.get_microphone(self.device.name, include_loopback=True)
            except Exception as exc:
                raise AudioCaptureError(
                    f"无法打开音频设备“{self.device.display_name}”。\n"
                    "设备可能已被拔出、被禁用，或正被独占程序占用。\n"
                    f"详细信息：{exc}"
                ) from exc

        last_error: Exception | None = None
        for channels in (self.channels, 2):
            recorder = None
            try:
                recorder = microphone.recorder(
                    samplerate=self.samplerate,
                    channels=channels,
                    blocksize=self._blocksize,
                )
                # soundcard 的 recorder 是上下文管理器：必须 __enter__ 后
                # 才会创建 capture client 并初始化 _pending_chunk。
                recorder.__enter__()
                self._recorder = recorder
                self._effective_channels = channels
                logger.info(
                    "已打开 loopback 设备：%s（%d Hz, %d ch）",
                    self.device.display_name,
                    self.samplerate,
                    channels,
                )
                return
            except Exception as exc:  # 设备忙 / 声道数不支持
                last_error = exc
                self._recorder = None
                if recorder is not None:
                    try:
                        recorder.__exit__(None, None, None)
                    except Exception:
                        pass
                if channels != self.channels:
                    break
                logger.debug("以 %d 声道打开失败，尝试双声道：%s", channels, exc)

        raise AudioCaptureError(
            f"无法开始录制“{self.device.display_name}”。\n"
            "常见原因：设备被其它程序独占、采样率不支持、或音频服务异常。\n"
            "请关闭占用麦克风/声卡的程序后重试（按回车重试，输入 n 退出）。\n"
            f"详细信息：{last_error}"
        ) from last_error

    def close(self) -> None:
        recorder = self._recorder
        self._recorder = None
        if recorder is None:
            return
        try:
            recorder.__exit__(None, None, None)
        except Exception as exc:  # pragma: no cover - 关闭失败不影响退出
            logger.debug("关闭录音流时出错（已忽略）：%s", exc)

    # -- 数据 --------------------------------------------------------------
    def frames(self, stop_event: threading.Event | None = None) -> Iterator[np.ndarray]:
        if self._recorder is None:
            self.open()
        assert self._recorder is not None
        while stop_event is None or not stop_event.is_set():
            try:
                data = self._recorder.record(self.block_frames)
            except Exception as exc:
                raise AudioCaptureError(
                    "读取系统音频失败，录音流可能已中断。\n"
                    "请检查输出设备是否仍然连接，然后重试。\n"
                    f"详细信息：{exc}"
                ) from exc
            block = _as_mono_float32(data)
            if block.size:
                yield block

    def describe(self) -> str:
        return f"系统音频：{self.device.display_name}"


class WavFileCapture:
    """调试用：把 PCM WAV 当作 16kHz 单声道音频源。

    仅用于开发验证（``--wav``），正式使用仍然是捕获系统音频。
    使用标准库 ``wave`` 解码，不依赖 PyAV/FFmpeg，因此发布包不会夹带
    GPL 的 FFmpeg 二进制。支持 8/16/24/32-bit PCM，多声道会下混为单声道，
    采样率不是 16kHz 时做线性重采样。
    """

    def __init__(self, path: str | Path, *, samplerate: int = 16000) -> None:
        self.path = Path(path)
        self.samplerate = samplerate
        self._audio: np.ndarray | None = None
        self._position = 0

    def __enter__(self) -> "WavFileCapture":
        if not self.path.is_file():
            raise AudioCaptureError(f"音频文件不存在：{self.path}")
        try:
            import wave

            with wave.open(str(self.path), "rb") as handle:
                channels = handle.getnchannels()
                width = handle.getsampwidth()
                rate = handle.getframerate()
                frames = handle.getnframes()
                raw = handle.readframes(frames)
        except Exception as exc:
            raise AudioCaptureError(
                f"无法读取 WAV 文件：{self.path}\n"
                "调试模式只支持 PCM WAV（不支持 MP3/OGG/FLAC）。\n"
                f"详细信息：{exc}"
            ) from exc

        if width == 1:
            data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif width == 2:
            data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif width == 3:
            triples = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            values = triples[:, 0] | (triples[:, 1] << 8) | (triples[:, 2] << 16)
            values = np.where(values & 0x800000, values - 0x1000000, values)
            data = values.astype(np.float32) / 8388608.0
        elif width == 4:
            data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        else:
            raise AudioCaptureError(f"不支持的 WAV 位深：{width * 8} bit（{self.path}）")

        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)

        if rate != self.samplerate and data.size:
            old_positions = np.arange(data.size, dtype=np.float64) / float(rate)
            new_size = max(1, int(round(data.size * self.samplerate / rate)))
            new_positions = np.arange(new_size, dtype=np.float64) / float(self.samplerate)
            data = np.interp(new_positions, old_positions, data).astype(np.float32)

        self._audio = np.ascontiguousarray(data, dtype=np.float32)
        self._position = 0
        return self

    def close(self) -> None:
        self._audio = None
        self._position = 0

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def frames(self, stop_event: threading.Event | None = None) -> Iterator[np.ndarray]:
        if self._audio is None:
            self.__enter__()
        assert self._audio is not None
        block = 384
        while self._position < self._audio.size:
            if stop_event is not None and stop_event.is_set():
                return
            chunk = self._audio[self._position : self._position + block]
            self._position += chunk.size
            if chunk.size:
                yield chunk

    def describe(self) -> str:
        return f"音频文件：{self.path}"
