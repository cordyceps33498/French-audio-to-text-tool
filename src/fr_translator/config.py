"""配置加载、校验与路径解析。

设计要点
--------
* 所有相对路径都相对于“程序根目录”解析，不依赖当前工作目录。
* 加载顺序：内置默认 -> 根目录 config.yaml -> 用户配置 -> --config 指定的文件。
* 用户级配置（设备选择、GUI 位置等）只写 ``%LOCALAPPDATA%\\fr-translator``，
  绝不写 U 盘，方便便携包换机器使用。
* 本模块不导入 torch / transformers / faster-whisper，保证可以在设置
  环境变量（HF_HOME 等）之前安全导入。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .errors import ConfigError

APP_NAME = "fr-translator"
SUPPORTED_SAMPLE_RATE = 16000
SUPPORTED_CHANNELS = 1

# OPUS-MT / Marian 模型目录里必须同时有 config.json 和权重文件
_TRANSLATION_WEIGHT_FILES = ("pytorch_model.bin", "model.safetensors")


def _translation_model_complete(path: Path) -> bool:
    """目录里必须有非空的 config.json 和权重文件。

    便携包默认使用“平铺目录”（snapshot_download 的 local_dir），不依赖
    Hugging Face 的 blobs/symlinks 缓存结构——Windows 没开开发者模式时
    创建符号链接会失败，容易留下 0 字节的占位文件。
    """

    config = path / "config.json"
    if not config.is_file() or config.stat().st_size == 0:
        return False
    for name in _TRANSLATION_WEIGHT_FILES:
        weight = path / name
        if weight.is_file() and weight.stat().st_size > 0:
            return True
    return False


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AppPaths:
    """程序使用到的全部路径。"""

    root: Path
    models: Path
    whisper: Path
    hub: Path
    silero: Path
    logs: Path
    user_dir: Path
    user_config: Path
    portable_config: Path

    @classmethod
    def discover(cls, root: str | Path | None = None) -> "AppPaths":
        base = Path(root).expanduser().resolve() if root else discover_root()
        user_dir = user_data_dir()
        return cls(
            root=base,
            models=base / "models",
            whisper=base / "models" / "faster-whisper",
            hub=base / "models" / "hub",
            silero=base / "models" / "silero-vad",
            logs=base / "logs",
            user_dir=user_dir,
            user_config=user_dir / "config.yaml",
            portable_config=base / "config.yaml",
        )

    # -- 模型位置 ----------------------------------------------------------
    def whisper_dir(self, model: str) -> Path:
        return self.whisper / model

    def has_whisper(self, model: str) -> bool:
        return (self.whisper_dir(model) / "model.bin").is_file()

    def translation_cache_dir(self, model_id: str) -> Path:
        return self.hub / ("models--" + model_id.replace("/", "--"))

    def translation_dir(self, model_id: str) -> Path:
        """平铺模型目录：models/hub/<owner>--<name>/。"""

        return self.hub / model_id.replace("/", "--")

    def translation_local_dir(self, model_id: str) -> Path | None:
        """返回可用的本地模型目录（兼容平铺、旧平铺、HF 缓存三种布局）。"""

        for candidate in (self.translation_dir(model_id), self.hub / model_id):
            if _translation_model_complete(candidate):
                return candidate
        cache_dir = self.translation_cache_dir(model_id)
        snapshots = cache_dir / "snapshots"
        if snapshots.is_dir():
            for candidate in snapshots.iterdir():
                if candidate.is_dir() and _translation_model_complete(candidate):
                    return candidate
        return None

    def has_translation(self, model_id: str) -> bool:
        return self.translation_local_dir(model_id) is not None

    def silero_onnx(self) -> Path:
        return self.silero / "silero_vad.onnx"

    # -- 目录 --------------------------------------------------------------
    def ensure_model_dirs(self) -> None:
        for path in (self.whisper, self.hub, self.silero):
            path.mkdir(parents=True, exist_ok=True)

    def writable_log_dir(self) -> Path:
        """返回可写的日志目录；U 盘只读时回退到用户目录。"""

        for candidate in (self.logs, self.user_dir / "logs"):
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                probe = candidate / ".write-test"
                probe.write_text("", encoding="utf-8")
                probe.unlink()
                return candidate
            except OSError:
                continue
        return self.logs


def discover_root() -> Path:
    """定位程序根目录。

    优先级：
    1. 环境变量 ``FR_TRANSLATOR_ROOT``（run.bat 会设置）；
    2. 嵌入式 Python：``python\\python.exe`` 位于根目录下，且同级存在 ``app``；
    3. 源码布局：包含 ``pyproject.toml`` 与 ``run-dev.bat`` 的祖先目录；
    4. 当前工作目录。
    """

    env = os.environ.get("FR_TRANSLATOR_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()

    prefix = Path(sys.prefix)
    try:
        if (prefix / "python.exe").is_file() and (prefix.parent / "app").is_dir():
            return prefix.parent.resolve()
    except OSError:
        pass

    try:
        here = Path(__file__).resolve()
    except OSError:
        return Path.cwd()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "run-dev.bat").is_file():
            return parent
    return Path.cwd().resolve()


def user_data_dir() -> Path:
    """用户级配置目录（Windows: %LOCALAPPDATA%\\fr-translator）。"""

    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Local" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".config" / APP_NAME


# ---------------------------------------------------------------------------
# 配置结构
# ---------------------------------------------------------------------------
@dataclass
class AudioConfig:
    device: str = "auto"
    device_id: str = ""
    samplerate: int = SUPPORTED_SAMPLE_RATE
    channels: int = SUPPORTED_CHANNELS
    block_ms: int = 24

    @property
    def block_frames(self) -> int:
        return max(1, int(round(self.samplerate * self.block_ms / 1000.0)))


@dataclass
class VadConfig:
    threshold: float = 0.45
    min_silence_ms: int = 700
    min_speech_ms: int = 250
    speech_pad_ms: int = 300
    pre_roll_ms: int = 300
    max_utterance_s: float = 20.0


@dataclass
class AsrConfig:
    model: str = "small"
    language: str = "fr"
    compute_type: str = "int8"
    beam_size: int = 5
    vad_filter: bool = True
    # 把上一句文本作为 initial_prompt，减少句子被切开时的首词丢失。
    use_context: bool = True
    cpu_threads: int = 0


@dataclass
class TranslateConfig:
    # 官方文档中给出的直译模型。需要说明：截至 2026-10，Hugging Face 上
    # 并不存在 Helsinki-NLP/opus-mt-fr-zh 这个仓库（Helsinki-NLP 没有发布
    # 法语→中文的 OPUS-MT 直译模型）。因此默认开启 fallback，改用两段式：
    #   opus-mt-fr-en（法→英） + opus-mt-en-zh（英→中）
    model: str = "Helsinki-NLP/opus-mt-fr-zh"
    pivot_model: str = "Helsinki-NLP/opus-mt-fr-en"
    pivot_target_model: str = "Helsinki-NLP/opus-mt-en-zh"
    fallback: bool = True
    max_length: int = 512
    num_beams: int = 4


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/fr-translator.log"

    def resolve_file(self, root: Path) -> Path:
        path = Path(self.file)
        return path if path.is_absolute() else root / path


@dataclass
class AppConfig:
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    translate: TranslateConfig = field(default_factory=TranslateConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    raw: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 加载与校验
# ---------------------------------------------------------------------------
def load_config(paths: AppPaths, extra_files: Sequence[str | Path] = ()) -> AppConfig:
    """按优先级合并 YAML 配置并校验。"""

    candidates: list[Path] = [paths.portable_config]
    candidates.append(paths.user_config)
    candidates.extend(Path(p).expanduser() for p in extra_files)

    merged: dict[str, Any] = {}
    sources: list[str] = []
    for path in candidates:
        if not path.is_file():
            continue
        data = _read_yaml(path)
        _deep_merge(merged, data)
        sources.append(str(path))

    return _build_config(merged, sources)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件：{path}\n原因：{exc.strerror or exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件格式错误：{path}\n请检查缩进、冒号和引号。\n{exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件格式错误：{path}\n最外层必须是“键: 值”的形式。")
    return data


def _deep_merge(target: dict[str, Any], patch: Mapping[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def _build_config(data: Mapping[str, Any], sources: Sequence[str]) -> AppConfig:
    errors: list[str] = []

    audio_sec = _section(data, "audio", errors)
    vad_sec = _section(data, "vad", errors)
    asr_sec = _section(data, "asr", errors)
    mt_sec = _section(data, "translate", errors)
    log_sec = _section(data, "logging", errors)

    audio = AudioConfig(
        device=_get_str(audio_sec, "audio", "device", AudioConfig.device, errors),
        device_id=_get_str(audio_sec, "audio", "device_id", AudioConfig.device_id, errors),
        samplerate=_get_int(audio_sec, "audio", "samplerate", AudioConfig.samplerate, errors),
        channels=_get_int(audio_sec, "audio", "channels", AudioConfig.channels, errors),
        block_ms=_get_int(audio_sec, "audio", "block_ms", AudioConfig.block_ms, errors),
    )
    vad = VadConfig(
        threshold=_get_float(vad_sec, "vad", "threshold", VadConfig.threshold, errors),
        min_silence_ms=_get_int(vad_sec, "vad", "min_silence_ms", VadConfig.min_silence_ms, errors),
        min_speech_ms=_get_int(vad_sec, "vad", "min_speech_ms", VadConfig.min_speech_ms, errors),
        speech_pad_ms=_get_int(vad_sec, "vad", "speech_pad_ms", VadConfig.speech_pad_ms, errors),
        pre_roll_ms=_get_int(vad_sec, "vad", "pre_roll_ms", VadConfig.pre_roll_ms, errors),
        max_utterance_s=_get_float(vad_sec, "vad", "max_utterance_s", VadConfig.max_utterance_s, errors),
    )
    asr = AsrConfig(
        model=_get_str(asr_sec, "asr", "model", AsrConfig.model, errors),
        language=_get_str(asr_sec, "asr", "language", AsrConfig.language, errors),
        compute_type=_get_str(asr_sec, "asr", "compute_type", AsrConfig.compute_type, errors),
        beam_size=_get_int(asr_sec, "asr", "beam_size", AsrConfig.beam_size, errors),
        vad_filter=_get_bool(asr_sec, "asr", "vad_filter", AsrConfig.vad_filter, errors),
        use_context=_get_bool(asr_sec, "asr", "use_context", AsrConfig.use_context, errors),
        cpu_threads=_get_int(asr_sec, "asr", "cpu_threads", AsrConfig.cpu_threads, errors),
    )
    mt = TranslateConfig(
        model=_get_str(mt_sec, "translate", "model", TranslateConfig.model, errors),
        pivot_model=_get_str(mt_sec, "translate", "pivot_model", TranslateConfig.pivot_model, errors),
        pivot_target_model=_get_str(
            mt_sec, "translate", "pivot_target_model", TranslateConfig.pivot_target_model, errors
        ),
        fallback=_get_bool(mt_sec, "translate", "fallback", TranslateConfig.fallback, errors),
        max_length=_get_int(mt_sec, "translate", "max_length", TranslateConfig.max_length, errors),
        num_beams=_get_int(mt_sec, "translate", "num_beams", TranslateConfig.num_beams, errors),
    )
    log_cfg = LoggingConfig(
        level=_get_str(log_sec, "logging", "level", LoggingConfig.level, errors).upper(),
        file=_get_str(log_sec, "logging", "file", LoggingConfig.file, errors),
    )

    if audio.samplerate != SUPPORTED_SAMPLE_RATE:
        errors.append(
            f"audio.samplerate 目前仅支持 {SUPPORTED_SAMPLE_RATE}（silero-vad 的输入要求），"
            f"当前为 {audio.samplerate}。"
        )
    if audio.channels != SUPPORTED_CHANNELS:
        errors.append(f"audio.channels 必须为 {SUPPORTED_CHANNELS}（单声道），当前为 {audio.channels}。")
    if not 10 <= audio.block_ms <= 200:
        errors.append(f"audio.block_ms 应在 10~200 之间，当前为 {audio.block_ms}。")
    if not 0.1 <= vad.threshold <= 0.95:
        errors.append(f"vad.threshold 应在 0.1~0.95 之间，当前为 {vad.threshold}。")
    if not 100 <= vad.min_silence_ms <= 5000:
        errors.append(f"vad.min_silence_ms 应在 100~5000 之间，当前为 {vad.min_silence_ms}。")
    if not 50 <= vad.min_speech_ms <= 5000:
        errors.append(f"vad.min_speech_ms 应在 50~5000 之间，当前为 {vad.min_speech_ms}。")
    if not 0 <= vad.speech_pad_ms <= 1000:
        errors.append(f"vad.speech_pad_ms 应在 0~1000 之间，当前为 {vad.speech_pad_ms}。")
    if not 0 <= vad.pre_roll_ms <= 1000:
        errors.append(f"vad.pre_roll_ms 应在 0~1000 之间，当前为 {vad.pre_roll_ms}。")
    if not 1 <= vad.max_utterance_s <= 60:
        errors.append(f"vad.max_utterance_s 应在 1~60 之间，当前为 {vad.max_utterance_s}。")
    if vad.max_utterance_s * 1000 < vad.min_silence_ms + 500:
        errors.append("vad.max_utterance_s 太短，至少应大于 min_silence_ms + 0.5 秒。")
    if not 1 <= asr.beam_size <= 10:
        errors.append(f"asr.beam_size 应在 1~10 之间，当前为 {asr.beam_size}。")
    if asr.cpu_threads < 0:
        errors.append("asr.cpu_threads 不能为负数（0 表示自动）。")
    if asr.language.strip().lower() != "fr":
        errors.append("asr.language 目前固定为 fr（本工具只做法语识别）。")
    if not 32 <= mt.max_length <= 1024:
        errors.append(f"translate.max_length 应在 32~1024 之间，当前为 {mt.max_length}。")
    if not 1 <= mt.num_beams <= 10:
        errors.append(f"translate.num_beams 应在 1~10 之间，当前为 {mt.num_beams}。")
    if mt.fallback and not (mt.pivot_model.strip() and mt.pivot_target_model.strip()):
        errors.append("translate.fallback 为 true 时，pivot_model 和 pivot_target_model 都不能为空。")
    if log_cfg.level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        errors.append(f"logging.level 必须是 DEBUG/INFO/WARNING/ERROR 之一，当前为 {log_cfg.level}。")
    if not log_cfg.file.strip():
        errors.append("logging.file 不能为空。")

    if errors:
        source_text = "、".join(sources) if sources else "内置默认配置"
        bullet = "\n".join(f"  - {e}" for e in errors)
        raise ConfigError(
            f"配置有误（来源：{source_text}）：\n{bullet}\n"
            "请对照 config.example.yaml 修改后重试。"
        )

    return AppConfig(
        audio=audio,
        vad=vad,
        asr=asr,
        translate=mt,
        logging=log_cfg,
        raw=dict(data),
        sources=list(sources),
    )


def _section(data: Mapping[str, Any], name: str, errors: list[str]) -> dict[str, Any]:
    value = data.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        errors.append(f"{name} 节必须是“键: 值”形式。")
        return {}
    return dict(value)


def _get_str(section: Mapping[str, Any], section_name: str, key: str, default: str, errors: list[str]) -> str:
    if key not in section:
        return default
    value = section[key]
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    errors.append(f"{section_name}.{key} 应为字符串，当前为 {value!r}。")
    return default


def _get_int(section: Mapping[str, Any], section_name: str, key: str, default: int, errors: list[str]) -> int:
    if key not in section:
        return default
    value = section[key]
    if isinstance(value, bool):
        errors.append(f"{section_name}.{key} 应为整数，当前为布尔值 {value!r}。")
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    errors.append(f"{section_name}.{key} 应为整数，当前为 {value!r}。")
    return default


def _get_float(section: Mapping[str, Any], section_name: str, key: str, default: float, errors: list[str]) -> float:
    if key not in section:
        return default
    value = section[key]
    if isinstance(value, bool):
        errors.append(f"{section_name}.{key} 应为数字，当前为布尔值 {value!r}。")
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    errors.append(f"{section_name}.{key} 应为数字，当前为 {value!r}。")
    return default


def _get_bool(section: Mapping[str, Any], section_name: str, key: str, default: bool, errors: list[str]) -> bool:
    if key not in section:
        return default
    value = section[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    errors.append(f"{section_name}.{key} 应为 true 或 false，当前为 {value!r}。")
    return default


# ---------------------------------------------------------------------------
# 用户配置写回
# ---------------------------------------------------------------------------
def update_user_config(paths: AppPaths, updates: Mapping[str, Any]) -> str | None:
    """把用户级配置合并写回 ``%LOCALAPPDATA%``。

    返回 ``None`` 表示成功，否则返回面向用户的中文错误说明。
    写失败不应中断主流程，调用方可以选择只打印警告。
    """

    existing: dict[str, Any] = {}
    if paths.user_config.is_file():
        try:
            existing = _read_yaml(paths.user_config)
        except ConfigError:
            existing = {}
    _deep_merge(existing, updates)

    text = (
        "# fr-translator 用户配置（程序自动维护，可手工编辑）\n"
        "# 该文件只保存设备选择等本机信息，不会写入便携包。\n"
        + yaml.safe_dump(existing, allow_unicode=True, sort_keys=False)
    )
    try:
        paths.user_dir.mkdir(parents=True, exist_ok=True)
        tmp = paths.user_config.with_name(paths.user_config.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, paths.user_config)
    except OSError as exc:
        return f"无法保存用户配置（{paths.user_config}）：{exc.strerror or exc}"
    return None


# ---------------------------------------------------------------------------
# 运行环境（必须在大模型库导入之前调用）
# ---------------------------------------------------------------------------
def resolve_offline(
    paths: AppPaths,
    config: AppConfig,
    *,
    force_online: bool = False,
    force_offline: bool = False,
) -> tuple[bool, list[tuple[str, str]]]:
    """决定本次运行是否离线，并设置 Hugging Face 相关环境变量。

    规则：
    * full 包（模型齐全）默认离线；
    * lite 包首次运行允许联网下载；
    * 用户显式 ``--offline`` 时强制离线，缺模型就报错；
    * 用户显式 ``--online`` 时强制联网（可用于补全损坏的模型）。

    返回值第二项是 [(级别, 提示文本), ...]，级别为 info / warning。
    """

    has_asr = paths.has_whisper(config.asr.model)
    has_mt = has_translation_models(paths, config)
    notes: list[tuple[str, str]] = []

    if force_offline:
        offline = True
        if not (has_asr and has_mt):
            notes.append(("warning", "已按 --offline 强制离线，但本地模型不完整，稍后会提示缺哪个模型。"))
    elif force_online:
        offline = False
        notes.append(("info", "已按 --online 允许联网，缺失的模型会自动下载。"))
    else:
        offline = has_asr and has_mt
        if not offline:
            notes.append(("warning", "本地模型不完整，本次运行允许联网下载缺失的模型。"))
            notes.append(("info", "国内网络可设置 HF_ENDPOINT=https://hf-mirror.com 使用镜像。"))

    apply_model_environment(paths, offline)
    return offline, notes


def apply_model_environment(paths: AppPaths, offline: bool) -> None:
    """在导入 huggingface_hub / transformers / faster-whisper 之前调用。"""

    os.environ["HF_HOME"] = str(paths.models)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(paths.hub)
    os.environ["HF_HUB_OFFLINE"] = "1" if offline else "0"
    os.environ["TRANSFORMERS_OFFLINE"] = "1" if offline else "0"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ["TRANSFORMERS_VERBOSITY"] = "error"
    os.environ["TQDM_DISABLE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def detect_edition(paths: AppPaths, config: AppConfig) -> str:
    """full / lite 只影响错误提示文案。"""

    env = os.environ.get("FR_TRANSLATOR_EDITION", "").strip().lower()
    if env in {"full", "lite"}:
        return env
    if paths.has_whisper(config.asr.model) and has_translation_models(paths, config):
        return "full"
    return "lite"


def has_translation_models(paths: AppPaths, config: AppConfig) -> bool:
    """直译模型或两段式回退模型任一齐备即可。"""

    if paths.has_translation(config.translate.model):
        return True
    return paths.has_translation(config.translate.pivot_model) and paths.has_translation(
        config.translate.pivot_target_model
    )


def model_missing_message(edition: str, model_desc: str) -> str:
    if edition == "full":
        return (
            f"{model_desc}缺失：模型文件不完整，请重新下载 full 包。\n"
            "（full 包应包含 models/ 下的全部模型；也可以改用 --online 联网补全。）"
        )
    return (
        f"{model_desc}缺失：请联网后重试，或设置 HF_ENDPOINT=https://hf-mirror.com 使用镜像。\n"
        "（lite 包首次运行需要联网下载模型，下载完成后即可离线使用。）"
    )
