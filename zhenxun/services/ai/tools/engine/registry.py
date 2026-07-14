from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Callable, Iterable
from typing import Any, Generic, SupportsIndex, TypeVar, cast, overload
from typing_extensions import Self

from zhenxun.services.ai.core.exceptions import ConfigurationException
from zhenxun.services.ai.core.protocols.tool import (
    ToolExecutable,
    ToolProvider,
    ToolResolvable,
)
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.tools.core.tool import FunctionTool
from zhenxun.services.ai.tools.core.toolkit import BaseToolkit
from zhenxun.services.ai.tools.models import Query, ResolvedToolPayload
from zhenxun.services.ai.utils.logger import log_tool as logger
from zhenxun.services.ai.utils.utils import parse_routing_string
from zhenxun.utils.utils import infer_plugin_namespace

T = TypeVar("T", bound=ToolExecutable)


class ToolCollection(Generic[T]):
    """不可变的工具集合"""

    def __init__(self, iterable: Iterable[T] | None = None):
        """初始化工具集合。"""
        self._tuple: tuple[T, ...] = tuple(iterable) if iterable else ()
        self._name_cache: dict[str, T] = {}
        self._build_name_cache()

    def _build_name_cache(self) -> None:
        """构建工具名称小写到工具实例的映射缓存。"""
        self._name_cache.clear()
        for tool in self._tuple:
            name = getattr(tool, "name", None)
            if name:
                self._name_cache[name.lower()] = tool

    def __iter__(self):
        """获取工具元组的迭代器。"""
        return iter(self._tuple)

    def __len__(self):
        """获取工具集合中的工具数量。"""
        return len(self._tuple)

    def __bool__(self):
        """检查工具集合是否非空。"""
        return bool(self._tuple)

    @overload
    def __getitem__(self, key: SupportsIndex) -> T: ...

    @overload
    def __getitem__(self, key: slice) -> tuple[T, ...]: ...

    @overload
    def __getitem__(self, key: str) -> T: ...

    def __getitem__(self, key: Any) -> Any:
        """按索引、切片或名称获取工具。"""
        if isinstance(key, str):
            return self._name_cache[key.lower()]
        return self._tuple[key]

    def get(self, key: str, default: Any = None) -> T | Any:
        """通过名称获取工具，若不存在则返回默认值。"""
        return self._name_cache.get(key.lower(), default)

    def filter_by_names(self, names: list[str] | None = None) -> "ToolCollection[T]":
        """根据名称列表筛选并返回新的工具子集合。"""
        if names is None:
            return self
        return ToolCollection(
            [
                tool
                for name in names
                if (tool := self._name_cache.get(name.lower())) is not None
            ]
        )

    def keys(self):
        """获取所有工具名称缓存的键。"""
        return self._name_cache.keys()

    def values(self):
        """获取所有已缓存的工具实例。"""
        return self._name_cache.values()

    def items(self):
        """获取所有工具名称与实例的键值对。"""
        return self._name_cache.items()


