import asyncio
from collections.abc import Callable
from typing import Any

from aiocache import SimpleMemoryCache

from zhenxun.services.ai.capabilities import (
    AbstractCapability,
    WrapToolExecuteHandler,
    WrapToolValidateHandler,
)
from zhenxun.services.ai.run import RunContext
from zhenxun.services.ai.run.di import DependencyInjector
from zhenxun.services.ai.tools.models import ToolResult
from zhenxun.services.ai.utils import PermissionUtils
from zhenxun.services.log import logger
from zhenxun.utils.pydantic_compat import model_dump, parse_as

_TOOL_RESULT_CACHE = SimpleMemoryCache(namespace="zhenxun_tool_cache")


class CacheCapability(AbstractCapability):
    """
    极速缓存能力中间件 (Tool-Level)
    拦截相同的参数调用，直接返回缓存结果，无需请求底层。
    """

    def __init__(self, ttl: int = 3600, cache_function: Callable | None = None):
        """
        初始化极速缓存能力中间件。

        参数:
            ttl: 缓存结果的有效存活时长（秒），默认 3600。
            cache_function: 自定义判定函数，接收 arguments 与 result，
                返回布尔值决定该次结果是否允许缓存，默认 None。
        """
        self.ttl = ttl
        self.cache_function = cache_function

    async def wrap_tool_execute(
        self,
        context: RunContext,
        tool_name: str,
        arguments: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        tool = context.call.current_tool
        if tool is None:
            return await handler(arguments)
        if not hasattr(tool, "_generate_cache_key"):
            return await handler(arguments)

        cache_key = tool._generate_cache_key(arguments)
        cached_data = await _TOOL_RESULT_CACHE.get(cache_key)
        if cached_data is not None:
            cached_result = (
                parse_as(ToolResult, cached_data)
                if isinstance(cached_data, dict)
                else cached_data
            )
            logger.info(
                f"⚡ [Cache Hit] 工具 {tool_name} 命中极速缓存, "
                f"熔断执行! Key: {cache_key}"
            )
            return cached_result

        result = await handler(arguments)

        if result and not getattr(result, "is_error", False):
            should_cache = True
            if self.cache_function:
                try:
                    should_cache = self.cache_function(arguments, result)
                except Exception as ce:
                    logger.warning(f"Cache function 执行失败: {ce}")
                    should_cache = False
            if should_cache:
                await _TOOL_RESULT_CACHE.set(
                    cache_key, model_dump(result), ttl=self.ttl
                )

        return result


class ApprovalCapability(AbstractCapability):
    """
    人工审批能力中间件 (Tool-Level / HITL)
    执行高危操作前在群聊中发起授权确认，拒绝则抛出取消异常。
    """

    async def wrap_tool_execute(
        self,
        context: RunContext,
        tool_name: str,
        arguments: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        tool = context.call.current_tool
        if tool is None:
            return await handler(arguments)

        import json

        args_str = json.dumps(arguments, ensure_ascii=False, indent=2)
        confirm_msg = f"即将在本地执行高危工具 [{tool_name}]\n参数：\n{args_str}"

        if "global" not in context.run.hitl_locks:
            context.run.hitl_locks["global"] = asyncio.Lock()
        hitl_lock = context.run.hitl_locks["global"]

        await hitl_lock.acquire()
        try:
            from zhenxun.services.ai.run.hitl import HITLController

            hitl = HITLController(context)
            await hitl.ask_confirm(f"⚠️ **安全交互审批**\n\n{confirm_msg}", timeout=60.0)
            logger.info(f"🛡️ [HITL] 工具 {tool_name} 审批通过。")
        finally:
            hitl_lock.release()

        return await handler(arguments)


class LifecycleCapability(AbstractCapability):
    """
    通用生命周期能力中间件 (Generic Lifecycle Capability)
    接收开发者传入的简单函数，自动为其提供依赖注入 (DI) 和洋葱模型拦截。
    """

    def __init__(
        self,
        before_execute: Callable | None = None,
        after_execute: Callable | None = None,
        validate_args: Callable | None = None,
        prepare_tool: Callable | None = None,
    ):
        """
        初始化通用生命周期能力中间件。

        参数:
            before_execute: 在工具开始执行前触发的钩子函数，默认 None。
            after_execute: 在工具执行完毕后触发的钩子/拦截函数，默认 None。
            validate_args: 在工具参数验证阶段拦截的自定义校验函数，默认 None。
            prepare_tool: 在加载并提供大模型 Schema 定义前进行拦截加工的准备函数，
                默认 None。
        """
        self.before_execute_hook = before_execute
        self.after_execute_hook = after_execute
        self.validate_args_hook = validate_args
        self.prepare_tool_hook = prepare_tool

    async def prepare_tools(
        self, context: RunContext, tool_defs: list[Any]
    ) -> list[Any]:
        if not self.prepare_tool_hook:
            return tool_defs
        new_defs = []
        for tdef in tool_defs:
            res = await DependencyInjector.invoke(
                self.prepare_tool_hook, {"tool_def": tdef}, context
            )
            if res is not None:
                new_defs.append(res)
        return new_defs

    async def wrap_tool_validate(
        self,
        context: RunContext,
        tool_name: str,
        args: str | dict[str, Any],
        handler: WrapToolValidateHandler,
    ) -> dict[str, Any]:
        if not self.validate_args_hook or not isinstance(args, dict):
            return await handler(args)
        call_kwargs = dict(args)
        call_kwargs["args"] = args
        call_kwargs["tool_args"] = args
        call_kwargs["tool_name"] = tool_name
        await DependencyInjector.invoke(self.validate_args_hook, call_kwargs, context)
        return await handler(args)

    async def wrap_tool_execute(
        self,
        context: RunContext,
        tool_name: str,
        arguments: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        if not self.before_execute_hook:
            pass
        else:
            call_kwargs = dict(arguments)
            call_kwargs["args"] = arguments
            call_kwargs["tool_args"] = arguments
            call_kwargs["tool_name"] = tool_name
            await DependencyInjector.invoke(
                self.before_execute_hook, call_kwargs, context
            )

        result = await handler(arguments)

        if not self.after_execute_hook:
            return result
        call_kwargs = dict(arguments)
        call_kwargs["args"] = arguments
        call_kwargs["tool_args"] = arguments
        call_kwargs["tool_name"] = tool_name
        call_kwargs["result"] = result
        call_kwargs["tool_result"] = result

        res = await DependencyInjector.invoke(
            self.after_execute_hook, call_kwargs, context
        )
        return res if res is not None else result


class SuperuserCapability(AbstractCapability):
    """仅超级管理员可见"""

    async def prepare_tools(
        self, context: RunContext, tool_defs: list[Any]
    ) -> list[Any]:
        if await PermissionUtils.check_superuser(context):
            return tool_defs
        return []


class AdminLevelCapability(AbstractCapability):
    """需要指定群聊权限等级"""

    def __init__(self, min_level: int):
        """
        初始化群聊权限等级限制能力。

        参数:
            min_level: 允许执行该工具的最小群聊管理权限等级。
        """
        self.min_level = min_level

    async def prepare_tools(
        self, context: RunContext, tool_defs: list[Any]
    ) -> list[Any]:
        if await PermissionUtils.check_admin_level(context, self.min_level):
            return tool_defs
        return []


class GroupOnlyCapability(AbstractCapability):
    """仅群聊可用"""

    async def prepare_tools(
        self, context: RunContext, tool_defs: list[Any]
    ) -> list[Any]:
        if context.get_group_id():
            return tool_defs
        return []


class ConfigDependencyCapability(AbstractCapability):
    """依赖特定配置开关"""

    def __init__(self, module: str, key: str, expected_value: Any = True):
        """
        初始化配置依赖开关能力。

        参数:
            module: 目标配置所属的模块名。
            key: 目标配置项的键名。
            expected_value: 期望该配置项所匹配的值，如果匹配通过才可见/可用该工具，默认 True。
        """  # noqa: E501
        self.module = module
        self.key = key
        self.expected_value = expected_value

    async def prepare_tools(
        self, context: RunContext, tool_defs: list[Any]
    ) -> list[Any]:
        from zhenxun.configs.config import Config

        if Config.get_config(self.module, self.key) == self.expected_value:
            return tool_defs
        return []


class InteractiveCapability(AbstractCapability):
    """交互式补全局部中间件：在验证阶段拦截参数缺失异常并向用户提问"""

    async def wrap_tool_validate(
        self,
        context: RunContext,
        tool_name: str,
        args: str | dict[str, Any],
        handler: WrapToolValidateHandler,
    ) -> dict[str, Any]:
        from zhenxun.services.ai.core.exceptions import (
            NeedsInputException,
            ToolFatalError,
            ToolRetryError,
        )

        tool = context.call.current_tool
        current_kwargs = dict(args) if isinstance(args, dict) else args

        while True:
            try:
                return await handler(current_kwargs)
            except NeedsInputException as e:
                bot = context.get_bot()
                event = context.get_event()

                if not bot or not event:
                    logger.warning(
                        "交互式工具 "
                        f"{getattr(tool, 'name', 'unknown')} "
                        "缺少参数，但处于非交互环境。"
                    )
                    raise ToolRetryError(f"缺少必填参数: {e.missing_description}。")

                prompt_msg = (
                    f"执行 {getattr(tool, 'name', '该操作')} "
                    f"需要补充参数：\n[{e.missing_description}]\n"
                    "请发送文本补充，或回复“取消”中止。"
                )
                try:
                    from zhenxun.services.ai.run.hitl import HITLController

                    hitl = HITLController(context)
                    user_input = await hitl.ask_text(prompt_msg, timeout=60.0)
                except ToolFatalError:
                    raise ToolFatalError(
                        "参数收集超时，用户已离开，任务已中止。",
                        display_content=(
                            "❌ 工具 "
                            f"'{getattr(tool, 'name', '')}' "
                            "等待参数输入超时被取消。"
                        ),
                    )
                if not isinstance(current_kwargs, dict):
                    current_kwargs = {}
                current_kwargs[e.missing_field] = user_input


class FallbackCapability(AbstractCapability):
    """
    降级路由器局部中间件。
    拦截执行结果，若发现报错且配置了备用工具，则透明重定向执行流。
    """

    def __init__(self, fallback_tool_name: str):
        """
        初始化降级路由器局部中间件。

        参数:
            fallback_tool_name: 当主工具执行遇到错误时，自动透明重定向到的备用工具名称。
        """
        self.fallback_tool_name = fallback_tool_name

    async def wrap_tool_execute(
        self,
        context: RunContext,
        tool_name: str,
        arguments: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        result = await handler(arguments)
        if result and getattr(result, "is_error", False):
            available_tools = context.state.get("__available_tools", {})
            fallback_executable = available_tools.get(self.fallback_tool_name)
            if fallback_executable:
                logger.info(
                    f"🔄 [语义容错路由] 主工具 '{tool_name}' 故障，"
                    "正在透明重定向至备用工具 "
                    f"'{self.fallback_tool_name}'..."
                )
                try:
                    fallback_result = await fallback_executable.execute(
                        context=context, **arguments
                    )
                    notice = (
                        f"[系统底座：由于主工具 '{tool_name}' 发生故障，"
                        "系统已自动路由至备用工具 "
                        f"'{self.fallback_tool_name}' 并执行成功]\n"
                    )
                    if isinstance(fallback_result.output, str):
                        fallback_result.output = notice + fallback_result.output
                    return fallback_result
                except Exception as fallback_e:
                    logger.error(
                        "容错路由工具 "
                        f"'{self.fallback_tool_name}' "
                        f"也执行失败: {fallback_e}"
                    )
                    context.run.add_system_prompt(
                        "🚨 [系统警告] 原工具和备用工具"
                        f"({self.fallback_tool_name})均执行失败，"
                        "请停止尝试并告知用户。"
                    )
        return result
