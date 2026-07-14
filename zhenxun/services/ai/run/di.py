from collections import defaultdict
from collections.abc import Awaitable, Callable
from functools import lru_cache
import inspect
from typing import Annotated, Any, ClassVar, cast


@lru_cache(maxsize=2048)
def _get_signature_cached(func: Callable) -> inspect.Signature:
    return inspect.signature(func)


def get_signature(func: Callable) -> inspect.Signature:
    try:
        return _get_signature_cached(func)
    except TypeError:
        return inspect.signature(func)


from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.utils import is_coroutine_callable
from nonebot_plugin_session import EventSession, extract_session

from zhenxun.utils.utils import infer_plugin_namespace

from .blackboard import BlackboardManager
from .context import ProviderFunc, RunContext, _is_run_context_type
from .hitl import HITLController
from .ui import UIController


class Hidden:
    """
    标记一个参数为隐藏参数。
    在生成大模型工具 Schema 时，带有该标记的 Annotated 参数将被剔除。
    """

    pass


class _InjectMarker:
    """内部魔术标记，用于识别 DI 类型糖"""

    def __init__(self, key: str):
        self.key = key


class _UpstreamResultMarker:
    """内部魔术标记，用于精准捕获上游特定节点的产出"""

    def __init__(self, step_name: str):
        self.step_name = step_name


CurrentUserId = Annotated[str | None, Hidden(), _InjectMarker("user_id")]
CurrentGroupId = Annotated[str | None, Hidden(), _InjectMarker("group_id")]
CurrentPlatform = Annotated[str, Hidden(), _InjectMarker("platform")]
CurrentBot = Annotated[Bot | None, Hidden(), _InjectMarker("bot")]
CurrentEvent = Annotated[Event | None, Hidden(), _InjectMarker("event")]
CurrentMatcher = Annotated[Matcher | None, Hidden(), _InjectMarker("matcher")]
CurrentSession = Annotated[EventSession | None, Hidden(), _InjectMarker("session")]
CurrentUI = Annotated[UIController, Hidden(), _InjectMarker("ui")]
CurrentHITL = Annotated[HITLController, Hidden(), _InjectMarker("hitl")]
CurrentModelName = Annotated[str | None, Hidden(), _InjectMarker("model_name")]
CurrentToolRetries = Annotated[int, Hidden(), _InjectMarker("tool_retries")]
CurrentState = Annotated[dict[str, Any], Hidden(), _InjectMarker("state")]
CurrentSharedState = Annotated[dict[str, Any], Hidden(), _InjectMarker("shared_state")]
CurrentOriginalInput = Annotated[str, Hidden(), _InjectMarker("original_input")]
UpstreamResults = Annotated[dict[str, Any], Hidden(), _InjectMarker("upstream_results")]
CurrentEventPayload = Annotated[Any, Hidden(), _InjectMarker("stream_event")]
CurrentBlackboard = Annotated[
    BlackboardManager | None, Hidden(), _InjectMarker("blackboard")
]

CurrentSandbox = Annotated[Any, Hidden(), _InjectMarker("sandbox")]


