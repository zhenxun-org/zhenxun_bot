from collections import defaultdict
from collections.abc import Callable
from typing import Any, cast

from zhenxun.services.ai.context.rag.backends import StorageBackend
from zhenxun.services.ai.utils.logger import log_memory as logger
from zhenxun.services.ai.utils.scope import BaseScopeBuilder
from zhenxun.utils.utils import infer_plugin_namespace

from .models import MemoryConfig
from .storage.backends import (
    InMemoryChatContext,
)
from .storage.interfaces import (
    BaseChatContext,
    IClearableBackend,
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

    async def clear_target(self, target_name: str):
        """底层派发器：定向清理指定注册名称的泛型扩展后端数据"""
        ns_dict = self.manager._backends.get(target_name, {})
        for backend in ns_dict.values():
            if isinstance(backend, IClearableBackend):
                await backend.clear_by_query(self._selector)
            else:
                logger.warning(
                    f"后端 {backend.__class__.__name__}"
                    "未实现 IClearableBackend 协议，已跳过清理。"
                )

    async def clear_short_term(self):
        """一键清理目标范围下的短期对话历史记忆"""
        if isinstance(self._config, MemoryConfig) and isinstance(
            self._config.short_term.backend, IClearableBackend
        ):
            await self._config.short_term.backend.clear_by_query(self._selector)
        else:
            await self.clear_target("chat")

    async def clear_slots(self):
        """一键清理目标范围下的记忆槽数据"""
        await self.clear_target("slots")

    async def clear_long_term(self):
        """一键清理目标范围下的长期向量记忆 (RAG Vector Database)"""
        for factory in self.manager._storage_factories.values():
            storage = factory()
            if isinstance(storage, IClearableBackend):
                await storage.clear_by_query(self._selector)

    async def clear_all(self):
        """一键清理指定范围下的所有生命周期记忆（对话、记忆槽、RAG、及其他泛型扩展后端）"""
        if isinstance(self._config, MemoryConfig) and isinstance(
            self._config.short_term.backend, IClearableBackend
        ):
            await self._config.short_term.backend.clear_by_query(self._selector)
        for target_name in self.manager._backends:
            await self.clear_target(target_name)
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
        self._backends: dict[str, dict[str, Any]] = defaultdict(dict)
        self.register_backend("chat", InMemoryChatContext(), "global")

        from zhenxun.services.ai.context.rag.backends import DictStorageBackend

        self._storage_factories: dict[str, Callable[[], StorageBackend]] = {
            "global": lambda: DictStorageBackend()
        }

    def register_backend(
        self, backend_type: str, backend: Any, scope: str | None = None
    ) -> None:
        """泛型注册：注册任意类型的存储后端"""
        ns = scope if scope is not None else infer_plugin_namespace()
        self._backends[backend_type][ns] = backend

    def get_backend(self, backend_type: str, namespace: str = "global") -> Any | None:
        """泛型获取：获取任意类型的存储后端"""
        backends = self._backends.get(backend_type, {})
        return backends.get(namespace) or backends.get("global")

    def register_chat_backend(
        self, backend: BaseChatContext, scope: str | None = None
    ) -> None:
        """注册特定命名空间的短期记忆存储后端。"""
        self.register_backend("chat", backend, scope)

    def register_storage_factory(
        self, factory: Callable[[], StorageBackend], scope: str | None = None
    ) -> None:
        """注册特定命名空间的长期记忆向量存储工厂。"""
        ns = scope if scope is not None else infer_plugin_namespace()
        self._storage_factories[ns] = factory

    def cleaner(self) -> MemoryCleaner:
        """获取声明式记忆清理构建器，供第三方开发者极速清理指定记忆"""
        return MemoryCleaner(self)

    def get_chat_context(
        self, config: MemoryConfig | None, namespace: str = "global"
    ) -> BaseChatContext | None:
        """根据配置分配对应的短期对话历史实例"""
        if not config or not config.short_term.enable:
            return None

        backend_cfg = config.short_term.backend
        if backend_cfg is not None:
            return cast(BaseChatContext, backend_cfg)

        return self.get_backend("chat", namespace)


memory_manager = GlobalMemoryManager()
