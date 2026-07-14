from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from zhenxun.services.ai.context.memory.types import (
    MemorySlot,
    SessionMetadata,
)
from zhenxun.services.ai.core.messages import AgentMessage, LLMMessage
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.utils.scope import ScopeSelector


@runtime_checkable
class IClearableBackend(Protocol):
    """支持声明式清理的作用域后端协议"""

    async def clear_by_query(self, query: ScopeSelector) -> int | None: ...


class BaseChatContext(IClearableBackend, ABC):
    """短期对话历史记忆接口"""

    @abstractmethod
    async def get_messages(self, session: SessionMetadata) -> list[LLMMessage]:
        """获取当前会话的所有历史消息。"""
        ...

    @abstractmethod
    async def search(
        self, query: str, session: SessionMetadata, limit: int = 10
    ) -> list[LLMMessage]:
        """根据查询词搜索当前会话的历史消息。"""
        ...

    @abstractmethod
    async def add_messages(
        self, session: SessionMetadata, messages: list[LLMMessage]
    ) -> None:
        """向当前会话追加消息。"""
        ...

    @abstractmethod
    async def set_messages(
        self, session: SessionMetadata, messages: list[LLMMessage]
    ) -> None:
        """重置并设置当前会话的消息列表。"""
        ...

    @abstractmethod
    async def clear(self, session: SessionMetadata) -> None:
        """清空当前会话的历史消息。"""
        ...


class BaseSlotContext(IClearableBackend, ABC):
    """中期记忆槽持久化接口"""

    @abstractmethod
    async def get_slot(self, session: SessionMetadata, label: str) -> MemorySlot | None:
        """获取指定会话下特定标签的记忆槽。"""
        ...

    @abstractmethod
    async def set_slot(self, session: SessionMetadata, slot: MemorySlot) -> None:
        """设置或更新指定会话下的记忆槽。"""
        ...

    @abstractmethod
    async def delete_slot(
        self, session: SessionMetadata, label: str, scope: str
    ) -> None:
        """删除指定会话下特定标签和作用域的记忆槽。"""
        ...

    @abstractmethod
    async def list_pinned_slots(self, session: SessionMetadata) -> list[MemorySlot]:
        """列出当前会话所有固定的记忆槽。"""
        ...

    @abstractmethod
    async def list_all_slots(self, session: SessionMetadata) -> list[MemorySlot]:
        """列出当前会话的所有记忆槽（包括未置顶的）。"""
        ...


class BaseMemoryReducer(ABC):
    """记忆压缩器基类"""

    @abstractmethod
    async def reduce(
        self,
        messages: list[LLMMessage],
        current_tokens: int,
        model_name: str,
        base_overhead: int = 0,
    ) -> tuple[list[LLMMessage], bool, int]:
        """
        执行记忆压缩处理，精简或提炼对话上下文以降低 Token 消耗。

        参数:
            messages: 需要进行压缩的原始 LLM 消息历史列表。
            current_tokens: 压缩前消息列表的当前 Token 总数。
            model_name: 用于判定压缩阈值或计算 Token 的底层大模型名称。
            base_overhead: 基础系统提示词等静态开销的 Token 计数。

        返回:
            tuple[list[LLMMessage], bool, int]: 包含压缩后的新消息历史列表、
                本次是否实际触发了压缩的布尔标记、以及压缩后的新 Token 总数。
        """
        ...


class BaseMemoryIngestionMiddleware(ABC):
    """记忆入库中间件基类，在写入数据库前拦截并修改/清洗消息"""

    @abstractmethod
    async def process(
        self, messages: Sequence[AgentMessage], context: RunContext
    ) -> list[AgentMessage]: ...
