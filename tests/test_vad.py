from __future__ import annotations

import importlib.util
import unittest

import numpy as np

from fr_translator.config import VadConfig
from fr_translator.vad import WINDOW_SAMPLES, SileroSegmenter

HAS_TORCH = importlib.util.find_spec("torch") is not None
SR = 16000
WINDOW = WINDOW_SAMPLES[SR]


class _Score:
    def __init__(self, value: float) -> None:
        self.value = value

    def reshape(self, *args):  # noqa: ANN002
        return self

    def __getitem__(self, index):  # noqa: ANN001
        return self

    def item(self) -> float:
        return self.value


class FakeVAD:
    def __init__(self, probabilities: list[float]) -> None:
        self.probabilities = probabilities
        self.index = 0
        self.reset_count = 0

    def __call__(self, tensor, samplerate):  # noqa: ANN001
        probability = self.probabilities[min(self.index, len(self.probabilities) - 1)]
        self.index += 1
        return _Score(probability)

    def reset_states(self) -> None:
        self.reset_count += 1


def build_probabilities(segments, total_seconds: float) -> list[float]:
    """segments: [(start_s, end_s, probability), ...]"""

    windows = int(total_seconds * SR / WINDOW) + 2
    result: list[float] = []
    for index in range(windows):
        center = (index * WINDOW + WINDOW / 2) / SR
        probability = 0.02
        for start, end, value in segments:
            if start <= center < end:
                probability = value
                break
        result.append(probability)
    return result


def run_segmenter(
    probabilities: list[float],
    total_seconds: float,
    config: VadConfig,
    block: int = 384,
) -> tuple[list[np.ndarray], SileroSegmenter]:
    model = FakeVAD(probabilities)
    segmenter = SileroSegmenter(model, config, samplerate=SR)
    total = int(total_seconds * SR)
    outputs: list[np.ndarray] = []
    position = 0
    while position < total:
        count = min(block, total - position)
        outputs.extend(segmenter.push(np.zeros(count, dtype=np.float32)))
        position += count
    leftover = segmenter.flush()
    if leftover is not None:
        outputs.append(leftover)
    return outputs, segmenter


@unittest.skipUnless(HAS_TORCH, "需要 torch 才能运行 VAD 切句测试")
class SegmenterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = VadConfig(
            threshold=0.5,
            min_silence_ms=500,
            min_speech_ms=250,
            speech_pad_ms=200,
            pre_roll_ms=200,
            max_utterance_s=12.0,
        )

    def test_single_sentence_is_cut_on_silence(self) -> None:
        probabilities = build_probabilities([(1.0, 2.0, 0.9)], total_seconds=3.0)
        outputs, _ = run_segmenter(probabilities, 3.0, self.config)
        self.assertEqual(len(outputs), 1)
        duration = len(outputs[0]) / SR
        # 1 秒语音 + 前后余量，应该明显长于 1 秒但不会包含整段静音
        self.assertGreater(duration, 1.0)
        self.assertLess(duration, 2.0)

    def test_short_noise_is_discarded(self) -> None:
        probabilities = build_probabilities([(1.0, 1.1, 0.9)], total_seconds=2.0)
        outputs, _ = run_segmenter(probabilities, 2.0, self.config)
        self.assertEqual(outputs, [])

    def test_pure_music_stays_silent(self) -> None:
        probabilities = [0.1] * 200
        outputs, _ = run_segmenter(probabilities, 2.0, self.config)
        self.assertEqual(outputs, [])

    def test_long_speech_is_forced_into_chunks(self) -> None:
        config = VadConfig(
            threshold=0.5,
            min_silence_ms=500,
            min_speech_ms=250,
            speech_pad_ms=200,
            pre_roll_ms=200,
            max_utterance_s=1.0,
        )
        probabilities = build_probabilities([(0.5, 4.0, 0.95)], total_seconds=4.0)
        outputs, _ = run_segmenter(probabilities, 4.0, config)
        self.assertGreaterEqual(len(outputs), 3)
        for segment in outputs:
            self.assertLessEqual(len(segment) / SR, 1.3)

    def test_flush_emits_last_sentence(self) -> None:
        probabilities = build_probabilities([(0.5, 1.5, 0.95)], total_seconds=1.55)
        outputs, _ = run_segmenter(probabilities, 1.55, self.config)
        self.assertEqual(len(outputs), 1)

    def test_two_sentences_with_pause(self) -> None:
        probabilities = build_probabilities(
            [(1.0, 2.0, 0.9), (3.0, 4.0, 0.9)], total_seconds=5.0
        )
        outputs, _ = run_segmenter(probabilities, 5.0, self.config)
        self.assertEqual(len(outputs), 2)


if __name__ == "__main__":
    unittest.main()