class LocalToolProvider(ToolProvider):
    """本地工具提供者。统一管理基于 @tool 注册的普通函数或工具箱。"""

    def __init__(self):
        """初始化本地工具提供者。"""
        self._namespaced_tools: dict[str, list[Any]] = {}

    def register_tool(self, tool: ToolExecutable, namespace: str):
        """向指定命名空间注册单一工具。"""
        if namespace not in self._namespaced_tools:
            self._namespaced_tools[namespace] = []
        self._namespaced_tools[namespace].append(tool)

    def register_toolkit(self, toolkit: Any, namespace: str):
        """向指定命名空间注册工具箱。"""
        if namespace not in self._namespaced_tools:
            self._namespaced_tools[namespace] = []
        self._namespaced_tools[namespace].append(toolkit)

    async def initialize(self) -> None:
        """初始化本地工具提供者。"""
        pass

    async def discover_tools(self) -> dict[str, ToolExecutable]:
        """发现并获取所有注册的本地工具。"""
        res = {}
        for tools in self._namespaced_tools.values():
            for t in tools:
                name = getattr(t, "name", getattr(t, "__class__", type).__name__)
                res[name] = t
        return res

    async def get_tool_executable(
        self, name: str, config: dict[str, Any]
    ) -> ToolExecutable | None:
        """根据名称获取本地工具实例。"""
        tools = await self.discover_tools()
        return tools.get(name)

    async def query_tools(self, query: Query) -> list[ToolExecutable]:
        """根据查询条件检索本地工具。"""
        matched_tools = []
        target_namespace = query.namespace
        namespaces_to_search = []

        if target_namespace == "global":
            namespaces_to_search = self.get_all_namespaces()
        elif target_namespace:
            namespaces_to_search = [target_namespace]
        else:
            namespaces_to_search = self.get_all_namespaces()

        for ns in namespaces_to_search:
            for tool in self.get_tools_by_namespace(ns):
                if query.match(tool):
                    matched_tools.append(tool)

        return matched_tools

    def get_tools_by_namespace(self, namespace: str) -> list[Any]:
        """获取指定命名空间下的所有工具和工具箱。"""
        return self._namespaced_tools.get(namespace, [])

    def get_all_namespaces(self) -> list[str]:
        """获取所有已注册工具的命名空间列表。"""
        return list(self._namespaced_tools.keys())


class BaseToolResolver(ABC):
    """工具载荷解析器抽象基类协议 (责任链模式)"""

    @abstractmethod
    def match(self, item: Any) -> bool:
        """判断解析器是否匹配当前工具对象。"""
        pass

    @abstractmethod
    async def resolve(
        self,
        item: Any,
        manager: "ToolProviderManager",
        default_namespace: str,
        context: RunContext | None = None,
    ) -> ResolvedToolPayload:
        """解析工具对象并返回其载荷。"""
        pass


class ProtocolResolver(BaseToolResolver):
    """处理已经实现了 ToolResolvable 协议的对象"""

    def match(self, item: Any) -> bool:
        """检查是否实现 ToolResolvable 协议。"""
        return hasattr(item, "resolve")

    async def resolve(
        self,
        item: Any,
        manager: "ToolProviderManager",
        default_namespace: str,
        context: RunContext | None = None,
    ) -> ResolvedToolPayload:
        """直接调用对象的 resolve 方法进行解析。"""
        return await item.resolve(context)


class StringRoutingResolver(BaseToolResolver):
    """处理带语法的路由字符串"""

    def match(self, item: Any) -> bool:
        """检查是否为非宏的路由字符串。"""
        return isinstance(item, str)

    async def resolve(
        self,
        item: Any,
        manager: "ToolProviderManager",
        default_namespace: str,
        context: RunContext | None = None,
    ) -> ResolvedToolPayload:
        s = cast(str, item)
        parsed_args = parse_routing_string(s, default_namespace)
        query = Query(**parsed_args)

        logger.debug(f"🔍 [StringRouter] 语法解析: '{item}' -> {query}")
        return await manager._resolve_single(query, default_namespace, context)


class QueryResolver(BaseToolResolver):
    """处理 Query 查询对象"""

    def match(self, item: Any) -> bool:
        """检查对象是否为 Query 实例。"""
        return isinstance(item, Query)

    async def resolve(
        self,
        item: Any,
        manager: "ToolProviderManager",
        default_namespace: str,
        context: RunContext | None = None,
    ) -> ResolvedToolPayload:
        query = cast(Query, item)
        payload = ResolvedToolPayload()

        if query.namespace == "*":
            query.namespace = "global"
        elif not query.namespace:
            query.namespace = default_namespace

        matched_tools = await manager.local_provider.query_tools(query)
        for tool in matched_tools:
            if hasattr(tool, "resolve"):
                resolvable_tool = cast(ToolResolvable, tool)
                p = await resolvable_tool.resolve(context)
                if p:
                    for t in p.tools:
                        if query.match(t):
                            payload.tools.append(t)
                    payload.injected_prompts.extend(p.injected_prompts)
                    for tk in p.toolkits:
                        if tk not in payload.toolkits:
                            payload.toolkits.append(tk)
            else:
                payload.tools.append(tool)

        return payload


