from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from zhenxun.services.ai.context.memory.types import (
    MemorySlot,
    SessionMetadata,
)
from zhenxun.services.ai.core.messages import AgentMessage, LLMMessage

if TYPE_CHECKING:
    from zhenxun.services.ai.context.memory.manager import GlobalMemoryManager
    from zhenxun.services.ai.context.memory.storage.interfaces import (
        BaseChatContext,
        BaseSlotContext,
    )


class ChatHistoryFacade:
    """短期对话历史门面"""

    def __init__(self, manager: "GlobalMemoryManager", session_meta: SessionMetadata):
        self.manager = manager
        self.session_meta = session_meta

    @property
    def _backend(self) -> "BaseChatContext | None":
        return self.manager.get_chat_context(
            None, self.session_meta.namespace or "global"
        )

    async def get(self, limit: int | None = None) -> list[LLMMessage]:
        """获取当前会话的历史消息"""
        if not self._backend:
            return []
        msgs = await self._backend.get_messages(self.session_meta)
        return msgs[-limit:] if limit else msgs

    async def add(self, messages: Sequence[AgentMessage] | AgentMessage) -> None:
        """向当前会话追加一条或多条历史消息"""
        if not self._backend:
            return
        from zhenxun.services.ai.core.engine.context_renderer import ContextConverter

        msgs = messages if isinstance(messages, Sequence) else [messages]
        flattened = ContextConverter.flatten_to_llm_messages(msgs)
        if flattened:
            await self._backend.add_messages(self.session_meta, flattened)

    async def clear(self) -> None:
        """清空当前会话的短期对话历史"""
        if not self._backend:
            return
        await self._backend.clear(self.session_meta)


class SlotFacade:
    """中期记忆槽门面"""

    def __init__(self, manager: "GlobalMemoryManager", session_meta: SessionMetadata):
        self.manager = manager
        self.session_meta = session_meta

    @property
    def _backend(self) -> "BaseSlotContext | None":
        """获取底层槽位存储后端"""
        return self.manager.get_slot_context(
            None, self.session_meta.namespace or "global"
        )

    async def get(self, label: str) -> str | None:
        """获取指定标识的槽位记忆内容"""
        if not self._backend:
            return None
        slot = await self._backend.get_slot(self.session_meta, label)
        return slot.content if slot else None

    async def set(
        self,
        label: str,
        content: str,
        scope: Literal["session", "global"] = "session",
        size_limit: int = 2000,
        pinned: bool = True,
    ) -> None:
        """设置或更新指定的槽位记忆"""
        if not self._backend:
            return
        slot = MemorySlot(
            label=label,
            content=content,
            scope=scope,
            size_limit=size_limit,
            pinned=pinned,
        )
        await self._backend.set_slot(self.session_meta, slot)

    async def delete(
        self, label: str, scope: Literal["session", "global"] = "session"
    ) -> None:
        """删除指定的槽位记忆"""
        if not self._backend:
            return
        await self._backend.delete_slot(self.session_meta, label, scope)

    async def list_all(self) -> dict[str, str]:
        """获取当前会话所有被置顶的槽位记忆"""
        if not self._backend:
            return {}
        slots = await self._backend.list_pinned_slots(self.session_meta)
        return {s.label: s.content for s in slots}


class AgentSessionFacade:
    """
    提供给第三方开发者的会话记忆访问聚合门面 (Facade)。
    """

    def __init__(self, manager: "GlobalMemoryManager", session_meta: SessionMetadata):
        self.manager = manager
        self.session_meta = session_meta
        self.history = ChatHistoryFacade(manager, session_meta)
        self.slots = SlotFacade(manager, session_meta)

    async def clear_all(self) -> None:
        """一键清空当前会话下的短期对话历史与记忆槽"""
        cleaner = self.manager.cleaner().session(self.session_meta.session_id)
        await cleaner.clear_short_term()
        await cleaner.clear_slots()
