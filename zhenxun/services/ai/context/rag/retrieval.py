from abc import abstractmethod
import asyncio
import time
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from zhenxun.services.ai.utils.logger import log_rag as logger
from zhenxun.utils.pydantic_compat import model_copy

from .backends.embedders import Embedder
from .models import QueryRequest, SearchResult

if TYPE_CHECKING:
    from .backends.storages import StorageBackend


@runtime_checkable
class BaseRetriever(Protocol):
    """
    检索器核心协议。
    任何实现了 retrieve 方法的对象均可作为检索器
    （不仅限于向量检索，也可包含 BM25、SQL 搜索等）。
    """

    @abstractmethod
    async def retrieve(self, request: QueryRequest) -> list[SearchResult]: ...


@runtime_checkable
class PostProcessor(Protocol):
    """后处理器协议（如重排、时间衰减打分等）。"""

    @abstractmethod
    async def process(
        self, results: list[SearchResult], query: str
    ) -> list[SearchResult]: ...


@runtime_checkable
class PreProcessor(Protocol):
    """预处理器协议（如 LLM Query 改写、意图提取等）。"""

    @abstractmethod
    async def process(self, query: str) -> list[str]:
        """接收原始查询，返回一个或多个处理/改写后的查询词"""
        ...


class FilterEvaluator:
    """纯 Python 内存求值器，用于为轻量级 Storage 提供字典精确匹配过滤"""

    @classmethod
    def evaluate(
        cls, metadata: dict[str, Any], filter_dict: dict[str, Any] | None
    ) -> bool:
        if filter_dict is None:
            return True
        return all(metadata.get(k) == v for k, v in filter_dict.items())


class VectorDBRetriever(BaseRetriever):
    """基于向量数据库的标准检索器"""

    def __init__(
        self,
        storage: "StorageBackend",
        embedder: Embedder,
        scope_prefix: str | None = None,
        score_threshold: float = 0.4,
    ):
        """
        初始化向量数据库检索器。

        参数:
            storage: 存储后端，用于执行向量相似度搜索。
            embedder: 向量嵌入模型/函数，用于将文本转换为向量。
            scope_prefix: 作用域前缀，用于限制检索范围，默认 None。
            score_threshold: 分数阈值，过滤掉相似度低于该值的检索结果，默认 0.4。
        """
        self.storage = storage
        self.embedder = embedder
        self.scope_prefix = scope_prefix
        self.score_threshold = score_threshold

    async def retrieve(self, request: QueryRequest) -> list[SearchResult]:
        if not request.embedding and request.text:
            vecs = await self.embedder(request.text, task="query")
            request.embedding = vecs[0] if vecs else None

        if not request.text.strip() and not request.embedding:
            return []

        request.search_type = "dense"
        if not request.scopes and self.scope_prefix:
            request.scopes = [self.scope_prefix]

        original_limit = request.limit
        request.limit = original_limit * 2

        results = await self.storage.search(request)

        request.limit = original_limit
        return [r for r in results if r.score >= self.score_threshold][: request.limit]


class DatabaseSparseRetriever(BaseRetriever):
    """纯数据库下沉的稀疏检索器 (Keyword/FTS)"""

    def __init__(
        self,
        storage: "StorageBackend",
        scope_prefix: str | None = None,
        score_threshold: float = 0.0,
    ):
        """
        初始化数据库稀疏检索器。

        参数:
            storage: 存储后端，用于执行全文检索/关键词检索。
            scope_prefix: 作用域前缀，用于限制检索范围，默认 None。
            score_threshold: 分数阈值，过滤掉相关度低于该值的检索结果，默认 0.0。
        """
        self.storage = storage
        self.scope_prefix = scope_prefix
        self.score_threshold = score_threshold

    async def retrieve(self, request: QueryRequest) -> list[SearchResult]:
        if not request.text.strip():
            return []

        request.search_type = "sparse"
        if not request.scopes and self.scope_prefix:
            request.scopes = [self.scope_prefix]

        original_limit = request.limit
        request.limit = original_limit * 2

        results = await self.storage.search(request)
        request.limit = original_limit
        return [r for r in results if r.score > self.score_threshold][: request.limit]


