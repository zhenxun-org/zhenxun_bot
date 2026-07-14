from collections.abc import Callable
import copy
import inspect
from typing import Any, cast

from nonebot.utils import is_coroutine_callable

from zhenxun.services.ai.capabilities import (
    CombinedCapability,
)
from zhenxun.services.ai.context.memory.builder import MemoryBuilder
from zhenxun.services.ai.context.memory.engine import SessionMemoryContext
from zhenxun.services.ai.context.memory.models import MemoryConfig
from zhenxun.services.ai.context.memory.types import SessionMetadata
from zhenxun.services.ai.core.messages import LLMMessage, TextPart
from zhenxun.services.ai.core.options import BaseOutputDefinition, GenerationConfig
from zhenxun.services.ai.core.protocols.tool import ToolExecutable
from zhenxun.services.ai.core.templates import PromptTemplate
from zhenxun.services.ai.flow.agent.capabilities import (
    OutputValidationCapability,
    TaskTrackingCapability,
)
from zhenxun.services.ai.flow.agent.models import Persona
from zhenxun.services.ai.run import AgentTask, RunContext
from zhenxun.services.ai.run.di import DependencyInjector
from zhenxun.services.ai.tools.engine.registry import (
    ToolCollection,
    tool_provider_manager,
)
from zhenxun.services.ai.tools.models import ResolvedToolPayload
from zhenxun.services.ai.utils.runtime import ContextUtils
from zhenxun.utils.pydantic_compat import model_copy


class AgentProfileResolver:
    """Agent 配置解析器：负责提取与合并 Agent 的运行时 Profile"""

    @staticmethod
    def resolve_memory(
        agent_memory_config: MemoryConfig,
        override_memory: bool | MemoryConfig | MemoryBuilder | None,
    ) -> MemoryConfig:
        """
        解析并合并 Memory 记忆域的配置。
        支持从外部覆盖配置并重新构建。

        参数：
            agent_memory_config: 预置的 Agent 默认记忆域配置对象。
            override_memory: 运行时覆盖的记忆域配置，可为 dict, MemoryConfig 或其他合法结构。

        返回：
            MemoryConfig: 合并并生成的运行时记忆域配置实例。
        """  # noqa: E501
        if override_memory is not None:
            return MemoryBuilder.resolve(override_memory)
        return model_copy(agent_memory_config, deep=True)

    @staticmethod
    def resolve_generation_config(
        base_config: GenerationConfig,
        cap_config: GenerationConfig | None,
        profile_config: GenerationConfig | None,
    ) -> GenerationConfig:
        """
        解析并合并多层 GenerationConfig 模型生成配置。
        优先级顺序由低到高为：基础配置 -> 拦截器能力配置 -> 运行时 Profile 覆盖配置。

        参数：
            base_config: 基础的模型生成配置对象。
            cap_config: 拦截器能力中提取出的模型生成参数配置。
            profile_config: 运行时传入的 Profile 覆盖参数配置。

        返回：
            GenerationConfig: 合并多层配置后生成的最终运行时生成配置实例。
        """
        final_gen_config = model_copy(base_config, deep=True)
        if cap_config:
            final_gen_config = final_gen_config.merge_with(cap_config)
        if profile_config:
            final_gen_config = final_gen_config.merge_with(profile_config)
        return final_gen_config


