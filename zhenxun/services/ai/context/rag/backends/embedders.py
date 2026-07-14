from abc import ABC, abstractmethod
import asyncio
import threading
from typing import Any, Literal, Protocol, runtime_checkable

from zhenxun.services.ai.core.messages import EmbedBatch
from zhenxun.services.ai.core.options import LLMEmbeddingConfig
from zhenxun.services.ai.llm.api import embed as api_embed
from zhenxun.services.ai.message_builder import MessageBuilder
from zhenxun.services.ai.utils.logger import log_rag as logger

EmbedTaskType = Literal[
    "general", "query", "document", "similarity", "classification", "clustering"
]


@runtime_checkable
class Embedder(Protocol):
    """
    向量化引擎协议。
    任何实现了异步 __call__ 的对象或闭包函数均可作为 Embedder。
    """

    async def __call__(
        self, input_batch: Any, task: EmbedTaskType = "general", **kwargs
    ) -> list[list[float]]:
        """
        将文本、多模态或预构建的 EmbedBatch 转换为向量列表。
        """
        ...


class DefaultEmbedder(Embedder):
    """系统默认的向量化引擎，调用大模型底座 API"""

    def __init__(
        self, model_name: str | None = None, config: LLMEmbeddingConfig | None = None
    ):
        self.model_name = model_name
        self.config = config

    async def __call__(
        self, input_batch: Any, task: EmbedTaskType = "general", **kwargs
    ) -> list[list[float]]:
        if not input_batch:
            return []
        try:
            res = await api_embed(
                input_batch, model=self.model_name, task=task, config=self.config
            )
            return res.embeddings
        except Exception as e:
            logger.error(f"DefaultEmbedder 向量化失败: {e}", e=e)
            return []


class BaseLocalEmbedder(Embedder, ABC):
    """本地向量化引擎基类，统一处理多模态降级与同步推理由协程包裹逻辑。"""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model_lock = threading.Lock()
        self._model: Any | None = None

    def _ensure_model_loaded(self) -> None:
        """线程安全的懒加载机制"""
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    logger.info(
                        f"正在后台加载本地向量模型: {self.model_name} ... "
                        "(首次加载可能需要较长时间下载)"
                    )
                    self._model = self._load_model_impl()
                    logger.info(f"本地向量模型 {self.model_name} 加载完毕！")

    @abstractmethod
    def _load_model_impl(self) -> Any:
        """子类实现：执行具体的依赖导入与模型实例化，并返回模型对象。"""
        pass

    @abstractmethod
    def _encode_texts(self, texts: list[str]) -> list[list[float]]:
        """子类实现：执行同步的批量文本向量化方法。"""
        pass

    async def __call__(
        self, input_batch: Any, task: EmbedTaskType = "general", **kwargs
    ) -> list[list[float]]:
        if not input_batch:
            return []

        if isinstance(input_batch, EmbedBatch):
            batch = input_batch
        else:
            batch = await MessageBuilder.normalize_to_embed_batch(input_batch)

        texts = batch.to_text_only(f"本地模型 {self.model_name}")

        if not texts:
            return []

        def _sync_embed():
            return self._encode_texts(texts)

        return await asyncio.to_thread(_sync_embed)


class FastEmbedder(BaseLocalEmbedder):
    """
    基于 FastEmbed 的轻量级本地向量化引擎。
    零 PyTorch 依赖，CPU 推理极快。
    """

    def __init__(self, model_name: str | None = None):
        super().__init__(model_name or "BAAI/bge-small-zh-v1.5")

        import importlib.util

        if importlib.util.find_spec("fastembed") is None:
            raise ImportError(
                "⚠️ 使用 FastEmbed 需要额外依赖，请在终端执行: pip install fastembed"
            )

    def _load_model_impl(self) -> Any:
        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise ImportError(
                "⚠️ 使用 FastEmbed 需要额外依赖，请在终端执行: pip install fastembed"
            )
        return TextEmbedding(model_name=self.model_name)

    def _encode_texts(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model_loaded()
        assert self._model is not None
        return [vec.tolist() for vec in self._model.embed(texts)]


class SentenceTransformerEmbedder(BaseLocalEmbedder):
    """
    基于 Sentence-Transformers 的本地向量化引擎。
    支持 GPU 加速，适合重度用户。
    """

    def __init__(self, model_name: str | None = None):
        super().__init__(model_name or "BAAI/bge-small-zh-v1.5")

        import importlib.util

        if importlib.util.find_spec("sentence_transformers") is None:
            raise ImportError(
                "⚠️ 使用 SentenceTransformers 需要额外依赖，"
                "请在终端执行: pip install sentence-transformers"
            )

    def _load_model_impl(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "⚠️ 使用 SentenceTransformers 需要额外依赖，"
                "请在终端执行: pip install sentence-transformers"
            )
        return SentenceTransformer(self.model_name)

    def _encode_texts(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model_loaded()
        assert self._model is not None
        embeddings = self._model.encode(texts)
        return embeddings.tolist()
