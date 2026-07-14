from collections.abc import Callable
import copy
import inspect
from typing import Any, ClassVar

from zhenxun.services.ai.core.protocols.tool import ToolExecutable
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.run.di import DependencyInjector
from zhenxun.services.ai.tools.models import (
    ResolvedToolPayload,
    ToolkitConfig,
    ToolOptions,
)
from zhenxun.utils.pydantic_compat import (
    model_copy,
    model_dump,
)

from .tool import BaseTool, FunctionTool


class BaseToolkit:
    """
    状态工具箱基类。继承此类的对象可以维持自身状态，
    其内部被 @tool 标记的方法将被自动解析为工具。
    """

    default_instructions: str = ""
    config: ToolkitConfig
    _default_config: ClassVar[ToolkitConfig] = ToolkitConfig()

    @property
    def name(self) -> str:
        """工具箱的默认名称标识（取类名），主要用于全局注册表的 Hash 与展示"""
        return self.__class__.__name__

    def __init__(
        self,
        prefix: str | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        shared_options: ToolOptions | None = None,
        config: ToolkitConfig | None = None,
        tools: list[Callable | BaseTool | ToolExecutable] | None = None,
        instructions: str | None = None,
        **kwargs: Any,
    ):
        """
        初始化工具箱实例，装配并隔离工具组件。

        参数:
            prefix: 工具名称的前缀。如果不指定，则不添加前缀。
            include: 允许注册的工具名白名单，仅在此列表中的工具才会被加载。
            exclude: 排除注册的工具名黑名单，在此列表中的工具将被忽略。
            shared_options: 所有子工具默认继承的高阶配置项 (ToolOptions)。
            config: (可选) 显式传递的全局配置对象。若传入此项，将覆盖上述所有独立配置。
            tools: 除了带有 @tool 装饰器的方法外，要额外动态注入的独立工具列表。
            instructions: 当前工具箱实例的系统提示词补充说明。
        """
        base_config = getattr(self.__class__, "_default_config", ToolkitConfig())

        if config is not None:
            merged_dict = {
                **model_dump(base_config, exclude_unset=True),
                **model_dump(config, exclude_unset=True),
            }
            self.config = ToolkitConfig(**merged_dict)
        else:
            overrides = {}
            if prefix is not None:
                overrides["prefix"] = prefix
            if include is not None:
                overrides["include"] = include
            if exclude is not None:
                overrides["exclude"] = exclude
            if shared_options is not None:
                overrides["shared_options"] = shared_options

            merged_dict = {**model_dump(base_config, exclude_unset=True), **overrides}
            self.config = ToolkitConfig(**merged_dict)

        if self.config.prefix is None:
            self.config.prefix = ""

        self._instance_filter: Callable[[BaseTool], bool] | None = None
        self._injected_tools: list[BaseTool] = []
        self._cached_tools: dict[str, BaseTool] | None = None
        self._instance_instructions = (
            instructions
            if instructions is not None
            else getattr(self, "default_instructions", "")
        )

        if tools:
            for t in tools:
                if isinstance(t, BaseTool):
                    self._injected_tools.append(t)
                elif callable(t):
                    tool_name = getattr(
                        t, "__tool_name__", getattr(t, "__name__", "unnamed_tool")
                    )
                    tool_desc = getattr(t, "__tool_desc__", "未提供描述")
                    settings = model_copy(
                        getattr(t, "__tool_settings__", ToolOptions())
                    )

                    func_tool = FunctionTool(
                        func=t,
                        name=f"{self.config.prefix}{tool_name}",
                        description=tool_desc,
                        settings=settings,
                    )
                    self._injected_tools.append(func_tool)

    def _apply_global_settings(
        self, original_name: str, settings: ToolOptions
    ) -> ToolOptions:
        """合并工具箱全局配置到单个工具的配置项中"""
        if self.config.shared_options:
            settings = self.config.shared_options.merge(settings)

        return settings

    async def before_llm_request(
        self, context: RunContext, messages: list[Any]
    ) -> None:
        """
        LLM 请求发起前的拦截钩子。允许动态修改消息列表（如在尾部注入最新状态）。

        参数:
            context: 当前 Agent 的运行时上下文。
            messages: 即将发往底层 LLM 的完整消息历史列表，支持就地修改。
        """
        pass

    async def resolve(self, context: RunContext | None = None) -> ResolvedToolPayload:
        """实现 ToolResolvable 协议，递归解析内部工具并返回标准 Payload"""
        tools_dict = await self.get_tools(context)
        resolved_tools = []

        for t in tools_dict.values():
            if hasattr(t, "resolve"):
                payload = await t.resolve(context)
                if payload and payload.tools:
                    for rt in payload.tools:
                        rt.parent_toolkit = self
                    resolved_tools.extend(payload.tools)
            else:
                t.parent_toolkit = self
                resolved_tools.append(t)

        injected_prompts = []
        if instructions := self.get_instructions():
            injected_prompts.append(instructions)

        return ResolvedToolPayload(
            tools=resolved_tools, injected_prompts=injected_prompts, toolkits=[self]
        )

    async def get_tools(self, context: RunContext | None = None) -> dict[str, BaseTool]:
        """获取该工具箱中所有合法注册的工具实例映射表"""
        if self._cached_tools is not None:
            return self._cached_tools

        tools_dict: dict[str, BaseTool] = {}
        for t in self._injected_tools:
            t.parent_toolkit = self
            if self.config.prefix and not t.name.startswith(self.config.prefix):
                t.name = f"{self.config.prefix}{t.name}"
            t.settings = self._apply_global_settings(t.name, t.settings)
            tools_dict[t.name] = t

        for name, member in inspect.getmembers(self.__class__):
            if hasattr(member, "__toolkit_tool__"):
                bound_tool = getattr(self, name)
                original_name = getattr(member, "__tool_original_name__", name)

                if (
                    self.config.include is not None
                    and original_name not in self.config.include
                ):
                    continue
                if (
                    self.config.exclude is not None
                    and original_name in self.config.exclude
                ):
                    continue

                final_tool_name = f"{self.config.prefix}{original_name}"
                bound_tool.name = final_tool_name

                bound_tool.settings = self._apply_global_settings(
                    original_name, bound_tool.settings
                )
                bound_tool.parent_toolkit = self
                tools_dict[final_tool_name] = bound_tool

        if self._instance_filter:
            tools_dict = {
                name: t for name, t in tools_dict.items() if self._instance_filter(t)
            }
        self._cached_tools = tools_dict
        return tools_dict

    def get_instructions(self) -> str | None:
        """
        获取工具箱的系统提示词说明。
        """
        text = self._instance_instructions
        if not text:
            return None
        class_name = self.__class__.__name__
        tag_name = (
            f"{self.config.prefix}{class_name}_Instructions"
            if self.config.prefix
            else f"{class_name}_Instructions"
        )
        return f"<{tag_name}>\n{text}\n</{tag_name}>"

    def clone_with(self, **kwargs: Any) -> "BaseToolkit":
        """克隆当前工具箱原型，并透明注入新的运行时属性。"""
        new_tk = copy.copy(self)
        for k, v in kwargs.items():
            setattr(new_tk, k, v)
        new_tk._cached_tools = None
        return new_tk

    def prefixed(self, prefix: str) -> "BaseToolkit":
        """克隆工具箱并为其中所有工具追加统一的前缀"""
        new_tk = copy.copy(self)
        new_tk.config = model_copy(self.config, deep=True)
        current_prefix = new_tk.config.prefix or ""
        new_tk.config.prefix = f"{current_prefix}{prefix}"
        new_tk._cached_tools = None
        return new_tk

    def filtered(self, filter_func: Callable[[BaseTool], bool]) -> "BaseToolkit":
        """克隆工具箱并通过自定义过滤器筛选其中的工具"""
        new_tk = copy.copy(self)
        new_tk.config = model_copy(self.config, deep=True)
        old_filter = self._instance_filter
        if old_filter:
            new_tk._instance_filter = lambda t: old_filter(t) and filter_func(t)
        else:
            new_tk._instance_filter = filter_func
        new_tk._cached_tools = None
        return new_tk


