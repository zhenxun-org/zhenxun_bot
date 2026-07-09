import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
import contextlib
from pathlib import Path
from typing import Any, Generic, cast

from zhenxun.services.ai.capabilities import (
    CapabilitySource,
    CombinedCapability,
)
from zhenxun.services.ai.config import get_llm_config
from zhenxun.services.ai.context.knowledge.base import BaseKnowledge
from zhenxun.services.ai.context.memory.builder import MemoryBuilder
from zhenxun.services.ai.context.memory.capabilities import (
    AgenticMemoryCapability,
    SlotMemoryCapability,
)
from zhenxun.services.ai.context.memory.models import MemoryConfig
from zhenxun.services.ai.core.exceptions import (
    ConcurrencyInterruptException,
    ControlFlowExit,
)
from zhenxun.services.ai.core.messages import (
    LLMMessage,
    PromptInput,
    UsageInfo,
)
from zhenxun.services.ai.core.models import CancellationToken
from zhenxun.services.ai.core.options import (
    BaseOutputDefinition,
    GenerationConfig,
)
from zhenxun.services.ai.core.protocols.tool import ToolExecutable, ToolResolvable
from zhenxun.services.ai.core.stream_events import AgentStreamEvent, EventBus
from zhenxun.services.ai.core.templates import PromptTemplate
from zhenxun.services.ai.flow.base import BaseRunnable, ConcurrencyPolicy
from zhenxun.services.ai.flow.concurrency import apply_concurrency_policy
from zhenxun.services.ai.guardrails import GuardrailSource, parse_guardrails
from zhenxun.services.ai.llm.builder import IntentBuilder
from zhenxun.services.ai.message_builder import MessageBuilder
from zhenxun.services.ai.run import (
    AgentRunResult,
    AgentTask,
    RunContext,
)
from zhenxun.services.ai.run.context import AgentDepsT
from zhenxun.services.ai.run.di import DependencyInjector
from zhenxun.services.ai.run.models import (
    AgentRunEnd,
    AgentRunError,
    AgentRunStart,
    OutputDataT,
    StreamedRunResult,
)
from zhenxun.services.ai.run.subscribers import (
    DefaultUISubscriber,
    TelemetrySubscriber,
)
from zhenxun.services.ai.tools.bridges.delegate import DelegateTool
from zhenxun.services.ai.tools.core.tool import BaseTool, FunctionTool
from zhenxun.services.ai.tools.core.toolkit import BaseToolkit
from zhenxun.services.ai.tools.models import Query, ToolOptions
from zhenxun.services.ai.tools.providers.builtin.hitl import HITLToolkit
from zhenxun.services.ai.tools.providers.skills.capabilities import (
    SkillCapability,
)
from zhenxun.services.ai.tools.providers.skills.models import Skill, SkillSource
from zhenxun.services.ai.utils import ContextUtils
from zhenxun.services.ai.utils.logger import log_agent as logger
from zhenxun.utils.pydantic_compat import (
    model_construct,
    model_copy,
    model_dump,
    parse_as,
)
from zhenxun.utils.utils import infer_plugin_namespace

from .engine.builders import (
    AgentProfileResolver,
    CapabilityBuilder,
    ContextBuilder,
    SessionBuilder,
    ToolBuilder,
)
from .engine.executor import BaseAgentExecutor, StandardAgentExecutor
from .models import (
    AgentConfig,
    AgentRunResources,
    AgentState,
    Persona,
)

ToolSource = (
    Callable | BaseTool | dict[str, Any] | str | BaseToolkit | ToolResolvable | Query
)
"""任何可以作为工具提供给大模型的实体对象（函数、基础工具类、字典定义、工具名、工具箱、声明式查询对象）"""


