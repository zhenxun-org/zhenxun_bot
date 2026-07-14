from abc import ABC, abstractmethod
import asyncio
from collections.abc import Awaitable, Callable
import json
from typing import Any, Literal, cast

from zhenxun.services.ai.capabilities import CombinedCapability
from zhenxun.services.ai.core.engine.context_renderer import ContextConverter
from zhenxun.services.ai.core.engine.token_counter import (
    parse_usage_info,
    token_counter,
)
from zhenxun.services.ai.core.exceptions import (
    ControlFlowExit,
    UpstreamServerException,
)
from zhenxun.services.ai.core.messages import (
    AgentMessage,
    AssistantMessage,
    AudioPart,
    ChatRequest,
    ChatResponse,
    FilePart,
    ImagePart,
    LLMMessage,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    VideoPart,
)
from zhenxun.services.ai.core.models import CancellationToken, LLMContext, ToolChoice
from zhenxun.services.ai.core.options import GenerationConfig
from zhenxun.services.ai.core.protocols.tool import ToolExecutable
from zhenxun.services.ai.core.stream_events import (
    LLMEndEvent,
    LLMStartEvent,
    ToolStreamChunkEvent,
)
from zhenxun.services.ai.flow.agent.models import AgentRunResources, AgentState
from zhenxun.services.ai.llm.engine.router import LLMOrchestrator
from zhenxun.services.ai.run import AgentRunResult, RunContext
from zhenxun.services.ai.run.models import OutputDataT
from zhenxun.services.ai.run.session import SessionInfo, session_manager
from zhenxun.services.ai.tools.engine.executor import ToolExecutor
from zhenxun.services.ai.tools.models import ToolResult
from zhenxun.services.ai.utils.logger import log_agent as logger
from zhenxun.utils.pydantic_compat import dump_json_safely, model_construct

from .directive import (
    DirectiveHandlerFunc,
    directive_manager,
)