class CallableResolver(BaseToolResolver):
    """处理普通 Python 函数"""

    def match(self, item: Any) -> bool:
        """检查对象是否为可调用对象。"""
        return callable(item)

    async def resolve(
        self,
        item: Any,
        manager: "ToolProviderManager",
        default_namespace: str,
        context: RunContext | None = None,
    ) -> ResolvedToolPayload:
        func = cast(Callable, item)
        for candidate in (
            getattr(func, "__tool_name__", None),
            getattr(func, "__name__", None),
        ):
            if candidate:
                for ns in manager.local_provider.get_all_namespaces():
                    for t in manager.local_provider.get_tools_by_namespace(ns):
                        if getattr(t, "name", None) == candidate:
                            return await t.resolve(context)

        t = FunctionTool(func=func)
        return await t.resolve(context)


class DictToolResolver(BaseToolResolver):
    """处理字典类型的动态/按需工具配置解析器"""

    def match(self, item: Any) -> bool:
        """检查对象是否为配置字典。"""
        return isinstance(item, dict)

    async def resolve(
        self,
        item: Any,
        manager: "ToolProviderManager",
        default_namespace: str,
        context: RunContext | None = None,
    ) -> ResolvedToolPayload:
        config = cast(dict, item)
        name = config.get("name")
        if not name:
            raise ConfigurationException("工具配置字典必须包含 'name' 字段。")

        for provider in manager._providers:
            executable = await provider.get_tool_executable(name, config)
            if executable:
                return await manager._resolve_single(
                    executable, default_namespace, context
                )

        raise ConfigurationException(f"没有为 ad-hoc 工具 '{name}' 找到合适的提供者。")


