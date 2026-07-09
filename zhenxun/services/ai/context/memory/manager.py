from collections.abc import Callable
from typing import Any, cast

from zhenxun.services.ai.context.rag.backends import Embedder, StorageBackend
from zhenxun.services.ai.utils.logger import log_memory as logger
from zhenxun.services.ai.utils.scope import BaseScopeBuilder
from zhenxun.utils.utils import infer_plugin_namespace

from .models import MemoryConfig
from .storage.backends import (
    InMemoryChatContext,
    MemoryScope,
)
from .storage.interfaces import (
    BaseChatContext,
    BaseSlotContext,
)


class MemoryCleaner(BaseScopeBuilder["MemoryCleaner"]):
    """
    声明式记忆清理构建器 (Query Builder)。
    为第三方开发者提供极端友好的链式 API，彻底屏蔽底层前缀逻辑。
    """

    def __init__(self, manager: "GlobalMemoryManager"):
        super().__init__()
        self.manager = manager
        self._config: Any = None

    def config(self, cfg: Any):
        """指定私有记忆配置（自动识别未全局注册 of 第三方私有数据库实例）"""
        self._config = cfg.build() if hasattr(cfg, "build") else cfg
        return self

    async def clear_short_term(self):
        """一键清理目标范围下的短期对话历史记忆"""
        if self._config and self._config.short_term.backend:
            await self._config.short_term.backend.clear_by_query(self._selector)
        else:
            for backend in self.manager._chat_backends.values():
                await backend.clear_by_query(self._selector)

    async def clear_slots(self):
        """一键清理目标范围下的中期记忆槽 (Memory Slots)"""
        if self._config and self._config.slots.backend:
            await self._config.slots.backend.clear_by_query(self._selector)
        else:
            for backend in self.manager._slot_backends.values():
                await backend.clear_by_query(self._selector)

    async def clear_long_term(self):
        """一键清理目标范围下的长期向量记忆 (RAG Vector Database)"""
        if self._config and self._config.long_term.backend:
            from zhenxun.services.ai.context.rag.backends import StorageBackend

            storage = cast(StorageBackend, self._config.long_term.backend)
            await storage.clear_by_query(self._selector)
        else:
            for factory in self.manager._storage_factories.values():
                storage = factory()
                if hasattr(storage, "clear_by_query"):
                    await storage.clear_by_query(self._selector)
                else:
                    await storage.delete(scope_prefix=self._selector.scope_prefix)

    async def clear_all(self):
        """一键清理指定范围下的所有生命周期记忆（对话、槽位、RAG）"""
        await self.clear_short_term()
        await self.clear_slots()
        await self.clear_long_term()
        logger.info(
            f"🧹 成功清理作用域 '{self._selector.scope_prefix}'下的所有记忆痕迹！"
        )


class GlobalMemoryManager:
    """
    全局记忆大管家 (IoC 容器)。
    使用现代化依赖注入机制管理短/长期记忆引擎的默认实例。
    """

    def __init__(self):
        self._chat_backends: dict[str, BaseChatContext] = {
            "global": InMemoryChatContext()
        }
        self._slot_backends: dict[str, BaseSlotContext] = {}

        from zhenxun.services.ai.context.rag.backends import DictStorageBackend

        self._storage_factories: dict[str, Callable[[], StorageBackend]] = {
            "global": lambda: DictStorageBackend()
        }

    def register_chat_backend(
        self, backend: BaseChatContext, scope: str | None = None
    ) -> None:
        """注册特定命名空间的短期记忆存储后端。"""
        ns = scope if scope is not None else infer_plugin_namespace()
        self._chat_backends[ns] = backend

    def register_slot_backend(
        self, backend: BaseSlotContext, scope: str | None = None
    ) -> None:
        """注册特定命名空间的中期记忆槽存储后端。"""
        ns = scope if scope is not None else infer_plugin_namespace()
        self._slot_backends[ns] = backend

    def register_storage_factory(
        self, factory: Callable[[], StorageBackend], scope: str | None = None
    ) -> None:
        """注册特定命名空间的长期记忆向量存储工厂。"""
        ns = scope if scope is not None else infer_plugin_namespace()
        self._storage_factories[ns] = factory

    def cleaner(self) -> MemoryCleaner:
        """获取声明式记忆清理构建器，供第三方开发者极速清理指定记忆"""
        return MemoryCleaner(self)

    def get_embedder(self, embedder_val: "Embedder | str | None") -> Embedder | None:
        """获取向量化引擎实例。如果传入的是字符串，则视为 API 模型名称。"""
        if not embedder_val:
            return None

        if isinstance(embedder_val, str):
            from zhenxun.services.ai.context.rag.backends.embedders import (
                DefaultEmbedder,
            )

            return DefaultEmbedder(model_name=embedder_val)

        return embedder_val

    def get_chat_context(
        self, config: MemoryConfig | None, namespace: str = "global"
    ) -> BaseChatContext | None:
        """根据配置分配对应的短期对话历史实例"""
        if not config or not config.short_term.enable:
            return None

        backend_cfg = config.short_term.backend
        if backend_cfg is not None:
            return cast(BaseChatContext, backend_cfg)

        return self._chat_backends.get(namespace) or self._chat_backends["global"]

    def get_slot_context(
        self, config: MemoryConfig | None, namespace: str = "global"
    ) -> BaseSlotContext | None:
        """根据配置分配对应的槽位记忆实例"""
        if not config or not config.slots.enable:
            return None

        backend_cfg = config.slots.backend
        if backend_cfg is not None:
            return cast(BaseSlotContext, backend_cfg)

        return self._slot_backends.get(namespace) or self._slot_backends["global"]

    def get_long_term_memory(
        self, config: MemoryConfig | None, namespace: str = "global"
    ) -> MemoryScope | None:
        """根据声明式配置动态组装长期向量记忆实例"""
        if not config or not config.long_term.enable:
            return None

        if config.long_term.engine is not None:
            return MemoryScope(
                rag_client=config.long_term.engine,
            )

        storage_instance = None
        backend_cfg = config.long_term.backend
        if backend_cfg is not None:
            storage_instance = cast(StorageBackend, backend_cfg)
        else:
            factory = (
                self._storage_factories.get(namespace)
                or self._storage_factories["global"]
            )
            storage_instance = factory()

        embedder = self.get_embedder(config.long_term.embedder)

        from zhenxun.services.ai.context.rag.builder import RAGBuilder

        builder = RAGBuilder(storage_instance).with_scope("/")
        if embedder:
            builder.with_embedder(embedder)

        from .models import MemoryScoringConfig

        scoring_cfg = MemoryScoringConfig()

        builder.enable_lifecycle_scoring(
            half_life_days=scoring_cfg.recency_half_life_days,
            decay_weight=scoring_cfg.recency_weight,
            semantic_weight=scoring_cfg.semantic_weight,
            importance_weight=scoring_cfg.importance_weight,
            reinforcement_weight=scoring_cfg.reinforcement_weight,
        )

        client = builder.build()

        return MemoryScope(
            rag_client=client,
        )


memory_manager = GlobalMemoryManager()
