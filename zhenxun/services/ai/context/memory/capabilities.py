import inspect
from typing import Any

from zhenxun.services.ai.capabilities.base import AbstractCapability
from zhenxun.services.ai.context.rag.backends import Embedder, StorageBackend
from zhenxun.services.ai.context.rag.engine import ScopedRAGClient
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.tools.core.toolkit import BaseToolkit
from zhenxun.services.ai.tools.providers.builtin.memory import MemoryManagementToolkit
from zhenxun.services.ai.utils.logger import log_memory as logger
from zhenxun.services.ai.utils.runtime import ContextUtils
from zhenxun.services.ai.utils.scope import ScopeBuilder

from .manager import memory_manager
from .models import MemoryScoringConfig
from .storage.backends import MemoryScope
from .storage.interfaces import BaseSlotContext
from .types import (
    AutoRecallPolicy,
    Isolation,
    MemorySlot,
    SessionMetadata,
)


class LongTermMemoryCapability(AbstractCapability):
    """
    长期向量记忆 (RAG) 核心能力组件。
    负责静默执行自动召回 (Auto Recall)，并在必要时提供读写 RAG 数据库的工具链。
    """

    def __init__(
        self,
        engine: ScopedRAGClient | None = None,
        storage_backend: StorageBackend | None = None,
        embedder: Embedder | str | None = None,
        scopes: dict[str, ScopeBuilder] | ScopeBuilder | None = None,
        toolkit: bool | BaseToolkit = True,
        scoring_config: MemoryScoringConfig | None = None,
        auto_recall: AutoRecallPolicy = False,
        recall_limit: int = 5,
        recall_threshold: float = 0.5,
        namespace: str | None = None,
    ):
        """
        初始化长期记忆能力组件。

        参数：
            engine: RAG 客户端引擎实例。若为 None 则在运行时按需构建。
            storage_backend: RAG 向量存储后端。若为 None 则从管理器按命名空间提取。
            embedder: 嵌入模型实例或模型名称。
            scopes: 控制长期记忆的隔离级别。支持单 ScopeBuilder 或 映射字典。
            toolkit: 是否启用记忆管理工具箱，或传入自定义的工具箱实例。
            scoring_config: 记忆检索打分配置（包含时间衰减等参数）。
            auto_recall: 自动召回策略，可以是布尔值或自定义的回调函数。
            recall_limit: 自动召回的记忆条数限制。
            recall_threshold: 自动召回的相似度分数阈值。
            namespace: 指定的命名空间，用于自动路由存储后端及构建器。
        """
        self.engine = engine
        self.storage_backend = storage_backend
        self.embedder = embedder
        if isinstance(scopes, ScopeBuilder):
            self.scopes = {"默认": scopes}
        else:
            self.scopes = scopes or {"私有": Isolation.AGENT_USER()}
        self.toolkit = toolkit
        self.scoring_config = scoring_config or MemoryScoringConfig()
        self.auto_recall = auto_recall
        self.recall_limit = recall_limit
        self.recall_threshold = recall_threshold
        self.namespace = namespace

    def _build_session_meta(self, context: RunContext) -> SessionMetadata:
        scope_builder = next(iter(self.scopes.values())) if self.scopes else None
        return ContextUtils.build_session_meta(
            context=context,
            target_builder=scope_builder,
            extra_scopes=self.scopes,
            custom_namespace=self.namespace,
        )

    def _ensure_engine(self, context: RunContext) -> ScopedRAGClient | None:
        if self.engine is not None:
            return self.engine

        ns = self.namespace or getattr(context.session, "namespace", "global")

        storage_instance = self.storage_backend
        if not storage_instance:
            factory = memory_manager._storage_factories.get(
                ns
            ) or memory_manager._storage_factories.get("global")
            if factory:
                storage_instance = factory()

        if not storage_instance:
            return None

        embedder_instance = self.embedder
        if isinstance(embedder_instance, str):
            from zhenxun.services.ai.context.rag.backends.embedders import (
                DefaultEmbedder,
            )

            embedder_instance = DefaultEmbedder(model_name=embedder_instance)

        from zhenxun.services.ai.context.rag.builder import RAGBuilder

        builder = RAGBuilder(storage_instance).with_scope("/")
        if embedder_instance:
            builder.with_embedder(embedder_instance)

        builder.enable_lifecycle_scoring(
            half_life_days=self.scoring_config.recency_half_life_days,
            decay_weight=self.scoring_config.recency_weight,
            semantic_weight=self.scoring_config.semantic_weight,
            importance_weight=self.scoring_config.importance_weight,
            reinforcement_weight=self.scoring_config.reinforcement_weight,
        )

        self.engine = builder.build()
        return self.engine

    async def get_system_prompts(self, context: RunContext) -> list[str]:
        engine = self._ensure_engine(context)
        user_input = context.run.user_input
        if not user_input or not engine:
            return []

        should_recall = False
        session_meta = self._build_session_meta(context)

        if isinstance(self.auto_recall, bool):
            should_recall = self.auto_recall
        elif callable(self.auto_recall):
            try:
                res = self.auto_recall(user_input, session_meta)
                if inspect.isawaitable(res):
                    should_recall = await res
                else:
                    should_recall = bool(res)
            except Exception as e:
                logger.error(
                    f"[LongTermMemoryCapability] auto_recall 函数执行失败: {e}"
                )
                should_recall = False

        if not should_recall:
            return []

        scope = MemoryScope(rag_client=engine)
        matches = await scope.recall(
            session=session_meta,
            query=user_input,
            limit=self.recall_limit,
        )
        if matches:
            valid_matches = [m for m in matches if m.score >= self.recall_threshold]
            if valid_matches:
                fact_str = "\n".join(f"- {m.record.content}" for m in valid_matches)
                return [f"[系统补充：有关用户的长期记忆设定]\n{fact_str}"]
        return []

    async def get_tools(self, context: RunContext) -> list[Any]:
        if self.toolkit is False:
            return []

        engine = self._ensure_engine(context)
        if not engine:
            return []

        if isinstance(self.toolkit, BaseToolkit):
            tk = self.toolkit.clone_with(
                rag_client=engine, scopes=self.scopes, _namespace=self.namespace
            )
            return [tk]

        return [
            MemoryManagementToolkit(
                rag_client=engine, scopes=self.scopes, namespace=self.namespace
            )
        ]


