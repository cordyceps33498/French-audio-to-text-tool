"""环境自检：依赖、模型、音频设备。

用法：
    python scripts/check_env.py                  # 检查仓库 + 默认模型目录
    python scripts/check_env.py --root <便携包>  # 检查便携包
    python scripts/check_env.py --skip-devices   # 无音频环境（CI）使用
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import os
import platform
import sys
from pathlib import Path

OK = "[OK]  "
FAIL = "[失败]"
WARN = "[警告]"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="fr-translator 环境自检")
    parser.add_argument("--root", default=os.environ.get("FR_TRANSLATOR_ROOT") or str(repo_root))
    parser.add_argument("--skip-models", action="store_true", help="不检查 models/ 目录")
    parser.add_argument("--skip-devices", action="store_true", help="不枚举音频设备")
    return parser.parse_args(argv)


def check_import(module: str, label: str) -> bool:
    try:
        importlib.import_module(module)
    except Exception as exc:
        print(f"{FAIL} {label}：导入失败（{exc}）")
        return False
    version = "?"
    try:
        version = importlib.metadata.version(module.replace("_", "-"))
    except importlib.metadata.PackageNotFoundError:
        try:
            version = getattr(importlib.import_module(module), "__version__", "?")
        except Exception:
            pass
    print(f"{OK} {label}：{version}")
    return True


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def _model_complete(path: Path) -> bool:
    config = path / "config.json"
    if not config.is_file() or config.stat().st_size == 0:
        return False
    for name in ("pytorch_model.bin", "model.safetensors"):
        weight = path / name
        if weight.is_file() and weight.stat().st_size > 0:
            return True
    return False


def _has_model(hub: Path, model_id: str) -> bool:
    flat = hub / model_id.replace("/", "--")
    if _model_complete(flat):
        return True
    if _model_complete(hub / model_id):
        return True
    base = hub / ("models--" + model_id.replace("/", "--")) / "snapshots"
    if not base.is_dir():
        return False
    return any(item.is_dir() and _model_complete(item) for item in base.iterdir())


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    # 与大程序保持一致，避免 transformers 去写默认的 ~/.cache
    os.environ.setdefault("HF_HOME", str(root / "models"))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(root / "models" / "hub"))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    print("=" * 60)
    print("fr-translator 环境自检")
    print("=" * 60)
    print(f"Python      : {platform.python_version()} ({platform.architecture()[0]})")
    print(f"可执行文件  : {sys.executable}")
    print(f"程序根目录  : {root}")
    print(f"HF_ENDPOINT : {os.environ.get('HF_ENDPOINT', '(未设置)')}")
    print(f"HF_HUB_OFFLINE : {os.environ.get('HF_HUB_OFFLINE', '(未设置)')}")
    print()

    problems: list[str] = []

    print("[1/4] 检查依赖")
    deps = [
        ("numpy", "numpy"),
        ("yaml", "PyYAML"),
        ("soundcard", "soundcard"),
        ("cffi", "cffi"),
        ("torch", "torch"),
        ("ctranslate2", "ctranslate2"),
        ("faster_whisper", "faster-whisper"),
        ("transformers", "transformers"),
        ("sentencepiece", "sentencepiece"),
        ("huggingface_hub", "huggingface-hub"),
        ("silero_vad", "silero-vad"),
        ("onnxruntime", "onnxruntime"),
    ]
    for module, label in deps:
        if not check_import(module, label):
            problems.append(f"缺少依赖 {label}")

    # torch 必须是 CPU 版（便携包里不允许 CUDA 版）
    try:
        import torch  # type: ignore import-not-found

        if "+cu" in torch.__version__ or getattr(torch, "cuda", None) and torch.cuda.is_available():
            print(f"{WARN} 检测到 CUDA 版 torch（{torch.__version__}），便携包应安装 CPU 版。")
    except Exception:
        pass
    print()

    if not args.skip_models:
        print("[2/4] 检查模型")
        whisper_dir = root / "models" / "faster-whisper" / "small"
        model_bin = whisper_dir / "model.bin"
        if model_bin.is_file():
            print(f"{OK} faster-whisper small：{whisper_dir}（{human_size(dir_size(whisper_dir))}）")
        else:
            print(f"{FAIL} faster-whisper small：缺少 {model_bin}")
            problems.append("缺少语音识别模型（lite 包首次运行需联网下载，或重新下载 full 包）")

        hub = root / "models" / "hub"
        direct = _has_model(hub, "Helsinki-NLP/opus-mt-fr-zh")
        pivot_a = _has_model(hub, "Helsinki-NLP/opus-mt-fr-en")
        pivot_b = _has_model(hub, "Helsinki-NLP/opus-mt-en-zh")
        if direct:
            print(f"{OK} 翻译模型：opus-mt-fr-zh（直译）")
        elif pivot_a and pivot_b:
            print(f"{OK} 翻译模型：opus-mt-fr-en → opus-mt-en-zh（两段式）")
            print(f"        缓存目录：{hub}（{human_size(dir_size(hub))}）")
        else:
            print(f"{FAIL} 翻译模型：缺少 opus-mt-fr-zh，也没有完整的两段式模型")
            problems.append("缺少翻译模型（lite 包首次运行需联网下载，或重新下载 full 包）")

        silero = root / "models" / "silero-vad" / "silero_vad.onnx"
        if silero.is_file():
            print(f"{OK} silero-vad：{silero}（{human_size(silero.stat().st_size)}）")
        else:
            print(f"{WARN} silero-vad：未找到 {silero}，将使用 silero-vad 包内置 ONNX 模型。")
        print()
    else:
        print("[2/4] 已跳过模型检查\n")

    print("[3/4] 检查音频设备")
    if args.skip_devices:
        print(f"{WARN} 已跳过设备检查")
    else:
        try:
            sys.path.insert(0, str(root / "src"))
            from fr_translator.devices import list_loopback_devices, no_loopback_message

            devices = list_loopback_devices()
            if devices:
                for index, device in enumerate(devices, start=1):
                    print(f"{OK} {index}. {device.display_name}")
            else:
                print(f"{FAIL} 没有找到 loopback 设备")
                problems.append(no_loopback_message())
        except Exception as exc:
            print(f"{FAIL} 枚举音频设备失败：{exc}")
            problems.append(f"音频设备枚举失败：{exc}")
    print()

    print("[4/4] 检查目录权限")
    for label, path in (
        ("模型目录", root / "models"),
        ("日志目录", root / "logs"),
        ("用户配置目录", Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "fr-translator"),
    ):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            print(f"{OK} {label}可写：{path}")
        except OSError as exc:
            if "用户配置" in label:
                print(f"{WARN} {label}不可写：{path}（设备选择将无法保存，但不影响运行）")
            else:
                print(f"{FAIL} {label}不可写：{path}（{exc}）")
                problems.append(f"{label}不可写：{path}")

    print()
    print("=" * 60)
    if problems:
        print(f"自检发现问题（{len(problems)} 项）：")
        for index, problem in enumerate(problems, start=1):
            print(f"  {index}. {problem}")
        print("=" * 60)
        return 1
    print("自检通过，可以运行。")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
