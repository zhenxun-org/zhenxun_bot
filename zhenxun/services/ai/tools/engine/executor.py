from __future__ import annotations

from abc import ABC, abstractmethod
import ast
import asyncio
from contextlib import asynccontextmanager
import inspect
import json
from typing import TYPE_CHECKING, Any, cast

import json_repair
from nonebot.adapters import Message as PlatformMessage

from zhenxun.services.ai.capabilities import CombinedCapability
from zhenxun.services.ai.core.exceptions import (
    ControlFlowExit,
    ToolFatalError,
    ToolRetryError,
)
from zhenxun.services.ai.core.messages import AnyLLMMessage, LLMMessage, ToolCallPart
from zhenxun.services.ai.core.stream_events import (
    EventBus,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolStreamChunkEvent,
    UserCustomEvent,
)
from zhenxun.services.ai.message_builder import MessageBuilder
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.run.di import DependencyInjector
from zhenxun.services.ai.tools.models import (
    StateSyncResult,
    ToolOptions,
    ToolResult,
    ToolResultChunk,
    ValidatedToolCall,
)
from zhenxun.services.log import logger

if TYPE_CHECKING:
    from zhenxun.services.ai.tools.core.tool import BaseTool
    from zhenxun.services.ai.tools.engine.registry import ToolCollection