class ToolProviderManager:
    """工具提供者的中心化管理器，采用单例模式。"""

    _instance: "ToolProviderManager | None" = None

    def __new__(cls) -> Self:
        """单例模式的实例创建方法。"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cast(Self, cls._instance)

    def __init__(self):
        """初始化工具提供者管理器。"""
        if hasattr(self, "_initialized") and self._initialized:
            return

        self.local_provider = LocalToolProvider()
        self._providers: list[ToolProvider] = [self.local_provider]
        self._init_lock = asyncio.Lock()
        self._init_promise: asyncio.Task | None = None
        self._initialized = True

        self._resolvers: list[BaseToolResolver] = [
            ProtocolResolver(),
            QueryResolver(),
            StringRoutingResolver(),
            DictToolResolver(),
            CallableResolver(),
        ]

    def register_resolver(self, resolver: BaseToolResolver) -> None:
        """注册自定义的工具载荷解析器（插在兜底的 CallableResolver 之前）。"""
        self._resolvers.insert(-1, resolver)

    def register(self, provider: ToolProvider):
        """注册一个新的 ToolProvider。"""
        if provider not in self._providers:
            self._providers.append(provider)
            logger.debug(f"已注册工具提供者: {provider.__class__.__name__}")

    def register_tool(self, tool: ToolExecutable):
        """注册由 @tool 生成的单一工具"""
        ns = infer_plugin_namespace()
        self.local_provider.register_tool(tool, ns)
        tags = getattr(getattr(tool, "settings", None), "tags", [])
        tag_str = f" | Tags: {tags}" if tags else ""
        tool_name = getattr(tool, "name", "unknown")
        logger.debug(f"已注册工具: '{tool_name}' -> Namespace: '{ns}'{tag_str}")

    def register_toolkit(self, toolkit: Any) -> None:
        """
        注册一个完整的 Toolkit 实例，使其可通过智能字符串路由（Tag或Name）被动态发现。
        """
        ns = infer_plugin_namespace()
        self.local_provider.register_toolkit(toolkit, ns)
        tk_name = getattr(toolkit, "__class__", type).__name__

        config = getattr(toolkit, "config", None)
        shared_options = getattr(config, "shared_options", None) if config else None
        tags = getattr(shared_options, "tags", []) if shared_options else []
        tag_str = f" | Tags: {tags}" if tags else ""
        logger.debug(f"已注册工具箱: '{tk_name}' -> Namespace: '{ns}'{tag_str}")

    async def initialize(self) -> None:
        """懒加载初始化所有已注册的 ToolProvider。"""
        if not self._init_promise:
            async with self._init_lock:
                if not self._init_promise:
                    self._init_promise = asyncio.create_task(
                        self._initialize_providers()
                    )
        await self._init_promise

    async def _initialize_providers(self) -> None:
        """并发初始化所有已注册的工具提供者。"""
        logger.info(f"开始初始化 {len(self._providers)} 个工具提供者...")
        init_tasks = [provider.initialize() for provider in self._providers]
        await asyncio.gather(*init_tasks, return_exceptions=True)
        logger.info("所有工具提供者初始化完成。")

    def _process_discovery_results(
        self, results: list[Any], provider_indices: list[int]
    ) -> dict[str, ToolExecutable]:
        """处理并合并来自多个工具提供者的并发发现结果"""
        provider_tools: dict[str, ToolExecutable] = {}
        for result_idx, provider_result in enumerate(results):
            provider = self._providers[provider_indices[result_idx]]
            provider_name = provider.__class__.__name__

            if isinstance(provider_result, dict):
                logger.debug(
                    f"提供者 '{provider_name}' 发现了 {len(provider_result)} 个工具。"
                )
                for name, executable in provider_result.items():
                    if name in provider_tools:
                        logger.warning(
                            f"发现重复的工具名称 '{name}'，后发现的将覆盖前者。"
                        )
                    provider_tools[name] = executable
            elif isinstance(provider_result, Exception):
                logger.error(
                    f"提供者 '{provider_name}' 在发现工具时出错: {provider_result}"
                )
        return provider_tools

    async def discover_tools(self) -> dict[str, ToolExecutable]:
        """向所有已初始化的 ToolProvider 并发执行工具发现。"""
        discover_tasks = []
        provider_indices = []
        for i, provider in enumerate(self._providers):
            discover_tasks.append(provider.discover_tools())
            provider_indices.append(i)

        results = await asyncio.gather(*discover_tasks, return_exceptions=True)
        provider_tools = self._process_discovery_results(results, provider_indices)
        return provider_tools

    async def _resolve_single(
        self, item: Any, default_namespace: str, context: RunContext | None
    ) -> ResolvedToolPayload:
        """通过责任链解析单个工具意图并返回载荷"""
        for r in self._resolvers:
            if r.match(item):
                return await r.resolve(item, self, default_namespace, context)
        raise TypeError(
            f"严格协议校验失败: 工具对象 {type(item)} 必须实现 ToolResolvable 协议 "
            "(包含 resolve 方法)。如果你想注册普通函数，请使用 @tool 装饰器。"
        )

    async def resolve_tools(
        self,
        tool_definitions: Iterable[Any] | None,
        namespace: str | None = None,
        context: RunContext | None = None,
    ) -> ResolvedToolPayload:
        """
        统一解析工具配置，全面采用多态解析器与并发聚合管线。
        """
        if not tool_definitions:
            return ResolvedToolPayload()

        if not namespace:
            namespace = infer_plugin_namespace()
            logger.debug(
                f"🔍 [StringRouter] 自动推断当前调用者所在插件为: '{namespace}'"
            )

        defs = []

        def _flatten(items):
            for item in items:
                if isinstance(item, list):
                    _flatten(item)
                else:
                    defs.append(item)

        _flatten(tool_definitions)

        tasks = [self._resolve_single(t, namespace, context) for t in defs]
        payloads = await asyncio.gather(*tasks, return_exceptions=False)

        final_payload = ResolvedToolPayload()
        global_toolkit = BaseToolkit(prefix="")

        for p in payloads:
            if not p:
                continue
            p = cast(ResolvedToolPayload, p)

            for t in p.tools:
                if not getattr(t, "parent_toolkit", None):
                    t.parent_toolkit = global_toolkit

            final_payload.merge(p)

        return final_payload


tool_provider_manager = ToolProviderManager()
