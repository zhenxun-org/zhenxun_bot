from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .backends import Embedder, StorageBackend
from .ingestion import ChunkingStrategy
from .retrieval import (
    BaseRetriever,
    PostProcessor,
    PreProcessor,
)

StorageConfigType = dict[str, Any]


class ChunkingConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    strategy: ChunkingStrategy | None = None
    """文档切块策略"""


class DedupConfig(BaseModel):
    enable: bool = True
    """是否开启入库去重"""
    threshold: float = 0.98
    """去重相似度阈值"""


class RerankConfig(BaseModel):
    enable: bool = False
    """是否开启重排"""
    model_name: str | None = None
    """重排使用的模型名称"""
    top_n: int = 5
    """重排后保留的文档数量"""


class HybridSearchConfig(BaseModel):
    enable: bool = False
    """是否开启双轨混合检索及 RRF 融合"""
    dense_weight: float = 0.7
    """向量检索权重"""
    sparse_weight: float = 0.3
    """BM25 检索权重"""


class LifecycleConfig(BaseModel):
    enable: bool = False
    """是否开启生命周期打分(时间衰减+访问强化)后处理"""
    half_life_days: int = 30
    """半衰期天数"""
    decay_weight: float = 0.3
    """时间衰减权重"""
    semantic_weight: float = 0.7
    """语义相似度权重"""
    importance_weight: float = 0.0
    """重要性打分权重"""
    reinforcement_weight: float = 0.2
    """访问强化得分权重"""


class RAGConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    storage: StorageBackend | None = None
    """存储后端"""
    embedder: Embedder | None = None
    """向量化引擎"""
    custom_retriever: BaseRetriever | None = None
    """自定义召回器"""
    scopes: str | list[str] = "/"
    """数据隔离作用域"""
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    """文档切块配置"""
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    """去重配置"""

    rerank: RerankConfig = Field(default_factory=RerankConfig)
    """重排配置"""
    hybrid: HybridSearchConfig = Field(default_factory=HybridSearchConfig)
    """混合检索与 RRF 融合配置"""
    lifecycle: LifecycleConfig = Field(default_factory=LifecycleConfig)
    """生命周期打分配置"""
    pre_processors: list[PreProcessor] = Field(default_factory=list)
    """自定义查询前处理器列表"""
    post_processors: list[PostProcessor] = Field(default_factory=list)
    """自定义检索后处理器列表"""
