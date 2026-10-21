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

import logging

from .config import AppPaths, TranslateConfig, model_missing_message
from .errors import DependencyError, ModelLoadError, ModelMissingError, TranslationError

logger = logging.getLogger("fr_translator.translate")


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
        _install_sentencepiece_bytes_loader()

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
        logger.info("加载翻译模型 %s（目录=%s）", model_id, local_dir)
        tokenizer = self._marian_tokenizer.from_pretrained(
            str(local_dir),
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


def _install_sentencepiece_bytes_loader() -> None:
    """让 MarianTokenizer 用字节加载 sentencepiece 模型。

    sentencepiece 的 C++ 层在 Windows 上使用窄字符文件 API：路径含中文时，
    即使 source.spm 确实存在，LoadFromFile 也会报 “Not found”。
    改为 Python 读 bytes，再调用 LoadFromSerializedProto，彻底绕开文件 API。
    """

    try:
        import sentencepiece as spm  # type: ignore import-not-found
        from transformers.models.marian import tokenization_marian  # type: ignore import-not-found
    except ImportError:  # pragma: no cover - 依赖由 load() 保证
        return

    if getattr(tokenization_marian, "_fr_translator_bytes_loader", False):
        return

    original = getattr(tokenization_marian, "load_spm", None)

    def load_spm(path, sp_model_kwargs):  # noqa: ANN001
        processor = spm.SentencePieceProcessor(**sp_model_kwargs)
        try:
            with open(path, "rb") as handle:
                processor.LoadFromSerializedProto(handle.read())
        except Exception:
            if original is None:
                raise
            # 极少数损坏文件/特殊模型，退回 transformers 原始实现
            return original(path, sp_model_kwargs)
        return processor

    tokenization_marian.load_spm = load_spm
    tokenization_marian._fr_translator_bytes_loader = True


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
