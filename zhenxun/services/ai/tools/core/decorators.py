from __future__ import annotations

from collections.abc import Callable
import inspect
import types
from typing import Any

from zhenxun.services.ai.capabilities import AbstractCapability
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.tools.engine.registry import tool_provider_manager
from zhenxun.services.ai.tools.models import (
    EndRunResult,
    ToolkitConfig,
    ToolOptions,
    ToolResult,
)
from zhenxun.services.ai.utils.logger import log_tool as logger
from zhenxun.utils.pydantic_compat import model_copy

from .capabilities import (
    AdminLevelCapability,
    ApprovalCapability,
    CacheCapability,
    FallbackCapability,
    GroupOnlyCapability,
    InteractiveCapability,
    LifecycleCapability,
    SuperuserCapability,
)
from .tool import FunctionTool


def toolkit(
    rules: list[ToolOptions] | ToolOptions | None = None,
    prefix: str = "",
    instructions: str | None = None,
    auto_register: bool = False,
    tags: list[str] | None = None,
):
    """
    类级别的工具箱装饰器，用于向内部所有 @tool 统一下发配置规则、名称前缀以及工具箱级系统提示词说明。

    参数:
        rules: 声明式规则集合或单个规则，应用于工具箱内所有工具。
        prefix: 工具箱内所有工具的名称前缀，通常以下划线结尾。
        instructions: 工具箱级别的系统提示词补充说明，大模型可见。
        auto_register: 是否在加载时自动实例化该类并注册到全局工具箱列表中。
        tags: 工具箱级别的路由标签，大模型路由系统将基于此发现整个工具箱。

    返回:
        Callable: 装饰器函数，接收一个类并返回该类。
    """  # noqa: E501

    def decorator(cls):
        merged_options = ToolOptions()
        if rules:
            rule_list = rules if isinstance(rules, list) else [rules]
            for r in rule_list:
                if isinstance(r, ToolOptions):
                    merged_options = merged_options.merge(r)

        if tags:
            merged_options.tags = list(set(merged_options.tags + tags))

        base_config = getattr(cls, "_default_config", None) or ToolkitConfig()
        new_config = model_copy(base_config, deep=True)

        if prefix:
            new_config.prefix = prefix

        if new_config.shared_options:
            new_config.shared_options = new_config.shared_options.merge(merged_options)
        else:
            new_config.shared_options = merged_options

        cls._default_config = new_config

        if instructions is not None:
            cls.default_instructions = instructions

        if auto_register:
            try:
                tool_provider_manager.register_toolkit(cls())
            except Exception as e:
                logger.error(
                    f"自动注册工具箱 '{cls.__name__}' 失败"
                    f"(通常是因为自定义了必填参数的 __init__): {e}"
                )

        return cls

    return decorator


def require_sandbox(
    python_packages: list[str] | None = None,
    node_packages: list[str] | None = None,
    system_packages: list[str] | None = None,
) -> ToolOptions:
    """显式声明该工具所需安装的沙箱环境依赖"""
    return ToolOptions(
        sandbox_requirements={
            "python": python_packages or [],
            "node": node_packages or [],
            "system": system_packages or [],
        }
    )


class ToolkitMethodDescriptor:
    """用于 Toolkit 类方法的描述符，确保在实例化时绑定正确的 self 并生成 FunctionTool"""

    def __init__(
        self, func: Callable, name: str, description: str | None, settings: ToolOptions
    ):
        """
        初始化 Toolkit 类方法描述符。

        参数:
            func: 底层的 Python 可执行函数对象。
            name: 大模型识别与调用的工具名。
            description: 大模型阅读的工具功能描述说明。
            settings: 工具的声明式高阶配置项 (ToolOptions)。
        """
        self.func = func
        self.name = name
        self.description = description
        self.settings = settings
        self.__toolkit_tool__ = True
        self.__tool_original_name__ = name

    def __get__(self, instance, owner):
        if instance is None:
            return self
        bound_func = types.MethodType(self.func, instance)
        return FunctionTool(
            func=bound_func,
            name=self.name,
            description=self.description,
            settings=model_copy(self.settings, deep=True),
        )

    def __call__(self, *args, **kwargs):
        """使其兼容 Callable 协议，避免静态类型检查器报错"""
        return self.func(*args, **kwargs)