class RerankRetriever(BaseRetriever):
    """带大模型交叉注意力重排的高阶检索器 (Decorator Pattern)"""

    def __init__(
        self,
        base_retriever: BaseRetriever,
        model_name: str | None = None,
        top_n: int = 5,
        oversample_factor: int = 2,
        min_oversample: int = 20,
    ):
        """
        初始化重排检索器。

        参数:
            base_retriever: 基础检索器，用于初筛。
            model_name: 重排模型的名称，默认 None。
            top_n: 重排后保留的前 N 个文档数，默认 5。
            oversample_factor: 过采样系数，决定初筛检索的文档数量倍数，默认 2。
            min_oversample: 最小过采样文档数，默认 20。
        """
        self.base_retriever = base_retriever
        self.model_name = model_name
        self.top_n = top_n
        self.oversample_factor = oversample_factor
        self.min_oversample = min_oversample

    async def retrieve(self, request: QueryRequest) -> list[SearchResult]:
        req_clone = model_copy(request, deep=True)
        req_clone.limit = max(
            request.limit * self.oversample_factor, self.min_oversample
        )

        initial_results = await self.base_retriever.retrieve(req_clone)

        if not initial_results:
            return []

        docs: list[str | dict[str, str]] = [
            res.record.content for res in initial_results
        ]

        from zhenxun.services.ai.llm.api import rerank

        try:
            reranked = await rerank(
                query=request.text,
                documents=docs,
                top_n=min(request.limit, self.top_n),
                model=self.model_name,
            )
        except Exception as e:
            logger.warning(f"Rerank 重排请求失败，将降级返回初筛结果: {e}")
            return initial_results[: request.limit]

        final_results = []
        for rr in reranked:
            original_res = initial_results[rr.index]
            original_res.score = rr.relevance_score
            final_results.append(original_res)

        return final_results


class PipelineRetriever(BaseRetriever):
    """支持挂载多个后处理器的流水线检索器"""

    def __init__(
        self,
        base_retriever: BaseRetriever,
        post_processors: list[PostProcessor] | None = None,
        pre_processors: list[PreProcessor] | None = None,
    ):
        """
        初始化流水线检索器。

        参数:
            base_retriever: 基础检索器，执行最初的检索过程。
            post_processors: 后处理器列表，用于对检索到的结果进行重排、过滤等后处理，默认 None。
            pre_processors: 预处理器列表，用于对查询词进行改写、扩展等预处理，默认 None。
        """  # noqa: E501
        self.base_retriever = base_retriever
        self.post_processors = post_processors or []
        self.pre_processors = pre_processors or []

    async def retrieve(self, request: QueryRequest) -> list[SearchResult]:
        requests_to_search = [request]

        if request.text.strip():
            processed_texts = [request.text]
            for pp in self.pre_processors:
                new_texts = []
                for t in processed_texts:
                    new_texts.extend(await pp.process(t))
                processed_texts = new_texts

            if len(processed_texts) > 1 or (
                len(processed_texts) == 1 and processed_texts[0] != request.text
            ):
                requests_to_search = []
                for pt in processed_texts:
                    new_req = model_copy(request, deep=True)
                    new_req.text = pt
                    requests_to_search.append(new_req)

        all_results = []
        seen_ids = set()

        for req in requests_to_search:
            original_limit = req.limit
            req.limit = original_limit * 2
            res = await self.base_retriever.retrieve(req)
            req.limit = original_limit

            for r in res:
                if r.record.id not in seen_ids:
                    seen_ids.add(r.record.id)
                    all_results.append(r)

        results = sorted(all_results, key=lambda x: x.score, reverse=True)

        for pp in self.post_processors:
            results = await pp.process(results, request.text)

        return results[: request.limit]


class LifecyclePostProcessor(PostProcessor):
    """生命周期后处理器（融合时间衰减与惰性访问强化）"""

    def __init__(
        self,
        half_life_days: int = 30,
        decay_weight: float = 0.3,
        semantic_weight: float = 0.7,
        importance_weight: float = 0.0,
        reinforcement_weight: float = 0.2,
    ):
        """
        初始化生命周期后处理器。

        参数:
            half_life_days: 记忆衰减半衰期天数，控制信息随时间的降权速度，默认 30。
            decay_weight: 时间衰减得分的权重，默认 0.3。
            semantic_weight: 语义相关度得分的权重，默认 0.7。
            importance_weight: 信息重要性得分的权重，默认 0.0。
            reinforcement_weight: 惰性访问强化（如访问次数得分）的权重，默认 0.2。
        """
        self.half_life_days = half_life_days
        self.decay_weight = decay_weight
        self.semantic_weight = semantic_weight
        self.importance_weight = importance_weight
        self.reinforcement_weight = reinforcement_weight

    async def process(
        self, results: list[SearchResult], query: str
    ) -> list[SearchResult]:
        now = time.time()
        import math

        for res in results:
            created_at = res.record.metadata.get("created_at", now)
            importance = res.record.metadata.get("importance", 0.5)
            access_count = res.record.metadata.get("access_count", 0)
            last_accessed_at = res.record.metadata.get("last_accessed_at", created_at)
            age_days = max(0.0, (now - last_accessed_at) / 86400.0)
            decay = 0.5 ** (age_days / self.half_life_days)
            access_score = min(1.0, math.log1p(access_count) / 5.0)
            res.score = (
                (self.semantic_weight * res.score)
                + (self.decay_weight * decay)
                + (self.importance_weight * importance)
                + (self.reinforcement_weight * access_score)
            )

        results.sort(key=lambda x: x.score, reverse=True)
        return results


