from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar, overload

import anyio
from nonebot.utils import is_coroutine_callable

from zhenxun.services.ai.capabilities import (
    AbstractCapability,
    WrapModelRequestHandler,
    WrapRunHandler,
    WrapToolExecuteHandler,
    WrapToolValidateHandler,
)
from zhenxun.services.ai.core.messages import ChatRequest, ChatResponse
from zhenxun.services.ai.core.models import LLMContext

from .context import RunContext
from .models import AgentRunResult

_FuncT = TypeVar("_FuncT", bound=Callable[..., Any])


class HookTimeoutError(TimeoutError):
    """当 Hook 函数执行超过配置的时间时抛出此异常。"""

    def __init__(self, hook_name: str, func_name: str, timeout: float):
        """初始化 HookTimeoutError 异常实例。"""
        self.hook_name = hook_name
        self.func_name = func_name
        self.timeout = timeout
        super().__init__(
            f"Hook {hook_name!r} 中的函数 {func_name!r} 执行超时 ({timeout}s)"
        )


@dataclass
class _HookEntry(Generic[_FuncT]):
    """基础 Hook 注册实体，支持超时配置"""

    func: _FuncT
    timeout: float | None = None


@dataclass
class _ToolHookEntry(_HookEntry[_FuncT]):
    """工具层 Hook 注册实体，支持工具过滤器"""

    tools: frozenset[str] | None = None


class BeforeRunHookFunc(Protocol):
    """运行前钩子：在 Agent 启动任何流转前触发。"""

    def __call__(self, ctx: RunContext[Any], /) -> None | Awaitable[None]: ...


class AfterRunHookFunc(Protocol):
    """运行后钩子：在 Agent 获取最终结果后触发，可用于修改最终输出。"""

    def __call__(
        self, ctx: RunContext[Any], /, *, result: AgentRunResult[Any]
    ) -> AgentRunResult[Any] | Awaitable[AgentRunResult[Any]]: ...


class WrapRunHookFunc(Protocol):
    """包裹运行钩子：以洋葱模型接管整个 Agent 运行生命周期。"""

    def __call__(
        self, ctx: RunContext[Any], /, *, handler: WrapRunHandler
    ) -> AgentRunResult[Any] | Awaitable[AgentRunResult[Any]]: ...


class OnRunErrorHookFunc(Protocol):
    """运行异常钩子：捕获 Agent 级别的致命错误。"""

    def __call__(
        self, ctx: RunContext[Any], /, *, error: BaseException
    ) -> AgentRunResult[Any] | Awaitable[AgentRunResult[Any]]: ...


class BeforeModelRequestHookFunc(Protocol):
    """大模型请求前钩子：在发送网络请求前触发，可篡改 Messages 上下文。"""

    def __call__(
        self,
        ctx: RunContext[Any],
        request_context: LLMContext[ChatRequest, ChatResponse],
        /,
    ) -> (
        LLMContext[ChatRequest, ChatResponse]
        | Awaitable[LLMContext[ChatRequest, ChatResponse]]
    ): ...


class AfterModelRequestHookFunc(Protocol):
    """大模型请求后钩子：在接收到网络响应后触发，可验证或篡改原始 Response。"""

    def __call__(
        self,
        ctx: RunContext[Any],
        /,
        *,
        request_context: LLMContext[ChatRequest, ChatResponse],
        response: ChatResponse,
    ) -> ChatResponse | Awaitable[ChatResponse]: ...


class WrapModelRequestHookFunc(Protocol):
    """包裹大模型请求钩子：以洋葱模型接管 LLM 网络请求过程（可用于实现缓存）。"""

    def __call__(
        self,
        ctx: RunContext[Any],
        /,
        *,
        request_context: LLMContext[ChatRequest, ChatResponse],
        handler: WrapModelRequestHandler,
    ) -> ChatResponse | Awaitable[ChatResponse]: ...


class OnModelRequestErrorHookFunc(Protocol):
    """大模型请求异常钩子：捕获超时或网络等异常，可用于发起重试。"""

    def __call__(
        self,
        ctx: RunContext[Any],
        /,
        *,
        request_context: LLMContext[ChatRequest, ChatResponse],
        error: Exception,
    ) -> ChatResponse | Awaitable[ChatResponse]: ...


