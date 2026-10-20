"""Helsinki-NLP OPUS-MT 翻译封装（MarianMT）。

关于模型选择
------------
需求里指定的是 ``Helsinki-NLP/opus-mt-fr-zh``，但截至 2026-10，Hugging Face
上并不存在这个仓库（Helsinki-NLP 没有发布法语→中文的 OPUS-MT 直译模型）。
因此这里保留该 id 作为首选，并默认开启两段式回退：

    Helsinki-NLP/opus-mt-fr-en （法→英）
    Helsinki-NLP/opus-mt-en-zh （英→中）

两者都是 OPUS-MT / MarianMT，离线加载方式、显存/内存占用与直译模型相同。
如果以后官方发布了 fr-zh 直译模型，只要它在本地缓存里，程序会优先使用。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
from pathlib import Path

from .config import AppPaths, TranslateConfig, model_missing_message
from .errors import DependencyError, ModelLoadError, ModelMissingError, TranslationError

logger = logging.getLogger("fr_translator.translate")

# sentencepiece 的 C++ 实现使用窄字符文件 API，Windows 下无法打开含中文的路径。
# 分词器只需要 3~4 个小文件，复制到纯 ASCII 的临时目录即可。
_TOKENIZER_FILES = (
    "vocab.json",
    "source.spm",
    "target.spm",
    "tokenizer_config.json",
    "special_tokens_map.json",
)


class MarianTranslator:
    def __init__(
        self,
        config: TranslateConfig,
        paths: AppPaths,
        *,
        offline: bool,
        edition: str,
    ) -> None:
        self.config = config
        self.paths = paths
        self.offline = offline
        self.edition = edition
        self._torch = None
        self._mode = ""
        self._direct = None  # (tokenizer, model)
        self._pivot = None  # ((tok_fr_en, model_fr_en), (tok_en_zh, model_en_zh))

    @property
    def is_loaded(self) -> bool:
        return self._mode != ""

    @property
    def mode(self) -> str:
        return self._mode

    def describe(self) -> str:
        if self._mode == "direct":
            return f"{self.config.model}（直译）"
        if self._mode == "pivot":
            return f"{self.config.pivot_model} → {self.config.pivot_target_model}（两段式）"
        return "（未加载）"

    # -- 加载 --------------------------------------------------------------
    def load(self) -> None:
        if self._mode:
            return
        try:
            import torch  # type: ignore import-not-found
            from transformers import MarianMTModel, MarianTokenizer  # type: ignore import-not-found
        except ImportError as exc:  # pragma: no cover
            raise DependencyError(
                "缺少依赖 transformers 或 torch，无法进行翻译。\n"
                "请执行 pip install -r requirements.txt 后重试。"
            ) from exc
        self._torch = torch
        self._marian_model = MarianMTModel
        self._marian_tokenizer = MarianTokenizer

        primary_local = self.paths.has_translation(self.config.model)
        pivot_local = self.paths.has_translation(
            self.config.pivot_model
        ) and self.paths.has_translation(self.config.pivot_target_model)

        if primary_local:
            logger.info("使用本地直译模型：%s", self.config.model)
            self._direct = self._load_pair(self.config.model, local_only=True)
            self._mode = "direct"
            return

        if pivot_local:
            logger.info(
                "本地没有 %s，使用两段式模型：%s + %s",
                self.config.model,
                self.config.pivot_model,
                self.config.pivot_target_model,
            )
            self._pivot = self._load_pivot(local_only=True)
            self._mode = "pivot"
            return

        if self.offline:
            raise ModelMissingError(
                model_missing_message(
                    self.edition,
                    "翻译模型（"
                    + self.config.model
                    + " 或 "
                    + self.config.pivot_model
                    + " + "
                    + self.config.pivot_target_model
                    + "）",
                )
            )

        # 联网：先尝试直译模型，404 时回退到两段式
        try:
            self._direct = self._load_pair(self.config.model, local_only=False)
            self._mode = "direct"
            return
        except Exception as exc:
            if not self.config.fallback or not _looks_like_missing_repo(exc):
                raise ModelLoadError(
                    "翻译模型下载失败。\n"
                    "请检查网络连接，国内网络可设置 HF_ENDPOINT=https://hf-mirror.com 后重试。\n"
                    f"详细信息：{exc}"
                ) from exc
            logger.warning(
                "直译模型 %s 不可用（%s），自动改用两段式翻译。",
                self.config.model,
                exc,
            )

        try:
            self._pivot = self._load_pivot(local_only=False)
            self._mode = "pivot"
        except Exception as exc:
            raise ModelLoadError(
                "翻译模型下载失败。\n"
                f"直译模型 {self.config.model} 不存在，两段式模型也下载失败。\n"
                "请检查网络，或设置 HF_ENDPOINT=https://hf-mirror.com 后重试。\n"
                f"详细信息：{exc}"
            ) from exc

    def _load_pair(self, model_id: str, *, local_only: bool):
        local_dir = self.paths.translation_local_dir(model_id)
        if local_dir is None:
            if local_only:
                raise ModelMissingError(f"翻译模型未下载：{model_id}")
            local_dir = self._download_model(model_id)

        # 从平铺目录加载，避免 HF 缓存的 blobs/symlink 在 Windows 上
        # 因“没有创建符号链接的特权”而留下 0 字节文件。
        tokenizer_dir = _ascii_tokenizer_dir(local_dir)
        logger.info("加载翻译模型 %s（目录=%s，分词器=%s）", model_id, local_dir, tokenizer_dir)
        tokenizer = self._marian_tokenizer.from_pretrained(
            str(tokenizer_dir),
            local_files_only=True,
            clean_up_tokenization_spaces=False,
        )
        model = self._marian_model.from_pretrained(
            str(local_dir), local_files_only=True
        )
        model.to("cpu")
        model.eval()
        return tokenizer, model

    def _download_model(self, model_id: str):
        try:
            from huggingface_hub import snapshot_download  # type: ignore import-not-found
        except ImportError as exc:  # pragma: no cover
            raise DependencyError(
                "缺少依赖 huggingface-hub，无法下载翻译模型。\n"
                "请执行 pip install -r requirements.txt 后重试。"
            ) from exc

        target = self.paths.translation_dir(model_id)
        target.mkdir(parents=True, exist_ok=True)
        logger.info("下载翻译模型 %s -> %s", model_id, target)

        # 优先 safetensors（体积通常更小）；没有时再补 pytorch_model.bin。
        base_patterns = ["*.json", "*.model", "*.spm", "*.txt"]
        snapshot_download(
            repo_id=model_id,
            local_dir=str(target),
            allow_patterns=base_patterns + ["*.safetensors"],
            local_files_only=False,
        )
        if self.paths.translation_local_dir(model_id) is None:
            snapshot_download(
                repo_id=model_id,
                local_dir=str(target),
                allow_patterns=base_patterns + ["*.bin"],
                local_files_only=False,
            )

        local_dir = self.paths.translation_local_dir(model_id)
        if local_dir is None:
            raise ModelLoadError(f"翻译模型下载不完整：{model_id}（目录：{target}）")
        return local_dir

    def _load_pivot(self, *, local_only: bool):
        first = self._load_pair(self.config.pivot_model, local_only=local_only)
        second = self._load_pair(self.config.pivot_target_model, local_only=local_only)
        return first, second

    # -- 翻译 --------------------------------------------------------------
    def translate(self, text: str) -> str:
        """法语 -> 中文；空输入直接返回空串。"""

        text = (text or "").strip()
        if not text:
            return ""
        if not self._mode or self._torch is None:
            raise TranslationError("翻译模型尚未加载。")

        if self._mode == "direct":
            return self._generate(self._direct, text)

        # 两段式：法→英→中。中间结果为空时直接返回，避免把空串喂给第二个模型。
        english = self._generate(self._pivot[0], text)
        if not english:
            return ""
        return self._generate(self._pivot[1], english)

    def _generate(self, pair, text: str) -> str:
        tokenizer, model = pair
        try:
            inputs = tokenizer(
                [text],
                return_tensors="pt",
                truncation=True,
                max_length=self.config.max_length,
                padding=False,
            )
            with self._torch.inference_mode():
                generated = model.generate(
                    **inputs,
                    max_length=self.config.max_length,
                    num_beams=self.config.num_beams,
                )
            result = tokenizer.batch_decode(generated, skip_special_tokens=True)
        except Exception as exc:
            raise TranslationError(f"翻译失败：{exc}") from exc
        return (result[0] if result else "").strip()


def _ascii_tokenizer_dir(model_dir: Path) -> Path:
    """sentencepiece 打不开含中文的路径，因此把分词器文件复制到 ASCII 临时目录。"""

    if str(model_dir).isascii():
        return model_dir

    base = _ascii_temp_base()
    digest = hashlib.sha1(str(model_dir).encode("utf-8")).hexdigest()[:16]
    target = base / digest
    target.mkdir(parents=True, exist_ok=True)
    for name in _TOKENIZER_FILES:
        source = model_dir / name
        if not source.is_file():
            continue
        destination = target / name
        try:
            if (
                destination.is_file()
                and destination.stat().st_size == source.stat().st_size
                and destination.stat().st_mtime >= source.stat().st_mtime
            ):
                continue
            shutil.copy2(source, destination)
        except OSError as exc:
            logger.warning("复制分词器文件失败：%s -> %s（%s）", source, destination, exc)
    return target


def _ascii_temp_base() -> Path:
    candidates = [Path(tempfile.gettempdir())]
    public = os.environ.get("PUBLIC")
    if public:
        candidates.append(Path(public) / "fr-translator-tokenizer")
    for candidate in candidates:
        if not str(candidate).isascii():
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError:
            continue
    return Path(tempfile.gettempdir())


def _looks_like_missing_repo(exc: Exception) -> bool:
    """区分“仓库不存在/无权限”和真正的网络错误。"""

    if exc.__class__.__name__ in {"RepositoryNotFoundError", "GatedRepoError", "RevisionNotFoundError"}:
        return True
    message = str(exc).lower()
    hints = (
        "404",
        "not found",
        "repository not found",
        "is not a valid model identifier",
        "does not appear to have a file named",
    )
    return any(hint in message for hint in hints)