class HybridRetriever(BaseRetriever):
    """
    双轨混合检索器 (Hybrid Search Engine)。
    并发调用 Dense (VectorDB) 和 Sparse (BM25)，并使用倒数秩融合 (RRF) 算法合并结果。
    """

    def __init__(
        self,
        dense_retriever: BaseRetriever,
        sparse_retriever: BaseRetriever,
        dense_weight: float = 0.7,
        sparse_weight: float = 0.3,
        rrf_k: int = 60,
        oversample_factor: int = 2,
        min_oversample: int = 20,
    ):
        """
        初始化双轨混合检索器。

        参数:
            dense_retriever: 稠密向量检索器，用于语义召回。
            sparse_retriever: 稀疏文本检索器，用于关键词召回（如 BM25）。
            dense_weight: 稠密向量检索的加权权重，默认 0.7。
            sparse_weight: 稀疏文本检索的加权权重，默认 0.3。
            rrf_k: 倒数秩融合(RRF)算法中的常数参数，默认 60。
        """
        self.dense_retriever = dense_retriever
        self.sparse_retriever = sparse_retriever
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight
        self.rrf_k = rrf_k
        self.oversample_factor = oversample_factor
        self.min_oversample = min_oversample

    async def retrieve(self, request: QueryRequest) -> list[SearchResult]:
        oversample_limit = max(
            request.limit * self.oversample_factor, self.min_oversample
        )

        dense_req = model_copy(request, deep=True)
        dense_req.limit = oversample_limit
        dense_req.search_type = "dense"

        sparse_req = model_copy(request, deep=True)
        sparse_req.limit = oversample_limit
        sparse_req.search_type = "sparse"

        results = await asyncio.gather(
            self.dense_retriever.retrieve(dense_req),
            self.sparse_retriever.retrieve(sparse_req),
            return_exceptions=True,
        )

        for res in results:
            if isinstance(res, ImportError):
                raise res

        dense_res = (
            cast(list[SearchResult], results[0])
            if not isinstance(results[0], BaseException)
            else []
        )
        sparse_res = (
            cast(list[SearchResult], results[1])
            if not isinstance(results[1], BaseException)
            else []
        )

        if isinstance(results[0], BaseException):
            logger.error(f"[HybridSearch] 向量检索异常: {results[0]}")
        if isinstance(results[1], BaseException):
            logger.error(f"[HybridSearch] BM25 检索异常: {results[1]}")

        rrf_scores: dict[str, float] = {}
        merged_records = {}

        for rank, res in enumerate(dense_res):
            record_id = res.record.id
            merged_records[record_id] = res.record
            rrf_score = 1.0 / (self.rrf_k + rank + 1)
            rrf_scores[record_id] = rrf_scores.get(record_id, 0.0) + (
                self.dense_weight * rrf_score
            )

        for rank, res in enumerate(sparse_res):
            record_id = res.record.id
            merged_records[record_id] = res.record
            rrf_score = 1.0 / (self.rrf_k + rank + 1)
            rrf_scores[record_id] = rrf_scores.get(record_id, 0.0) + (
                self.sparse_weight * rrf_score
            )

        max_possible_score = (self.dense_weight * (1.0 / (self.rrf_k + 1))) + (
            self.sparse_weight * (1.0 / (self.rrf_k + 1))
        )

        final_results = []
        for record_id, score in sorted(
            rrf_scores.items(), key=lambda x: x[1], reverse=True
        ):
            normalized_score = (
                score / max_possible_score if max_possible_score > 0 else 0.0
            )
            final_results.append(
                SearchResult(record=merged_records[record_id], score=normalized_score)
            )

        logger.debug(
            f"⚖️ [HybridSearch] 融合完成: "
            f"Dense({len(dense_res)}) + Sparse({len(sparse_res)}) "
            f"-> Merged({len(final_results)}), 截取 Top {request.limit}"
        )
        return final_results[: request.limit]