class Inject:
    """
    [命名空间] 大模型工具依赖注入类型糖。
    用于在工具参数中快捷获取群聊/用户的上下文，或注册自定义的依赖提供者。
    """

    _providers: ClassVar[dict[str, dict[str, ProviderFunc]]] = defaultdict(dict)

    @classmethod
    def register_provider(
        cls, key: str, provider: ProviderFunc, scope: str | None = None
    ) -> None:
        """
        (底层) 注册一个自定义的依赖提供者。
        """
        ns = scope if scope is not None else infer_plugin_namespace()
        cls._providers[key][ns] = provider

    @classmethod
    def provider(cls, key: str, scope: str | None = None):
        def decorator(func: ProviderFunc) -> ProviderFunc:
            cls.register_provider(key, func, scope)
            return func

        return decorator

    @classmethod
    def bind(cls, key: str, type_hint: Any = Any) -> Any:
        """
        动态生成一个用于类型注解的 Inject 标记。
        示例：DbSession = Inject.bind("db_session", AsyncSession)
        """
        return Annotated[type_hint, Hidden(), _InjectMarker(key)]

    @classmethod
    def UpstreamResult(cls, step_name: str) -> Any:
        """
        自动注入：精准获取工作流中指定前置节点的产出。可作为参数的默认值使用以通过静态检查。
        示例: def my_step(data: str = Inject.UpstreamResult("AgentA")):
        """
        return _UpstreamResultMarker(step_name)

    CurrentBlackboardState = Annotated[Any, Hidden(), _InjectMarker("blackboard_state")]
    BlackboardState = CurrentBlackboardState
    """自动注入：当前挂载的强类型黑板底层 Pydantic 实例（需配合 Annotated 使用）"""

    UserId = CurrentUserId
    """自动注入：触发当前任务的用户 ID"""

    GroupId = CurrentGroupId
    """自动注入：触发当前任务的群聊 ID（私聊时为 None）"""

    Platform = CurrentPlatform
    """自动注入：当前对接的平台名称（如 qq）"""

    Bot = CurrentBot
    """自动注入：当前的 NoneBot Bot 实例"""

    Event = CurrentEvent
    """自动注入：当前的 NoneBot Event 实例"""

    Matcher = CurrentMatcher
    """自动注入：当前的 NoneBot Matcher 实例"""

    Session = CurrentSession
    """自动注入：当前的 nonebot_plugin_session 会话实例"""

    UI = CurrentUI
    """自动注入：前端 UI 控制器实例"""

    HITL = CurrentHITL
    """自动注入：人机协同控制器实例 (提供底层 waiter 交互封装)"""

    ModelName = CurrentModelName
    """自动注入：当前大模型正在执行的底层模型名称"""

    ToolRetries = CurrentToolRetries
    """自动注入：当前工具正在经历的重试次数 (Int)"""

    State = CurrentState
    """自动注入：当前 Agent 轮次隔离的业务状态字典"""

    OriginalInput = CurrentOriginalInput
    """自动注入：当前工作流最初始的用户输入文本"""

    SharedState = CurrentSharedState
    """自动注入：当前 Team 或全局穿透的共享黑板状态字典"""

    UpstreamResults = UpstreamResults
    """自动注入：当前工作流中所有上游节点的产出字典 (Key: Agent Name)"""

    EventPayload = CurrentEventPayload
    """自动注入：当前生命周期钩子流转中的 EventBus 载荷实体"""

    Blackboard = CurrentBlackboard
    """自动注入：当前工作流/团队挂载的强类型黑板 (BlackboardManager) 实例"""

    Sandbox = CurrentSandbox
    """自动注入：当前沙箱环境管理器实例"""


class BaseParamResolver:
    """可插拔参数解析器基类协议"""

    def match(
        self,
        name: str,
        param: inspect.Parameter,
        context: RunContext,
    ) -> bool:
        return False

    def static_match(self, param: inspect.Parameter) -> bool:
        return False

    async def resolve(
        self,
        name: str,
        param: inspect.Parameter,
        context: RunContext,
    ) -> Any:
        raise NotImplementedError


class RunContextResolver(BaseParamResolver):
    def match(self, name, param, context) -> bool:
        return _is_run_context_type(param.annotation)

    def static_match(self, param: inspect.Parameter) -> bool:
        return _is_run_context_type(param.annotation)

    async def resolve(self, name, param, context) -> Any:
        return context


class TypeSugarResolver(BaseParamResolver):
    def _get_marker(self, param: inspect.Parameter) -> Any | None:
        anno = param.annotation
        if hasattr(anno, "__metadata__"):
            for arg in anno.__metadata__:
                if isinstance(arg, _InjectMarker | _UpstreamResultMarker):
                    return arg
        default_val = param.default
        if isinstance(default_val, _InjectMarker | _UpstreamResultMarker):
            return default_val
        if hasattr(default_val, "__metadata__"):
            for arg in default_val.__metadata__:
                if isinstance(arg, _InjectMarker | _UpstreamResultMarker):
                    return arg
        return None

    def match(self, name, param, context) -> bool:
        return self._get_marker(param) is not None

    def static_match(self, param: inspect.Parameter) -> bool:
        return self._get_marker(param) is not None

    async def resolve(self, name, param, context) -> Any:
        marker = self._get_marker(param)
        if not marker:
            raise ValueError(f"参数 {name} 缺失 Inject 标记")
        if isinstance(marker, _UpstreamResultMarker):
            return context.upstream_results.get(marker.step_name)

        marker_key = marker.key

        if marker_key in context.di_cache:
            return context.di_cache[marker_key]

        provider_dict = Inject._providers.get(marker_key, {})
        ns = getattr(context.session, "namespace", "global")
        provider = provider_dict.get(ns) or provider_dict.get("global")

        if not provider:
            raise ValueError(f"未知的类型糖标记或未注册的 Provider: {marker_key}")

        if is_coroutine_callable(provider):
            result = await provider(context)
        else:
            result = provider(context)

        context.di_cache[marker_key] = result
        return result