class CapabilityBuilder:
    """拦截器能力组装器：负责合并 Agent, AgentTask, Profile 和全局的中间件"""

    @staticmethod
    async def build_for_run(
        agent_name: str,
        namespace: str,
        output_type: type[Any] | BaseOutputDefinition | None,
        raw_schema: dict | None,
        agent_guardrails: list,
        task_guardrails: list,
        task_obj: AgentTask | None,
        agent_capabilities: list,
        profile_capabilities: list | None,
        context: RunContext,
    ) -> CombinedCapability:
        """
        为当前的 Agent 运行实例组装并实例化所有能力拦截器中间件。
        整合全局能力、任务追踪、格式校验以及动态注入的能力。

        参数：
            agent_name: 执行推理的 Agent 标识名。
            namespace: 会话所归属的命名空间。
            output_type: 期待大模型返回的结构化 Pydantic 模型类型（支持 None 或 str）。
            raw_schema: 原始结构化 Schema 定义字典。
            agent_guardrails: Agent 自身定义的业务语义安全护栏列表。
            task_guardrails: 本次任务定义的业务语义安全护栏列表。
            task_obj: 被追踪的任务上下文对象实例。
            agent_capabilities: Agent 定义的静态拦截器列表。
            profile_capabilities: 运行时动态传入的能力或中间件列表。
            context: 运行上下文对象实例。

        返回：
            CombinedCapability: 已经过运行初始化完毕的合并能力拦截器实例。
        """
        dynamic_caps = []
        combined_guardrails = agent_guardrails + task_guardrails

        if output_type is not None and output_type is not str:
            dynamic_caps.append(
                OutputValidationCapability(output_type, combined_guardrails)
            )
        elif raw_schema is not None:
            dynamic_caps.append(
                OutputValidationCapability(
                    None, combined_guardrails, raw_schema=raw_schema
                )
            )
        elif combined_guardrails:
            dynamic_caps.append(OutputValidationCapability(None, combined_guardrails))

        if task_obj:
            dynamic_caps.append(TaskTrackingCapability(task_obj, agent_name))

        from zhenxun.services.ai.capabilities.manager import capability_manager

        run_level_caps = capability_manager.resolve_capabilities(
            profile_capabilities or [], namespace
        )
        agent_level_caps = capability_manager.resolve_capabilities(
            agent_capabilities or [], namespace
        )

        auto_caps = capability_manager.get_auto_apply_capabilities(namespace)

        combined_cap = CombinedCapability(
            auto_caps
            + getattr(context, "capabilities", [])
            + agent_level_caps
            + run_level_caps
            + dynamic_caps
        )
        return cast(CombinedCapability, await combined_cap.for_run(context))


