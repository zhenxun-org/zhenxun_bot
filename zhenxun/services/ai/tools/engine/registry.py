from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
import inspect
from typing import Any, Generic, SupportsIndex, TypeVar, cast, overload
from typing_extensions import Self

from nonebot.utils import is_coroutine_callable

from zhenxun.services.ai.core.exceptions import ConfigurationException
from zhenxun.services.ai.core.protocols.tool import (
    ToolExecutable,
    ToolProvider,
)
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.tools.core.toolkit import BaseToolkit
from zhenxun.services.ai.tools.models import Query, ResolvedToolPayload
from zhenxun.services.log import logger
from zhenxun.utils.utils import infer_plugin_namespace

T = TypeVar("T", bound=ToolExecutable)


class ToolCollection(list[T], Generic[T]):
    """支持按索引和按名称获取的工具集合 (List + Dict)"""

    def __init__(self, iterable: Iterable[T] | None = None):
        """初始化工具集合。"""
        super().__init__(iterable or [])
        self._name_cache: dict[str, T] = {}
        self._build_name_cache()

    def _build_name_cache(self) -> None:
        """构建工具名称小写到工具实例的映射缓存。"""
        self._name_cache = {}
        for tool in self:
            name = getattr(tool, "name", None)
            if name:
                self._name_cache[name.lower()] = tool

    @overload
    def __getitem__(self, key: SupportsIndex) -> T: ...

    @overload
    def __getitem__(self, key: slice) -> list[T]: ...

    @overload
    def __getitem__(self, key: str) -> T: ...

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            return self._name_cache[key.lower()]
        return super().__getitem__(key)

    @overload
    def __setitem__(self, key: SupportsIndex, value: T) -> None: ...

    @overload
    def __setitem__(self, key: slice, value: Iterable[T]) -> None: ...

    @overload
    def __setitem__(self, key: str, value: T) -> None: ...

    def __setitem__(self, key: Any, value: Any) -> None:
        if isinstance(key, str):
            name = key.lower()
            if name in self._name_cache:
                old_tool = self._name_cache[name]
                try:
                    idx = super().index(old_tool)
                    super().__setitem__(idx, value)
                except ValueError:
                    super().append(value)
            else:
                super().append(value)
            self._name_cache[name] = value
        else:
            super().__setitem__(key, value)
            self._name_cache[value.name.lower()] = value

    def get(self, key: str, default: Any = None) -> T | Any:
        """通过名称获取工具，若不存在则返回默认值。"""
        return self._name_cache.get(key.lower(), default)

    def append(self, object: T) -> None:
        """向集合中添加工具，并更新名称缓存。"""
        name = object.name
        if name.lower() in self._name_cache:
            old_tool = self._name_cache[name.lower()]
            try:
                idx = super().index(old_tool)
                super().__setitem__(idx, object)
            except ValueError:
                super().append(object)
        else:
            super().append(object)
        self._name_cache[name.lower()] = object

    def extend(self, iterable: Iterable[T]) -> None:
        """批量添加工具到集合中。"""
        for t in iterable:
            self.append(t)

    def remove(self, value: T) -> None:
        """从集合中移除指定工具，并同步更新缓存。"""
        super().remove(value)
        name = getattr(value, "name", None)
        if name and name.lower() in self._name_cache:
            del self._name_cache[name.lower()]

    def pop(self, index: SupportsIndex = -1) -> T:
        """弹出指定位置的工具，并从缓存中移除。"""
        tool = super().pop(index)
        name = getattr(tool, "name", None)
        if name and name.lower() in self._name_cache:
            del self._name_cache[name.lower()]
        return tool

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

    def clear(self) -> None:
        """清空集合及所有名称缓存。"""
        super().clear()
        self._name_cache.clear()

    def keys(self):
        """获取所有工具名称缓存的键。"""
        return self._name_cache.keys()

    def values(self):
        """获取所有已缓存的工具实例。"""
        return self._name_cache.values()

    def items(self):
        """获取所有工具名称与实例的键值对。"""
        return self._name_cache.items()