class ToolExecutor:
    """
    全能工具执行器。
    负责接收工具调用请求，解析参数，触发回调，执行工具，并返回标准化的结果。
    """

    def __init__(self):
        pass

    def _get_combined_capability(
        self, executable: Any, context: RunContext
    ) -> CombinedCapability:
        """合并 Agent 上下文 (已包含 Global) 与 Tool 私有的 Capability"""
        tool_caps = getattr(getattr(executable, "settings", None), "capabilities", [])
        agent_caps = getattr(context, "capabilities", [])

        all_caps = list(agent_caps)
        for c in tool_caps:
            if c not in all_caps:
                all_caps.append(c)
        return CombinedCapability(all_caps)

    @asynccontextmanager
    async def _tool_stream_scope(
        self,
        event_bus: EventBus | None,
        tool_name: str,
        arguments: dict[str, Any],
        intent: str | None,
    ):
        """生命周期上下文管理器：接管事件流的发送与异常包装样板代码"""
        if event_bus:
            await event_bus.emit(
                ToolCallStartEvent(
                    tool_name=tool_name, arguments=arguments, intent=intent
                )
            )
        result_box: dict[str, Any] = {}
        try:
            yield result_box
        finally:
            if event_bus and "result" in result_box:
                res = result_box["result"]
                await event_bus.emit(
                    ToolCallEndEvent(
                        tool_name=tool_name, result=res, is_error=res.is_error
                    )
                )

    @staticmethod
    def _robust_parse_args(
        args_raw: str | dict[str, Any],
    ) -> tuple[bool, dict[str, Any], str | None, str]:
        """容错解析参数纯函数，返回 (是否成功, 解析后的字典, intent意图, 原始字符串)"""
        if isinstance(args_raw, dict):
            arguments = args_raw.copy()
            parsed_successfully = True
            args_str = json.dumps(args_raw, ensure_ascii=False)
        else:
            args_str = args_raw
            arguments = {}
            parsed_successfully = False
            if not args_str.strip():
                parsed_successfully = True
            else:
                try:
                    parsed = json.loads(args_str)
                    if isinstance(parsed, dict):
                        arguments, parsed_successfully = parsed, True
                except json.JSONDecodeError:
                    try:
                        parsed = ast.literal_eval(args_str)
                        if isinstance(parsed, dict):
                            arguments, parsed_successfully = parsed, True
                    except (ValueError, SyntaxError):
                        try:
                            repaired_str = str(
                                json_repair.repair_json(args_str, skip_json_loads=True)
                            )
                            parsed = json.loads(repaired_str)
                            if isinstance(parsed, dict):
                                arguments, parsed_successfully = parsed, True
                                logger.debug(
                                    "⚒️ 成功修复损坏的工具参数: "
                                    f"{args_str} -> {repaired_str}",
                                    "ToolExecutor",
                                )
                        except Exception:
                            pass
        intent_str = None
        if parsed_successfully:
            _intent = arguments.pop("_intent", None)
            if _intent:
                intent_str = str(_intent)
        return parsed_successfully, arguments, intent_str, args_str

    def _prepare_tool_context(
        self,
        context: RunContext | None,
        tool_call_id: str,
        tool_name: str,
        executable: Any,
        event_bus: EventBus | None,
        available_tools: "ToolCollection | dict[str, Any] | None" = None,
    ) -> RunContext:
        """准备/克隆工具调用所使用的隔离 RunContext"""
        safe_context = (
            context.clone_for_tool_call(tool_call_id, tool_name)
            if context
            else RunContext()
        )
        safe_context.run.event_bus = event_bus
        safe_context.call.tool_name = tool_name
        safe_context.call.current_tool = executable
        if available_tools is not None:
            safe_context.state["__available_tools"] = available_tools
        return safe_context

    async def validate_tool_call(
        self,
        tool_call: ToolCallPart,
        available_tools: "ToolCollection | dict[str, Any] | None",
        context: RunContext | None = None,
        event_bus: EventBus | None = None,
    ) -> ValidatedToolCall:
        """验证单一工具调用，完成参数解析、类型检查与交互式补全(如果触发)。"""
        if not available_tools:
            available_tools = {}
        if not tool_call.tool_name:
            return ValidatedToolCall(
                call=tool_call,
                args_valid=False,
                validation_error=ToolRetryError("tool_call.tool_name 不能为空"),
            )

        tool_name = tool_call.tool_name
        parsed_successfully, arguments, intent_str, arguments_str = (
            self._robust_parse_args(tool_call.args)
        )

        if tool_call.args and not parsed_successfully:
            if context:
                context.run.tool_retries[tool_name] = (
                    context.run.tool_retries.get(tool_name, 0) + 1
                )
            return ValidatedToolCall(
                call=tool_call,
                args_valid=False,
                validation_error=ToolRetryError(
                    f"参数JSON解析失败，请检查 JSON 语法是否合法: {arguments_str}"
                ),
            )
        elif intent_str:
            logger.info(f"🧠 [Agent Intent] 调用工具 {tool_name} 的意图: {intent_str}")

        executable = available_tools.get(tool_name)
        if not executable or not hasattr(executable, "execute"):
            return ValidatedToolCall(
                call=tool_call,
                args_valid=False,
                validation_error=ToolRetryError(f"Tool '{tool_name}' not found."),
            )

        safe_context = self._prepare_tool_context(
            context, tool_call.id, tool_name, executable, event_bus
        )

        combined_cap = self._get_combined_capability(executable, safe_context)

        async def inner_validate(args_inner):
            if isinstance(args_inner, dict) and hasattr(executable, "validate_args"):
                import inspect

                sig = inspect.signature(executable.validate_args)
                if "context" in sig.parameters:
                    return await executable.validate_args(
                        args_inner, context=safe_context
                    )
                return await executable.validate_args(args_inner)
            return args_inner

        try:
            validated_args = await combined_cap.wrap_tool_validate(
                safe_context, tool_name, arguments, inner_validate
            )
            return ValidatedToolCall(
                call=tool_call,
                tool=executable,
                args_valid=True,
                validated_args=validated_args,
                intent=intent_str,
            )
        except BaseException as e:
            if isinstance(e, asyncio.CancelledError):
                raise e
            return ValidatedToolCall(
                call=tool_call,
                tool=executable,
                args_valid=False,
                validation_error=e,
            )

    async def execute_tool_call(
        self,
        validated: ValidatedToolCall,
        available_tools: "ToolCollection | dict[str, Any] | None",
        context: RunContext | None = None,
        model_name: str | None = None,
        max_retries: int = 0,
        event_bus: EventBus | None = None,
    ) -> tuple[ToolCallPart, ToolResult]:
        """核心执行阶段。只接收已经通过 Validation 阶段的 ValidatedToolCall 载体。"""
        if not available_tools:
            available_tools = {}
        tool_name = validated.call.tool_name
        if not validated.args_valid or validated.tool is None:
            if isinstance(validated.validation_error, ControlFlowExit):
                raise validated.validation_error

            err_msg = getattr(
                validated.validation_error, "message", str(validated.validation_error)
            )
            res = ToolResult(output=f"执行被拦截或参数错误: {err_msg}").as_error()
            if event_bus:
                await event_bus.emit(
                    ToolCallEndEvent(
                        tool_name=tool_name, result=res, is_error=res.is_error
                    )
                )
            return validated.call, res

        executable = validated.tool
        arguments = validated.validated_args or {}

        safe_context = self._prepare_tool_context(
            context,
            validated.call.id,
            tool_name,
            executable,
            event_bus,
            available_tools,
        )

        from zhenxun.services.ai.run.context import set_run_context

        combined_cap = self._get_combined_capability(executable, safe_context)

        async def inner_handler(args_inner: dict) -> Any:
            return await executable.execute(context=safe_context, **args_inner)

        async with self._tool_stream_scope(
            event_bus, tool_name, arguments, validated.intent
        ) as box:
            with set_run_context(safe_context):
                try:
                    result = await combined_cap.wrap_tool_execute(
                        safe_context, tool_name, arguments, inner_handler
                    )
                    if not isinstance(result, ToolResult):
                        result = ToolResult(output=result)

                    if isinstance(result, StateSyncResult) and result.state_notice:
                        if safe_context:
                            safe_context.run.add_system_prompt(
                                f"[系统通知(状态同步)]：{result.state_notice}"
                            )
                except BaseException as e:
                    if isinstance(e, ControlFlowExit):
                        raise e
                    if isinstance(e, asyncio.CancelledError):
                        raise e
                    logger.error(f"洋葱模型异常穿透: {e}")
                    result = ToolResult(output=f"System Fatal Error: {e}").as_error()
            box["result"] = result

        return validated.call, box["result"]

    async def execute_batch(
        self,
        tool_calls: list[ToolCallPart],
        available_tools: "ToolCollection | dict[str, Any] | None",
        context: RunContext | None = None,
        model_name: str | None = None,
        max_retries: int = 0,
        event_bus: EventBus | None = None,
    ) -> list[AnyLLMMessage]:
        """批量并发执行多个工具调用。"""
        if not available_tools:
            available_tools = {}
        if not tool_calls:
            return []

        val_tasks = [
            self.validate_tool_call(
                call,
                available_tools,
                context,
                event_bus=event_bus,
            )
            for call in tool_calls
        ]
        validated_calls = await asyncio.gather(*val_tasks)

        results: list[Any] = [None] * len(validated_calls)

        async def _run_tool(index: int, val_call: ValidatedToolCall):
            try:
                res = await self.execute_tool_call(
                    val_call,
                    available_tools,
                    context,
                    model_name=model_name,
                    max_retries=max_retries,
                    event_bus=event_bus,
                )
                results[index] = res
            except Exception as e:
                results[index] = e

        chunks: list[list[tuple[int, ValidatedToolCall]]] = []
        current_chunk: list[tuple[int, ValidatedToolCall]] = []

        for i, val_call in enumerate(validated_calls):
            executable = getattr(val_call, "tool", None)
            concurrency_mode = getattr(
                getattr(executable, "settings", None), "concurrency", "shared"
            )

            if concurrency_mode == "exclusive":
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = []
                chunks.append([(i, val_call)])
            else:
                current_chunk.append((i, val_call))

        if current_chunk:
            chunks.append(current_chunk)

        for chunk in chunks:
            await asyncio.gather(*[_run_tool(i, call) for i, call in chunk])

        tool_messages: list[AnyLLMMessage] = []
        for index, result_pair in enumerate(results):
            original_call = tool_calls[index]
            func_name = original_call.tool_name

            if isinstance(result_pair, BaseException):
                if isinstance(result_pair, ControlFlowExit):
                    raise result_pair
                if isinstance(result_pair, asyncio.CancelledError):
                    raise result_pair

                logger.error(f"工具批量执行并发崩溃: {func_name}, 错误: {result_pair}")
                result_pair = (
                    original_call,
                    ToolResult(output=f"Crash: {result_pair}").as_error(),
                )

            tool_call_result = cast(tuple[ToolCallPart, ToolResult], result_pair)
            _, tool_result = tool_call_result
            tool_messages.append(
                LLMMessage.tool_response(
                    tool_call_id=original_call.id,
                    function_name=func_name,
                    result=tool_result.output,
                )
            )
        return tool_messages