class AgentBuilder(Generic[AgentDepsT, OutputDataT]):
    """
    Agent 链式构建器 (Fluent Builder)。
    """

    def __init__(self, name: str):
        self._kwargs: dict[str, Any] = {"name": name}
        self._config: AgentConfig | dict | None = None
        self._executor: Any | None = None
        self._directive_handlers: dict[str, Any] = {}

    def with_instruction(
        self, instruction: str | PromptTemplate
    ) -> "AgentBuilder[AgentDepsT, OutputDataT]":
        """
        配置静态系统指令。

        参数:
            instruction: 静态系统指令，可为普通字符串或模板字符串。
        """
        self._kwargs["instruction"] = instruction
        return self

    def with_persona(
        self, role: str, goal: str, backstory: str | None = None
    ) -> "AgentBuilder[AgentDepsT, OutputDataT]":
        """
        配置智能体人设与角色设定。

        参数:
            role: 扮演的角色身份。
            goal: 角色的核心目标。
            backstory: 角色背景故事或性格设定。
        """
        self._kwargs["persona"] = Persona(role=role, goal=goal, backstory=backstory)
        return self

    def with_model(
        self, model: str | Callable[[], str]
    ) -> "AgentBuilder[AgentDepsT, OutputDataT]":
        """
        配置默认调用的语言模型。

        参数:
            model: 默认模型名（如 `Provider/Model`）或返回模型名的回调。
        """
        self._kwargs["model"] = model
        return self

    def with_tools(
        self, *tools: ToolSource | Sequence[ToolSource]
    ) -> "AgentBuilder[AgentDepsT, OutputDataT]":
        """
        配置可供智能体调用的工具列表。

        参数:
            tools: 初始工具定义，支持工具对象、函数、字典定义或工具名称。
        """
        current_tools = self._kwargs.setdefault("tools", [])
        for t in tools:
            if isinstance(t, Sequence) and not isinstance(t, str):
                current_tools.extend(t)
            else:
                current_tools.append(t)
        return self

    def with_skills(
        self, *skills: str | Path | Skill | SkillSource | Sequence
    ) -> "AgentBuilder[AgentDepsT, OutputDataT]":
        """
        配置注入的领域知识技能。

        参数:
            skills: 注入的技能，支持 ID、目录 Path、Skill 对象或 SkillSource 动态源。
        """
        current_skills = self._kwargs.setdefault("skills", [])
        for s in skills:
            if isinstance(s, list | tuple | set):
                current_skills.extend(s)
            else:
                current_skills.append(cast(Any, s))
        return self

    def with_knowledge(
        self, *knowledge: BaseKnowledge | list[BaseKnowledge]
    ) -> "AgentBuilder[AgentDepsT, OutputDataT]":
        """
        配置挂载的知识库。

        参数:
            knowledge: 挂载的知识库，支持单个或列表。底层会自动将其注册入工具链。
        """
        current_knowledge = self._kwargs.setdefault("knowledge", [])
        for k in knowledge:
            if isinstance(k, list):
                current_knowledge.extend(k)
            else:
                current_knowledge.append(k)
        return self

    def with_memory(
        self, memory: bool | MemoryConfig | MemoryBuilder
    ) -> "AgentBuilder[AgentDepsT, OutputDataT]":
        """
        配置对话记忆与上下文管理策略。

        参数:
            memory: 是否开启长期记忆与上下文压缩，支持布尔值或显式配置对象。
        """
        self._kwargs["memory"] = memory
        return self

    def with_generation_config(
        self, config: GenerationConfig | IntentBuilder | dict
    ) -> "AgentBuilder[AgentDepsT, OutputDataT]":
        """
        配置大模型基础生成参数。

        参数:
            config: 默认生成配置，支持 `GenerationConfig`、`IntentBuilder` 或 dict。
        """
        self._kwargs["generation_config"] = config
        return self

    def with_intervention(self, policy: Any) -> "AgentBuilder[AgentDepsT, OutputDataT]":
        """配置运行时消息干预策略。"""
        if self._config is None:
            self._config = AgentConfig()
        elif isinstance(self._config, dict):
            self._config = AgentConfig(**self._config)
        self._config.intervention_policy = policy
        return self

    def with_response_model(
        self, response_model: BaseOutputDefinition | type[Any]
    ) -> "AgentBuilder[AgentDepsT, Any]":
        """
        配置期望大模型输出的强类型结构化数据模型。

        参数:
            response_model: 结构化输出模型，传入 Pydantic 模型类或声明式输出对象。
        """
        self._kwargs["response_model"] = response_model
        return cast(AgentBuilder[AgentDepsT, Any], self)

    def with_guardrails(
        self, *guardrails: GuardrailSource | list[GuardrailSource]
    ) -> "AgentBuilder[AgentDepsT, OutputDataT]":
        """
        配置输入/输出安全合规护栏。

        参数:
            guardrails: 护栏定义，支持可调用对象、自然语言规则字符串或护栏实例。
        """
        current_guardrails = self._kwargs.setdefault("guardrails", [])
        for g in guardrails:
            if isinstance(g, list):
                current_guardrails.extend(g)
            else:
                current_guardrails.append(g)
        return self

    def with_capabilities(
        self, *capabilities: CapabilitySource | list[CapabilitySource]
    ) -> "AgentBuilder[AgentDepsT, OutputDataT]":
        """
        配置智能体的高阶能力拦截器组件。

        参数:
            capabilities: 能力组件，可传入函数或 `AbstractCapability` 实例。
        """
        current_capabilities = self._kwargs.setdefault("capabilities", [])
        for c in capabilities:
            if isinstance(c, list):
                current_capabilities.extend(c)
            else:
                current_capabilities.append(c)
        return self

    def with_config(
        self, config: AgentConfig | dict | None = None, **kwargs
    ) -> "AgentBuilder[AgentDepsT, OutputDataT]":
        """
        配置智能体全局通用设置。

        参数:
            config: 统一配置，合并了全局与单次运行策略，可传入 `AgentConfig` 或 dict。
            kwargs: 零散的配置参数，将自动覆盖或组装进配置对象中。
        """
        merged_kwargs = {}
        if config:
            merged_kwargs.update(
                config if isinstance(config, dict) else model_dump(config)
            )
        merged_kwargs.update(kwargs)

        self._config = AgentConfig(**merged_kwargs)
        return self

    def with_executor(
        self, executor: BaseAgentExecutor
    ) -> "AgentBuilder[AgentDepsT, OutputDataT]":
        """
        配置核心思考大循环的执行策略。

        参数:
            executor: 核心思考大循环的执行策略。

        返回:
            AgentBuilder[AgentDepsT, OutputDataT]: 构建器自身。
        """
        self._executor = executor
        return self

    def with_directive_handler(
        self, name: str, handler: Any
    ) -> "AgentBuilder[AgentDepsT, OutputDataT]":
        """
        动态注入自定义大模型工具控制流指令。
        """
        self._directive_handlers[name] = handler
        return self

    def build(self) -> "Agent[AgentDepsT, OutputDataT]":
        """
        构建并输出最终 of Agent 实例。
        """
        return Agent(
            **self._kwargs,
            config=self._config,
            executor=self._executor,
            directive_handlers=self._directive_handlers,
        )


