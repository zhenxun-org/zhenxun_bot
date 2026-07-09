from __future__ import annotations

from collections.abc import Callable
from typing import Any
from typing_extensions import Self

from pydantic import BaseModel

from zhenxun.services.ai.context.memory.compression import MemoryPolicy
from zhenxun.services.ai.context.memory.models import (
    ContextCompressionConfig,
    IngestionConfig,
    LongTermConfig,
    MemoryConfig,
    MemorySlot,
    ShortTermConfig,
    SlotMemoryConfig,
)
from zhenxun.services.ai.context.memory.storage.interfaces import (
    BaseChatContext,
    BaseMemoryIngestionMiddleware,
    BaseSlotContext,
)
from zhenxun.services.ai.context.memory.types import (
    AutoRecallPolicy,
)
from zhenxun.services.ai.context.rag.backends import Embedder, StorageBackend
from zhenxun.services.ai.context.rag.engine import ScopedRAGClient
from zhenxun.services.ai.utils.scope import ScopeBuilder


class MemoryBuilder:
    """
    记忆配置的链式构建器 (Fluent Builder)。
    """

    def __init__(self):
        """
        初始化 MemoryBuilder 实例。

        创建一个默认关闭短期和长期记忆，并包含默认上下文压缩配置的构建器。
        """
        self._config = MemoryConfig(
            short_term=ShortTermConfig(enable=False),
            slots=SlotMemoryConfig(enable=False),
            long_term=LongTermConfig(enable=False),
            compression=ContextCompressionConfig(),
            ingestion=IngestionConfig(),
        )

    @classmethod
    def auto(cls) -> MemoryBuilder:
        """
        创建一个开箱即用的默认记忆配置构建器。

        默认开启隔离的短期记忆，并使用 LLM 对话摘要进行上下文压缩。
        """
        return cls().with_short_term(enable=True).with_llm_summary()

    @classmethod
    def resolve(
        cls, memory: bool | MemoryConfig | MemoryBuilder | None
    ) -> MemoryConfig:
        if isinstance(memory, MemoryConfig):
            return memory
        if isinstance(memory, cls):
            return memory.build()
        if isinstance(memory, bool):
            return MemoryConfig(
                short_term=ShortTermConfig(enable=memory),
                long_term=LongTermConfig(enable=memory),
            )

        return MemoryConfig(short_term=ShortTermConfig(enable=False))

    def with_base_isolation(self, isolation: ScopeBuilder) -> Self:
        """设置顶层基准隔离级别，短期/中期/长期记忆将默认继承此级别"""
        self._config.base_isolation = isolation
        self._config.short_term.isolation = isolation
        return self

    def with_short_term(
        self,
        enable: bool = True,
        isolation: ScopeBuilder | None = None,
        backend: str | BaseChatContext | None = None,
    ) -> Self:
        """
        配置短期对话历史记忆。

        参数:
            enable: 是否开启短期记忆。
            isolation: 记忆隔离级别 (ScopeBuilder)，决定会话历史记录的区分范围。
            backend: 短期记忆存储后端实例，如果为 None 则使用全局默认后端。
        """
        self._config.short_term.enable = enable
        if isolation is not None:
            self._config.base_isolation = isolation
            self._config.short_term.isolation = isolation
        if backend is not None:
            self._config.short_term.backend = backend
        return self

    def with_slots(
        self,
        enable: bool = True,
        scopes: dict[str, ScopeBuilder] | None = None,
        default_slots: list[MemorySlot] | None = None,
        backend: str | BaseSlotContext | None = None,
        instructions: str | None = None,
    ) -> Self:
        """
        配置核心槽位记忆 (Memory Slots)。

        参数:
            enable: 是否启用槽位记忆。
            scopes: 语义化作用域映射字典。如果只有一个键值对，则大模型不可见该参数。
            default_slots: 首次初始化时自动写入的默认槽位列表。
            backend: 槽位记忆存储后端，如果为 None 则使用全局默认后端。
            instructions: 覆写内置槽位管理工具箱的默认系统提示词规则。
        """
        self._config.slots.enable = enable
        if scopes is not None:
            self._config.slots.scopes = scopes
        if default_slots is not None:
            self._config.slots.default_slots = default_slots
        if backend is not None:
            self._config.slots.backend = backend
        if instructions is not None:
            self._config.slots.instructions = instructions
        return self

    def with_long_term(
        self,
        enable: bool = True,
        scopes: dict[str, ScopeBuilder] | None = None,
        engine: ScopedRAGClient | None = None,
        backend: str | StorageBackend | None = None,
        embedder: Embedder | str | None = None,
        agentic: bool = True,
        auto_recall: AutoRecallPolicy = False,
        instructions: str | None = None,
    ) -> Self:
        """
        配置长期向量记忆与 RAG 设定。

        参数:
            enable: 是否启用长期记忆。
            scopes: 语义化作用域映射字典。如果只有一个键值对，则大模型不可见该参数。
            engine: 高级 RAG 检索引擎实例 (推荐)。若提供，将接管记忆的底层检索、混合与重排。
            backend: 长期记忆存储后端。
            embedder: 用于向量化的文本嵌入模型实例。
            agentic: 是否开启主动智能体记忆管理 (增删改查工具自动注入)。
            auto_recall: 长期记忆的自动召回策略，支持 bool 或 Callable 函数。
            instructions: 覆写内置长期记忆工具箱的默认系统提示词规则。
        """  # noqa: E501
        self._config.long_term.enable = enable
        self._config.long_term.engine = engine
        if scopes is not None:
            self._config.long_term.scopes = scopes
        self._config.long_term.backend = backend
        self._config.long_term.embedder = embedder
        self._config.long_term.agentic = agentic
        self._config.long_term.auto_recall = auto_recall
        if instructions is not None:
            self._config.long_term.instructions = instructions
        return self

    def with_multimodal_window(self, window_size: int = 5) -> Self:
        """
        配置多模态历史视窗大小。

        超出此窗口的图片/视频等富媒体消息会自动转换为纯文本占位符，以节省 Token 预算。

        参数:
            window_size: 允许保留多模态信息的最新的对话轮数。
        """
        self._config.compression.vision_window = window_size
        return self

    def with_llm_summary(
        self,
        trigger_tokens: int = 4000,
        max_turns: int = 0,
        keep_recent_turns: int = 0,
        summarization_model: str | None = None,
        summarization_prompt: str = "请概括以下对话内容，保留关键的约束条件、用户偏好、已完成的任务状态和未解决的问题。",  # noqa: E501
    ) -> Self:
        """
        配置使用大模型自然语言总结作为上下文压缩策略。

        参数:
            trigger_tokens: 触发压缩的 Token 门槛。
            max_turns: 压缩策略作用的最大历史对话轮数上限。
            keep_recent_turns: 在大模型总结之外，强制保留的最近原始对话轮数。
            summarization_model: 负责生成总结的大模型名称。
            summarization_prompt: 生成总结时所使用的系统提示词。
        """
        self._config.compression.policy = MemoryPolicy.llm_summarize(
            trigger_tokens=trigger_tokens,
            max_turns=max_turns,
            keep_recent_turns=keep_recent_turns,
            summarization_model=summarization_model,
            summarization_prompt=summarization_prompt,
        )
        return self

    def with_structured_summary(
        self,
        trigger_tokens: int = 4000,
        max_turns: int = 0,
        keep_recent_turns: int = 0,
        summarization_model: str | None = None,
        response_model: type[BaseModel] | None = None,
        prompt_template: str | None = None,
        format_callback: Callable[[Any], str] | None = None,
    ) -> Self:
        """
        配置使用自定义结构化 JSON 提取作为上下文压缩策略。

        参数:
            trigger_tokens: 触发压缩的 Token 门槛。
            max_turns: 压缩策略作用的最大历史对话轮数上限。
            keep_recent_turns: 强制保留的最近原始对话轮数。
            summarization_model: 负责生成结构化总结的大模型名称。
            response_model: (可选) 自定义的 Pydantic 数据模型，用于指导提取的结构。
            prompt_template: (可选) 提取提示词模板，
                支持 {prev_summary} 和 {dialogue} 变量。
            format_callback: (可选) 将提取出的 Pydantic 实例格式化为字符串的回调函数。
        """
        self._config.compression.policy = MemoryPolicy.structured_summarize(
            trigger_tokens=trigger_tokens,
            max_turns=max_turns,
            keep_recent_turns=keep_recent_turns,
            summarization_model=summarization_model,
            response_model=response_model,
            prompt_template=prompt_template,
            format_callback=format_callback,
        )
        return self

    def unlimited(self) -> Self:
        """
        配置为不进行任何截断和压缩的策略。
        适用于短程会话或者具备超长上下文窗口的底层语言模型。
        """
        self._config.compression.policy = MemoryPolicy.unlimited()
        return self

    def with_ingestion_middlewares(
        self, *middlewares: BaseMemoryIngestionMiddleware
    ) -> Self:
        """
        配置记忆入库管线中间件。
        用于在消息正式落盘前进行实体消解、隐私脱敏、自动打标签等操作。
        """
        self._config.ingestion.middlewares.extend(middlewares)
        return self

    def build(self) -> MemoryConfig:
        """
        生成最终构建好的 MemoryConfig 配置对象。
        """
        if not self._config.slots.scopes:
            self._config.slots.scopes = {"私有": self._config.base_isolation}
        if not self._config.long_term.scopes:
            self._config.long_term.scopes = {"私有": self._config.base_isolation}
        return self._config