class ToolExecutionPolicy:
    """
    工具执行策略 (Strategy Pattern)。
    负责解析工具私有配置与系统全局配置，决定最大重试次数、Fallback 路由目标等流转行为。
    """

    def __init__(self, tool: BaseTool, global_max_retries: int = 0):
        self.tool = tool
        self.settings: ToolOptions = getattr(tool, "settings", ToolOptions())
        self.metadata: dict[str, Any] = (
            self.settings.metadata if self.settings else getattr(tool, "metadata", {})
        )
        self.global_max_retries = global_max_retries

    @property
    def max_retries(self) -> int:
        """
        计算当前工具的绝对最大重试次数。
        优先使用工具级配置 (ToolOptions.max_retries)，如果未设置，则使用全局配置。
        """
        tool_retries = getattr(self.settings, "max_retries", None)
        if tool_retries is not None:
            return tool_retries
        return max(self.global_max_retries, 1)


class ToolRunner(ABC):
    """
    工具运行器基类协议。
    负责将参数请求物理落实为目标执行。
    """

    @abstractmethod
    async def run(
        self, tool: BaseTool, context: RunContext, **kwargs: Any
    ) -> ToolResult:
        pass


class NativeToolRunner(ToolRunner):
    """
    原生 Python 函数工具运行器。
    负责处理依赖注入 (DI)、异步包装、生成器流式收集以及框架级的多模态消息转换。
    """

    async def run(
        self, tool: BaseTool, context: RunContext, **kwargs: Any
    ) -> ToolResult:
        target_func = tool.get_execute_target()
        signature_target = tool.get_signature_target()

        if not target_func:
            return ToolResult(output="Error: 未找到有效的执行目标(run 方法)").as_error()

        call_kwargs = dict(kwargs)

        try:
            target_call_kwargs = await DependencyInjector.resolve_all(
                sig=inspect.signature(signature_target),
                call_kwargs=dict(call_kwargs),
                context=context,
            )
        except ValueError as e:
            logger.error(f"工具 {tool.name} 依赖注入失败: {e}", e=e)
            raise ToolFatalError(f"框架依赖注入失败: {e}")

        is_async_gen = getattr(
            target_func, "_is_async_gen", False
        ) or inspect.isasyncgenfunction(target_func)
        if is_async_gen:
            res = None
            async for chunk in target_func(**target_call_kwargs):
                if isinstance(chunk, ToolResult):
                    res = chunk
                else:
                    chunk_obj = (
                        chunk
                        if isinstance(chunk, ToolResultChunk)
                        else ToolResultChunk(content=str(chunk))
                    )
                    is_silent = (
                        getattr(tool.settings, "silent", False)
                        if tool and hasattr(tool, "settings")
                        else False
                    )
                    if context.run.event_bus and not is_silent:
                        await context.run.event_bus.emit(
                            ToolStreamChunkEvent(
                                tool_name=tool.name,
                                content=chunk_obj.content,
                                metadata=chunk_obj.metadata,
                            )
                        )
            if res is None:
                res = ToolResult(output="Stream finished successfully.")
        else:
            res = await target_func(**target_call_kwargs)

        if isinstance(res, ToolResult):
            final_result = res
        else:
            if str(type(res)).find("Message") != -1:
                uni_msg = (
                    MessageBuilder.message_to_unimessage(res)
                    if isinstance(res, PlatformMessage)
                    else res
                )
                parts = await MessageBuilder.unimsg_to_llm_parts(uni_msg)
                if context and context.run.event_bus:
                    await context.run.event_bus.emit(UserCustomEvent(display=uni_msg))
                final_result = ToolResult(output=parts)
            else:
                final_result = ToolResult(output=res)

        return final_result


from zhenxun.services.ai.tools.core.tool import register_tool_runner

register_tool_runner(NativeToolRunner)
