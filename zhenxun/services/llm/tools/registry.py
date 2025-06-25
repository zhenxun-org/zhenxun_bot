"""
工具注册表

负责加载、管理和实例化来自配置的工具。
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from functools import partial
from typing import TYPE_CHECKING

from pydantic import BaseModel

from zhenxun.services.log import logger

from ..types import LLMTool

if TYPE_CHECKING:
    from ..config.providers import ToolConfig
    from ..types.protocols import MCPCompatible


class ToolRegistry:
    """工具注册表，用于管理和实例化配置的工具。"""

    def __init__(self):
        self._function_tools: dict[str, LLMTool] = {}

        self._mcp_config_models: dict[str, type[BaseModel]] = {}
        if TYPE_CHECKING:
            self._mcp_factories: dict[
                str, Callable[..., AbstractAsyncContextManager["MCPCompatible"]]
            ] = {}
        else:
            self._mcp_factories: dict[str, Callable] = {}

        self._tool_configs: dict[str, "ToolConfig"] | None = None
        self._tool_cache: dict[str, "LLMTool"] = {}

    def _load_configs_if_needed(self):
        """如果尚未加载，则从主配置中加载MCP工具定义。"""
        if self._tool_configs is None:
            logger.debug("首次访问，正在加载MCP工具配置...")
            from ..config.providers import get_llm_config

            llm_config = get_llm_config()
            self._tool_configs = {tool.name: tool for tool in llm_config.mcp_tools}
            logger.info(f"已加载 {len(self._tool_configs)} 个MCP工具配置。")

    def function_tool(
        self,
        name: str,
        description: str,
        parameters: dict,
        required: list[str] | None = None,
    ):
        """
        装饰器：在代码中注册一个简单的、无状态的函数工具。

        Args:
            name: 工具的唯一名称。
            description: 工具功能的描述。
            parameters: OpenAPI格式的函数参数schema的properties部分。
            required: 必需的参数列表。
        """

        def decorator(func: Callable):
            if name in self._function_tools or name in self._mcp_factories:
                logger.warning(f"正在覆盖已注册的工具: {name}")

            tool_definition = LLMTool.create(
                name=name,
                description=description,
                parameters=parameters,
                required=required,
            )
            self._function_tools[name] = tool_definition
            logger.info(f"已在代码中注册函数工具: '{name}'")
            tool_definition.annotations = tool_definition.annotations or {}
            tool_definition.annotations["executable"] = func
            return func

        return decorator

    def mcp_tool(self, name: str, config_model: type[BaseModel]):
        """
        装饰器：注册一个MCP工具及其配置模型。

        Args:
            name: 工具的唯一名称，必须与配置文件中的名称匹配。
            config_model: 一个Pydantic模型，用于定义和验证该工具的 `mcp_config`。
        """

        def decorator(factory_func: Callable):
            if name in self._mcp_factories:
                logger.warning(f"正在覆盖已注册的 MCP 工厂: {name}")
            self._mcp_factories[name] = factory_func
            self._mcp_config_models[name] = config_model
            logger.info(f"已注册 MCP 工具 '{name}' (配置模型: {config_model.__name__})")
            return factory_func

        return decorator

    def get_mcp_config_model(self, name: str) -> type[BaseModel] | None:
        """根据名称获取MCP工具的配置模型。"""
        return self._mcp_config_models.get(name)

    def register_mcp_factory(
        self,
        name: str,
        factory: Callable,
    ):
        """
        在代码中注册一个 MCP 会话工厂，将其与配置中的工具名称关联。

        Args:
            name: 工具的唯一名称，必须与配置文件中的名称匹配。
            factory: 一个返回异步生成器的可调用对象（会话工厂）。
        """
        if name in self._mcp_factories:
            logger.warning(f"正在覆盖已注册的 MCP 工厂: {name}")
        self._mcp_factories[name] = factory
        logger.info(f"已注册 MCP 会话工厂: '{name}'")

    def get_tool(self, name: str) -> "LLMTool":
        """
        根据名称获取一个 LLMTool 定义。
        对于MCP工具，返回的 LLMTool 实例包含一个可调用的会话工厂，
        而不是一个已激活的会话。
        """
        logger.debug(f"🔍 请求获取工具定义: {name}")

        if name in self._tool_cache:
            logger.debug(f"✅ 从缓存中获取工具定义: {name}")
            return self._tool_cache[name]

        if name in self._function_tools:
            logger.debug(f"🛠️ 获取函数工具定义: {name}")
            tool = self._function_tools[name]
            self._tool_cache[name] = tool
            return tool

        self._load_configs_if_needed()
        if self._tool_configs is None or name not in self._tool_configs:
            known_tools = list(self._function_tools.keys()) + (
                list(self._tool_configs.keys()) if self._tool_configs else []
            )
            logger.error(f"❌ 未找到名为 '{name}' 的工具定义")
            logger.debug(f"📋 可用工具定义列表: {known_tools}")
            raise ValueError(f"未找到名为 '{name}' 的工具定义。已知工具: {known_tools}")

        config = self._tool_configs[name]
        tool: "LLMTool"

        if name not in self._mcp_factories:
            logger.error(f"❌ MCP工具 '{name}' 缺少工厂函数")
            available_factories = list(self._mcp_factories.keys())
            logger.debug(f"📋 已注册的MCP工厂: {available_factories}")
            raise ValueError(
                f"MCP 工具 '{name}' 已在配置中定义，但没有注册对应的工厂函数。"
                "请使用 `@tool_registry.mcp_tool` 装饰器进行注册。"
            )

        logger.info(f"🔧 创建MCP工具定义: {name}")
        factory = self._mcp_factories[name]
        typed_mcp_config = config.mcp_config
        logger.debug(f"📋 MCP工具配置: {typed_mcp_config}")

        configured_factory = partial(factory, config=typed_mcp_config)
        tool = LLMTool.from_mcp_session(session=configured_factory)

        self._tool_cache[name] = tool
        logger.debug(f"💾 MCP工具定义已缓存: {name}")
        return tool

    def get_tools(self, names: list[str]) -> list["LLMTool"]:
        """根据名称列表获取多个 LLMTool 实例。"""
        return [self.get_tool(name) for name in names]


tool_registry = ToolRegistry()
