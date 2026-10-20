"""预下载模型到便携包 / 仓库的 models/ 目录。

用法：
    python scripts/prepare_models.py --root .                 # 下载全部模型
    python scripts/prepare_models.py --root portable/xxx --skip-silero
    set HF_ENDPOINT=https://hf-mirror.com && python scripts/prepare_models.py --root .

下载内容：
    models/faster-whisper/<model>/      faster-whisper CTranslate2 模型（small 约 480MB）
    models/hub/models--Helsinki-NLP--opus-mt-fr-zh/   opus-mt 翻译模型（约 300MB）
    models/silero-vad/silero_vad.onnx   silero-vad 内置 ONNX 模型的副本（可选，约 2MB）

脚本不依赖 fr_translator 包，方便在构建脚本里用嵌入式 Python 直接运行。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

TRANSLATION_MODEL = "Helsinki-NLP/opus-mt-fr-zh"
PIVOT_MODELS = ("Helsinki-NLP/opus-mt-fr-en", "Helsinki-NLP/opus-mt-en-zh")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="预下载 fr-translator 需要的模型（faster-whisper / opus-mt / silero-vad）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        default=str(repo_root),
        help="程序根目录（模型写到 <root>/models 下），默认仓库根目录",
    )
    parser.add_argument("--whisper-model", default="small", help="faster-whisper 模型名，默认 small")
    parser.add_argument("--skip-whisper", action="store_true", help="跳过语音识别模型")
    parser.add_argument("--skip-translate", action="store_true", help="跳过翻译模型")
    parser.add_argument("--skip-silero", action="store_true", help="跳过 silero-vad 模型副本")
    parser.add_argument(
        "--hf-endpoint",
        default="",
        help="Hugging Face 镜像地址，例如 https://hf-mirror.com（等价于设置 HF_ENDPOINT）",
    )
    parser.add_argument("--retries", type=int, default=3, help="每个模型的最大重试次数，默认 3")
    return parser.parse_args(argv)


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def retry(description: str, attempts: int, action) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            action()
            return
        except Exception as exc:  # 网络错误类型很多，统一重试
            last_error = exc
            if attempt == attempts:
                break
            wait = min(30, 5 * attempt)
            print(f"  [重试] {description} 第 {attempt} 次失败：{exc}")
            print(f"          {wait} 秒后重试（{attempt + 1}/{attempts}）...")
            time.sleep(wait)
    raise RuntimeError(f"{description} 失败：{last_error}")


def download_whisper(root: Path, model: str, attempts: int) -> bool:
    target = root / "models" / "faster-whisper" / model
    if (target / "model.bin").is_file():
        print(f"[跳过] faster-whisper {model} 已存在：{target}")
        return True

    print(f"[下载] faster-whisper {model} -> {target}")
    target.mkdir(parents=True, exist_ok=True)

    def action() -> None:
        from faster_whisper.utils import download_model  # type: ignore import-not-found

        download_model(model, output_dir=str(target))

    try:
        retry(f"下载 faster-whisper {model}", attempts, action)
    except Exception as exc:
        print(f"[失败] {exc}")
        print("       可以设置 HF_ENDPOINT=https://hf-mirror.com 后重试。")
        return False

    missing = [name for name in ("model.bin", "config.json") if not (target / name).is_file()]
    if missing:
        print(f"[失败] 模型文件不完整，缺少：{', '.join(missing)}")
        return False
    print(f"[完成] {target}（{human_size(dir_size(target))}）")
    return True


def download_translation(root: Path, attempts: int) -> bool:
    hub_dir = root / "models" / "hub"
    hub_dir.mkdir(parents=True, exist_ok=True)

    direct_target = _model_dir(hub_dir, TRANSLATION_MODEL)
    if _model_complete(direct_target):
        print(f"[跳过] 翻译模型 {TRANSLATION_MODEL} 已存在：{direct_target}")
        return True

    pivot_targets = [_model_dir(hub_dir, model) for model in PIVOT_MODELS]
    if all(_model_complete(path) for path in pivot_targets):
        print("[跳过] 两段式翻译模型已存在：")
        for model, path in zip(PIVOT_MODELS, pivot_targets):
            print(f"        {model} -> {path}")
        return True

    # 优先下载直译模型；Helsinki-NLP 实际并未发布 fr-zh（2026-10 核实），
    # 仓库不存在时自动改用 fr-en + en-zh 两段式。
    print(f"[尝试] 直译模型 {TRANSLATION_MODEL}")
    direct_ok = False
    try:
        retry(
            f"下载 {TRANSLATION_MODEL}",
            attempts,
            lambda: _snapshot_download(TRANSLATION_MODEL, direct_target),
        )
        direct_ok = _model_complete(direct_target)
    except Exception as exc:
        print(f"[提示] 直译模型不可用：{exc}")
        print("       改用两段式：opus-mt-fr-en + opus-mt-en-zh")

    if direct_ok:
        print(f"[完成] {direct_target}（{human_size(dir_size(direct_target))}）")
        return True

    # 两段式
    ok = True
    for model, target in zip(PIVOT_MODELS, pivot_targets):
        if _model_complete(target):
            print(f"[跳过] {model} 已存在：{target}")
            continue
        print(f"[下载] {model} -> {target}")
        try:
            retry(
                f"下载 {model}",
                attempts,
                lambda m=model, t=target: _snapshot_download(m, t),
            )
        except Exception as exc:
            print(f"[失败] {exc}")
            ok = False
            continue
        if not _model_complete(target):
            print(f"[失败] {model} 文件不完整（缺少 config.json 或权重文件）。")
            ok = False
        else:
            print(f"[完成] {target}（{human_size(dir_size(target))}）")

    if ok:
        print("[完成] 两段式翻译模型已就绪（法→英→中）。")
    else:
        print("       可以设置 HF_ENDPOINT=https://hf-mirror.com 后重试。")
    return ok


def _model_dir(hub_dir: Path, model_id: str) -> Path:
    """平铺模型目录，避免 Windows 无符号链接权限时的 HF blobs 问题。"""

    return hub_dir / model_id.replace("/", "--")


def _snapshot_download(model_id: str, target: Path) -> None:
    from huggingface_hub import snapshot_download  # type: ignore import-not-found

    target.mkdir(parents=True, exist_ok=True)
    base_patterns = ["*.json", "*.model", "*.spm", "*.txt"]
    # 优先 safetensors，没有权重时再补 pytorch_model.bin
    snapshot_download(
        repo_id=model_id,
        local_dir=str(target),
        allow_patterns=base_patterns + ["*.safetensors"],
        max_workers=4,
    )
    if not _model_complete(target):
        snapshot_download(
            repo_id=model_id,
            local_dir=str(target),
            allow_patterns=base_patterns + ["*.bin"],
            max_workers=4,
        )


def _model_complete(path: Path) -> bool:
    """目录里必须同时有非空的 config.json 和权重文件。"""

    config = path / "config.json"
    if not config.is_file() or config.stat().st_size == 0:
        return False
    for name in ("pytorch_model.bin", "model.safetensors"):
        weight = path / name
        if weight.is_file() and weight.stat().st_size > 0:
            return True
    return False


def copy_silero(root: Path) -> bool:
    target = root / "models" / "silero-vad"
    target.mkdir(parents=True, exist_ok=True)
    try:
        import silero_vad  # type: ignore import-not-found
    except ImportError:
        print("[跳过] 未安装 silero-vad，无法复制内置 ONNX 模型")
        return False

    package_dir = Path(silero_vad.__file__).resolve().parent
    source = package_dir / "data" / "silero_vad.onnx"
    if not source.is_file():
        print(f"[跳过] silero-vad 包内没有 ONNX 模型：{source}")
        return False

    destination = target / "silero_vad.onnx"
    if destination.is_file() and destination.stat().st_size == source.stat().st_size:
        print(f"[跳过] silero-vad 模型已存在：{destination}")
        return True

    shutil.copy2(source, destination)
    print(f"[完成] {destination}（{human_size(destination.stat().st_size)}）")
    return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # 这个脚本的唯一目的就是下载，必须确保 huggingface_hub 不处于离线模式
    os.environ["HF_HUB_OFFLINE"] = "0"
    os.environ["TRANSFORMERS_OFFLINE"] = "0"
    if args.hf_endpoint.strip():
        os.environ["HF_ENDPOINT"] = args.hf_endpoint.strip()
    endpoint = os.environ.get("HF_ENDPOINT", "").strip()
    if endpoint:
        print(f"[信息] 使用 Hugging Face 镜像：{endpoint}")

    root = Path(args.root).expanduser().resolve()
    print(f"[信息] 程序根目录：{root}")
    models_dir = root / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    # 必须显式指定缓存目录：默认的 ~/.cache 在某些受控环境/U 盘只读场景不可写
    os.environ["HF_HOME"] = str(models_dir)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(models_dir / "hub")
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

    ok = True
    if not args.skip_whisper:
        ok = download_whisper(root, args.whisper_model, max(1, args.retries)) and ok
    if not args.skip_translate:
        ok = download_translation(root, max(1, args.retries)) and ok
    if not args.skip_silero:
        # silero 是可选优化，失败只提示，不影响主流程（运行时可用包内置模型）
        copy_silero(root)

    if ok:
        print("\n[全部完成] 模型已就绪，可以在离线环境运行。")
        return 0
    print("\n[存在失败项] 请检查网络后重试；国内网络建议设置 HF_ENDPOINT=https://hf-mirror.com。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