class ContextBuilder:
    """系统提示词与上下文记忆构建器"""

    @staticmethod
    async def build_prompts(
        instruction: str | PromptTemplate,
        system_prompts: list[Callable],
        run_context: RunContext,
        run_scoped_cap: CombinedCapability,
        persona: Persona | None = None,
    ) -> tuple[str, list[LLMMessage]]:
        """
        解析、合并并渲染 Agent 的系统提示词和上下文记忆。
        包含对依赖参数的动态注入和 Jinja 模板渲染。

        参数：
            instruction: 任务级别的初始指令或提示词模板。
            system_prompts: 系统提示词生成函数（支持依赖注入）列表。
            run_context: 运行上下文对象实例。
            run_scoped_cap: 运行域下的合并能力中间件。
            persona: 设定的 Agent 人设配置实例。

        返回：
            tuple[str, list[Any]]: 包含 (渲染后的静态系统提示词文本, 渲染后的动态消息列表) 的元组。
        """  # noqa: E501
        static_instructions = []
        dynamic_messages = []

        for sp_func in system_prompts:
            sig = inspect.signature(sp_func)
            if len(sig.parameters) > 0:
                injected_kwargs = await DependencyInjector.resolve_all(
                    sig=sig,
                    call_kwargs={},
                    context=run_context,
                )
                res = (
                    (await sp_func(**injected_kwargs))
                    if is_coroutine_callable(sp_func)
                    else sp_func(**injected_kwargs)
                )
            else:
                res = (await sp_func()) if is_coroutine_callable(sp_func) else sp_func()
            if res:
                if isinstance(res, LLMMessage):
                    dynamic_messages.append(res)
                elif isinstance(res, list) and all(
                    isinstance(m, LLMMessage) for m in res
                ):
                    dynamic_messages.extend(res)
                else:
                    if isinstance(res, list):
                        for item in res:
                            if item:
                                dynamic_messages.append(LLMMessage.system(str(item)))
                    else:
                        dynamic_messages.append(LLMMessage.system(str(res)))

        if persona:
            persona_parts = [
                f"## 扮演角色 (Role)\n{persona.role}",
                f"## 核心目标 (Goal)\n{persona.goal}",
            ]
            if persona.backstory:
                persona_parts.append(f"## 角色背景 (Backstory)\n{persona.backstory}")
            static_instructions.append("\n\n".join(persona_parts))

            if instruction:
                static_instructions.append("## 本次任务指令 (AgentTask)")

        if instruction:
            if isinstance(instruction, PromptTemplate):
                static_instructions.append(instruction.format_with_context(run_context))
            else:
                static_instructions.append(str(instruction))

        caps = (
            run_scoped_cap.capabilities
            if run_scoped_cap
            else getattr(run_context, "capabilities", [])
        )
        for cap in caps:
            cap_prompts = await cap.get_system_prompts(run_context)
            for prompt_text in cap_prompts:
                if prompt_text and prompt_text.strip():
                    dynamic_messages.append(LLMMessage.system(prompt_text))

        static_text = "\n\n".join(static_instructions)

        render_context = {
            "deps": run_context.deps,
            "bot": getattr(run_context.deps, "bot", None),
            "event": getattr(run_context.deps, "event", None),
            "matcher": getattr(run_context.deps, "matcher", None),
        }
        if run_context.state:
            render_context.update(run_context.state)

        rendered_dynamic_messages = []

        for msg in dynamic_messages:
            if msg.role == "system":
                new_content = []
                changed = False
                for part in msg.content:
                    if isinstance(part, TextPart) and part.text:
                        try:
                            rendered_text = PromptTemplate(part.text).render(
                                **render_context
                            )
                            new_content.append(TextPart(text=rendered_text))
                            if rendered_text != part.text:
                                changed = True
                        except Exception:
                            new_content.append(part)
                    else:
                        new_content.append(part)
                if changed:
                    new_msg = msg.model_copy(deep=True)
                    new_msg.content = new_content
                    rendered_dynamic_messages.append(new_msg)
                else:
                    rendered_dynamic_messages.append(msg)
            else:
                rendered_dynamic_messages.append(msg)

        return (
            PromptTemplate(static_text).render(**render_context),
            rendered_dynamic_messages,
        )


