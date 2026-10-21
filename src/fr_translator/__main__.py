"""命令行入口：``python -m fr_translator``。"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import warnings
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .audio_capture import LoopbackCapture, WavFileCapture
from .config import (
    AppConfig,
    AppPaths,
    ConfigError,
    detect_edition,
    load_config,
    resolve_offline,
    update_user_config,
)
from .console_ui import ConsoleUI
from .devices import (
    list_loopback_devices,
    no_loopback_message,
    resolve_capture_device,
)
from .errors import AudioCaptureError, DeviceError, FrTranslatorError
from .pipeline import TranslationEngine

logger = logging.getLogger("fr_translator")


# ---------------------------------------------------------------------------
# 控制台 / 日志
# ---------------------------------------------------------------------------
def _configure_console() -> None:
    """尽量把 Windows 控制台切到 UTF-8，避免中文和法语重音乱码。"""

    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            if kernel32.GetConsoleWindow():
                kernel32.SetConsoleOutputCP(65001)
                kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
            if encoding and encoding != "utf8" and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _setup_logging(paths: AppPaths, config: AppConfig, *, debug: bool, ui: ConsoleUI) -> Path | None:
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.setLevel(logging.DEBUG if debug else getattr(logging, config.logging.level, logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    log_file = config.logging.resolve_file(paths.root)
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_file = paths.writable_log_dir() / log_file.name

    handler: RotatingFileHandler | None = None
    try:
        handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except OSError as exc:
        ui.status(f"无法写入日志文件（{log_file}），本次仅输出到控制台：{exc}", "warning")

    if debug:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # 第三方库的 INFO 日志太吵，压到 WARNING
    for noisy in ("urllib3", "httpx", "huggingface_hub", "transformers", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return log_file if handler is not None else None


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fr-translator",
        description="Windows 系统音频法语实时翻译（faster-whisper + opus-mt-fr-zh）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python -m fr_translator                  # 首次运行会提示选择输出设备\n"
            "  python -m fr_translator --list-devices   # 只列出可录制的系统音频设备\n"
            "  python -m fr_translator --device \"扬声器\"  # 指定设备\n"
            "  python -m fr_translator --offline        # 强制离线（缺模型直接报错）\n"
            "  python -m fr_translator --wav test.wav   # 开发调试：从音频文件读取\n"
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"fr-translator {__version__}")
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        metavar="FILE",
        help="额外配置文件，优先级最高，可重复指定",
    )
    parser.add_argument(
        "--device",
        default=None,
        metavar="NAME_OR_ID",
        help="系统音频输出设备名称或 id；auto 表示交互选择",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="列出所有可录制系统声音的输出设备后退出",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--online", action="store_true", help="允许联网下载缺失的模型")
    group.add_argument("--offline", action="store_true", help="强制离线运行，缺模型直接报错")
    parser.add_argument(
        "--no-translate",
        action="store_true",
        help="仅识别法语原文，不翻译（asr 轻量模式）",
    )
    parser.add_argument(
        "--wav",
        metavar="FILE",
        default=None,
        help="调试用：从音频文件读取，而不是捕获系统音频",
    )
    parser.add_argument("--debug", action="store_true", help="输出调试日志和完整异常堆栈")
    return parser


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_console()
    # 第三方库的 FutureWarning / 缺 sacremoses 提示对最终用户没有意义
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", message=".*pip install sacremoses.*")
    warnings.filterwarnings("ignore", message=".*clean_up_tokenization_spaces.*")
    ui = ConsoleUI()

    try:
        return _run(args, ui)
    except KeyboardInterrupt:
        ui.status("已停止。")
        return 0
    except ConfigError as exc:
        logger.error("配置错误：%s", exc)
        ui.error(str(exc))
        return 2
    except FrTranslatorError as exc:
        logger.error("%s", exc)
        ui.error(str(exc))
        if args.debug:
            import traceback

            traceback.print_exc()
        return 1
    except Exception as exc:  # 兜底：任何情况下都不要直接把堆栈甩给用户
        logger.exception("未预期的错误")
        ui.error(f"程序遇到未预期的错误：{exc}")
        if args.debug:
            import traceback

            traceback.print_exc()
        else:
            ui.status("如需排查，请加 --debug 重新运行，日志在 logs/fr-translator.log。", "warning")
        return 1


def _run(args: argparse.Namespace, ui: ConsoleUI) -> int:
    paths = AppPaths.discover()
    config = load_config(paths, args.config)
    if args.device:
        config.audio.device = args.device
        config.audio.device_id = ""
    if args.no_translate:
        config.translate.enabled = False

    log_file = _setup_logging(paths, config, debug=args.debug, ui=ui)
    logger.info("fr-translator %s 启动，根目录：%s", __version__, paths.root)
    for source in config.sources:
        logger.info("已加载配置：%s", source)
    if log_file is not None:
        logger.info("日志文件：%s", log_file)

    ui.status(f"fr-translator {__version__}（根目录：{paths.root}）")

    if args.list_devices:
        _print_devices(ui)
        return 0

    offline, notes = resolve_offline(
        paths,
        config,
        force_online=args.online,
        force_offline=args.offline,
    )
    edition = detect_edition(paths, config)
    for level, message in notes:
        ui.status(message, level)
    ui.status(
        "运行模式："
        + ("离线（不联网）" if offline else "联网（缺失模型将自动下载，首次会较慢）")
        + f"，版本标识：{edition}"
    )

    if args.wav:
        logger.info("使用音频文件调试：%s", args.wav)
        source = WavFileCapture(args.wav, samplerate=config.audio.samplerate)
        engine = TranslationEngine(
            config,
            paths,
            source,
            ui,
            offline=offline,
            edition=edition,
        )
        engine.load()
        engine.run()
        return 0

    devices = list_loopback_devices()
    device, changed = resolve_capture_device(
        devices,
        config.audio.device,
        config.audio.device_id,
        ui,
    )
    if changed:
        error = update_user_config(
            paths,
            {"audio": {"device": device.name, "device_id": device.id}},
        )
        if error:
            ui.status(error, "warning")
        else:
            ui.status(f"已记住输出设备：{device.display_name}")
            logger.info("设备选择已写入 %s", paths.user_config)

    source = LoopbackCapture(
        device,
        samplerate=config.audio.samplerate,
        channels=config.audio.channels,
        block_frames=config.audio.block_frames,
    )
    engine = TranslationEngine(
        config,
        paths,
        source,
        ui,
        offline=offline,
        edition=edition,
    )
    engine.load()
    return _run_with_retry(engine, ui)


def _run_with_retry(engine: TranslationEngine, ui: ConsoleUI) -> int:
    while True:
        try:
            engine.run()
            return 0
        except AudioCaptureError as exc:
            logger.warning("音频捕获失败：%s", exc)
            ui.error(str(exc))
            if not ui.confirm("按回车重试，输入 n 退出："):
                return 1


def _print_devices(ui: ConsoleUI) -> None:
    devices = list_loopback_devices()
    if not devices:
        raise DeviceError(no_loopback_message())
    ui.status(f"找到 {len(devices)} 个可录制系统声音的设备：")
    for index, device in enumerate(devices, start=1):
        ui.status(f"  {index}. {device.display_name}")
        ui.status(f"      设备 id：{device.id}")


# ---------------------------------------------------------------------------
# 兼容 python -m 与 console_scripts
# ---------------------------------------------------------------------------
def _entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover
    # Ctrl+C 交给默认的 KeyboardInterrupt 处理；SIGBREAK 也转换成正常退出
    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        except Exception:
            pass
    _entry()
