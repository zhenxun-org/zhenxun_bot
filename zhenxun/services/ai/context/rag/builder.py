from typing import Any

from zhenxun.services.ai.utils.logger import log_rag as logger

from .backends import Embedder, StorageBackend
from .configs import RAGConfig
from .engine import ScopedRAGClient
from .ingestion import (
    ChunkingStrategy,
    DedupNode,
    DocumentChunking,
    DynamicChunkingNode,
    EmbeddingNode,
    IndexPipeline,
    StorageCommitNode,
)
from .retrieval import (
    BaseRetriever,
    DatabaseSparseRetriever,
    HybridRetriever,
    LifecyclePostProcessor,
    PipelineRetriever,
    PostProcessor,
    PreProcessor,
    RerankRetriever,
    VectorDBRetriever,
)


class RAGBuilder:
    """
    RAG 管线组装构建器。
    使用内部状态驱动模式，内部维护私有的 RAGConfig 实例。
    """

    def __init__(
        self, storage: StorageBackend | None = None, config: RAGConfig | None = None
    ):
        """
        初始化 RAG 管线组装构建器。

        参数:
            storage: (可选) 底层存储后端实例。
            config: (可选) RAG 配置对象。若不提供，将新建默认的 RAGConfig 实例。
        """
        self._config = config or RAGConfig()
        if storage is not None:
            self._config.storage = storage

    def with_embedder(self, embedder: Embedder) -> "RAGBuilder":
        """
        设置向量化引擎。

        参数:
            embedder: 实现了向量化协议的引擎实例 (如 DefaultEmbedder, FastEmbedder)。
        """
        self._config.embedder = embedder
        return self

    def with_retriever(self, retriever: BaseRetriever) -> "RAGBuilder":
        """
        替换底层的向量库查表算法，注入自定义召回器。

        参数:
            retriever: 实现了 BaseRetriever 协议的自定义召回器实例。
        """
        self._config.custom_retriever = retriever
        return self

    def with_scope(self, scopes: str | list[str]) -> "RAGBuilder":
        """
        设置数据隔离作用域。

        参数:
            scopes: 支持单作用域前缀(字符串)或联合检索多作用域(列表)。
            如 "/" 或 ["/group_1", "/user_2"]。
        """
        self._config.scopes = scopes
        return self

    def with_chunking(self, strategy: ChunkingStrategy) -> "RAGBuilder":
        """
        设置文档切块策略。

        参数:
            strategy: 切块策略实例 (如 DocumentChunking, RecursiveCharacterChunking)。
        """
        self._config.chunking.strategy = strategy
        return self

    def enable_dedup(self, threshold: float = 0.98) -> "RAGBuilder":
        """
        开启批处理入库去重。
        在文本分块入库前，通过对比向量相似度拦截高度重复的内容。

        参数:
            threshold: 去重相似度阈值，大于该值的块将被判定为重复并丢弃。
        """
        self._config.dedup.enable = True
        self._config.dedup.threshold = threshold
        return self

    def disable_dedup(self) -> "RAGBuilder":
        """
        关闭批处理入库去重。
        """
        self._config.dedup.enable = False
        return self

    def enable_rerank(self, model_name: str, top_n: int = 5) -> "RAGBuilder":
        """
        开启大模型交叉注意力重排。
        对初筛召回的结果使用专用的 Rerank 模型进行二次排序，极大提升召回准确率。

        参数:
            model_name: 用于重排序的模型名称 (如 'BAAI/bge-reranker-v2-m3')。
            top_n: 重排后最终保留并返回的文档数量。
        """
        self._config.rerank.enable = True
        self._config.rerank.model_name = model_name
        self._config.rerank.top_n = top_n
        return self

    def enable_hybrid_search(
        self, dense_weight: float = 0.7, sparse_weight: float = 0.3
    ) -> "RAGBuilder":
        """
        开启双轨混合检索 (Hybrid Search) 及 RRF 融合。
        并发调用向量检索 (Dense) 和关键词检索 (Sparse)，结合两者优势。

        参数:
            dense_weight: 稠密向量检索的分数计算权重。
            sparse_weight: 稀疏关键词(BM25等)检索的分数计算权重。
        """
        self._config.hybrid.enable = True
        self._config.hybrid.dense_weight = dense_weight
        self._config.hybrid.sparse_weight = sparse_weight
        return self

    def enable_lifecycle_scoring(
        self,
        half_life_days: int = 30,
        decay_weight: float = 0.3,
        semantic_weight: float = 0.7,
        importance_weight: float = 0.0,
        reinforcement_weight: float = 0.2,
    ) -> "RAGBuilder":
        """
        开启生命周期打分后处理。
        综合考虑记忆的时间新鲜度、基础语义相似度、重要性以及访问频次，模拟人类记忆遗忘曲线。

        参数:
            half_life_days: 时间衰减的半衰期(天)。经过这么多天后，时间得分衰减一半。
            decay_weight: 时间衰减得分所占权重。
            semantic_weight: 基础语义相似度所占权重。
            importance_weight: 客观重要性所占权重。
            reinforcement_weight: 访问强化(被回想次数越多越容易想起)所占权重。
        """
        self._config.lifecycle.enable = True
        self._config.lifecycle.half_life_days = half_life_days
        self._config.lifecycle.decay_weight = decay_weight
        self._config.lifecycle.semantic_weight = semantic_weight
        self._config.lifecycle.importance_weight = importance_weight
        self._config.lifecycle.reinforcement_weight = reinforcement_weight
        return self

    def add_pre_processor(self, processor: PreProcessor) -> "RAGBuilder":
        """
        挂载自定义查询前处理器。

        参数:
            processor: 实现了 PreProcessor 协议的处理器实例。
        """
        self._config.pre_processors.append(processor)
        return self

    def add_post_processor(self, processor: PostProcessor) -> "RAGBuilder":
        """
        挂载自定义检索后处理器。

        参数:
            processor: 实现了 PostProcessor 协议的处理器实例。
        """
        self._config.post_processors.append(processor)
        return self

    @classmethod
    def resolve(cls, config: RAGConfig | dict | Any | None) -> RAGConfig:
        """
        解析多种格式的配置，统一转换为 RAGConfig 对象。

        参数:
            config: RAGConfig 实例、字典或其他类型配置。
        """
        if isinstance(config, RAGConfig):
            return config
        if isinstance(config, dict):
            return RAGConfig(**config)
        return RAGConfig()

    def build(self) -> ScopedRAGClient:
        """
        完成所有积木组装，输出最终的 RAG 客户端实体。
        """
        cfg = self._config
        storage = cfg.storage
        if not storage:
            raise ValueError("RAGBuilder 必须配置 storage 才能 build。")

        embedder = cfg.embedder
        if not embedder:
            from zhenxun.services.ai.llm.manager import get_default_model

            from .backends import DefaultEmbedder

            embedder = DefaultEmbedder(model_name=get_default_model("embedding"))
            logger.debug("RAGBuilder: 未指定 Embedder，已使用系统默认 Embedder。")

        chunking_strategy = cfg.chunking.strategy or DocumentChunking()

        scopes = cfg.scopes
        nodes = [
            DynamicChunkingNode(chunking_strategy),
            EmbeddingNode(embedder),
        ]
        if cfg.dedup.enable:
            nodes.append(DedupNode(cfg.dedup.threshold))

        nodes.append(StorageCommitNode(storage))
        pipeline = IndexPipeline(nodes)

        base_retriever: BaseRetriever = cfg.custom_retriever or VectorDBRetriever(
            storage,
            embedder,
            scopes[0] if isinstance(scopes, list) else scopes,
        )

        if cfg.hybrid.enable:
            database_sparse_retriever = DatabaseSparseRetriever(
                storage, scopes[0] if isinstance(scopes, list) else scopes
            )
            base_retriever = HybridRetriever(
                dense_retriever=base_retriever,
                sparse_retriever=database_sparse_retriever,
                dense_weight=cfg.hybrid.dense_weight,
                sparse_weight=cfg.hybrid.sparse_weight,
            )

        if cfg.rerank.enable and cfg.rerank.model_name:
            base_retriever = RerankRetriever(
                base_retriever, cfg.rerank.model_name, cfg.rerank.top_n
            )

        pre_processors = list(cfg.pre_processors)

        post_processors = list(cfg.post_processors)
        if cfg.lifecycle.enable:
            post_processors.append(
                LifecyclePostProcessor(
                    half_life_days=cfg.lifecycle.half_life_days,
                    decay_weight=cfg.lifecycle.decay_weight,
                    semantic_weight=cfg.lifecycle.semantic_weight,
                    importance_weight=cfg.lifecycle.importance_weight,
                    reinforcement_weight=cfg.lifecycle.reinforcement_weight,
                )
            )

        if pre_processors or post_processors:
            base_retriever = PipelineRetriever(
                base_retriever,
                pre_processors=pre_processors,
                post_processors=post_processors,
            )

        return ScopedRAGClient(
            storage=storage,
            retriever=base_retriever,
            pipeline=pipeline,
            scopes=scopes,
            config=cfg,
        )