class ToolBuilder:
    """系统工具集合解析与构建器"""

    @staticmethod
    async def resolve_tools(
        tool_definitions: list[ToolExecutable | Callable | dict[str, Any] | str],
        toolset_funcs: list[Callable],
        system_tools: list[ToolExecutable | Callable | dict[str, Any] | str],
        namespace: str,
        run_context: RunContext,
        run_scoped_cap: CombinedCapability,
    ) -> ResolvedToolPayload:
        """
        解析并合并来自静态定义、动态函数依赖以及能力的工具列表。
        通过工具提供者管理器完成工具的具体实例化及参数绑定。

        参数：
            tool_definitions: 静态工具或工具集合的定义列表。
            toolset_funcs: 待依赖注入解析的工具集生成函数列表。
            system_tools: 系统默认强制集成的工具定义列表.
            namespace: 会话命名空间。
            run_context: 运行上下文对象实例.
            run_scoped_cap: 运行域下的合并能力中间件，用于提供特定的能力工具。

        返回：
            ResolvedToolPayload: 解析完毕并附带依赖绑定关系的工具负载载体。
        """
        defs_to_resolve = list(tool_definitions)

        for ts_func in toolset_funcs:
            sig = inspect.signature(ts_func)
            injected_kwargs = {}
            if len(sig.parameters) > 0:
                injected_kwargs = await DependencyInjector.resolve_all(
                    sig=sig,
                    call_kwargs={},
                    context=run_context,
                )

            res = (
                (await ts_func(**injected_kwargs))
                if is_coroutine_callable(ts_func)
                else ts_func(**injected_kwargs)
            )

            if res is not None:
                if isinstance(res, list):
                    defs_to_resolve.extend(res)
                else:
                    defs_to_resolve.append(res)

        if system_tools:
            for st in system_tools:
                if st not in defs_to_resolve:
                    defs_to_resolve.append(st)

        caps = (
            run_scoped_cap.capabilities
            if run_scoped_cap
            else getattr(run_context, "capabilities", [])
        )
        for cap in caps:
            cap_tools = await cap.get_tools(run_context)
            defs_to_resolve.extend(cap_tools)

        payload = await tool_provider_manager.resolve_tools(
            defs_to_resolve, namespace, context=run_context
        )

        return payload

    @staticmethod
    async def prepare_effective_tools(
        effective_tools: list[ToolExecutable],
        context: RunContext,
        tool_filters: list[Callable],
        run_scoped_cap: CombinedCapability,
    ) -> ToolCollection:
        """
        在将工具发往模型执行器之前，触发最终的过滤器与能力拦截，进行 schema 的清洗。

        参数：
            effective_tools: 备选的工具执行实例列表。
            context: 运行上下文对象实例。
            tool_filters: 运行时自定义工具过滤与清洗函数列表。
            run_scoped_cap: 运行域下的合并能力中间件，提供拦截入口。

        返回：
            ToolCollection: 准备就绪的、可直接发往模型的最终有效工具执行集。
        """
        current_tool_defs = []
        for t_exec in effective_tools:
            if hasattr(t_exec, "get_definition"):
                t_def = await t_exec.get_definition(context)
                if t_def:
                    current_tool_defs.append(t_def)

        if tool_filters:
            for filter_func in tool_filters:
                sig = inspect.signature(filter_func)
                call_kwargs = {"tool_defs": current_tool_defs}
                resolved_kwargs = await DependencyInjector.resolve_all(
                    sig, call_kwargs, context
                )
                filtered_kwargs = {
                    k: v for k, v in resolved_kwargs.items() if k in sig.parameters
                }
                _res = (
                    await filter_func(**filtered_kwargs)
                    if is_coroutine_callable(filter_func)
                    else filter_func(**filtered_kwargs)
                )
                if _res is not None:
                    current_tool_defs = list(_res)

        _cap_res = await run_scoped_cap.prepare_tools(context, current_tool_defs)
        if _cap_res is not None:
            current_tool_defs = list(_cap_res)

        final_defs_map = {d.name.lower(): d for d in current_tool_defs if d}
        final_effective_tools_list = []
        seen_names = set()

        for t_exec in effective_tools:
            t_name = getattr(t_exec, "name", "unknown")
            t_name_lower = t_name.lower()
            if t_name_lower in final_defs_map and t_name_lower not in seen_names:
                seen_names.add(t_name_lower)
                cloned_tool = copy.copy(t_exec)
                setattr(cloned_tool, "_dynamic_def", final_defs_map[t_name_lower])
                final_effective_tools_list.append(cloned_tool)
        return ToolCollection(final_effective_tools_list)


class SessionBuilder:
    """会话与记忆域构建器：负责隔离前缀计算和读写门面装配"""

    @staticmethod
    def build_session_and_memory(
        context: RunContext,
        namespace: str,
        agent_name: str,
        effective_memory: MemoryConfig,
    ) -> tuple[SessionMetadata, SessionMemoryContext]:
        """
        根据当前用户、群组和平台标识，动态隔离前缀，计算并构建会话与记忆存储的读写门面。

        参数：
            context: 运行上下文对象实例。
            namespace: 当前会话的命名空间。
            agent_name: 执行推理的 Agent 标识名。
            effective_memory: 运行时最终生效的 MemoryConfig 配置对象。

        返回：
            tuple[SessionMetadata, SessionMemoryContext]: 包含 (会话元数据, 会话记忆门面上下文) 的元组。
        """  # noqa: E501
        target_builder = None
        if effective_memory.short_term and effective_memory.short_term.isolation:
            target_builder = effective_memory.short_term.isolation

        session_metadata = ContextUtils.build_session_meta(
            context=context, target_builder=target_builder, custom_namespace=namespace
        )
        context.session.session_meta = session_metadata

        memory_context = SessionMemoryContext(
            session_meta=session_metadata,
            memory_config=effective_memory,
            context=context,
        )

        return session_metadata, memory_context
