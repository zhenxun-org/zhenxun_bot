import asyncio
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
import dataclasses
import inspect
from typing import Any, Generic, cast, get_origin
from typing_extensions import TypeVar

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from pydantic import BaseModel, ConfigDict, Field

from zhenxun.services.ai.core.messages import AgentEvent, AgentMessage
from zhenxun.services.ai.utils import ContextUtils
from zhenxun.utils.utils import infer_plugin_namespace

AgentDepsT = TypeVar("AgentDepsT", default=Any)
"""泛型类型变量：外部环境依赖对象 (Agent Dependencies)。"""
ProviderFunc = Callable[["RunContext"], Any | Awaitable[Any]]
"""函数签名类型别名：依赖提供者函数 (Dependency Provider)。"""
ToolsPrepareFunc = Callable[["RunContext[AgentDepsT]", list[Any]], Any | Awaitable[Any]]
"""全局/Agent 级动态工具干预函数类型"""


class NoneBotDeps(BaseModel):
    """NoneBot 环境下的标准依赖容器。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    bot: Bot | None = Field(default=None)
    """当前触发事件的 Bot 实例"""

    event: Event | None = Field(default=None)
    """当前触发的事件实例"""

    matcher: Matcher | None = Field(default=None)
    """当前处理该事件的 Matcher 实例"""

    @classmethod
    def get_current(cls) -> "NoneBotDeps | None":
        """利用 NoneBot 原生魔法，基于 ContextVars 隐式提取当前执行上下文"""
        try:
            from nonebot.matcher import current_bot, current_event, current_matcher

            bot = current_bot.get(None)
            event = current_event.get(None)
            matcher = current_matcher.get(None)
            if bot or event:
                return cls(bot=bot, event=event, matcher=matcher)
        except Exception:
            pass
        return None


@dataclasses.dataclass
class SessionContext(Generic[AgentDepsT]):
    """
    生命周期：Session（会话）层。
    跨越多次对话轮次，负责保存长线状态与物理隔离信息。
    """

    session_id: str
    """核心会话标识符，用于区分不同用户或群组的上下文隔离。"""
    deps: AgentDepsT
    """强类型的外部依赖注入对象（如 Bot, Event），供跨工具共享。"""
    shared_state: dict[str, Any] = dataclasses.field(default_factory=dict)
    """共享状态字典：全局引用穿透，用于主智能体与嵌套子智能体之间的数据通信。"""
    auth_tokens: dict[str, str] = dataclasses.field(default_factory=dict)
    """授权凭证字典：保存用户针对各 Provider 的 OAuth 或 API Token。"""
    blackboard: Any | None = None
    """结构化黑板管理器，作为共享状态的高级替代方案，提供并发锁和强类型校验。"""
    namespace: str = "global"
    """触发事件的插件命名空间"""
    append_only_manager: Any = dataclasses.field(default=None)
    """用于大模型前缀缓存命中优化的追加写入管理器。"""

    @property
    def memory(self) -> Any:
        """
        获取当前会话的持久化记忆访问门面 (AgentSessionFacade)。
        提供极简的 history 和 slots 操作 API。
        """
        from zhenxun.services.ai.context.memory.facades import AgentSessionFacade
        from zhenxun.services.ai.context.memory.manager import memory_manager
        from zhenxun.services.ai.context.memory.types import SessionMetadata
        from zhenxun.services.ai.utils.scope import ScopeSelector

        user_id = ContextUtils.extract_user_id(self.deps)
        group_id = ContextUtils.extract_group_id(self.deps)
        platform = ContextUtils.extract_platform(self.deps)

        meta = SessionMetadata(
            session_id=self.session_id,
            selector=ScopeSelector(
                user_id=user_id,
                group_id=group_id,
                platform=platform,
                namespace=self.namespace,
            )
        )
        return AgentSessionFacade(memory_manager, meta)


@dataclasses.dataclass
class AgentRunContext(Generic[AgentDepsT]):
    """
    生命周期：Run（运行）层。
    伴随 Agent 的单次执行 (run_stream)，保存大模型推理时的状态与原生消息历史。
    """

    session: SessionContext[AgentDepsT]
    """指向所属 Session 层上下文的引用。"""
    state: dict[str, Any] = dataclasses.field(default_factory=dict)
    """状态字典：用于在当前 Agent 执行轮次、工具和中间件中透传动态变量。"""
    agent_name: str | None = None
    """当前正在执行的 Agent 名称。"""
    current_model: str | None = None
    """当前实际调用的底层大模型名称 (Provider/Model)。"""
    user_input: str | None = None
    """当前轮次用户的原始文本输入。"""
    messages: list[AgentMessage] = dataclasses.field(default_factory=list)
    """大模型原生上下文 (LLMMessage 列表)，与执行器中的执行历史保持内存引用同步。"""
    hitl_locks: dict[str, asyncio.Lock] = dataclasses.field(default_factory=dict)
    """人机交互 (HITL) 并发锁，防止同群组内并发审批冲突。"""
    delegate_depth: int = 0
    """子智能体委派深度标记，用于防范无限递归嵌套。"""
    tool_retries: dict[str, int] = dataclasses.field(default_factory=dict)
    """记录当前轮次内各个工具的累积失败重试次数，用于系统熔断。"""
    cancellation_token: Any | None = None
    """全局级联取消令牌，用于跨 Agent 的协程挂起中断。"""
    event_bus: Any | None = None
    """底层的事件流发射器 (EventBus)，由执行引擎在运行时挂载。"""

    dynamic_prompts: dict[str, str] = dataclasses.field(default_factory=dict)
    """动态提示词字典（保持插入顺序并去重）。
    仅在 HTTP 请求前 JIT 渲染，不会污染持久化的上下文对话历史。"""

    def add_system_prompt(self, prompt: str, key: str | None = None) -> None:
        """动态追加临时系统提示词到大模型上下文中（实时生效且不污染历史）。"""
        dict_key = key or prompt
        if prompt:
            self.dynamic_prompts[dict_key] = prompt

    def add_event(self, event: AgentEvent) -> None:
        """向当前运行上下文中安全追加业务事件"""
        self.messages.append(event)


@dataclasses.dataclass
class ToolCallContext(Generic[AgentDepsT]):
    """
    生命周期：Call（工具调用）层。
    单次工具调用分配的绝对私有状态，彻底消灭并发调用时的属性污染。
    """

    run: AgentRunContext[AgentDepsT]
    """指向所属 Run 层上下文的引用。"""
    tool_call_id: str = "unknown"
    """大模型为本次工具调用分配的唯一 ID。"""
    tool_name: str = "unknown"
    """本次调用的工具名称。"""
    retry_count: int = 0
    """当前工具调用的重试序号 (第几次重试)。"""
    current_tool: Any | None = None
    """当前工具的可执行实例 (ToolExecutable)。"""


@dataclasses.dataclass
class RunContext(Generic[AgentDepsT]):
    """
    依赖注入容器（DI Container），保留原有上下文信息的同时提升获取类型的能力。
    """

    session_id: str | None = None
    """当前运行所在的会话ID，用于区分不同用户的独立上下文"""

    di_cache: dict[str, Any] = dataclasses.field(default_factory=dict)
    """依赖注入引擎的缓存容器，支持父子层级浅拷贝隔离"""

    state: dict[str, Any] = dataclasses.field(default_factory=dict)
    """状态字典：用于在会话轮次、工具和中间件中透传动态变量"""

    shared_state: dict[str, Any] = dataclasses.field(default_factory=dict)
    """共享状态字典：全局引用穿透，用于主智能体与嵌套子智能体之间的
    黑板模式 (Blackboard) 数据通信"""

    upstream_results: dict[str, Any] = dataclasses.field(default_factory=dict)
    """前置节点产出字典：标准化的数据流载荷契约，键为 Agent Name，值为输出内容"""

    capabilities: list[Any] = dataclasses.field(default_factory=list)
    """当前上下文绑定的拦截器 (Capabilities) 链，用于生命周期拦截"""

    deps: AgentDepsT = dataclasses.field(default=cast(AgentDepsT, None))
    """强类型的外部依赖注入对象，用于跨工具共享业务状态或配置"""

    session: SessionContext[AgentDepsT] = dataclasses.field(
        init=False, repr=False, compare=False
    )
    """会话层上下文：承载跨轮次共享的依赖与共享状态引用。"""
    run: AgentRunContext[AgentDepsT] = dataclasses.field(
        init=False, repr=False, compare=False
    )
    """运行层上下文：承载当前 Agent 执行轮次的模型状态与运行时元信息。"""
    call: ToolCallContext[AgentDepsT] = dataclasses.field(
        init=False, repr=False, compare=False
    )
    """调用层上下文：承载单次工具调用的私有状态与执行引用。"""
    _is_auto_session_id: bool = dataclasses.field(
        default=False, init=False, repr=False, compare=False
    )
    """标记 session_id 是否为框架隐式生成的。"""

    def get_bot(self) -> Bot | None:
        """强类型安全地提取 Bot 实例"""
        if not self.deps:
            return None
        bot = getattr(self.deps, "bot", None)
        return bot if isinstance(bot, Bot) else None

    def get_event(self) -> Event | None:
        """强类型安全地提取 Event 实例"""
        if not self.deps:
            return None
        event = getattr(self.deps, "event", None)
        return event if isinstance(event, Event) else None

    def get_matcher(self) -> Matcher | None:
        """强类型安全地提取 Matcher 实例"""
        if not self.deps:
            return None
        matcher = getattr(self.deps, "matcher", None)
        return matcher if isinstance(matcher, Matcher) else None

    def get_user_id(self) -> str | None:
        """安全提取当前触发任务的用户 ID"""
        return ContextUtils.extract_user_id(self.deps)

    def get_group_id(self) -> str | None:
        """安全提取当前触发任务的群组 ID（私聊则为 None）"""
        return ContextUtils.extract_group_id(self.deps)

    def get_platform(self) -> str:
        """安全提取当前连接的适配器平台标识"""
        return ContextUtils.extract_platform(self.deps)

    def __post_init__(self):
        if self.deps is None:
            self.deps = cast(AgentDepsT, NoneBotDeps.get_current())

        if not self.session_id and self.deps:
            from zhenxun.services.ai.context.memory.types import (
                Isolation,
            )

            bot = self.get_bot()
            event = self.get_event()

            if bot and event:
                meta = ContextUtils.generate_session_meta(
                    bot, event, scope_builder=Isolation.AGENT_USER()
                )
                self.session_id = meta.session_id
                self._is_auto_session_id = True
            else:
                uid = self.get_user_id()
                gid = self.get_group_id()
                if uid and gid:
                    self.session_id = f"auto_{gid}_{uid}"
                    self._is_auto_session_id = True
                elif uid:
                    self.session_id = f"auto_private_{uid}"
                    self._is_auto_session_id = True

        ns = "global"
        if self.deps:
            ns = getattr(self.deps, "namespace", None) or (
                self.deps.get("namespace") if isinstance(self.deps, dict) else None
            )
        if not ns:
            ns = infer_plugin_namespace(default="global")

        self.session = SessionContext(
            session_id=self.session_id or "default_session",
            deps=self.deps,
            shared_state=self.shared_state,
            namespace=ns,
        )
        from zhenxun.services.ai.core.engine.append_only import (
            AppendOnlyContextManager,
        )

        self.session.append_only_manager = AppendOnlyContextManager()

        self.run = AgentRunContext(session=self.session, state=self.state)
        self.call = ToolCallContext(run=self.run)

    def clone_for_execution(self, **kwargs) -> "RunContext":
        new_state = self.state.copy()
        changes = {k: v for k, v in kwargs.items() if hasattr(self, k)}
        if "state" not in changes:
            changes["state"] = new_state
        if "shared_state" not in changes:
            changes["shared_state"] = self.shared_state

        new_ctx = dataclasses.replace(cast(Any, self), **changes)
        new_ctx.upstream_results = self.upstream_results.copy()
        new_ctx.di_cache = self.di_cache.copy()

        new_ctx.session = self.session
        new_ctx.run = AgentRunContext(
            session=new_ctx.session,
            state=new_ctx.state,
            agent_name=self.run.agent_name,
            current_model=self.run.current_model,
            user_input=self.run.user_input,
            messages=list(self.run.messages),
            hitl_locks=self.run.hitl_locks,
            delegate_depth=self.run.delegate_depth,
            tool_retries=self.run.tool_retries,
            cancellation_token=self.run.cancellation_token,
            event_bus=self.run.event_bus,
            dynamic_prompts=self.run.dynamic_prompts.copy(),
        )
        new_ctx.call = ToolCallContext(run=new_ctx.run)
        return new_ctx

    def clone_for_tool_call(self, tool_call_id: str, tool_name: str) -> "RunContext":
        """
        为每个并发的工具调用派生绝对独立的 ToolCallContext。消除多工具并行时的属性污染。
        """
        new_ctx = dataclasses.replace(cast(Any, self))
        new_ctx.di_cache = self.di_cache.copy()
        new_ctx.session = self.session
        new_ctx.run = self.run
        new_ctx.call = ToolCallContext(
            run=new_ctx.run,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            retry_count=new_ctx.run.tool_retries.get(tool_name, 0),
        )
        return new_ctx

    def clone_for_member(self, member_name: str = "unknown") -> "RunContext":
        """
        为团队子成员克隆上下文。
        强制清空消息历史并分配独立 SessionID，实现绝对的记忆沙箱物理隔离。
        """
        new_ctx = self.clone_for_execution()
        new_ctx.run.delegate_depth = 0
        new_ctx.run.messages = []
        new_ctx.capabilities = []

        import uuid

        base_sid = self.session_id or "default"
        new_ctx.session_id = f"{base_sid}/sub_{member_name}_{uuid.uuid4().hex[:6]}"
        new_ctx.session.session_id = new_ctx.session_id
        return new_ctx


_CURRENT_RUN_CONTEXT: ContextVar[RunContext | None] = ContextVar(
    "current_run_context", default=None
)


def get_current_run_context() -> RunContext | None:
    """
    [全局逃生舱] 获取当前运行中的上下文对象。
    适用于深层嵌套业务逻辑，无需层层透传 context 参数。
    """
    return _CURRENT_RUN_CONTEXT.get()


@contextmanager
def set_run_context(ctx: RunContext):
    """[内部 API] 挂载当前上下文至全局"""
    token = _CURRENT_RUN_CONTEXT.set(ctx)
    try:
        yield
    finally:
        _CURRENT_RUN_CONTEXT.reset(token)


def _is_run_context_type(annotation: Any) -> bool:
    if annotation is RunContext:
        return True
    origin = get_origin(annotation)
    if origin is RunContext:
        return True
    if inspect.isclass(origin) and issubclass(origin, RunContext):
        return True
    if inspect.isclass(annotation) and issubclass(annotation, RunContext):
        return True
    if "RunContext" in str(annotation):
        return True
    return False


__all__ = [
    "AgentDepsT",
    "AgentRunContext",
    "NoneBotDeps",
    "RunContext",
    "SessionContext",
    "ToolCallContext",
    "ToolsPrepareFunc",
    "get_current_run_context",
    "set_run_context",
]
