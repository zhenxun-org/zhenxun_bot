"""
记忆域类型定义
"""

from pydantic import BaseModel, ConfigDict, Field

from zhenxun.services.ai.utils.scope import ScopeBuilder

from .storage.interfaces import (
    BaseChatContext,
    BaseMemoryIngestionMiddleware,
    BaseMemoryReducer,
)
from .types import (
    Isolation,
    SessionMetadata,
)


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
    short_term: ShortTermConfig = Field(default_factory=ShortTermConfig)
    """短期对话记忆配置"""
    compression: ContextCompressionConfig = Field(
        default_factory=ContextCompressionConfig
    )
    """上下文压缩与管理配置"""
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    """记忆入库前的清洗与过滤管线配置"""


__all__ = [
    "BaseMemoryIngestionMiddleware",
    "ContextCompressionConfig",
    "IngestionConfig",
    "Isolation",
    "MemoryConfig",
    "MemoryScoringConfig",
    "SessionMetadata",
    "ShortTermConfig",
]