class CompositeToolkit(BaseToolkit):
    """
    聚合工具箱 (Composite Toolkit)。
    将一组独立的 Toolkit 组合在一起，统一其生命周期并合并其工具。
    可以为其指定统一的 prefix、全局标签或拦截器，底层会自动向内部的所有 Toolkit 透传。
    """

    def __init__(
        self,
        toolkits: list[BaseToolkit],
        config: ToolkitConfig | None = None,
        **kwargs: Any,
    ):
        super().__init__(config=config, **kwargs)
        self.toolkits = toolkits

    async def before_llm_request(
        self, context: RunContext, messages: list[Any]
    ) -> None:
        for tk in self.toolkits:
            await DependencyInjector.invoke(
                tk.before_llm_request, {"messages": messages}, context
            )

    async def resolve(self, context: RunContext | None = None) -> ResolvedToolPayload:
        payload = ResolvedToolPayload()
        prefix = self.config.prefix or ""

        for tk in self.toolkits:
            active_tk = tk
            if prefix:
                active_tk = active_tk.prefixed(prefix)
            if self._instance_filter:
                active_tk = active_tk.filtered(self._instance_filter)

            child_payload = await active_tk.resolve(context)

            for tool in child_payload.tools:
                if self.config.shared_options:
                    tool.settings = self.config.shared_options.merge(tool.settings)

            payload.tools.extend(child_payload.tools)
            payload.injected_prompts.extend(child_payload.injected_prompts)
            payload.toolkits.extend(child_payload.toolkits)

        payload.toolkits.append(self)
        if instructions := self.get_instructions():
            payload.injected_prompts.append(instructions)

        return payload