class DependencyInjector:
    """可插拔依赖注入管线 (Resolver Pipeline)"""

    _resolvers: ClassVar[list[BaseParamResolver]] = []

    @classmethod
    def register(cls, resolver: BaseParamResolver) -> None:
        cls._resolvers.append(resolver)

    @classmethod
    def can_resolve_statically(cls, param: inspect.Parameter) -> bool:
        for resolver in cls._resolvers:
            if resolver.static_match(param):
                return True
        return False

    @classmethod
    async def resolve_all(
        cls,
        sig: inspect.Signature,
        call_kwargs: dict[str, Any],
        context: RunContext,
    ) -> dict[str, Any]:
        resolved_kwargs = dict(call_kwargs)
        for k, v in call_kwargs.items():
            context.di_cache[k] = v
        for name, param in sig.parameters.items():
            if name in ("self", "cls") or name in resolved_kwargs:
                continue

            if param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue

            resolved = False
            for resolver in cls._resolvers:
                if resolver.match(name, param, context):
                    val = await resolver.resolve(name, param, context)
                    resolved_kwargs[name] = val
                    resolved = True
                    break

            if not resolved:
                if param.default is not inspect.Parameter.empty:
                    continue

                raise ValueError(
                    f"参数 '{name}' 未被大模型提供且缺少显式的依赖注入标记"
                    "(如 Inject.* 或 RunContext)。"
                )

        return resolved_kwargs

    @classmethod
    async def invoke(
        cls,
        func: Callable[..., Any],
        call_kwargs: dict[str, Any],
        context: RunContext,
    ) -> Any:
        """统一执行带有依赖注入的函数 (支持同步/异步)"""
        sig = get_signature(func)
        resolved_kwargs = await cls.resolve_all(sig, call_kwargs, context)
        filtered_kwargs = {
            k: v for k, v in resolved_kwargs.items() if k in sig.parameters
        }

        if is_coroutine_callable(func):
            return await cast(Callable[..., Awaitable[Any]], func)(**filtered_kwargs)
        return cast(Callable[..., Any], func)(**filtered_kwargs)


DependencyInjector.register(RunContextResolver())
DependencyInjector.register(TypeSugarResolver())

Inject.register_provider("user_id", lambda ctx: ctx.get_user_id(), scope="global")
Inject.register_provider("group_id", lambda ctx: ctx.get_group_id(), scope="global")
Inject.register_provider("platform", lambda ctx: ctx.get_platform(), scope="global")
Inject.register_provider("bot", lambda ctx: ctx.get_bot(), scope="global")
Inject.register_provider("event", lambda ctx: ctx.get_event(), scope="global")
Inject.register_provider("matcher", lambda ctx: ctx.get_matcher(), scope="global")

Inject.register_provider("hitl", lambda ctx: HITLController(ctx), scope="global")
Inject.register_provider(
    "model_name", lambda ctx: ctx.run.current_model, scope="global"
)

Inject.register_provider(
    "tool_retries", lambda ctx: ctx.call.retry_count, scope="global"
)

Inject.register_provider(
    "original_input", lambda ctx: ctx.run.user_input, scope="global"
)
Inject.register_provider("state", lambda ctx: ctx.state, scope="global")
Inject.register_provider(
    "upstream_results", lambda ctx: ctx.upstream_results, scope="global"
)
Inject.register_provider(
    "shared_state", lambda ctx: ctx.session.shared_state, scope="global"
)


def _resolve_blackboard(ctx: RunContext):
    bb = ctx.session.blackboard
    if bb is None:
        raise ValueError(
            "Inject.Blackboard 注入失败："
            "当前会话上下文中未挂载 BlackboardManager 实例。"
        )
    return bb


Inject.register_provider("blackboard", _resolve_blackboard, scope="global")


def _resolve_blackboard_state(ctx: RunContext):
    bb = ctx.session.blackboard
    if bb is None:
        raise ValueError(
            "Inject.BlackboardState 注入失败："
            "当前会话上下文中未挂载 BlackboardManager 实例。"
        )
    return bb._state


Inject.register_provider("blackboard_state", _resolve_blackboard_state, scope="global")


def _resolve_session(ctx):
    bot = ctx.get_bot()
    event = ctx.get_event()
    return extract_session(bot, event) if bot and event else None


Inject.register_provider("session", _resolve_session, scope="global")
Inject.register_provider("ui", lambda ctx: UIController(ctx), scope="global")

from zhenxun.services.ai.sandbox.manager import sandbox_manager

Inject.register_provider("sandbox", lambda ctx: sandbox_manager, scope="global")


__all__ = [
    "DependencyInjector",
    "Hidden",
    "Inject",
]
