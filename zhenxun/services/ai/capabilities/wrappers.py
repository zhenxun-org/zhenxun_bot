from __future__ import annotations

from collections.abc import Callable
import copy
import graphlib
from typing import TYPE_CHECKING, Any

from nonebot.utils import is_coroutine_callable

from zhenxun.services.ai.core.messages import ChatRequest, ChatResponse
from zhenxun.services.ai.core.models import LLMContext
from zhenxun.services.ai.core.options import GenerationConfig

from .base import (
    AbstractCapability,
    CapabilityRef,
    WrapModelRequestHandler,
    WrapRunHandler,
    WrapToolExecuteHandler,
    WrapToolValidateHandler,
)

if TYPE_CHECKING:
    from zhenxun.services.ai.run import AgentRunResult, RunContext


def sort_capabilities(caps: list["AbstractCapability"]) -> list["AbstractCapability"]:
    """使用标准库 graphlib.TopologicalSorter 实现拦截器拓扑排序，解决执行顺序冲突"""
    if len(caps) <= 1:
        return caps

    ts = graphlib.TopologicalSorter()
    n = len(caps)
    for i in range(n):
        ts.add(i)

    orderings = [c.get_ordering() for c in caps]
    leaf_types = [{type(c)} for c in caps]

    def _ref_matches(
        ref: CapabilityRef, types: set[type], inst: AbstractCapability
    ) -> bool:
        if isinstance(ref, type):
            return any(issubclass(t, ref) for t in types)
        return inst is ref

    all_types = set().union(*leaf_types)
    for i, o in enumerate(orderings):
        if o and o.requires:
            for req in o.requires:
                if not any(issubclass(t, req) for t in all_types):
                    raise ValueError(
                        f"Capability '{type(caps[i]).__name__}' 依赖 '{req.__name__}' "
                        f"但未在管线中找到该组件。"
                    )

    outermost = {i for i, o in enumerate(orderings) if o and o.position == "outermost"}
    innermost = {i for i, o in enumerate(orderings) if o and o.position == "innermost"}

    for oi in outermost:
        for j in range(n):
            if j != oi and j not in outermost:
                ts.add(j, oi)

    for ii in innermost:
        for j in range(n):
            if j != ii and j not in innermost:
                ts.add(ii, j)

    for i, o in enumerate(orderings):
        if not o:
            continue
        for ref in o.wraps:
            for j in range(n):
                if i != j and _ref_matches(ref, leaf_types[j], caps[j]):
                    ts.add(j, i)
        for ref in o.wrapped_by:
            for j in range(n):
                if i != j and _ref_matches(ref, leaf_types[j], caps[j]):
                    ts.add(i, j)

    try:
        order = list(ts.static_order())
    except graphlib.CycleError:
        raise ValueError(
            "Capability 拓扑排序失败，存在循环依赖约束。"
            "请检查 wraps 或 wrapped_by 的配置。"
        )

    return [caps[i] for i in order]


