from typing import Any

from zhenxun.services.ai.capabilities.base import AbstractCapability
from zhenxun.services.ai.context.memory.models import MemoryConfig
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.tools.providers.builtin.memory import MemoryManagementToolkit


class AgenticMemoryCapability(AbstractCapability):
    """
    智能体主动记忆管理能力 (Agentic Memory Management)。
    当 `MemoryConfig.long_term.enable == True` 且 `agentic == True` 时隐式挂载，
    在运行时动态组装并向大模型提供 `MemoryManagementToolkit` 工具箱。
    """

    def __init__(self, memory_config: MemoryConfig, namespace: str):
        self.memory_config = memory_config
        self.namespace = namespace

    async def get_tools(self, context: RunContext) -> list[Any]:
        kwargs = self.memory_config.long_term.toolkit_kwargs.copy()
        kwargs["memory_config"] = self.memory_config
        kwargs["namespace"] = self.namespace
        if self.memory_config.long_term.instructions is not None:
            kwargs["instructions"] = self.memory_config.long_term.instructions

        toolkit = MemoryManagementToolkit(**kwargs)
        return [toolkit]


class SlotMemoryCapability(AbstractCapability):
    """
    槽位记忆能力组件。
    当 `MemoryConfig.slots.enable == True` 时隐式挂载，
    在运行时动态组装并向大模型提供 `MemorySlotToolkit` 工具箱。
    """

    def __init__(self, memory_config: MemoryConfig, namespace: str):
        self.memory_config = memory_config
        self.namespace = namespace

    async def get_tools(self, context: RunContext) -> list[Any]:
        from zhenxun.services.ai.tools.providers.builtin.slots import MemorySlotToolkit

        kwargs = self.memory_config.slots.toolkit_kwargs.copy()
        kwargs["memory_config"] = self.memory_config
        kwargs["namespace"] = self.namespace
        if self.memory_config.slots.instructions is not None:
            kwargs["instructions"] = self.memory_config.slots.instructions

        toolkit = MemorySlotToolkit(**kwargs)
        return [toolkit]