def tool(
    name: str | None = None,
    description: str | None = None,
    rules: list[ToolOptions] | ToolOptions | None = None,
    settings: ToolOptions | None = None,
    tags: list[str] | None = None,
    auto_register: bool = False,
    require_prefix: bool = False,
):
    """
    将普通函数或类方法注册为 LLM 工具的统一装饰器大一统。

    参数:
        name: 工具的名称(英文字母及下划线)，大模型将看到此名称，如果为空则默认使用函数名。
        description: 工具描述，大模型将基于此决定何时、如何使用该工具，如果为空，将读取函数 docstring。
        rules: 声明式规则集合预设列表，用于接收通过 Rules.xxx() 生成的策略载荷并组合。
        settings: 工具的高阶配置对象 (ToolOptions)，用于控制极速缓存、人工审批拦截、静默执行等扩展能力。
        tags: 工具的标签列表，用于被 Agent 的智能字符串路由识别并进行能力注入。
        auto_register: 是否自动注册到当前命名空间的工具注册表，默认为 False，若为 True 方可通过字符串调用。
        require_prefix: 是否自动添加插件命名空间前缀（仅对游离函数生效），默认为 False。
    返回:
        Callable | FunctionTool: 包装后的函数或方法。
    """  # noqa: E501

    def decorator(func: Callable):
        base_settings = settings or ToolOptions()

        if rules:
            rule_list = rules if isinstance(rules, list) else [rules]
            for r in rule_list:
                if isinstance(r, ToolOptions):
                    base_settings = base_settings.merge(r)

        if tags:
            base_settings.tags = list(set(base_settings.tags + tags))
        existing_settings = getattr(func, "__tool_settings__", None)
        if existing_settings and isinstance(existing_settings, ToolOptions):
            base_settings = existing_settings.merge(base_settings)

        reqs = getattr(func, "__sandbox_requirements__", None)
        if reqs:
            if not base_settings.sandbox_requirements:
                base_settings.sandbox_requirements = reqs
            else:
                base_settings.sandbox_requirements.update(reqs)

        tool_name = name or func.__name__
        tool_desc = description

        is_method = False
        if (
            hasattr(func, "__qualname__")
            and "." in func.__qualname__
            and "<locals>" not in func.__qualname__
        ):
            is_method = True
        else:
            try:
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                if params and params[0] in ("self", "cls"):
                    is_method = True
            except Exception:
                pass

        if is_method:
            return ToolkitMethodDescriptor(
                func=func, name=tool_name, description=tool_desc, settings=base_settings
            )
        else:
            if require_prefix:
                from zhenxun.utils.utils import infer_plugin_namespace

                ns = infer_plugin_namespace(default="global")
                if ns and ns not in ("global", "unknown"):
                    if not tool_name.startswith(f"{ns}_"):
                        tool_name = f"{ns}_{tool_name}"

            from zhenxun.services.ai.tools.engine.registry import tool_provider_manager

            from .tool import FunctionTool

            func_tool = FunctionTool(
                func=func,
                name=tool_name,
                description=tool_desc,
                settings=base_settings,
            )
            if auto_register:
                tool_provider_manager.register_tool(func_tool)

            setattr(func_tool, "__tool_settings__", base_settings)
            return func_tool

    return decorator


def with_cache(ttl: int = 3600, cache_function: Callable | None = None) -> ToolOptions:
    """开启极速缓存，阻止参数相同的重复请求发往底层。"""
    return ToolOptions(
        capabilities=[CacheCapability(ttl=ttl, cache_function=cache_function)]
    )


def silent() -> ToolOptions:
    """静默执行。工具执行过程与结果不会作为界面流渲染给用户，仅作大模型内部参考。"""
    return ToolOptions(silent=True)


def direct_reply() -> ToolOptions:
    """直出模式。工具执行完毕后强制中断大模型的思考循环，将工具输出作为最终回答返回。"""

    class DirectReplyCapability(AbstractCapability):
        async def wrap_tool_execute(self, context, tool_name, arguments, handler):
            result = await handler(arguments)
            if isinstance(result, ToolResult):
                if getattr(result, "is_error", False):
                    return result
                return EndRunResult(output=result.output)
            return EndRunResult(output=result)

    return ToolOptions(capabilities=[DirectReplyCapability()])


def interactive() -> ToolOptions:
    """开启交互式参数补全。如果参数缺失或校验失败，
    会主动通过 Bot 向用户提问要求补全。"""
    return ToolOptions(capabilities=[InteractiveCapability()])


def require_approval() -> ToolOptions:
    """高危操作标记。调用前将拦截并发送至群组要求超管人工审核。"""
    return ToolOptions(capabilities=[ApprovalCapability()])


def fallback(tool_name: str) -> ToolOptions:
    """降级备用工具。主工具执行失败时自动路由至备用工具。"""
    return ToolOptions(capabilities=[FallbackCapability(fallback_tool_name=tool_name)])


def before_execute(hook_func: Callable) -> ToolOptions:
    """生命周期拦截：在工具即将执行前触发，可在此篡改全局状态或通过依赖注入访问当前参数。"""
    return ToolOptions(capabilities=[LifecycleCapability(before_execute=hook_func)])


