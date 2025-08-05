from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any

from .types import LLMMessage


class BaseMemory(ABC):
    """
    记忆系统的抽象基类。
    定义了任何记忆后端都必须实现的接口。
    """

    @abstractmethod
    async def get_history(self, session_id: str) -> list[LLMMessage]:
        """根据会话ID获取历史记录。"""
        raise NotImplementedError

    @abstractmethod
    async def add_message(self, session_id: str, message: LLMMessage) -> None:
        """向指定会话添加一条消息。"""
        raise NotImplementedError

    @abstractmethod
    async def add_messages(self, session_id: str, messages: list[LLMMessage]) -> None:
        """向指定会话添加多条消息。"""
        raise NotImplementedError

    @abstractmethod
    async def clear_history(self, session_id: str) -> None:
        """清空指定会话的历史记录。"""
        raise NotImplementedError


class InMemoryMemory(BaseMemory):
    """
    一个简单的、默认的内存记忆后端。
    将历史记录存储在进程内存中的字典里。
    """

    def __init__(self, **kwargs: Any):
        self._history: dict[str, list[LLMMessage]] = defaultdict(list)

    async def get_history(self, session_id: str) -> list[LLMMessage]:
        return self._history.get(session_id, []).copy()

    async def add_message(self, session_id: str, message: LLMMessage) -> None:
        self._history[session_id].append(message)

    async def add_messages(self, session_id: str, messages: list[LLMMessage]) -> None:
        self._history[session_id].extend(messages)

    async def clear_history(self, session_id: str) -> None:
        if session_id in self._history:
            del self._history[session_id]