class _StringResolver:
    """字符串格式的工具路由解析器。

    负责解析像 'ns.tool_name'、'ns.*' 或 'ns.#tag' 的语法路由。
    """

    def __init__(
        self, name: str, manager: "ToolProviderManager", default_namespace: str
    ):
        """初始化字符串路由解析器。"""
        self.name = name
        self.manager = manager
        self.default_namespace = default_namespace

    async def resolve(self, context: RunContext | None = None) -> ResolvedToolPayload:
        """解析字符串路由并返回匹配的工具载荷。"""
        if self.name in self.manager._macro_resolvers:
            resolver = self.manager._macro_resolvers[self.name]
            resolved = (
                await resolver() if is_coroutine_callable(resolver) else resolver()
            )
            return await self.manager._normalize_to_resolver(
                resolved, self.default_namespace
            ).resolve(context)

        s = self.name

        if "." in s:
            ns, target = s.split(".", 1)
        else:
            ns = self.default_namespace
            target = s

        from zhenxun.services.ai.tools.models import Query

        if target == "*":
            query = Query(namespace=ns)
        elif target.startswith("#"):
            tags = [t for t in target.split("#") if t]
            query = Query(tags=tags, namespace=ns)
        else:
            query = Query(name=target, namespace=ns)

        logger.debug(f"🔍 [StringRouter] 语法解析: '{self.name}' -> {query}")
        return await _QueryResolver(
            query, self.manager, self.default_namespace
        ).resolve(context)


class _QueryResolver:
    """Query 查询对象格式的工具路由解析器。

    负责根据 namespace、标签或工具名称检索匹配的工具。
    """

    def __init__(
        self, query: Query, manager: "ToolProviderManager", default_namespace: str
    ):
        """初始化查询对象路由解析器。"""
        self.query = query
        self.manager = manager
        self.default_namespace = default_namespace

    async def resolve(self, context: RunContext | None = None) -> ResolvedToolPayload:
        """执行查询以解析并返回匹配的工具载荷。"""
        payload = ResolvedToolPayload()
        namespaces_to_search = []

        target_namespace = self.query.namespace or self.default_namespace

        if target_namespace == "global":
            namespaces_to_search = list(self.manager._namespaced_tools.keys())
        elif target_namespace:
            namespaces_to_search = [target_namespace]
        else:
            raise ValueError(f"Query 对象必须显式指定 namespace 作用域: {self.query}")

        for ns in namespaces_to_search:
            if ns in self.manager._namespaced_tools:
                for tool in self.manager._namespaced_tools[ns]:
                    if self.query.match(tool):
                        p = await tool.resolve(context)
                        if p:
                            payload.tools.extend(p.tools)
                            payload.injected_prompts.extend(p.injected_prompts)
                            payload.toolkits.extend(p.toolkits)

        if self.query.name and not payload.tools and not self.query.tags:
            specific = await self.manager.resolve_specific_tools([self.query.name])
            for t in specific:
                if self.query.match(t):
                    payload.tools.append(t)

        return payload


class _CallableResolver:
    """普通 Python 函数/可调用对象格式的工具路由解析器。

    负责将其包装为 FunctionTool 实例。
    """

    def __init__(self, func: Callable, manager: "ToolProviderManager"):
        """初始化可调用对象路由解析器。"""
        self.func = func
        self.manager = manager

    async def resolve(self, context: RunContext | None = None) -> ResolvedToolPayload:
        """将可调用对象转换为函数工具并返回其解析载荷。"""
        for candidate in (
            getattr(self.func, "__tool_name__", None),
            getattr(self.func, "__name__", None),
        ):
            if candidate:
                for ns_tools in self.manager._namespaced_tools.values():
                    if t := ns_tools.get(candidate):
                        return await t.resolve(context)
        from zhenxun.services.ai.tools.core.tool import FunctionTool

        t = FunctionTool(func=self.func)
        return await t.resolve(context)


