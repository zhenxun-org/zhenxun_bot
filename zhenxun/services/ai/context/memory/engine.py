from collections.abc import Sequence
from typing import cast

from zhenxun.services.ai.core.engine.context_renderer import ContextConverter
from zhenxun.services.ai.core.messages import AgentMessage
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.utils.logger import log_memory as logger
from zhenxun.utils.pydantic_compat import model_copy

from .compression import (
    CondenserPipeline,
)
from .manager import memory_manager
from .models import MemoryConfig
from .types import SessionMetadata


class SessionMemoryContext:
    """
    会话记忆门面。
    封装了当前会话记忆的读写操作、上下文压缩管线以及入库清洗中间件。
    """

    def __init__(
        self,
        session_meta: SessionMetadata,
        memory_config: MemoryConfig | None,
        context: RunContext,
    ):
        """
        初始化会话记忆门面。

        参数:
            session_meta: 会话元数据，包含 Namespace 与作用域映射等上下文信息。
            memory_config: 记忆系统的配置对象，控制长期、短期记忆的启用与逻辑。
            context: 必填的运行时上下文环境 (RunContext)，供中间件进行依赖注入。
        """
        self.session_meta = session_meta
        self.memory_config = memory_config
        self.context = context

    async def read(
        self,
        model_name: str,
        override_history: Sequence[AgentMessage] | None = None,
    ) -> list[AgentMessage]:
        """
        拉取短期对话历史，并执行 Token 压缩与管线修剪。
        """
        current_history: list[AgentMessage] = []
        if override_history is not None:
            current_history = list(override_history)

        chat_context = memory_manager.get_chat_context(
            self.memory_config,
            namespace=self.session_meta.selector.namespace or "global",
        )

        if self.memory_config and self.memory_config.short_term.enable and chat_context:
            if override_history is not None:
                flattened_override = ContextConverter.flatten_to_llm_messages(
                    override_history
                )
                await chat_context.set_messages(self.session_meta, flattened_override)
            else:
                current_history = cast(
                    list[AgentMessage],
                    await chat_context.get_messages(self.session_meta),
                )

            pipeline = CondenserPipeline.create_from_configs(
                self.memory_config, model_name
            )
            if pipeline.reducers:
                flattened_to_reduce = ContextConverter.flatten_to_llm_messages(
                    current_history
                )
                new_history, changed = await pipeline.run(
                    flattened_to_reduce, model_name=model_name, base_overhead=0
                )
                if changed:
                    await chat_context.set_messages(self.session_meta, new_history)
                    logger.info(
                        "💾 [SessionMemory] 压缩截断完毕，已同步覆写数据库。"
                        f"压缩后条数: {len(new_history)}"
                    )
                current_history = cast(list[AgentMessage], new_history)

        return current_history

    async def write(
        self,
        new_messages: Sequence[AgentMessage],
    ) -> None:
        """将新产生的对话增量，经过入库中间件清洗后保存到数据库"""
        if not new_messages:
            return

        messages_to_save = new_messages

        if self.memory_config and self.memory_config.ingestion.middlewares:
            messages_to_save = [model_copy(m, deep=True) for m in new_messages]

            for middleware in self.memory_config.ingestion.middlewares:
                try:
                    messages_to_save = await middleware.process(
                        messages_to_save, self.context
                    )
                except Exception as e:
                    logger.error(
                        f"[MemoryIngestion] 中间件 {middleware.__class__.__name__} "
                        f"执行失败: {e}",
                        e=e,
                    )

        if not messages_to_save:
            return

        chat_ctx = memory_manager.get_chat_context(
            self.memory_config,
            namespace=self.session_meta.selector.namespace or "global",
        )

        flattened_msgs = ContextConverter.flatten_to_llm_messages(
            messages_to_save, self.context
        )

        if chat_ctx and self.memory_config and self.memory_config.short_term.enable:
            if flattened_msgs:
                await chat_ctx.add_messages(self.session_meta, flattened_msgs)
