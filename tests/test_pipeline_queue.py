from __future__ import annotations

import queue
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

from fr_translator.config import AppConfig, AppPaths
from fr_translator.pipeline import _SENTINEL, TranslationEngine

SENTINEL = _SENTINEL


class FakeSink:
    def __init__(self) -> None:
        self.statuses: list[tuple[str, str]] = []

    def status(self, message: str, level: str = "info") -> None:
        self.statuses.append((level, message))

    def caption(self, french: str, chinese: str) -> None:
        pass

    def translation_failed(self, french: str, reason: str = "") -> None:
        pass


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


class RecognitionQueueTests(unittest.TestCase):
    def make_engine(self, maxsize: int = 2):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
        sink = FakeSink()
        engine = TranslationEngine(
            AppConfig(),
            paths,
            source=object(),  # type: ignore[arg-type]
            sink=sink,
            offline=True,
            edition="full",
        )
        engine._utterances = queue.Queue(maxsize=maxsize)
        processed: list[np.ndarray] = []
        engine._handle_utterance = lambda audio: processed.append(audio)  # type: ignore[method-assign]
        return engine, sink, processed

    def test_submit_does_not_block_and_drops_oldest(self) -> None:
        engine, sink, _ = self.make_engine()
        first = np.zeros(10, dtype=np.float32)
        second = np.ones(10, dtype=np.float32)
        third = np.full(10, 2.0, dtype=np.float32)
        engine._submit(first)
        engine._submit(second)
        engine._submit(third)  # 队列满：丢弃 first

        self.assertEqual(engine._utterances.qsize(), 2)
        self.assertTrue(any("丢弃" in message for _, message in sink.statuses))
        kept = [engine._utterances.get_nowait() for _ in range(2)]
        self.assertTrue(np.array_equal(kept[0], second))
        self.assertTrue(np.array_equal(kept[1], third))

    def test_worker_processes_queue_in_order_and_stops_on_sentinel(self) -> None:
        engine, _, processed = self.make_engine()
        first = np.zeros(10, dtype=np.float32)
        second = np.ones(10, dtype=np.float32)
        engine._submit(first)
        engine._submit(second)

        worker = threading.Thread(target=engine._recognition_loop, daemon=True)
        worker.start()
        engine._utterances.put(SENTINEL)
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(processed), 2)
        self.assertTrue(np.array_equal(processed[0], first))
        self.assertTrue(np.array_equal(processed[1], second))

    def test_clear_pending_drops_backlog(self) -> None:
        engine, _, _ = self.make_engine()
        engine._submit(np.zeros(10, dtype=np.float32))
        engine._submit(np.ones(10, dtype=np.float32))
        engine._clear_pending()
        self.assertEqual(engine._utterances.qsize(), 0)


if __name__ == "__main__":
    unittest.main()