class BaseAgentExecutor(ABC):
    """
    Agent 核心执行器基类 (Template Method Pattern)。
    定义了基于生命周期的大模型控制流。第三方开发者可通过重写特定钩子，
    """

    async def run(
        self, state: AgentState, resources: AgentRunResources
    ) -> AgentRunResult[OutputDataT]:
        """
        核心模板方法 (Template Method)。
        组织整个大模型推导与工具调用的生命周期循环。如无必要，请勿重写此方法。
        """
        await self.on_start(state, resources)

        try:
            for cycle_index in range(resources.config.max_cycles):
                state.current_cycle = cycle_index
                await self.on_cycle_start(state, resources)

                await self.build_llm_request(state, resources)
                await self.execute_llm(state, resources)

                await self.handle_llm_response(state, resources)
                if state.is_finished:
                    assert state.final_result is not None
                    return state.final_result

                await self.filter_tool_calls(state, resources)
                if state.is_finished:
                    assert state.final_result is not None
                    return state.final_result

                await self.execute_tools(state, resources)

                await self.handle_tool_results(state, resources)
                if state.is_finished:
                    assert state.final_result is not None
                    return state.final_result

            return await self.on_fallback(state, resources)
        except Exception as e:
            raise e

    @abstractmethod
    async def on_start(self, state: AgentState, resources: AgentRunResources) -> None:
        """生命周期: Agent 启动时调用，用于初始化状态或资源。"""
        pass

    @abstractmethod
    async def on_cycle_start(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        """生命周期: 每次推理循环开始时调用。可用于 Token 预估或防死循环检测。"""
        pass

    @abstractmethod
    async def build_llm_request(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        """生命周期: 构造请求大模型的 Messages 上下文和 Extra 参数。"""
        pass

    @abstractmethod
    async def execute_llm(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        """生命周期: 触发大模型 API 请求并返回响应。"""
        pass

    @abstractmethod
    async def handle_llm_response(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        """
        生命周期: 处理大模型返回的结果，解析 Token 用量，
        并将模型回复追加至对话历史。
        """
        pass

    @abstractmethod
    async def filter_tool_calls(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        """生命周期: 从大模型的响应中提取并过滤出需要在本地客户端执行的工具调用请求。"""
        pass

    @abstractmethod
    async def execute_tools(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        """生命周期: 并发执行提取出的工具，并收集结果或异常。"""
        pass

    @abstractmethod
    async def handle_tool_results(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        """
        生命周期: 处理工具返回的结果。
        包括异常拦截、UI 渲染、Handoff 移交指令以及将结果追加至对话历史。
        """
        pass

    @abstractmethod
    async def on_fallback(
        self, state: AgentState, resources: AgentRunResources
    ) -> AgentRunResult[OutputDataT]:
        """生命周期: 当大模型思考循环达到 max_cycles 时触发，执行兜底策略。"""
        pass


class StandardAgentExecutor(BaseAgentExecutor):
    """
    LLM 任务执行器（核心推理引擎）。
    负责：生命周期回调触发、工具循环调用、
    错误反思(Reflexion)、Token消耗追踪。
    """

    def __init__(
        self, directive_handlers: dict[str, DirectiveHandlerFunc] | None = None
    ):
        self.tool_executor = ToolExecutor()
        self._directive_handlers: dict[str, DirectiveHandlerFunc] = (
            directive_handlers or {}
        )

    def _can_retry_via_llm(self, result: ToolResult) -> bool:
        """通过新版的专属字段直接判断是否允许重试"""
        return result.is_retryable

    def _check_follow_up(
        self, state: AgentState, resources: AgentRunResources, session_info: SessionInfo
    ) -> bool:
        """检查追加队列，排空并合并数据到上下文，返回是否发现新消息"""
        follow_ups = session_info.follow_up_queue.drain()
        if follow_ups:
            for fm in follow_ups:
                state.messages.append(LLMMessage.user(f"💬 [用户追加指示]：{fm}"))
            resources.run_context.session.append_only_manager.sync_messages(
                state.messages
            )
            state.is_finished = False
            state.final_result = None
            return True
        return False

    async def _invoke_and_record_llm(
        self,
        state: AgentState,
        resources: AgentRunResources,
        messages: list[AgentMessage],
        tools: list[ToolExecutable | dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | ToolChoice | None = None,
    ) -> ChatResponse:
        """执行 LLM 请求，处理基础指标遥测统计，并将新对话上下文追加到状态流"""
        run_context = resources.run_context
        cancellation_token = run_context.run.cancellation_token

        current_extra = run_context.state.copy()
        current_extra["__global_max_cycles__"] = getattr(
            resources.config, "global_max_cycles", None
        )
        current_extra["__sys_capabilities"] = getattr(run_context, "capabilities", [])
        current_extra["run_context"] = run_context

        flattened_messages = ContextConverter.flatten_to_llm_messages(
            messages, run_context
        )

        await run_context.run.emit(
            LLMStartEvent(
                model_name=resources.model_name or "unknown",
                messages=flattened_messages,
            )
        )

        response = await self._execute_model_request(
            model_name=resources.model_name,
            messages=flattened_messages,
            config=resources.generation_config or GenerationConfig(),
            run_context=run_context,
            tools=tools,
            tool_choice=tool_choice,
            extra=current_extra,
            cancellation_token=cancellation_token,
        )

        await run_context.run.emit(LLMEndEvent(response=response))

        assistant_content = (
            response.content_parts if response.content_parts else response.text
        )
        if response.thought_signature and isinstance(assistant_content, list):
            for part in assistant_content:
                if part.type == "thought":
                    if part.metadata is None:
                        part.metadata = {}
                    part.metadata["thought_signature"] = response.thought_signature
                    break

        assistant_message = AssistantMessage(content=response.content_parts)

        if hasattr(response, "parsed_obj") and response.parsed_obj is not None:
            if not isinstance(response.parsed_obj, str):
                if assistant_message.metadata is None:
                    assistant_message.metadata = {}
                assistant_message.metadata["parsed_obj"] = response.parsed_obj

        usage_obj = parse_usage_info(response.usage_info)
        state.usage += usage_obj
        if usage_obj.completion_tokens > 0:
            assistant_message.token_cost = usage_obj.completion_tokens

        state.messages.append(assistant_message)
        run_context.session.append_only_manager.sync_messages(state.messages)

        return response

    async def _execute_model_request(
        self,
        model_name: str | None,
        messages: list[LLMMessage],
        config: GenerationConfig,
        run_context: RunContext,
        tools: list[ToolExecutable | dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | ToolChoice | None = None,
        extra: dict[str, Any] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> ChatResponse:
        request = ChatRequest(
            messages=messages,
            config=config,
            tools=tools,
            tool_choice=tool_choice,
            extra=extra or {},
        )

        sys_caps = request.extra.pop("__sys_capabilities", [])
        llm_context = LLMContext(request=request, cancellation_token=cancellation_token)
        combined_cap = CombinedCapability(sys_caps)

        async def inner_handler(ctx: LLMContext[Any, Any]) -> ChatResponse:
            return await LLMOrchestrator.invoke(
                request=ctx.request,
                model_name=model_name,
                task="chat",
                override_config=config,
                cancellation_token=ctx.cancellation_token,
            )

        return await combined_cap.wrap_model_request(
            run_context, llm_context, inner_handler
        )

    async def on_start(self, state: AgentState, resources: AgentRunResources) -> None:
        resources.run_context.run.messages = state.messages

    async def _execute_phase(
        self,
        phase_func: Callable[[AgentState, AgentRunResources], Awaitable[None]],
        state: AgentState,
        resources: AgentRunResources,
        session_info: SessionInfo,
    ) -> Literal["NEXT", "RESET_CYCLE", "FINISH"]:
        """统一处理阶段执行与状态机完成/追加检查的高阶函数"""
        await phase_func(state, resources)
        if state.is_finished:
            if self._check_follow_up(state, resources, session_info):
                return "RESET_CYCLE"
            return "FINISH"
        return "NEXT"

    async def run(
        self, state: AgentState, resources: AgentRunResources
    ) -> AgentRunResult[OutputDataT]:
        """覆盖基类的模板方法，实现灵活的 while 控制流和 FOLLOW_UP 合并"""
        await self.on_start(state, resources)
        session_info = await session_manager.get_or_create(
            resources.run_context.session_id or "default_session"
        )

        try:
            cycle_count = 0
            while cycle_count < resources.config.max_cycles:
                state.current_cycle = cycle_count
                await self.on_cycle_start(state, resources)

                await self.build_llm_request(state, resources)
                await self.execute_llm(state, resources)

                status = await self._execute_phase(
                    self.handle_llm_response, state, resources, session_info
                )
                if status == "RESET_CYCLE":
                    cycle_count = 0
                    continue
                if status == "FINISH":
                    assert state.final_result is not None
                    return state.final_result

                status = await self._execute_phase(
                    self.filter_tool_calls, state, resources, session_info
                )
                if status == "RESET_CYCLE":
                    cycle_count = 0
                    continue
                if status == "FINISH":
                    assert state.final_result is not None
                    return state.final_result

                await self.execute_tools(state, resources)

                status = await self._execute_phase(
                    self.handle_tool_results, state, resources, session_info
                )
                if status == "RESET_CYCLE":
                    cycle_count = 0
                    continue
                if status == "FINISH":
                    assert state.final_result is not None
                    return state.final_result

                cycle_count += 1

            if self._check_follow_up(state, resources, session_info):
                return await self.run(state, resources)

            return await self.on_fallback(state, resources)
        except Exception as e:
            raise e

    async def on_cycle_start(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        cancellation_token = resources.run_context.run.cancellation_token
        if cancellation_token:
            cancellation_token.raise_if_cancelled()

        try:
            est_tokens = token_counter.count_context(
                state.messages, resources.model_name or "", base_overhead=0
            )
            logger.debug(
                f"(Iter {state.current_cycle + 1}) "
                f"预估将消耗 {est_tokens} Token "
                f"(Model: {resources.model_name or 'Unknown'})"
            )
        except Exception:
            pass

    async def build_llm_request(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        run_context = resources.run_context
        session_info = await session_manager.get_or_create(
            run_context.session_id or "default_session"
        )
        steer_msgs = session_info.steer_queue.drain()
        if steer_msgs:
            for sm in steer_msgs:
                state.messages.append(LLMMessage.user(f"💬 [用户实时修正指示]：{sm}"))
            run_context.session.append_only_manager.sync_messages(state.messages)

        messages_to_send = []
        if state.static_system_prompt:
            if isinstance(state.static_system_prompt, list):
                for sp in state.static_system_prompt:
                    if sp and sp.strip():
                        messages_to_send.append(LLMMessage.system(sp))
            else:
                if state.static_system_prompt and state.static_system_prompt.strip():
                    messages_to_send.append(
                        LLMMessage.system(state.static_system_prompt)
                    )

        if state.dynamic_system_messages:
            messages_to_send.extend(state.dynamic_system_messages)

        if (
            hasattr(run_context.run, "dynamic_prompts")
            and run_context.run.dynamic_prompts
        ):
            for prompt_text in run_context.run.dynamic_prompts.values():
                if prompt_text and prompt_text.strip():
                    messages_to_send.append(LLMMessage.system(prompt_text))

        messages_to_send.extend(state.messages)

        state.current_request_messages = messages_to_send

    async def execute_llm(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        tools = state.tools

        state.current_response = await self._invoke_and_record_llm(
            state=state,
            resources=resources,
            messages=state.current_request_messages,
            tools=list(tools) if tools else None,
            tool_choice=None,
        )

    async def handle_llm_response(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        response = state.current_response
        if not response:
            return

        if not response.tool_calls:
            logger.debug("✅ 模型未请求工具调用，推理循环结束。")
            state.is_finished = True
            state.final_result = model_construct(
                AgentRunResult,
                output=response.text,
                messages=state.messages,
                usage=state.usage,
            )

    async def filter_tool_calls(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        response = state.current_response
        if not response:
            return
        tools = state.tools
        event_bus = resources.run_context.run.event_bus

        completed_call_ids = {
            p.tool_call_id
            for p in response.content_parts
            if isinstance(p, ToolReturnPart)
        }
        client_tool_calls = []
        for call in response.tool_calls:
            tool_inst = tools.get(call.tool_name) if tools else None
            is_server_side = call.id in completed_call_ids or (
                tool_inst and getattr(tool_inst, "execution_side", "client") == "server"
            )

            if is_server_side:
                logger.debug(
                    f"☁️ 检测到云端工具调用: {call.tool_name}，已跳过本地执行。"
                )
                async with self.tool_executor._tool_stream_scope(
                    event_bus,
                    call.tool_name,
                    call.args if isinstance(call.args, dict) else {},
                    getattr(call, "intent", None),
                ) as box:
                    return_part = next(
                        (
                            p
                            for p in response.content_parts
                            if isinstance(p, ToolReturnPart)
                            and p.tool_call_id == call.id
                        ),
                        None,
                    )
                    if return_part:
                        box["result"] = ToolResult(output=return_part.output)
            else:
                client_tool_calls.append(call)

        if not client_tool_calls:
            logger.info("✅ 无本地客户端工具需执行，推理循环平滑结束。")

            state.is_finished = True
            state.final_result = model_construct(
                AgentRunResult,
                output=response.text,
                messages=state.messages,
                usage=state.usage,
            )

        state.current_tool_calls = client_tool_calls

    async def execute_tools(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        run_context = resources.run_context
        tools = state.tools
        event_bus = run_context.run.event_bus
        tool_calls = state.current_tool_calls

        if not tool_calls:
            return

        val_tasks = [
            self.tool_executor.validate_tool_call(
                call,
                tools,
                run_context,
                event_bus=event_bus,
            )
            for call in tool_calls
        ]
        validated_calls = await asyncio.gather(*val_tasks)

        exec_tasks = [
            self.tool_executor.execute_tool_call(
                val_call,
                tools,
                run_context,
                event_bus=event_bus,
            )
            for val_call in validated_calls
        ]
        tool_results = await asyncio.gather(*exec_tasks, return_exceptions=True)
        state.current_tool_results = tool_results

    def _assemble_tool_message(
        self,
        original_call: ToolCallPart,
        res_or_exc: BaseException | tuple[ToolCallPart, ToolResult],
        tool_res: ToolResult | None,
        state: AgentState,
    ) -> LLMMessage:
        """负责处理异常、解析多模态、序列化，并装配为最终的工具消息载体"""
        media_parts = []
        final_content = "Success"

        if isinstance(res_or_exc, BaseException):
            if isinstance(res_or_exc, ControlFlowExit):
                raise res_or_exc
            final_content = json.dumps(
                {"error": str(res_or_exc), "status": "failed"},
                ensure_ascii=False,
            )
        elif tool_res is not None:
            if isinstance(tool_res.output, list):
                texts = []
                for item in tool_res.output:
                    if isinstance(item, ImagePart | AudioPart | VideoPart | FilePart):
                        media_parts.append(item)
                    elif isinstance(item, TextPart):
                        texts.append(item.text)
                    else:
                        texts.append(str(item))
                final_content = " ".join(texts) if texts else "Success"
            elif isinstance(tool_res.output, str):
                final_content = tool_res.output
            else:
                final_content = dump_json_safely(tool_res.output, ensure_ascii=False)

            tool_usage = getattr(tool_res, "usage", None)
            if tool_usage is not None:
                state.usage += tool_usage

        msg = LLMMessage.tool_response(
            original_call.id, original_call.tool_name, final_content
        )
        if media_parts:
            msg.content.extend(media_parts)
        return msg

    async def handle_tool_results(
        self, state: AgentState, resources: AgentRunResources
    ) -> None:
        """处理所有工具执行结果，调度副作用指令并装配对话回传报文。"""
        tool_calls = state.current_tool_calls
        tool_results = state.current_tool_results
        if not tool_calls or not tool_results:
            return

        for i, res_or_exc in enumerate(tool_results):
            original_call = tool_calls[i]
            tool_res = None

            if not isinstance(res_or_exc, BaseException):
                _, raw_tool_res = res_or_exc
                tool_res = raw_tool_res

            msg = self._assemble_tool_message(
                original_call, res_or_exc, tool_res, state
            )
            state.messages.append(msg)

            if tool_res and getattr(tool_res, "directive", None):
                ns = getattr(resources.run_context.session, "namespace", "global")
                handler = directive_manager.get_handler(
                    tool_res.directive.name, namespace=ns
                )

                if handler:
                    await handler(state, resources, tool_res)
                    if state.is_finished:
                        resources.run_context.session.append_only_manager.sync_messages(
                            state.messages
                        )
                        return
                else:
                    logger.warning(
                        f"⚠️ 未能找到名为 '{tool_res.directive.name}' "
                        f"的指令处理器 (Namespace: {ns})"
                    )

        resources.run_context.session.append_only_manager.sync_messages(state.messages)

    async def on_fallback(
        self, state: AgentState, resources: AgentRunResources
    ) -> AgentRunResult[OutputDataT]:
        run_context = resources.run_context

        if not resources.config.enable_fallback_summary:
            raise UpstreamServerException(
                f"超过最大工具调用循环次数 ({resources.config.max_cycles})。",
            )

        logger.warning(
            f"达到最大循环次数 ({resources.config.max_cycles})，触发兜底总结机制。"
        )

        await run_context.run.emit(
            ToolStreamChunkEvent(
                tool_name="System",
                content="⏳ 思考过程过于复杂，正在强制生成最终总结...",
            )
        )

        fallback_msg = LLMMessage.user(
            "### 🚨 [系统强制指令]\n"
            "你的任务执行已达到最大工具调用循环次数上限，当前思考流已被框架强制中断。\n"
            "请**诚实地**向用户总结：你目前进行到了哪一步？遇到了什么困难导致循环耗尽？还有哪些预期步骤未能完成？\n"
            "**绝对禁止**对用户撒谎声声称你已经完成了任务。严禁再次尝试调用任何工具！请直接输出纯文本结果。"
        )
        state.messages.append(fallback_msg)

        fallback_response = await self._invoke_and_record_llm(
            state=state,
            resources=resources,
            messages=state.messages,
            tools=[],
            tool_choice="none",
        )

        return cast(
            AgentRunResult[OutputDataT],
            model_construct(
                AgentRunResult,
                output=fallback_response.text,
                messages=state.messages,
                structured_data=None,
                usage=state.usage,
            ),
        )
