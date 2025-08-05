"""
工具提供者管理器

负责注册、生命周期管理（包括懒加载）和统一提供所有工具。
"""

import asyncio
from collections.abc import Callable
import inspect
from typing import Any

from pydantic import BaseModel

from zhenxun.services.log import logger
from zhenxun.utils.pydantic_compat import model_json_schema

from ..types import ToolExecutable, ToolProvider
from ..types.models import ToolDefinition, ToolResult


class FunctionExecutable(ToolExecutable):
    """一个 ToolExecutable 的实现，用于包装一个普通的 Python 函数。"""

    def __init__(
        self,
        func: Callable,
        name: str,
        description: str,
        params_model: type[BaseModel] | None,
    ):
        self._func = func
        self._name = name
        self._description = description
        self._params_model = params_model

    async def get_definition(self) -> ToolDefinition:
        if not self._params_model:
            return ToolDefinition(
                name=self._name,
                description=self._description,
                parameters={"type": "object", "properties": {}},
            )

        schema = model_json_schema(self._params_model)

        return ToolDefinition(
            name=self._name,
            description=self._description,
            parameters={
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        raw_result: Any

        if self._params_model:
            try:
                params_instance = self._params_model(**kwargs)

                if inspect.iscoroutinefunction(self._func):
                    raw_result = await self._func(params_instance)
                else:
                    loop = asyncio.get_event_loop()
                    raw_result = await loop.run_in_executor(
                        None, lambda: self._func(params_instance)
                    )
            except Exception as e:
                logger.error(
                    f"执行工具 '{self._name}' 时参数验证或实例化失败: {e}", e=e
                )
                raise
        else:
            if inspect.iscoroutinefunction(self._func):
                raw_result = await self._func(**kwargs)
            else:
                loop = asyncio.get_event_loop()
                raw_result = await loop.run_in_executor(
                    None, lambda: self._func(**kwargs)
                )

        return ToolResult(output=raw_result, display_content=str(raw_result))


class BuiltinFunctionToolProvider(ToolProvider):
    """一个内置的 ToolProvider，用于处理通过装饰器注册的函数。"""

    def __init__(self):
        self._functions: dict[str, dict[str, Any]] = {}

    def register(
        self,
        name: str,
        func: Callable,
        description: str,
        params_model: type[BaseModel] | None,
    ):
        self._functions[name] = {
            "func": func,
            "description": description,
            "params_model": params_model,
        }

    async def initialize(self) -> None:
        pass

    async def discover_tools(
        self,
        allowed_servers: list[str] | None = None,
        excluded_servers: list[str] | None = None,
    ) -> dict[str, ToolExecutable]:
        executables = {}
        for name, info in self._functions.items():
            executables[name] = FunctionExecutable(
                func=info["func"],
                name=name,
                description=info["description"],
                params_model=info["params_model"],
            )
        return executables

    async def get_tool_executable(
        self, name: str, config: dict[str, Any]
    ) -> ToolExecutable | None:
        if config.get("type") == "function" and name in self._functions:
            info = self._functions[name]
            return FunctionExecutable(
                func=info["func"],
                name=name,
                description=info["description"],
                params_model=info["params_model"],
            )
        return None


class ToolProviderManager:
    """工具提供者的中心化管理器，采用单例模式。"""

    _instance: "ToolProviderManager | None" = None

    def __new__(cls) -> "ToolProviderManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return

        self._providers: list[ToolProvider] = []
        self._resolved_tools: dict[str, ToolExecutable] | None = None
        self._init_lock = asyncio.Lock()
        self._init_promise: asyncio.Task | None = None
        self._builtin_function_provider = BuiltinFunctionToolProvider()
        self.register(self._builtin_function_provider)
        self._initialized = True

    def register(self, provider: ToolProvider):
        """注册一个新的 ToolProvider。"""
        if provider not in self._providers:
            self._providers.append(provider)
            logger.info(f"已注册工具提供者: {provider.__class__.__name__}")

    def function_tool(
        self,
        name: str,
        description: str,
        params_model: type[BaseModel] | None = None,
    ):
        """装饰器：将一个函数注册为内置工具。"""

        def decorator(func: Callable):
            if name in self._builtin_function_provider._functions:
                logger.warning(f"正在覆盖已注册的函数工具: {name}")

            self._builtin_function_provider.register(
                name=name,
                func=func,
                description=description,
                params_model=params_model,
            )
            logger.info(f"已注册函数工具: '{name}'")
            return func

        return decorator

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
        """内部初始化逻辑。"""
        logger.info(f"开始初始化 {len(self._providers)} 个工具提供者...")
        init_tasks = [provider.initialize() for provider in self._providers]
        await asyncio.gather(*init_tasks, return_exceptions=True)
        logger.info("所有工具提供者初始化完成。")

    async def get_resolved_tools(
        self,
        allowed_servers: list[str] | None = None,
        excluded_servers: list[str] | None = None,
    ) -> dict[str, ToolExecutable]:
        """
        获取所有已发现和解析的工具。
        此方法会触发懒加载初始化，并根据是否传入过滤器来决定是否使用全局缓存。
        """
        await self.initialize()

        has_filters = allowed_servers is not None or excluded_servers is not None

        if not has_filters and self._resolved_tools is not None:
            logger.debug("使用全局工具缓存。")
            return self._resolved_tools

        if has_filters:
            logger.info("检测到过滤器，执行临时工具发现 (不使用缓存)。")
            logger.debug(
                f"过滤器详情: allowed_servers={allowed_servers}, "
                f"excluded_servers={excluded_servers}"
            )
        else:
            logger.info("未应用过滤器，开始全局工具发现...")

        all_tools: dict[str, ToolExecutable] = {}

        discover_tasks = []
        for provider in self._providers:
            sig = inspect.signature(provider.discover_tools)
            params_to_pass = {}
            if "allowed_servers" in sig.parameters:
                params_to_pass["allowed_servers"] = allowed_servers
            if "excluded_servers" in sig.parameters:
                params_to_pass["excluded_servers"] = excluded_servers

            discover_tasks.append(provider.discover_tools(**params_to_pass))

        results = await asyncio.gather(*discover_tasks, return_exceptions=True)

        for i, provider_result in enumerate(results):
            provider_name = self._providers[i].__class__.__name__
            if isinstance(provider_result, dict):
                logger.debug(
                    f"提供者 '{provider_name}' 发现了 {len(provider_result)} 个工具。"
                )
                for name, executable in provider_result.items():
                    if name in all_tools:
                        logger.warning(
                            f"发现重复的工具名称 '{name}'，后发现的将覆盖前者。"
                        )
                    all_tools[name] = executable
            elif isinstance(provider_result, Exception):
                logger.error(
                    f"提供者 '{provider_name}' 在发现工具时出错: {provider_result}"
                )

        if not has_filters:
            self._resolved_tools = all_tools
            logger.info(f"全局工具发现完成，共找到并缓存了 {len(all_tools)} 个工具。")
        else:
            logger.info(f"带过滤器的工具发现完成，共找到 {len(all_tools)} 个工具。")

        return all_tools

    async def get_function_tools(
        self, names: list[str] | None = None
    ) -> dict[str, ToolExecutable]:
        """
        仅从内置的函数提供者中解析指定的工具。
        """
        all_function_tools = await self._builtin_function_provider.discover_tools()
        if names is None:
            return all_function_tools

        resolved_tools = {}
        for name in names:
            if name in all_function_tools:
                resolved_tools[name] = all_function_tools[name]
            else:
                logger.warning(
                    f"本地函数工具 '{name}' 未通过 @function_tool 注册，将被忽略。"
                )
        return resolved_tools


tool_provider_manager = ToolProviderManager()