class PrepareToolsHookFunc(Protocol):
    """准备工具集钩子：向大模型渲染 JSON Schema 前触发，可动态增删工具。"""

    def __call__(
        self, ctx: RunContext[Any], tool_defs: list[Any], /
    ) -> list[Any] | Awaitable[list[Any]]: ...


class BeforeToolValidateHookFunc(Protocol):
    """工具验证前钩子：在反序列化前触发，可篡改原始 JSON 字符串或字典。"""

    def __call__(
        self, ctx: RunContext[Any], /, *, tool_name: str, args: str | dict[str, Any]
    ) -> str | dict[str, Any] | Awaitable[str | dict[str, Any]]: ...


class AfterToolValidateHookFunc(Protocol):
    """工具验证后钩子：在 Schema 校验通过后触发，接收并可篡改强类型参数字典。"""

    def __call__(
        self, ctx: RunContext[Any], /, *, tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any] | Awaitable[dict[str, Any]]: ...


class WrapToolValidateHookFunc(Protocol):
    """包裹工具验证钩子：以洋葱模型接管工具参数校验过程（可用于交互式拦截）。"""

    def __call__(
        self,
        ctx: RunContext[Any],
        /,
        *,
        tool_name: str,
        args: str | dict[str, Any],
        handler: WrapToolValidateHandler,
    ) -> dict[str, Any] | Awaitable[dict[str, Any]]: ...


class OnToolValidateErrorHookFunc(Protocol):
    """工具验证异常钩子：捕获校验失败的异常，可将其转化为大模型自愈提示。"""

    def __call__(
        self,
        ctx: RunContext[Any],
        /,
        *,
        tool_name: str,
        args: str | dict[str, Any],
        error: Exception,
    ) -> dict[str, Any] | Awaitable[dict[str, Any]]: ...