class SlotMemoryCapability(AbstractCapability):
    """
    独立的槽位记忆 (Memory Slots) 能力组件。
    直接作为插件挂载至 Agent 的 capabilities 列表中。
    """

    def __init__(
        self,
        scopes: dict[str, ScopeBuilder] | ScopeBuilder | None = None,
        default_slots: list[MemorySlot] | None = None,
        backend: BaseSlotContext | None = None,
        toolkit: bool | BaseToolkit = True,
        namespace: str | None = None,
    ):
        """
        初始化槽位记忆能力组件。

        参数：
            scopes: 控制槽位记忆的隔离级别。支持单 ScopeBuilder 或 映射字典。
            default_slots: 默认记忆槽列表，初始时自动创建未存在的槽位。
            backend: 中期记忆槽持久化后端。若为 None 则从管理器按命名空间提取。
            toolkit: 是否启用记忆槽管理工具箱，或传入自定义的工具箱实例。
            namespace: 指定的命名空间，用于自动路由后端及工具箱。
        """
        if isinstance(scopes, ScopeBuilder):
            self.scopes = {"默认": scopes}
        else:
            self.scopes = scopes or {"私有": Isolation.AGENT_USER()}
        self.default_slots = default_slots or []
        self.backend = backend
        self.toolkit = toolkit
        self.namespace = namespace

    async def _get_slot_ctx_and_meta(self, context: RunContext):
        ns = self.namespace or getattr(context.session, "namespace", "global")
        slot_ctx = self.backend or memory_manager.get_backend("slots", namespace=ns)

        target_builder = next(iter(self.scopes.values())) if self.scopes else None
        session_meta = ContextUtils.build_session_meta(
            context=context,
            target_builder=target_builder,
            extra_scopes=self.scopes,
            custom_namespace=self.namespace,
        )
        return slot_ctx, session_meta

    async def get_system_prompts(self, context: RunContext) -> list[str]:
        slot_ctx, session_meta = await self._get_slot_ctx_and_meta(context)
        if not slot_ctx:
            return []

        for default_slot in self.default_slots:
            if not await slot_ctx.get_slot(session_meta, default_slot.label):
                await slot_ctx.set_slot(session_meta, default_slot)

        slots = await slot_ctx.list_pinned_slots(session_meta)
        if not slots:
            return []

        xml_parts = ["<memory_slots>"]
        for slot in slots:
            semantic_name = session_meta.scope_name_mapping.get(slot.scope, "未知")
            xml_parts.append(
                f'  <slot name="{slot.label}" scope="{semantic_name}">\n'
                f"    {slot.content}\n"
                "  </slot>"
            )
        xml_parts.append("</memory_slots>")
        return ["\n".join(xml_parts)]

    async def get_tools(self, context: RunContext) -> list[Any]:
        from zhenxun.services.ai.tools.providers.builtin.slots import MemorySlotToolkit

        if self.toolkit is False:
            return []

        if isinstance(self.toolkit, BaseToolkit):
            toolkit = self.toolkit.clone_with(
                scopes=self.scopes, backend=self.backend, _namespace=self.namespace
            )
        else:
            toolkit = MemorySlotToolkit(
                scopes=self.scopes, backend=self.backend, namespace=self.namespace
            )
        return [toolkit]