def after_execute(hook_func: Callable) -> ToolOptions:
    """生命周期拦截：在工具执行完毕后触发，可在此修改将发往大模型的返回结果。"""
    return ToolOptions(capabilities=[LifecycleCapability(after_execute=hook_func)])


def validate_args(hook_func: Callable) -> ToolOptions:
    """生命周期拦截：在工具反序列化校验前触发。如果校验失败直接抛出异常，将触发大模型重试机制。"""
    return ToolOptions(capabilities=[LifecycleCapability(validate_args=hook_func)])


def prepare_tool(hook_func: Callable) -> ToolOptions:
    """生命周期拦截：在向大模型渲染 JSON Schema 前触发，
    可在此动态修改该工具的定义或返回 None 对大模型隐藏。"""
    return ToolOptions(capabilities=[LifecycleCapability(prepare_tool=hook_func)])


def require_superuser() -> ToolOptions:
    """仅限超级管理员使用。若调用者无权限，该工具将直接对大模型隐藏。"""
    return ToolOptions(capabilities=[SuperuserCapability()])


def require_admin_level(min_level: int = 1) -> ToolOptions:
    """仅限满足指定群聊权限等级的用户使用。"""
    return ToolOptions(
        capabilities=[AdminLevelCapability(min_level)],
        metadata={"admin_level": min_level},
    )


def require_group() -> ToolOptions:
    """限制该工具仅能在群聊环境中被大模型调用。"""
    return ToolOptions(capabilities=[GroupOnlyCapability()])


def require_minimum_gold(amount: int) -> ToolOptions:
    """需满足一定的金币余额才可调用，同时执行后会自动扣除该金币。"""
    return ToolOptions(metadata={"cost_gold": amount})


def require_session_state(key: str, expected_value: Any = None) -> ToolOptions:
    """需要当前执行上下文中包含指定状态变量，否则隐藏工具"""

    class StateDependencyCapability(AbstractCapability):
        async def prepare_tools(
            self, context: RunContext, tool_defs: list[Any]
        ) -> list[Any]:
            val = context.state.get(key)
            if expected_value is not None:
                if val == expected_value:
                    return tool_defs
            elif val is not None and val is not False:
                return tool_defs
            return []

    return ToolOptions(capabilities=[StateDependencyCapability()])


class Rules:
    """
    [命名空间] 大模型工具规则与权限装饰器聚合。
    用于控制工具的极速缓存、沙箱要求、前端静默以及人机交互审批等。
    """

    @staticmethod
    def combine(*rules: ToolOptions) -> ToolOptions:
        """打包组合多个规则预设载荷"""
        base = ToolOptions()
        for r in rules:
            if isinstance(r, ToolOptions):
                base = base.merge(r)
        return base

    sandbox = staticmethod(require_sandbox)
    """环境：声明执行该工具所需的物理沙箱及 pip/npm 包依赖"""

    approval = staticmethod(require_approval)
    """高危：拦截执行，并在群内发起人工审批 (HITL) 确认"""

    superuser = staticmethod(require_superuser)
    """权限：仅允许被超级管理员触发时，该工具才对大模型可见"""

    admin_level = staticmethod(require_admin_level)
    """权限：要求触发用户的群等级达到指定级别"""

    group_only = staticmethod(require_group)
    """环境：限制该工具只有在群聊场景下才可被调用"""

    minimum_gold = staticmethod(require_minimum_gold)
    """经济：要求用户满足金币余额，且执行后将自动扣除该金币"""

    session_state = staticmethod(require_session_state)
    """状态：要求当前会话上下文中包含指定的状态变量时才可用"""

    before_execute = staticmethod(before_execute)
    """拦截器：在工具正式执行前触发，可动态修改传入参数 (利用 DI 注入)"""

    after_execute = staticmethod(after_execute)
    """拦截器：在工具执行完毕后触发，可动态修改返回结果 (利用 DI 注入)"""

    validate_args = staticmethod(validate_args)
    """拦截器：在工具参数校验前触发，校验失败抛出异常直接由大模型重试"""

    prepare_tool = staticmethod(prepare_tool)
    """拦截器：向大模型渲染 JSON Schema 前触发，可篡改工具定义对象"""

    cache = staticmethod(with_cache)
    """性能：开启极速缓存，参数相同时直接返回上次结果，免去重复计算"""

    silent = staticmethod(silent)
    """UI：静默执行，执行过程与结果不以气泡形式发送给用户，仅供大模型参考"""

    interactive = staticmethod(interactive)
    """交互：开启交互式参数补全，必填参数缺失时自动向用户提问补全"""

    fallback = staticmethod(fallback)
    """容错：执行失败时透明降级重定向至备用工具"""

    direct_reply = staticmethod(direct_reply)
    """流控：直出模式，执行后直接将结果发给用户并强制中断大模型后续思考"""


__all__ = [
    "Rules",
    "tool",
]