class BeforeToolExecuteHookFunc(Protocol):
    """工具执行前钩子：在工具物理执行前触发，可用于最后的权限判定或参数篡改。"""

    def __call__(
        self, ctx: RunContext[Any], /, *, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | Awaitable[dict[str, Any]]: ...


class AfterToolExecuteHookFunc(Protocol):
    """工具执行后钩子：工具执行完毕后触发，可篡改最终发往大模型的返回结果。"""

    def __call__(
        self,
        ctx: RunContext[Any],
        /,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
    ) -> Any | Awaitable[Any]: ...


class WrapToolExecuteHookFunc(Protocol):
    """包裹工具执行钩子：以洋葱模型接管特定工具的物理执行逻辑。"""

    def __call__(
        self,
        ctx: RunContext[Any],
        /,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any | Awaitable[Any]: ...


class OnToolExecuteErrorHookFunc(Protocol):
    """工具执行异常钩子：捕获工具执行时的崩溃异常，可用于兜底返回或自愈。"""

    def __call__(
        self, ctx: RunContext[Any], /, *, tool_name: str, error: Exception
    ) -> Any | Awaitable[Any]: ...


async def _call_func(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """统一执行同步或异步的可调用函数。"""
    if is_coroutine_callable(func):
        return await func(*args, **kwargs)
    return func(*args, **kwargs)


async def _call_entry(
    entry: _HookEntry[Any], hook_name: str, *args: Any, **kwargs: Any
) -> Any:
    """调用 Hook 函数实体，并自动应用熔断超时保护"""
    func = entry.func
    if entry.timeout is not None:
        try:
            with anyio.fail_after(entry.timeout):
                return await _call_func(func, *args, **kwargs)
        except TimeoutError:
            raise HookTimeoutError(
                hook_name=hook_name,
                func_name=getattr(func, "__name__", repr(func)),
                timeout=entry.timeout,
            ) from None
    return await _call_func(func, *args, **kwargs)


def _filter_tool_entries(
    entries: list[_HookEntry[Any]], *, tool_name: str
) -> list[_HookEntry[Any]]:
    """按工具名称过滤 Hook 实体"""
    return [
        entry
        for entry in entries
        if not (
            isinstance(entry, _ToolHookEntry)
            and entry.tools is not None
            and tool_name not in entry.tools
        )
    ]


def _bare_or_parameterized(
    registry: dict[str, list[_HookEntry[Any]]],
    key: str,
    func: _FuncT | None,
    *,
    timeout: float | None = None,
) -> _FuncT | Callable[[_FuncT], _FuncT]:
    """处理无参数钩子的带参/不带参装饰器逻辑"""
    if func is not None:
        registry.setdefault(key, []).append(_HookEntry(func, timeout=timeout))
        return func

    def decorator(f: _FuncT) -> _FuncT:
        registry.setdefault(key, []).append(_HookEntry(f, timeout=timeout))
        return f

    return decorator


def _tool_bare_or_parameterized(
    registry: dict[str, list[_HookEntry[Any]]],
    key: str,
    func: _FuncT | None,
    *,
    tools: list[str] | None = None,
    timeout: float | None = None,
) -> _FuncT | Callable[[_FuncT], _FuncT]:
    """处理工具钩子的带参/不带参装饰器逻辑"""
    frozen_tools = frozenset(tools) if tools is not None else None
    if func is not None:
        registry.setdefault(key, []).append(
            _ToolHookEntry(func, timeout=timeout, tools=frozen_tools)
        )
        return func

    def decorator(f: _FuncT) -> _FuncT:
        registry.setdefault(key, []).append(
            _ToolHookEntry(f, timeout=timeout, tools=frozen_tools)
        )
        return f

    return decorator


class BoundHookPoint(Generic[_FuncT]):
    """绑定到具体实例的 Hook 注册点。"""

    def __init__(self, key: str, registry_holder: Any):
        """初始化绑定的 Hook 注册点。"""
        self.key = key
        self.registry_holder = registry_holder

    @overload
    def __call__(self, func: _FuncT, /) -> _FuncT: ...

    @overload
    def __call__(
        self, *, timeout: float | None = None
    ) -> Callable[[_FuncT], _FuncT]: ...

    def __call__(
        self, func: _FuncT | None = None, *, timeout: float | None = None
    ) -> Any:
        """支持装饰器语法注册钩子函数。"""
        return _bare_or_parameterized(
            self.registry_holder._r, self.key, func, timeout=timeout
        )


class HookPoint(Generic[_FuncT]):
    """描述符形式的 Hook 注册点。"""

    def __init__(self, key: str):
        """初始化 Hook 描述符。"""
        self.key = key

    def __get__(self, instance: Any, owner: Any) -> BoundHookPoint[_FuncT]:
        """通过描述符协议绑定 Hook 实例。"""
        return BoundHookPoint(self.key, instance)


class BoundToolHookPoint(Generic[_FuncT]):
    """绑定到具体实例的工具级别 Hook 注册点。"""

    def __init__(self, key: str, registry_holder: Any):
        """初始化绑定的工具 Hook 注册点。"""
        self.key = key
        self.registry_holder = registry_holder

    @overload
    def __call__(self, func: _FuncT, /) -> _FuncT: ...

    @overload
    def __call__(
        self, *, tools: list[str] | None = None, timeout: float | None = None
    ) -> Callable[[_FuncT], _FuncT]: ...

    def __call__(
        self,
        func: _FuncT | None = None,
        *,
        tools: list[str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """支持装饰器语法注册工具钩子函数。"""
        return _tool_bare_or_parameterized(
            self.registry_holder._r, self.key, func, tools=tools, timeout=timeout
        )


class ToolHookPoint(Generic[_FuncT]):
    """描述符形式的工具级别 Hook 注册点。"""

    def __init__(self, key: str):
        """初始化工具 Hook 描述符。"""
        self.key = key

    def __get__(self, instance: Any, owner: Any) -> BoundToolHookPoint[_FuncT]:
        """通过描述符协议绑定工具 Hook 实例。"""
        return BoundToolHookPoint(self.key, instance)


class _HookRegistration:
    """Hooks 装饰器注册辅助类，用于各阶段钩子的声明式注册"""

    def __init__(self, hooks: "Hooks"):
        """初始化钩子注册辅助实例。"""
        self._hooks = hooks

    @property
    def _r(self) -> dict[str, list[_HookEntry[Any]]]:
        """获取底层注册的钩子字典。"""
        return self._hooks._registry

    before_run = HookPoint[BeforeRunHookFunc]("before_run")
    """注册运行前钩子。在 Agent 启动任何流转前触发。"""
    after_run = HookPoint[AfterRunHookFunc]("after_run")
    """注册运行后钩子。在 Agent 获取最终结果后触发，可修改结果。"""
    wrap_run = HookPoint[WrapRunHookFunc]("wrap_run")
    """注册运行包裹钩子。以洋葱模型接管整个 Agent 运行过程。"""
    on_run_error = HookPoint[OnRunErrorHookFunc]("on_run_error")
    """注册运行异常钩子。捕获 Agent 级别的致命错误。"""

    before_model_request = HookPoint[BeforeModelRequestHookFunc]("before_model_request")
    """注册大模型请求前钩子。可在此修改发送给 LLM 的 Messages 等上下文。"""
    after_model_request = HookPoint[AfterModelRequestHookFunc]("after_model_request")
    """注册大模型请求后钩子。可在此验证或修改 LLM 的原始 Response。"""
    wrap_model_request = HookPoint[WrapModelRequestHookFunc]("wrap_model_request")
    """注册大模型请求包裹钩子。以洋葱模型接管 LLM 的网络请求过程。"""
    on_model_request_error = HookPoint[OnModelRequestErrorHookFunc](
        "on_model_request_error"
    )
    """注册大模型请求异常钩子。捕获超时或网络等异常。"""

    before_tool_validate = HookPoint[BeforeToolValidateHookFunc]("before_tool_validate")
    """注册工具验证前钩子。在工具输入参数校验前触发，可篡改或校验参数。"""
    after_tool_validate = HookPoint[AfterToolValidateHookFunc]("after_tool_validate")
    """注册工具验证后钩子。在工具输入参数校验成功后触发，可篡改最终参数。"""
    wrap_tool_validate = HookPoint[WrapToolValidateHookFunc]("wrap_tool_validate")
    """注册工具验证包裹钩子。以洋葱模型接管工具参数校验过程。"""
    on_tool_validate_error = HookPoint[OnToolValidateErrorHookFunc](
        "on_tool_validate_error"
    )
    """注册工具验证异常钩子。捕获并处理校验阶段抛出的异常。"""

    before_tool_execute = ToolHookPoint[BeforeToolExecuteHookFunc](
        "before_tool_execute"
    )
    """注册工具执行前钩子。可通过 tools 参数指定拦截特定工具，可篡改传入参数。"""
    after_tool_execute = ToolHookPoint[AfterToolExecuteHookFunc]("after_tool_execute")
    """注册工具执行后钩子。可通过 tools 参数指定拦截特定工具，可篡改返回结果。"""
    wrap_tool_execute = ToolHookPoint[WrapToolExecuteHookFunc]("wrap_tool_execute")
    """注册工具执行包裹钩子。以洋葱模型接管特定工具的执行逻辑。"""
    on_tool_execute_error = ToolHookPoint[OnToolExecuteErrorHookFunc](
        "on_tool_execute_error"
    )
    """注册工具执行异常钩子。捕获特定工具的崩溃异常，可用于自愈重试。"""


class Hooks(AbstractCapability):
    """
    面向开发者的极简拦截器语法糖。
    允许通过 `@hooks.on.xxx` 装饰器快速介入大模型及工具生命周期的各个阶段。
    """

    def __init__(self):
        """初始化 Hooks 拦截器实例。"""
        self._registry: dict[str, list[_HookEntry[Any]]] = {}
        self.on = _HookRegistration(self)

    async def _dispatch_pipeline(
        self,
        hook_prefix: str,
        context: RunContext,
        handler: Callable,
        get_entries: Callable[[str], list[_HookEntry[Any]]],
        do_before: Callable[[_HookEntry[Any]], Awaitable[Any]],
        get_wrap_kwargs: Callable[[Callable], dict[str, Any]],
        invoke_chain: Callable[[Callable], Awaitable[Any]],
        do_error: Callable[[_HookEntry[Any], BaseException], Awaitable[Any]],
        do_after: Callable[[_HookEntry[Any], Any], Awaitable[Any]],
    ) -> Any:
        """核心泛型流水线引擎，按阶段分发并执行注册的钩子链。"""
        for entry in get_entries(f"before_{hook_prefix}"):
            await do_before(entry)

        chain = handler
        wrap_entries = get_entries(f"wrap_{hook_prefix}")
        if wrap_entries:
            for entry in reversed(wrap_entries):

                def _wrap(e: _HookEntry[Any], h: Callable) -> Callable:
                    async def _wrapped(*args, **kwargs) -> Any:
                        return await _call_entry(
                            e, f"wrap_{hook_prefix}", context, **get_wrap_kwargs(h)
                        )

                    return _wrapped

                chain = _wrap(entry, chain)

        try:
            result = await invoke_chain(chain)
        except BaseException as error:
            err_entries = get_entries(f"on_{hook_prefix}_error")
            for err_entry in reversed(err_entries):
                try:
                    return await do_error(err_entry, error)
                except BaseException as new_err:
                    error = new_err
            raise error

        for after_entry in reversed(get_entries(f"after_{hook_prefix}")):
            res = await do_after(after_entry, result)
            if res is not None:
                result = res
        return result

    async def wrap_run(
        self, context: RunContext, handler: WrapRunHandler
    ) -> AgentRunResult[Any]:
        """接管并包裹 Agent 运行生命周期的执行。"""
        return await self._dispatch_pipeline(
            "run",
            context,
            handler,
            get_entries=lambda p: self._registry.get(p, []),
            do_before=lambda e: _call_entry(e, "before_run", context),
            get_wrap_kwargs=lambda h: {"handler": h},
            invoke_chain=lambda c: c(),
            do_error=lambda e, err: _call_entry(e, "on_run_error", context, error=err),
            do_after=lambda e, res: _call_entry(e, "after_run", context, result=res),
        )

    async def wrap_model_request(
        self,
        context: RunContext,
        llm_context: LLMContext[ChatRequest, ChatResponse],
        handler: WrapModelRequestHandler,
    ) -> ChatResponse:
        """接管并包裹 LLM 大模型请求的网络交互过程。"""

        async def do_before(e):
            nonlocal llm_context
            res = await _call_entry(e, "before_model_request", context, llm_context)
            if res is not None:
                llm_context = res

        return await self._dispatch_pipeline(
            "model_request",
            context,
            handler,
            get_entries=lambda p: self._registry.get(p, []),
            do_before=do_before,
            get_wrap_kwargs=lambda h: {"request_context": llm_context, "handler": h},
            invoke_chain=lambda c: c(llm_context),
            do_error=lambda e, err: _call_entry(
                e,
                "on_model_request_error",
                context,
                request_context=llm_context,
                error=err,
            ),
            do_after=lambda e, res: _call_entry(
                e,
                "after_model_request",
                context,
                request_context=llm_context,
                response=res,
            ),
        )

    async def wrap_tool_validate(
        self,
        context: RunContext,
        tool_name: str,
        args: str | dict[str, Any],
        handler: WrapToolValidateHandler,
    ) -> dict[str, Any]:
        """接管并包裹工具参数的校验过程。"""

        async def do_before(e):
            nonlocal args
            res = await _call_entry(
                e, "before_tool_validate", context, tool_name=tool_name, args=args
            )
            if res is not None:
                args = res

        return await self._dispatch_pipeline(
            "tool_validate",
            context,
            handler,
            get_entries=lambda p: self._registry.get(p, []),
            do_before=do_before,
            get_wrap_kwargs=lambda h: {
                "tool_name": tool_name,
                "args": args,
                "handler": h,
            },
            invoke_chain=lambda c: c(args),
            do_error=lambda e, err: _call_entry(
                e,
                "on_tool_validate_error",
                context,
                tool_name=tool_name,
                args=args,
                error=err,
            ),
            do_after=lambda e, res: _call_entry(
                e, "after_tool_validate", context, tool_name=tool_name, args=res
            ),
        )

    async def wrap_tool_execute(
        self,
        context: RunContext,
        tool_name: str,
        arguments: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        """接管并包裹特定工具的物理执行逻辑。"""

        async def do_before(e):
            nonlocal arguments
            res = await _call_entry(
                e,
                "before_tool_execute",
                context,
                tool_name=tool_name,
                arguments=arguments,
            )
            if res is not None:
                arguments = res

        return await self._dispatch_pipeline(
            "tool_execute",
            context,
            handler,
            get_entries=lambda p: _filter_tool_entries(
                self._registry.get(p, []), tool_name=tool_name
            ),
            do_before=do_before,
            get_wrap_kwargs=lambda h: {
                "tool_name": tool_name,
                "arguments": arguments,
                "handler": h,
            },
            invoke_chain=lambda c: c(arguments),
            do_error=lambda e, err: _call_entry(
                e, "on_tool_execute_error", context, tool_name=tool_name, error=err
            ),
            do_after=lambda e, res: _call_entry(
                e,
                "after_tool_execute",
                context,
                tool_name=tool_name,
                arguments=arguments,
                result=res,
            ),
        )
