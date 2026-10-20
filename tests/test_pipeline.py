from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from fr_translator.audio_capture import _as_mono_float32
from fr_translator.config import AppConfig, AppPaths
from fr_translator.errors import TranslationError
from fr_translator.pipeline import TranslationEngine


class FakeSink:
    def __init__(self) -> None:
        self.captions: list[tuple[str, str]] = []
        self.failures: list[tuple[str, str]] = []
        self.statuses: list[tuple[str, str]] = []

    def status(self, message: str, level: str = "info") -> None:
        self.statuses.append((level, message))

    def caption(self, french: str, chinese: str) -> None:
        self.captions.append((french, chinese))

    def translation_failed(self, french: str, reason: str = "") -> None:
        self.failures.append((french, reason))


class FakeASR:
    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe(self, audio: np.ndarray) -> str:
        return self.text


class FakeTranslator:
    def __init__(self, text: str) -> None:
        self.text = text

    def translate(self, text: str) -> str:
        return self.text


class FailingTranslator:
    def translate(self, text: str) -> str:
        raise TranslationError("翻译服务不可用")


def make_paths(tmp: Path) -> AppPaths:
    root = tmp / "root"
    (root / "models").mkdir(parents=True)
    user = tmp / "user"
    user.mkdir()
    return AppPaths(
        root=root,
        models=root / "models",
        whisper=root / "models" / "faster-whisper",
        hub=root / "models" / "hub",
        silero=root / "models" / "silero-vad",
        logs=root / "logs",
        user_dir=user,
        user_config=user / "config.yaml",
        portable_config=root / "config.yaml",
    )


class PipelineTests(unittest.TestCase):
    def make_engine(self, sink: FakeSink, asr_text: str = "Bonjour tout le monde") -> TranslationEngine:
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
        engine = TranslationEngine(
            AppConfig(),
            paths,
            source=object(),  # type: ignore[arg-type]
            sink=sink,
            offline=True,
            edition="full",
        )
        engine._asr = FakeASR(asr_text)  # type: ignore[assignment]
        engine._translator = FakeTranslator("大家好")  # type: ignore[assignment]
        return engine

    def test_caption_is_emitted(self) -> None:
        sink = FakeSink()
        engine = self.make_engine(sink)
        engine._handle_utterance(np.zeros(16000, dtype=np.float32))
        self.assertEqual(sink.captions, [("Bonjour tout le monde", "大家好")])
        self.assertEqual(sink.failures, [])

    def test_empty_asr_result_is_skipped(self) -> None:
        sink = FakeSink()
        engine = self.make_engine(sink, asr_text="")
        engine._handle_utterance(np.zeros(16000, dtype=np.float32))
        self.assertEqual(sink.captions, [])
        self.assertEqual(sink.failures, [])

    def test_translation_failure_keeps_french_and_continues(self) -> None:
        sink = FakeSink()
        engine = self.make_engine(sink)
        engine._translator = FailingTranslator()  # type: ignore[assignment]
        engine._handle_utterance(np.zeros(16000, dtype=np.float32))
        self.assertEqual(sink.captions, [])
        self.assertEqual(len(sink.failures), 1)
        self.assertEqual(sink.failures[0][0], "Bonjour tout le monde")
        self.assertIn("翻译服务不可用", sink.failures[0][1])


class AudioConversionTests(unittest.TestCase):
    def test_stereo_is_averaged(self) -> None:
        data = np.array([[1.0, -1.0], [0.5, 0.5]], dtype=np.float32)
        mono = _as_mono_float32(data)
        self.assertEqual(mono.shape, (2,))
        np.testing.assert_allclose(mono, [0.0, 0.5])

    def test_values_are_clipped_and_nan_removed(self) -> None:
        data = np.array([2.0, -3.0, np.nan], dtype=np.float32)
        mono = _as_mono_float32(data)
        np.testing.assert_allclose(mono, [1.0, -1.0, 0.0])

    def test_block_frames(self) -> None:
        self.assertEqual(AppConfig().audio.block_frames, 384)


if __name__ == "__main__":
    unittest.main()