class _TypeAdapterResolver:
    """基于自定义类型映射注册的工具路由解析器。负责调用对应类型的解析函数。"""

    def __init__(
        self,
        item: Any,
        resolver_func: Callable,
        manager: "ToolProviderManager",
        default_namespace: str,
    ):
        """初始化类型适配器解析器。"""
        self.item = item
        self.resolver_func = resolver_func
        self.manager = manager
        self.default_namespace = default_namespace

    async def resolve(self, context: RunContext | None = None) -> ResolvedToolPayload:
        """执行类型适配器函数并解析返回对应的工具载荷。"""
        resolved = (
            await self.resolver_func(self.item)
            if is_coroutine_callable(self.resolver_func)
            else self.resolver_func(self.item)
        )
        return await self.manager._normalize_to_resolver(
            resolved, self.default_namespace
        ).resolve(context)


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

        self._providers: list[ToolProvider] = []
        self._namespaced_tools: dict[str, ToolCollection] = {}
        self._resolved_tools: ToolCollection | None = None
        self._init_lock = asyncio.Lock()
        self._init_promise: asyncio.Task | None = None
        self._initialized = True

        self._macro_resolvers: dict[str, Callable] = {}
        self._type_resolvers: dict[type, Callable] = {}

    def register_macro_resolver(self, macro_str: str, resolver_func: Callable) -> None:
        """注册宏解析器函数。"""
        self._macro_resolvers[macro_str] = resolver_func

    def register_type_resolver(
        self, target_type: type, resolver_func: Callable
    ) -> None:
        """注册特定类型的工具解析函数。"""
        self._type_resolvers[target_type] = resolver_func

    def register(self, provider: ToolProvider):
        """注册一个新的 ToolProvider。"""
        if provider not in self._providers:
            self._providers.append(provider)
            logger.debug(f"已注册工具提供者: {provider.__class__.__name__}")

    def register_tool(self, tool: ToolExecutable):
        """注册由 @tool 生成的单一工具"""
        ns = infer_plugin_namespace()
        if ns not in self._namespaced_tools:
            self._namespaced_tools[ns] = ToolCollection()
        self._namespaced_tools[ns].append(tool)
        self._resolved_tools = None

    def register_toolkit(self, toolkit: Any) -> None:
        """
        注册一个完整的 Toolkit 实例，使其可通过智能字符串路由（Tag或Name）被动态发现。
        """
        ns = infer_plugin_namespace()
        if ns not in self._namespaced_tools:
            self._namespaced_tools[ns] = ToolCollection()
        self._namespaced_tools[ns].append(toolkit)
        self._resolved_tools = None

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

    async def discover_tools(
        self,
        allowed_servers: list[str] | None = None,
        excluded_servers: list[str] | None = None,
    ) -> dict[str, ToolExecutable]:
        """向所有已初始化的 ToolProvider 并发执行工具发现。"""
        discover_tasks = []
        provider_indices = []
        for i, provider in enumerate(self._providers):
            sig = inspect.signature(provider.discover_tools)
            params_to_pass = {}
            if "allowed_servers" in sig.parameters:
                params_to_pass["allowed_servers"] = allowed_servers
            if "excluded_servers" in sig.parameters:
                params_to_pass["excluded_servers"] = excluded_servers

            discover_tasks.append(provider.discover_tools(**params_to_pass))
            provider_indices.append(i)

        results = await asyncio.gather(*discover_tasks, return_exceptions=True)

        provider_tools = {}
        for result_idx, provider_result in enumerate(results):
            provider = self._providers[provider_indices[result_idx]]
            provider_name = provider.__class__.__name__

            if isinstance(provider_result, dict):
                logger.debug(
                    f"提供者 '{provider_name}' 发现了 {len(provider_result)} 个工具。"
                )
                for name, executable in provider_result.items():
                    if provider_tools.get(name):
                        logger.warning(
                            f"发现重复的工具名称 '{name}'，后发现的将覆盖前者。"
                        )
                    provider_tools[name] = executable
            elif isinstance(provider_result, Exception):
                logger.error(
                    f"提供者 '{provider_name}' 在发现工具时出错: {provider_result}"
                )
        return provider_tools

    async def _query_engine(
        self,
        names: list[str] | None = None,
        allowed_servers: list[str] | None = None,
        excluded_servers: list[str] | None = None,
        include_providers: bool = True,
    ) -> ToolCollection:
        """统一查询引擎：收敛所有本地与云端的工具检索逻辑"""
        await self.initialize()
        resolved = ToolCollection()

        for ns_tools in self._namespaced_tools.values():
            for t in ns_tools:
                if names and t.name not in names:
                    continue
                resolved.append(t)

        if not include_providers:
            return resolved

        if names:
            missing_names = [n for n in names if not resolved.get(n)]
            for name in missing_names:
                config = {"name": name}
                for provider in self._providers:
                    try:
                        if executable := await provider.get_tool_executable(
                            name, config
                        ):
                            resolved.append(executable)
                            break
                    except Exception as exc:
                        logger.error(
                            f"provider '{provider.__class__.__name__}'"
                            f"解析工具 '{name}' 出错: {exc}"
                        )
        else:
            provider_tools = await self.discover_tools(
                allowed_servers, excluded_servers
            )
            for t in provider_tools.values():
                resolved.append(t)

        return resolved

    async def get_resolved_tools(
        self,
        allowed_servers: list[str] | None = None,
        excluded_servers: list[str] | None = None,
        namespaces: list[str] | None = None,
    ) -> ToolCollection:
        """获取已解析完成的所有可用工具集合。"""
        has_filters = (
            allowed_servers is not None
            or excluded_servers is not None
            or namespaces is not None
        )
        if not has_filters and self._resolved_tools is not None:
            return self._resolved_tools

        tools = await self._query_engine(
            allowed_servers=allowed_servers, excluded_servers=excluded_servers
        )

        if not has_filters:
            self._resolved_tools = tools
        return tools

    async def resolve_specific_tools(self, tool_names: list[str]) -> ToolCollection:
        """根据名称列表检索并返回特定的工具集合。"""
        return await self._query_engine(names=tool_names, include_providers=True)

    async def get_function_tools(
        self, names: list[str] | None = None
    ) -> ToolCollection:
        """获取本地注册的所有函数工具集合。"""
        return await self._query_engine(names=names, include_providers=False)

    def _normalize_to_resolver(self, item: Any, default_ns: str) -> Any:
        """将任意工具配置或定义包装为标准的多态解析器对象。"""
        if hasattr(item, "resolve"):
            return item

        if isinstance(item, Query):
            return _QueryResolver(item, self, default_ns)
        if isinstance(item, str):
            return _StringResolver(item, self, default_ns)
        if type(item) in self._type_resolvers:
            return _TypeAdapterResolver(
                item, self._type_resolvers[type(item)], self, default_ns
            )
        if callable(item):
            return _CallableResolver(item, self)

        if not hasattr(item, "resolve"):
            raise TypeError(
                f"严格协议校验失败: 工具对象 {type(item)} 必须实现 ToolResolvable 协议 "
                "(包含 resolve 方法)。如果你想注册普通函数，请使用 @tool 装饰器。"
            )
        return item

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

        resolvers = [self._normalize_to_resolver(t, namespace) for t in defs]

        for i, r in enumerate(resolvers):
            if asyncio.iscoroutine(r):
                r = await r
                resolvers[i] = self._normalize_to_resolver(r, namespace)

        tasks = [r.resolve(context) for r in resolvers]
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
                final_payload.tools.append(t)

            final_payload.injected_prompts.extend(p.injected_prompts)
            final_payload.toolkits.extend(p.toolkits)

        final_payload.tools = ToolCollection(final_payload.tools)
        return final_payload


tool_provider_manager = ToolProviderManager()


async def _dict_ad_hoc_resolver(config: dict):
    """针对字典类型的 ad-hoc 工具配置的类型解析器。"""
    name = config.get("name")
    if not name:
        raise ConfigurationException(
            "工具配置字典必须包含 'name' 字段。",
        )

    for provider in tool_provider_manager._providers:
        executable = await provider.get_tool_executable(name, config)
        if executable:
            return executable

    raise ConfigurationException(
        f"没有为 ad-hoc 工具 '{name}' 找到合适的提供者。",
    )


tool_provider_manager.register_type_resolver(dict, _dict_ad_hoc_resolver)
