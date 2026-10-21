from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from fr_translator.audio_capture import WavFileCapture


def write_wav(path: Path, samples: np.ndarray, *, channels: int = 1) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(pcm.tobytes())


class WavFileCaptureTests(unittest.TestCase):
    def test_mono_pcm16_is_read_in_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tone.wav"
            samples = 0.3 * np.sin(2 * np.pi * 440 * np.arange(1600) / 16000)
            write_wav(path, samples)
            with WavFileCapture(path) as source:
                blocks = list(source.frames())
            audio = np.concatenate(blocks)
            self.assertEqual(audio.size, samples.size)
            self.assertAlmostEqual(float(np.max(np.abs(audio))), 0.3, places=2)

    def test_stereo_is_downmixed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stereo.wav"
            left = np.full(800, 0.5, dtype=np.float32)
            right = np.full(800, -0.5, dtype=np.float32)
            stereo = np.stack([left, right], axis=1).reshape(-1)
            write_wav(path, stereo, channels=2)
            with WavFileCapture(path) as source:
                audio = np.concatenate(list(source.frames()))
            self.assertEqual(audio.size, 800)
            np.testing.assert_allclose(audio, 0.0, atol=1e-4)


if __name__ == "__main__":
    unittest.main()