class Agent(
    BaseRunnable[AgentRunResult[OutputDataT]], Generic[AgentDepsT, OutputDataT]
):
    """
    Agent 运行时封装。
    负责组织模型、工具、记忆、护栏与能力插件，并驱动单轮或流式执行。
    """

    @classmethod
    def builder(cls, name: str) -> AgentBuilder[Any, str]:
        """创建一个智能体链式构建器"""
        return AgentBuilder(name=name)

    def __init__(
        self,
        name: str,
        instruction: str | PromptTemplate = "",
        description: str | None = None,
        persona: Persona | dict | None = None,
        model: str | Callable[[], str] | None = None,
        tools: Sequence[ToolSource] | None = None,
        skills: Sequence[str | Path | Skill | SkillSource] | None = None,
        generation_config: GenerationConfig | IntentBuilder | dict | None = None,
        response_model: BaseOutputDefinition | type[OutputDataT] | None = None,
        memory: bool | MemoryConfig | MemoryBuilder = False,
        knowledge: BaseKnowledge | list[BaseKnowledge] | None = None,
        config: AgentConfig | dict | None = None,
        guardrails: list[GuardrailSource] | None = None,
        capabilities: list[CapabilitySource] | None = None,
        executor: BaseAgentExecutor | None = None,
        directive_handlers: dict[str, Any] | None = None,
    ):
        """
        初始化 Agent。

        参数:
            name: Agent 名称，用于日志、事件和链路标识。
            instruction: 静态系统指令，可为普通字符串或模板字符串。
            description: 智能体描述，用于外部路由节点决定是否调用。
            persona: 角色设定配置，传入 dict 会自动构造成 Persona。
            model: 默认模型名称 (如 Provider/Model) 或返回模型名的回调。
            tools: 初始工具定义列表，支持混合使用工具对象与字符串工具名。
            skills: 注入的领域知识技能，支持 ID、目录 Path、Skill 对象或动态源。
            generation_config: 默认生成配置，支持 GenerationConfig、IntentBuilder 或 dict。
            response_model: 结构化输出模型，若为空则按纯文本输出。
            memory: 是否开启长期记忆与上下文压缩，支持布尔值或 MemoryBuilder/Config。
            knowledge: 挂载的知识库，支持单个或列表，底层自动将其注册入工具链。
            config: 统一配置，合并了全局与单次运行策略，支持字典。
            guardrails: 护栏定义列表，支持可调用对象、规则字符串或护栏实例。
            capabilities: 拦截器/能力插件列表，处理整个生命周期的切面逻辑。
            executor: 核心思考大循环的执行策略。
            directive_handlers: 自定义大模型工具控制流指令处理器字典。
        """  # noqa: E501
        self.name = name

        if description:
            self.description = description
        elif persona:
            p_obj = persona if isinstance(persona, Persona) else Persona(**persona)
            self.description = f"角色：{p_obj.role}，目标：{p_obj.goal}"
        else:
            self.description = str(instruction)[:150] if instruction else "AI Agent"

        self.instruction = instruction

        if isinstance(persona, dict):
            self.persona = Persona(**persona)
        else:
            self.persona = persona
        self.model_name = model

        self.namespace = infer_plugin_namespace() or "unknown"

        self.tool_names = [t for t in (tools or []) if isinstance(t, str)]
        self.response_model = response_model
        self.directive_handlers = directive_handlers or {}
        if isinstance(generation_config, IntentBuilder):
            generation_config = generation_config.build()

        if isinstance(generation_config, dict):
            base_config = parse_as(GenerationConfig, generation_config)
        else:
            base_config = (
                model_copy(generation_config, deep=True)
                if generation_config
                else GenerationConfig()
            )

        self._raw_response_schema = None
        if base_config.output.response_schema and self.response_model is None:
            self._raw_response_schema = base_config.output.response_schema
            base_config.output.response_schema = None
            base_config.output.response_format = None
            base_config.output.structured_output_strategy = None

        self.default_config = base_config
        self._resolved_tools: dict[str, Any] | None = None

        self.dynamic_prompts = []
        self.tool_filters = []
        self.toolset_funcs = []
        self._event_listeners: dict[type[AgentStreamEvent], list[Callable]] = {}
        self._guardrails = parse_guardrails(guardrails)

        self.memory_config = MemoryBuilder.resolve(memory)

        if isinstance(config, dict):
            self.config = AgentConfig(**config)
        else:
            self.config = config or AgentConfig()

        self.runtime_config = self.config
        self.engine_config = self.config

        if self.config.enable_hitl is None:
            self.config.enable_hitl = get_llm_config().agent_settings.enable_hitl

        self.config.stateless = not self.memory_config.short_term.enable

        self.executor = executor

        self._assemble_plugins(tools, knowledge, capabilities, skills)

    def _assemble_plugins(self, tools, knowledge, capabilities, skills):
        """私有方法：集中处理各类能力、知识与技能的挂载，消解冗余样板代码"""
        self.tool_definitions = list(tools) if tools else []

        if knowledge:
            if not isinstance(knowledge, list):
                knowledge = [knowledge]
            self.tool_definitions.extend(knowledge)

        self.capabilities: list[CapabilitySource] = []

        if self.memory_config.long_term.enable and self.memory_config.long_term.agentic:
            self.capabilities.append(
                AgenticMemoryCapability(self.memory_config, self.namespace)
            )

        if self.memory_config.slots.enable:
            self.capabilities.append(
                SlotMemoryCapability(self.memory_config, self.namespace)
            )

        if capabilities:
            self.capabilities.extend(capabilities)

        if self.config.enable_hitl:
            self.tool_definitions.append(HITLToolkit())

        if skills:
            self.capabilities.append(
                SkillCapability(skills=skills, namespace=self.namespace)
            )

    def tool(
        self,
        func: Callable | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        settings: Any | None = None,
    ):
        """
        实例级工具注册装饰器。
        将普通函数绑定为该智能体的专属工具。
        """

        def decorator(f: Callable):
            tool_name = name or f.__name__
            tool_desc = description or f.__doc__ or "未提供描述"
            base_settings = settings or getattr(f, "__tool_settings__", ToolOptions())

            func_tool = FunctionTool(
                func=f,
                name=tool_name,
                description=tool_desc,
                settings=base_settings,
            )
            if self.tool_definitions is None:
                self.tool_definitions = []
            self.tool_definitions.append(func_tool)
            return f

        return decorator if func is None else decorator(func)

    def system_prompt(self, func: Callable | None = None):
        """
        实例级动态系统提示词注册装饰器
        """

        def decorator(f: Callable):
            if self.dynamic_prompts is None:
                self.dynamic_prompts = []
            self.dynamic_prompts.append(f)
            return f

        return decorator if func is None else decorator(func)

    def tool_filter(self, func: Callable | None = None):
        """
        实例级工具动态过滤装饰器
        """

        def decorator(f: Callable):
            if getattr(self, "tool_filters", None) is None:
                self.tool_filters = []
            self.tool_filters.append(f)
            return f

        return decorator if func is None else decorator(func)

    def toolset(self, func: Callable | None = None):
        """
        实例级动态工具集注册装饰器
        """

        def decorator(f: Callable):
            if getattr(self, "toolset_funcs", None) is None:
                self.toolset_funcs = []
            self.toolset_funcs.append(f)
            return f

        return decorator if func is None else decorator(func)

    def guardrail(self, func: Callable | str | Any | None = None):
        """护栏装饰器/注册器 (支持传入函数或自然语言风控规则字符串)"""
        if func is None:

            def decorator(f: Callable):
                self._guardrails.extend(parse_guardrails([f]))
                return f

            return decorator
        else:
            self._guardrails.extend(parse_guardrails([func]))
            return func

    def on_event(self, event_type: type[AgentStreamEvent]) -> Callable:
        """
        [事件门面] 生命周期事件监听器注册装饰器。
        允许第三方开发者监听 Agent 运行时的各类事件，完美支持 Inject 依赖注入语法糖。
        """

        def decorator(func: Callable):
            if event_type not in self._event_listeners:
                self._event_listeners[event_type] = []
            self._event_listeners[event_type].append(func)
            return func

        return decorator

    async def __resolve_to_tools__(self) -> list[ToolExecutable]:
        """协议支持：将自身 Agent 转化为可被上级调用的工具"""
        return [DelegateTool(self)]

    async def run(
        self,
        prompt: PromptInput | AgentTask | None = None,
        *,
        config: AgentConfig | dict | None = None,
        deps: AgentDepsT | None = None,
        context: RunContext[AgentDepsT] | None = None,
        **kwargs: Any,
    ) -> AgentRunResult[OutputDataT]:
        """
        智能体单次运行阻塞核心入口，内部使用上下文管理器静默消费事件流直至执行结束。

        参数:
            prompt: 用户输入的消息内容或标准数据契约任务对象 (AgentTask)。
            deps: 强类型的外部依赖注入对象 (例如 NoneBot 的 Bot, Event)。
            context: 显式传入的运行时与会话上下文 (RunContext)。
            config: 单次运行时的动态配置覆盖字典或对象。
            kwargs: 透传的其他附加参数。
        """
        return await super().run(
            prompt=prompt,
            config=config,
            deps=deps,
            context=context,
            **kwargs,
        )

    @contextlib.asynccontextmanager
    async def run_stream(
        self,
        prompt: PromptInput | AgentTask | None = None,
        *,
        config: AgentConfig | dict | None = None,
        deps: AgentDepsT | None = None,
        context: RunContext[AgentDepsT] | None = None,
        event_bus: EventBus | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamedRunResult[OutputDataT]]:
        """
        智能体流式运行入口。
        返回上下文管理器，可安全、解耦地获取底层事件或纯净文本结果。
        """
        override_conf = (
            AgentConfig(**config)
            if isinstance(config, dict)
            else (config or AgentConfig())
        )
        effective_config = self.config.merge_with(override_conf)

        if effective_config.skills:
            if effective_config.capabilities is None:
                effective_config.capabilities = []
            effective_config.capabilities.append(
                SkillCapability(
                    skills=effective_config.skills, namespace=infer_plugin_namespace()
                )
            )
        bus = event_bus or EventBus()

        TelemetrySubscriber().attach(bus)

        if self._event_listeners:
            for ev_type, callbacks in self._event_listeners.items():
                for cb in callbacks:

                    def _make_handler(callback_func: Callable) -> Callable:
                        async def _di_handler(event: AgentStreamEvent):
                            await DependencyInjector.invoke(
                                callback_func, {"stream_event": event}, safe_context
                            )

                        return _di_handler

                    bus.subscribe(ev_type, _make_handler(cb))

        if context is None:
            explicit_session_id = kwargs.get("session_id")
            safe_context = RunContext[AgentDepsT](session_id=explicit_session_id)
            if deps is not None:
                safe_context.deps = cast(AgentDepsT, deps)
        else:
            safe_context = context
            if deps is not None and safe_context.deps is None:
                safe_context.deps = cast(AgentDepsT, deps)

        if safe_context.get_bot() and safe_context.get_event():
            verbose_ui = effective_config.verbose_ui
            DefaultUISubscriber(safe_context, verbose=verbose_ui).attach(bus)

        policy = getattr(self.config, "concurrency_policy", None)
        if policy is None:
            policy = (
                ConcurrencyPolicy.ALLOW
                if getattr(self.config, "stateless", True)
                else ConcurrencyPolicy.QUEUE
            )

        intervention_policy = getattr(self.config, "intervention_policy", None)

        lock_id = ContextUtils.extract_concurrency_lock_id(
            safe_context,
            getattr(self.config, "concurrency_scope", None),
            safe_context.session_id or "default_session",
        )

        async def _execution_task():
            cancel_token = safe_context.run.cancellation_token or CancellationToken()
            safe_context.run.cancellation_token = cancel_token

            try:
                async with apply_concurrency_policy(
                    session_id=safe_context.session_id or "default_session",
                    lock_id=lock_id,
                    policy=policy,
                    cancel_token=cancel_token,
                    intervention_policy=intervention_policy,
                    message=prompt,
                ):
                    await bus.emit(AgentRunStart(agent_name=self.name))
                    result = await self._run_step(
                        prompt=prompt,
                        context=safe_context,
                        config=effective_config,
                        cancellation_token=cancel_token,
                        event_bus=bus,
                        **kwargs,
                    )
                    await bus.emit(AgentRunEnd(result=result))
            except ControlFlowExit as e:
                await bus.emit(AgentRunError(error=e))
            except asyncio.CancelledError:
                logger.debug(f"Agent {self.name} 执行被并发策略中断取消。")
                await bus.emit(
                    AgentRunError(
                        error=ConcurrencyInterruptException("任务已被新请求打断并接管")
                    )
                )
            except Exception as e:
                await bus.emit(AgentRunError(error=e))
            finally:
                await bus.end()

        task = asyncio.create_task(_execution_task())
        result_obj = StreamedRunResult[OutputDataT](bus)

        try:
            yield result_obj
        finally:
            if not task.done():
                task.cancel()

    def _parse_task_prompt(
        self, prompt: PromptInput | AgentTask | None
    ) -> tuple[AgentTask | None, Any | None, list[Any], Any, list[Any]]:
        """解析输入意图，提取数据契约 (AgentTask)"""
        task_obj = None
        final_prompt_payload = None
        extra_tools = []
        run_output_type = self.response_model
        task_guardrails = []

        if isinstance(prompt, AgentTask):
            task_obj = prompt
            if task_obj.response_model:
                run_output_type = task_obj.response_model
            if task_obj.tools:
                extra_tools.extend(task_obj.tools)
            if hasattr(task_obj, "_parsed_guardrails"):
                task_guardrails.extend(task_obj._parsed_guardrails)

            prompt_parts = [
                f"### 📋 [任务指令]\n{task_obj.description}",
                f"### 🎯 [预期产出要求]\n{task_obj.expected_output}",
            ]
            final_prompt_payload = "\n\n".join(prompt_parts)
        elif prompt is not None:
            final_prompt_payload = prompt

        return (
            task_obj,
            final_prompt_payload,
            extra_tools,
            run_output_type,
            task_guardrails,
        )

    async def on_state_init(
        self,
        prompt: PromptInput | AgentTask | None = None,
        context: RunContext[AgentDepsT] | None = None,
        config: AgentConfig | None = None,
        cancellation_token: Any = None,
        event_bus: EventBus | None = None,
        **kwargs: Any,
    ) -> tuple[AgentState, AgentRunResources]:
        """解析任务意图，初始化隔离域与基础状态载体"""

        if context is None:
            raise ValueError("RunContext 不能为空")
        if config is None:
            config = AgentConfig()

        (
            task_obj,
            final_prompt_payload,
            extra_tools,
            run_output_type,
            task_guardrails,
        ) = self._parse_task_prompt(prompt)

        effective_memory = AgentProfileResolver.resolve_memory(
            self.memory_config, config.memory
        )

        session_metadata, reader, writer = SessionBuilder.build_session_and_memory(
            context, self.namespace, self.name, effective_memory
        )

        run_scoped_cap = await CapabilityBuilder.build_for_run(
            agent_name=self.name,
            namespace=self.namespace,
            output_type=run_output_type,
            raw_schema=getattr(self, "_raw_response_schema", None),
            agent_guardrails=self._guardrails,
            task_guardrails=task_guardrails,
            task_obj=task_obj,
            agent_capabilities=self.capabilities,
            profile_capabilities=config.capabilities,
            context=context,
        )

        resources = AgentRunResources(
            run_context=context,
            session_meta=session_metadata,
            memory_reader=reader,
            memory_writer=writer,
            run_scoped_cap=run_scoped_cap,
            task_obj=task_obj,
            config=config,
        )
        state = AgentState()
        state.current_request_extra["final_prompt_payload"] = final_prompt_payload
        state.current_request_extra["extra_tools"] = extra_tools

        if final_prompt_payload is not None:
            if isinstance(final_prompt_payload, str):
                context.run.user_input = final_prompt_payload
            elif hasattr(final_prompt_payload, "extract_plain_text"):
                context.run.user_input = final_prompt_payload.extract_plain_text()
            else:
                context.run.user_input = str(final_prompt_payload)

        context.run.agent_name = self.name
        context.run.cancellation_token = cancellation_token
        context.run.event_bus = event_bus
        if not context.run.current_model:
            context.run.current_model = (
                self.model_name() if callable(self.model_name) else self.model_name
            )

        return state, resources

    async def on_context_build(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        """装配记忆与提示词上下文、解析可用工具集"""

        context = resources.run_context
        reader = resources.memory_reader
        run_scoped_cap = (
            resources.run_scoped_cap
            if isinstance(resources.run_scoped_cap, CombinedCapability)
            else CombinedCapability([])
        )
        final_prompt_payload = state.current_request_extra.pop(
            "final_prompt_payload", None
        )
        extra_tools = state.current_request_extra.pop("extra_tools", [])

        static_prompt, dynamic_messages = await ContextBuilder.build_prompts(
            instruction=self.instruction,
            system_prompts=self.dynamic_prompts,
            run_context=context,
            run_scoped_cap=run_scoped_cap,
            persona=cast(Persona | None, self.persona),
        )

        if reader:
            long_term_fact = await reader.get_long_term_context(
                context.run.user_input or ""
            )
            if long_term_fact:
                dynamic_messages.append(LLMMessage.system(long_term_fact))
            slots_fact = await reader.get_slots_context()
            if slots_fact:
                dynamic_messages.append(LLMMessage.system(slots_fact))

        tool_payload = await ToolBuilder.resolve_tools(
            tool_definitions=self.tool_definitions,
            toolset_funcs=getattr(self, "toolset_funcs", []),
            system_tools=[],
            namespace=self.namespace or "unknown",
            run_context=context,
            run_scoped_cap=run_scoped_cap,
        )
        effective_tools = tool_payload.tools
        if extra_tools:
            effective_tools.extend(extra_tools)

        cap_dynamic_config = await run_scoped_cap.get_generation_config(context)
        final_gen_config = AgentProfileResolver.resolve_generation_config(
            base_config=self.default_config,
            cap_config=cap_dynamic_config,
            profile_config=resources.config.generation_config,
        )
        resources.generation_config = final_gen_config
        resources.toolkits = tool_payload.toolkits

        static_prompts_list = [static_prompt]
        if tool_payload.injected_prompts:
            static_prompts_list.extend(tool_payload.injected_prompts)

        messages_for_run = (
            await reader.get_short_term_context(
                model_name=context.run.current_model or "",
                override_history=resources.config.message_history,
            )
            if reader
            else []
        )

        if context.run.messages:
            messages_for_run.extend(context.run.messages)

        if final_prompt_payload is not None:
            if msgs := await MessageBuilder.normalize_to_llm_messages(
                final_prompt_payload, bot=context.get_bot(), event=context.get_event()
            ):
                messages_for_run.append(msgs[-1])
                if resources.memory_writer:
                    await resources.memory_writer.save_new_messages([msgs[-1]])

        final_tools = await ToolBuilder.prepare_effective_tools(
            effective_tools, context, self.tool_filters, run_scoped_cap
        )
        context.session.append_only_manager.build(static_prompts_list, final_tools)
        context.session.append_only_manager.sync_messages(messages_for_run)

        state.messages = messages_for_run
        state.tools = final_tools
        state.static_system_prompt = static_prompts_list
        state.dynamic_system_messages = dynamic_messages
        state.origin_msg_len = len(messages_for_run)

    async def on_execute(
        self, state: AgentState, resources: AgentRunResources
    ) -> AgentRunResult[OutputDataT]:
        """真正调度大模型执行器并执行记忆落盘"""
        context = resources.run_context

        for tk in resources.toolkits:
            if hasattr(tk, "before_llm_request"):
                await DependencyInjector.invoke(
                    tk.before_llm_request, {"messages": state.messages}, context
                )

        config_exec = resources.config.executor if resources.config else None
        executor = (
            config_exec
            or self.executor
            or StandardAgentExecutor(directive_handlers=self.directive_handlers)
        )
        resources.model_name = context.run.current_model
        raw_result: Any = await executor.run(state=state, resources=resources)

        new_msgs = raw_result.messages[state.origin_msg_len :]
        if resources.memory_writer:
            await resources.memory_writer.save_new_messages(new_msgs)

        final_output = getattr(raw_result, "output", None) or (
            raw_result.messages[-1].extract_text if raw_result.messages else ""
        )

        return cast(
            AgentRunResult[OutputDataT],
            model_construct(
                AgentRunResult,
                output=final_output,
                messages=new_msgs,
                structured_data=getattr(raw_result, "structured_data", None),
                usage=getattr(raw_result, "usage", None) or UsageInfo(),
                handoff=getattr(raw_result, "handoff", None),
            ),
        )

    async def _run_step(
        self,
        prompt: PromptInput | AgentTask | None = None,
        *,
        context: RunContext[AgentDepsT],
        config: AgentConfig,
        cancellation_token: Any = None,
        event_bus: EventBus | None = None,
        **kwargs: Any,
    ) -> AgentRunResult[OutputDataT]:
        """原子步总管：将具体的生命周期方法编织为洋葱模型管道"""
        state, resources = await self.on_state_init(
            prompt,
            context,
            config,
            cancellation_token,
            event_bus,
            **kwargs,
        )
        await self.on_context_build(state, resources)

        run_scoped_cap = (
            resources.run_scoped_cap
            if isinstance(resources.run_scoped_cap, CombinedCapability)
            else CombinedCapability([])
        )
        original_capabilities = getattr(context, "capabilities", [])
        context.capabilities = run_scoped_cap.capabilities

        async def inner_run_handler() -> AgentRunResult[OutputDataT]:
            return await self.on_execute(state, resources)

        try:
            return await run_scoped_cap.wrap_run(context, inner_run_handler)
        except ControlFlowExit as e:
            raise e
        except Exception as e:
            raise e
        finally:
            context.capabilities = original_capabilities
