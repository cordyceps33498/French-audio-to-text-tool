from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fr_translator.config import AppPaths, load_config, update_user_config
from fr_translator.errors import ConfigError


def make_paths(tmp: Path) -> AppPaths:
    root = tmp / "root"
    user = tmp / "user"
    (root / "models").mkdir(parents=True)
    user.mkdir(parents=True)
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


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            config = load_config(paths)
            self.assertEqual(config.audio.device, "auto")
            self.assertEqual(config.audio.samplerate, 16000)
            self.assertEqual(config.audio.block_frames, 384)
            self.assertTrue(config.asr.vad_filter)
            self.assertEqual(config.vad.max_utterance_s, 12.0)

    def test_merge_order_user_overrides_portable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            paths.portable_config.write_text(
                "vad:\n  threshold: 0.6\naudio:\n  block_ms: 20\n", encoding="utf-8"
            )
            paths.user_config.write_text(
                "vad:\n  threshold: 0.7\n", encoding="utf-8"
            )
            config = load_config(paths)
            self.assertEqual(config.vad.threshold, 0.7)
            self.assertEqual(config.audio.block_ms, 20)
            self.assertEqual(len(config.sources), 2)

    def test_extra_config_has_highest_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            extra = Path(tmp) / "extra.yaml"
            extra.write_text("vad:\n  threshold: 0.8\n", encoding="utf-8")
            config = load_config(paths, [extra])
            self.assertEqual(config.vad.threshold, 0.8)

    def test_invalid_samplerate_is_rejected_with_chinese_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            paths.portable_config.write_text(
                "audio:\n  samplerate: 48000\n", encoding="utf-8"
            )
            with self.assertRaises(ConfigError) as ctx:
                load_config(paths)
            self.assertIn("16000", str(ctx.exception))

    def test_broken_yaml_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            paths.portable_config.write_text("audio: [unclosed\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(paths)

    def test_update_user_config_keeps_existing_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            paths.user_config.write_text(
                "audio:\n  device: old\ncustom:\n  key: 1\n", encoding="utf-8"
            )
            error = update_user_config(
                paths, {"audio": {"device": "new", "device_id": "id-1"}}
            )
            self.assertIsNone(error)
            text = paths.user_config.read_text(encoding="utf-8")
            self.assertIn("device: new", text)
            self.assertIn("key: 1", text)
            config = load_config(paths)
            self.assertEqual(config.audio.device, "new")

    def test_model_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            self.assertFalse(paths.has_whisper("small"))
            whisper = paths.whisper_dir("small")
            whisper.mkdir(parents=True)
            (whisper / "model.bin").write_bytes(b"x")
            self.assertTrue(paths.has_whisper("small"))

            self.assertFalse(paths.has_translation("Helsinki-NLP/opus-mt-fr-zh"))
            snapshot = (
                paths.translation_cache_dir("Helsinki-NLP/opus-mt-fr-zh")
                / "snapshots"
                / "abc"
            )
            snapshot.mkdir(parents=True)
            (snapshot / "config.json").write_text("{}", encoding="utf-8")
            (snapshot / "pytorch_model.bin").write_bytes(b"x")
            self.assertTrue(paths.has_translation("Helsinki-NLP/opus-mt-fr-zh"))

    def test_pivot_translation_counts_as_available(self) -> None:
        from fr_translator.config import AppConfig, has_translation_models

        with tempfile.TemporaryDirectory() as tmp:
            paths = make_paths(Path(tmp))
            config = AppConfig()
            self.assertFalse(has_translation_models(paths, config))
            for model_id in (config.translate.pivot_model, config.translate.pivot_target_model):
                snapshot = paths.translation_cache_dir(model_id) / "snapshots" / "abc"
                snapshot.mkdir(parents=True)
                (snapshot / "config.json").write_text("{}", encoding="utf-8")
                (snapshot / "pytorch_model.bin").write_bytes(b"x")
            self.assertTrue(has_translation_models(paths, config))


if __name__ == "__main__":
    unittest.main()
