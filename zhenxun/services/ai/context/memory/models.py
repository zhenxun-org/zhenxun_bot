"""
记忆域类型定义
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from zhenxun.services.ai.context.rag.backends import Embedder, StorageBackend
from zhenxun.services.ai.context.rag.engine import ScopedRAGClient
from zhenxun.services.ai.utils.scope import ScopeBuilder

from .storage.interfaces import (
    BaseChatContext,
    BaseMemoryIngestionMiddleware,
    BaseMemoryReducer,
    BaseSlotContext,
)
from .types import (
    AutoRecallPolicy,
    Isolation,
    MemorySlot,
    SessionMetadata,
)


class SlotMemoryConfig(BaseModel):
    """槽位记忆 (Memory Slots) 配置"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    enable: bool = Field(default=False)
    """是否启用中期记忆槽"""
    scopes: dict[str, ScopeBuilder] | None = Field(default=None)
    """语义化作用域映射字典，供大模型作为 Literal 选择。如果只有一个，则自动隐藏参数"""
    default_slots: list[MemorySlot] = Field(default_factory=list)
    """首次初始化时自动写入的默认槽位列表"""
    backend: str | BaseSlotContext | None = Field(default=None)
    """
    指定底层槽位记忆数据库注册名称，或直接传入 BaseSlotContext 实例。
    为空则使用全局默认
    """
    instructions: str | None = Field(default=None)
    """覆写内置槽位管理工具箱的系统提示词"""
    toolkit_kwargs: dict[str, Any] = Field(default_factory=dict)
    """透传给底层 MemorySlotToolkit 的高级参数 (如 prefix, exclude, shared_options)"""


class MemoryScoringConfig(BaseModel):
    """长期记忆的复合打分与检索配置"""

    recency_weight: float = Field(default=0.3)
    """时间衰减权重"""
    semantic_weight: float = Field(default=0.5)
    """语义相似度权重"""
    importance_weight: float = Field(default=0.2)
    """重要性权重"""
    recency_half_life_days: int = Field(default=30)
    """时间衰减的半衰期(天)"""

    reinforcement_weight: float = Field(default=0.2)
    """访问强化的加权权重 (被检索越多得分越高)"""


class ShortTermConfig(BaseModel):
    """短期对话记忆配置"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    enable: bool = Field(default=True)
    """是否启用短期对话记忆上下文"""
    backend: str | BaseChatContext | None = Field(default=None)
    """
    指定底层短期记忆数据库注册名称，或直接传入 BaseChatContext 实例。
    为空则使用全局默认
    """
    isolation: ScopeBuilder = Field(default_factory=Isolation.AGENT_USER)
    """单一的记忆隔离级别 (ScopeBuilder)，决定短期记忆存储边界"""


class LongTermConfig(BaseModel):
    """长期向量记忆配置"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    enable: bool = Field(default=False)
    """是否启用长期记忆（开启后自动赋予 Agent 存取记忆的工具，并附加 RAG 召回能力）"""
    engine: ScopedRAGClient | None = Field(default=None)
    """
    [推荐] 指定底层的高级 RAG 检索引擎实例。若传入此项，将覆盖默认的 backend
    和 embedder 配置。
    """
    backend: str | StorageBackend | None = Field(default=None)
    """
    指定底层长期向量数据库 (Storage) 注册名称，或直接传入 StorageBackend 实例。
    为空则使用全局默认
    """
    scopes: dict[str, ScopeBuilder] | None = Field(default=None)
    """语义化作用域映射字典，决定长期记忆存储边界。如果只有一个，则自动隐藏参数"""
    embedder: str | Embedder | None = Field(default=None)
    """
    指定底层向量化引擎 (Embedder) 实例，若为字符串则视为 API 模型名称。
    为空则使用全局默认
    """

    agentic: bool = Field(default=True)
    """是否赋予大模型主动管理记忆的能力 (Agentic Memory)"""
    auto_recall: AutoRecallPolicy = Field(default=False)
    """长期记忆的自动召回策略，默认 False (从不自动召回)，由大模型自主
    决定调用搜索工具"""
    recall_threshold: float = Field(default=0.5)
    """长期记忆召回的最低余弦相似度要求"""
    instructions: str | None = Field(default=None)
    """覆写内置长期记忆管理工具箱的系统提示词"""
    toolkit_kwargs: dict[str, Any] = Field(default_factory=dict)
    """透传给底层 MemoryManagementToolkit 的高级参数"""


class ContextCompressionConfig(BaseModel):
    """上下文压缩与管理配置"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    threshold: float | None = Field(default=None)
    """(局部重写) 触发记忆压缩的 Token 阈值"""
    max_history_turns: int | None = Field(default=None)
    """(局部重写) 触发记忆压缩的对话轮数上限。设为 0 表示不限制轮数。"""
    vision_window: int | None = Field(default=None)
    """多模态滑动窗口大小。0表示关闭该功能，>0表示仅保留最近N轮包含多模态数据的消息，None表示跟随全局配置。"""
    policy: list[BaseMemoryReducer] | None = Field(default=None)
    """核心记忆压缩策略管线 (List[BaseMemoryReducer])。为 None 时将应用全局默认策略。"""


class IngestionConfig(BaseModel):
    """记忆入库管线配置"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    middlewares: list[BaseMemoryIngestionMiddleware] = Field(default_factory=list)
    """入库中间件列表（按顺序依次执行清洗过滤）"""


class MemoryConfig(BaseModel):
    """统一的记忆配置项声明"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    base_isolation: ScopeBuilder = Field(default_factory=Isolation.AGENT_USER)
    """顶层基准隔离级别，短期/中期/长期记忆将默认继承此级别"""
    short_term: ShortTermConfig = Field(default_factory=ShortTermConfig)
    """短期对话记忆配置"""
    slots: SlotMemoryConfig = Field(default_factory=SlotMemoryConfig)
    """槽位记忆配置"""
    long_term: LongTermConfig = Field(default_factory=LongTermConfig)
    """长期向量记忆配置"""
    compression: ContextCompressionConfig = Field(
        default_factory=ContextCompressionConfig
    )
    """上下文压缩与管理配置"""
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    """记忆入库前的清洗与过滤管线配置"""


__all__ = [
    "AutoRecallPolicy",
    "BaseMemoryIngestionMiddleware",
    "ContextCompressionConfig",
    "IngestionConfig",
    "Isolation",
    "LongTermConfig",
    "MemoryConfig",
    "MemoryScoringConfig",
    "SessionMetadata",
    "ShortTermConfig",
]