class CombinedCapability(AbstractCapability):
    """
    组合能力容器。
    将多个 Capability 按顺序融合成一个复合的洋葱模型，
    处理生命周期的正序/倒序 and 链式调用。
    """

    def __init__(self, capabilities: list[AbstractCapability]):
        """初始化组合能力，对传入能力集进行展平去重和拓扑排序"""
        flat = []
        for c in capabilities:
            if isinstance(c, CombinedCapability):
                flat.extend(c.capabilities)
            else:
                flat.append(c)

        deduped = []
        seen = set()
        for c in flat:
            if id(c) not in seen:
                seen.add(id(c))
                deduped.append(c)
        self.capabilities = sort_capabilities(deduped)

    async def for_run(self, context: RunContext) -> "AbstractCapability":
        """为当前运行实例解析并更新所包裹的能力列表"""
        new_caps = []
        changed = False
        for cap in self.capabilities:
            new_cap = await cap.for_run(context)
            new_caps.append(new_cap)
            if new_cap is not cap:
                changed = True

        if changed:
            return CombinedCapability(new_caps)
        return self

    async def get_generation_config(
        self, context: RunContext
    ) -> GenerationConfig | None:
        """获取组合中所有能力合并后的生成配置"""
        final_config = None
        for cap in self.capabilities:
            cap_config = await cap.get_generation_config(context)
            if cap_config:
                if final_config is None:
                    final_config = cap_config
                else:
                    final_config = final_config.merge_with(cap_config)
        return final_config

    async def get_system_prompts(self, context: RunContext) -> list[str]:
        """获取组合中所有能力提供的系统提示词列表"""
        prompts = []
        for cap in self.capabilities:
            prompts.extend(await cap.get_system_prompts(context))
        return prompts

    async def get_tools(self, context: RunContext) -> list[Any]:
        """获取组合中所有能力附带注册的工具列表"""
        tools = []
        for cap in self.capabilities:
            tools.extend(await cap.get_tools(context))
        return tools

    async def prepare_tools(
        self, context: RunContext, tool_defs: list[Any]
    ) -> list[Any]:
        """依次调用组合中所有能力的准备工具钩子处理工具定义"""
        current_defs = list(tool_defs)
        for cap in self.capabilities:
            res = await cap.prepare_tools(context, current_defs)
            if res is not None:
                current_defs = res
        return current_defs

    async def wrap_run(
        self, context: RunContext, handler: WrapRunHandler
    ) -> "AgentRunResult[Any]":
        """串联能力组合的 wrap_run 洋葱模型拦截器链"""
        chain = handler
        for cap in reversed(self.capabilities):
            chain = _make_wrap_link(cap, "wrap_run", context, {}, chain, None)
        return await chain()

    async def wrap_model_request(
        self,
        context: RunContext,
        llm_context: LLMContext[ChatRequest, ChatResponse],
        handler: WrapModelRequestHandler,
    ) -> ChatResponse:
        """串联能力组合的 wrap_model_request 洋葱模型拦截器链"""
        chain = handler
        for cap in reversed(self.capabilities):
            chain = _make_wrap_link(
                cap, "wrap_model_request", context, {}, chain, "llm_context"
            )
        return await chain(llm_context)

    async def wrap_tool_validate(
        self,
        context: RunContext,
        tool_name: str,
        args: str | dict[str, Any],
        handler: WrapToolValidateHandler,
    ) -> dict[str, Any]:
        """串联能力组合的 wrap_tool_validate 洋葱模型拦截器链"""
        chain = handler
        for cap in reversed(self.capabilities):
            chain = _make_wrap_link(
                cap,
                "wrap_tool_validate",
                context,
                {"tool_name": tool_name},
                chain,
                "args",
            )
        return await chain(args)

    async def wrap_tool_execute(
        self,
        context: RunContext,
        tool_name: str,
        arguments: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        """串联能力组合的 wrap_tool_execute 洋葱模型拦截器链"""
        chain = handler
        for cap in reversed(self.capabilities):
            chain = _make_wrap_link(
                cap,
                "wrap_tool_execute",
                context,
                {"tool_name": tool_name},
                chain,
                "arguments",
            )
        return await chain(arguments)


def _make_wrap_link(
    cap: AbstractCapability,
    hook_name: str,
    ctx: RunContext,
    static_kwargs: dict[str, Any],
    inner_handler: Callable[..., Any],
    handler_arg: str | None,
) -> Callable[..., Any]:
    """构建洋葱模型中间件链的单一闭包节点。"""
    frozen_kwargs = dict(static_kwargs)

    if handler_arg:

        async def wrapper(value: Any) -> Any:
            kw = dict(frozen_kwargs)
            kw[handler_arg] = value
            hook_method = getattr(cap, hook_name)
            return await hook_method(ctx, handler=inner_handler, **kw)

        return wrapper

    async def wrapper_no_arg() -> Any:
        hook_method = getattr(cap, hook_name)
        return await hook_method(ctx, handler=inner_handler, **frozen_kwargs)

    return wrapper_no_arg


class DynamicCapability(AbstractCapability):
    """动态能力注入：允许在运行时基于上下文生成真正的 Capability"""

    def __init__(self, capability_func: Callable):
        """初始化动态能力"""
        self.capability_func = capability_func

    async def for_run(self, context: RunContext) -> "AbstractCapability":
        """在运行时基于当前上下文动态实例化并执行真正的 Capability"""
        if is_coroutine_callable(self.capability_func):
            cap = await self.capability_func(context)
        else:
            cap = self.capability_func(context)
        if cap is None:
            return self
        return await cap.for_run(context)


class WrapperCapability(AbstractCapability):
    """
    代理包装能力基类 (Decorator Pattern)。
    默认将所有生命周期钩子透明透传给内部包裹的 (wrapped) 实例。
    """

    def __init__(self, wrapped: AbstractCapability):
        """初始化代理包装器"""
        self.wrapped = wrapped

    async def for_run(self, context: RunContext) -> "AbstractCapability":
        """对内部包裹的实例执行运行时解析并深度克隆"""
        new_wrapped = await self.wrapped.for_run(context)
        if new_wrapped is self.wrapped:
            return self

        new_self = copy.copy(self)
        new_self.wrapped = new_wrapped
        return new_self

    async def get_generation_config(
        self, context: RunContext
    ) -> GenerationConfig | None:
        """透传获取内部包裹实例的生成配置"""
        return await self.wrapped.get_generation_config(context)

    async def get_system_prompts(self, context: RunContext) -> list[str]:
        """透传获取内部包裹实例的系统提示词"""
        return await self.wrapped.get_system_prompts(context)

    async def get_tools(self, context: RunContext) -> list[Any]:
        """透传获取内部包裹实例的工具列表"""
        return await self.wrapped.get_tools(context)

    async def prepare_tools(
        self, context: RunContext, tool_defs: list[Any]
    ) -> list[Any]:
        """透传执行内部包裹实例的准备工具钩子"""
        return await self.wrapped.prepare_tools(context, tool_defs)

    async def wrap_run(
        self, context: RunContext, handler: WrapRunHandler
    ) -> "AgentRunResult[Any]":
        """透传执行内部包裹实例的 wrap_run 拦截器"""
        return await self.wrapped.wrap_run(context, handler)

    async def wrap_model_request(
        self,
        context: RunContext,
        llm_context: LLMContext[ChatRequest, ChatResponse],
        handler: WrapModelRequestHandler,
    ) -> ChatResponse:
        """透传执行内部包裹实例的 wrap_model_request 拦截器"""
        return await self.wrapped.wrap_model_request(context, llm_context, handler)

    async def wrap_tool_validate(
        self,
        context: RunContext,
        tool_name: str,
        args: str | dict[str, Any],
        handler: WrapToolValidateHandler,
    ) -> dict[str, Any]:
        """透传执行内部包裹实例的 wrap_tool_validate 拦截器"""
        return await self.wrapped.wrap_tool_validate(context, tool_name, args, handler)

    async def wrap_tool_execute(
        self,
        context: RunContext,
        tool_name: str,
        arguments: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        """透传执行内部包裹实例的 wrap_tool_execute 拦截器"""
        return await self.wrapped.wrap_tool_execute(
            context, tool_name, arguments, handler
        )
