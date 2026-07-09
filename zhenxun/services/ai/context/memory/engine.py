from collections.abc import Sequence
from typing import Any, cast

from zhenxun.services.ai.core.engine.context_renderer import ContextConverter
from zhenxun.services.ai.core.messages import AgentMessage
from zhenxun.services.ai.utils.logger import log_memory as logger
from zhenxun.utils.pydantic_compat import model_copy

from .compression import (
    CondenserPipeline,
)
from .manager import memory_manager
from .models import MemoryConfig
from .types import SessionMetadata


class MemoryReader:
    """
    记忆读取器 (Memory Reader)。
    负责从数据库中提取短期上下文历史，召回长期的背景知识，并执行自动压缩。
    """

    def __init__(
        self, session_meta: SessionMetadata, memory_config: MemoryConfig | None
    ):
        """
        初始化记忆读取器。

        参数:
            session_meta: 会话元数据，包含 Namespace 与作用域映射等上下文信息。
            memory_config: 记忆系统的配置对象，控制长期、短期及槽位记忆的启用与逻辑。
        """
        self.session_meta = session_meta
        self.memory_config = memory_config

    async def get_long_term_context(self, user_input: str) -> str:
        """
        基于用户输入召回长期记忆（RAG），返回格式化后的背景提示词。
        """
        if (
            not self.memory_config
            or not self.memory_config.long_term.enable
            or not user_input
        ):
            return ""

        policy = self.memory_config.long_term.auto_recall
        should_recall = False

        if isinstance(policy, bool):
            should_recall = policy
        elif callable(policy):
            import inspect

            try:
                res = policy(user_input, self.session_meta)
                if inspect.isawaitable(res):
                    should_recall = await res
                else:
                    should_recall = bool(res)
            except Exception as e:
                logger.error(f"[MemoryReader] 自定义 auto_recall 函数执行失败: {e}")
                should_recall = False

        if not should_recall:
            return ""

        ltm_scope = memory_manager.get_long_term_memory(
            self.memory_config,
            namespace=self.session_meta.selector.namespace or "global",
        )
        if not ltm_scope:
            return ""

        matches = await ltm_scope.recall(session=self.session_meta, query=user_input)
        if matches:
            logger.debug(f"🧠 [MemoryReader] 长期记忆召回详情 (Query: '{user_input}'):")
            for i, m in enumerate(matches):
                logger.debug(
                    f"  [{i + 1}] 得分: {m.score:.4f} | 内容: {m.record.content}"
                )

            threshold = self.memory_config.long_term.recall_threshold
            valid_matches = [m for m in matches if m.score >= threshold]
            if not valid_matches:
                logger.debug("🧠 [MemoryReader] 召回的记忆均未达到相关性阈值，已丢弃。")
                return ""

            fact_str = "\n".join(f"- {m.record.content}" for m in valid_matches)
            logger.debug(
                f"🧠 [MemoryReader]"
                f"成功截取并注入 {len(valid_matches)} 条高价值长期记忆。"
            )
            return f"[系统补充：有关用户的长期记忆设定]\n{fact_str}"
        return ""

    async def get_slots_context(self) -> str:
        """
        读取并组装核心槽位记忆 (Memory Slots)，返回 XML 格式字符串供大模型使用。
        """
        if not self.memory_config or not self.memory_config.slots.enable:
            return ""
        slot_ctx = memory_manager.get_slot_context(
            self.memory_config,
            namespace=self.session_meta.selector.namespace or "global",
        )
        if not slot_ctx:
            return ""

        if self.memory_config.slots.default_slots:
            for default_slot in self.memory_config.slots.default_slots:
                existing = await slot_ctx.get_slot(
                    self.session_meta, default_slot.label
                )
                if not existing:
                    await slot_ctx.set_slot(self.session_meta, default_slot)

        slots = await slot_ctx.list_pinned_slots(self.session_meta)
        if not slots:
            return ""

        show_scope = False
        if (
            self.memory_config
            and self.memory_config.slots.scopes
            and len(self.memory_config.slots.scopes) > 1
        ):
            show_scope = True

        xml_parts = ["<memory_slots>"]
        for slot in slots:
            if show_scope:
                semantic_name = self.session_meta.scope_name_mapping.get(
                    slot.scope, "未知"
                )
                xml_parts.append(
                    f'  <slot name="{slot.label}" scope="{semantic_name}">\n'
                    f"    {slot.content}\n"
                    "  </slot>"
                )
            else:
                xml_parts.append(
                    f'  <slot name="{slot.label}">\n    {slot.content}\n  </slot>'
                )
        xml_parts.append("</memory_slots>")
        return "\n".join(xml_parts)

    async def get_short_term_context(
        self,
        model_name: str,
        override_history: Sequence[AgentMessage] | None = None,
    ) -> list[AgentMessage]:
        """
        拉取短期对话历史，并执行 Token 压缩。
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
                        "💾 [MemoryReader] 压缩截断完毕，已同步覆写数据库。"
                        f"压缩后条数: {len(new_history)}"
                    )
                current_history = cast(list[AgentMessage], new_history)

        return current_history


class MemoryWriter:
    """
    记忆写入器 (Memory Writer)。
    负责将对话增量安全地写入数据库。
    """

    def __init__(
        self,
        session_meta: SessionMetadata,
        memory_config: MemoryConfig | None,
        context: Any = None,
    ):
        """
        初始化记忆写入器。

        参数:
            session_meta: 会话元数据，包含 Namespace 与作用域映射等上下文信息。
            memory_config: 记忆系统的配置对象，控制记忆存入的逻辑。
            context: 运行时上下文环境，作为可选参数传入，供中间件使用，默认 None。
        """
        self.session_meta = session_meta
        self.memory_config = memory_config
        self.context = context

    async def save_new_messages(
        self,
        new_messages: Sequence[AgentMessage],
    ):
        """将新产生的对话增量保存到数据库"""
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
