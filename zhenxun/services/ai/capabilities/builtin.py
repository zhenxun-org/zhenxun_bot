from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from zhenxun.models.user_console import UserConsole
from zhenxun.services.ai.capabilities import (
    AbstractCapability,
    WrapModelRequestHandler,
    WrapToolExecuteHandler,
)
from zhenxun.services.ai.config import get_llm_config
from zhenxun.services.ai.core.exceptions import (
    AbortException,
    ControlFlowExit,
    GuardrailViolationError,
    LLMException,
    ModelRetry,
    ResponseParseException,
    SchemaParseError,
    ToolFatalError,
    ToolFinishException,
    ToolRetryError,
    UpstreamServerException,
)
from zhenxun.services.ai.core.messages import (
    ChatRequest,
    ChatResponse,
    LLMMessage,
    ToolCallPart,
)
from zhenxun.services.ai.core.models import LLMContext
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.utils import PermissionUtils
from zhenxun.services.log import logger
from zhenxun.utils.enum import GoldHandle
from zhenxun.utils.exception import InsufficientGold


def _get_tool_meta(tool: Any, key: str, default: Any = None) -> Any:
    """辅助方法：安全地提取工具元数据中指定的键值"""
    if not tool:
        return default
    settings = getattr(tool, "settings", None)
    meta = (settings.metadata if settings else None) or getattr(tool, "metadata", {})
    return meta.get(key, default)


class StuckDetectionCapability(AbstractCapability):
    """死循环检测：使用前置请求拦截防止 LLM 陷入无限重试"""

    async def wrap_model_request(
        self,
        context: RunContext,
        llm_context: LLMContext[ChatRequest, ChatResponse],
        handler: WrapModelRequestHandler,
    ) -> ChatResponse:
        max_repeated_errors = 3
        action_hashes = []
        messages = list(llm_context.request.messages)
        idx = len(messages) - 1

        while idx >= 0:
            msg = messages[idx]
            if msg.role == "tool":
                batch_tool_contents = []
                while idx >= 0 and messages[idx].role == "tool":
                    for tr in messages[idx].tool_returns:
                        batch_tool_contents.append(f"{tr.tool_name}:{tr.output}")
                    idx -= 1

                if (
                    idx >= 0
                    and messages[idx].role == "assistant"
                    and messages[idx].tool_calls
                ):
                    assistant_msg = messages[idx]
                    batch_tool_calls = []
                    for tc in assistant_msg.tool_calls:
                        if isinstance(tc, ToolCallPart):
                            args_str = (
                                tc.args
                                if isinstance(tc.args, str)
                                else json.dumps(tc.args, ensure_ascii=False)
                            )
                            batch_tool_calls.append(f"{tc.tool_name}:{args_str}")

                    batch_tool_calls.sort()
                    batch_tool_contents.sort()

                    state_str = (
                        "|".join(batch_tool_calls)
                        + "||"
                        + "|".join(batch_tool_contents)
                    )
                    state_hash = hashlib.md5(state_str.encode("utf-8")).hexdigest()
                    action_hashes.append(state_hash)
                    idx -= 1
                else:
                    break
            elif msg.role == "assistant":
                idx -= 1
            else:
                break

        if len(action_hashes) >= max_repeated_errors:
            recent_hashes = action_hashes[:max_repeated_errors]
            if len(set(recent_hashes)) == 1:
                logger.warning(
                    "[StuckDetection] 拦截到死循环：连续 "
                    f"{max_repeated_errors} 次产生完全相同的状态哈希碰撞。"
                )
                raise ToolFatalError(
                    "Agent 触发终极防呆机制：连续 "
                    f"{max_repeated_errors} 次产生完全相同的"
                    "无效工具调用状态，已物理阻断以节省 Token。"
                )

        return await handler(llm_context)


class GlobalCycleLimitCapability(AbstractCapability):
    """全局防死循环检测中间件：跨 Agent 追踪大模型调用总次数"""

    async def wrap_model_request(
        self,
        context: RunContext,
        llm_context: LLMContext[ChatRequest, ChatResponse],
        handler: WrapModelRequestHandler,
    ) -> ChatResponse:
        global_cycles = (
            context.session.shared_state.get("__global_cycle_count__", 0) + 1
        )
        context.session.shared_state["__global_cycle_count__"] = global_cycles

        global_max = llm_context.request.extra.get("__global_max_cycles__")
        if global_max is None:
            global_max = get_llm_config().agent_settings.global_max_cycles

        if global_max is not None and global_cycles > global_max:
            logger.error(
                "🚨 触发全局防护：整个流水线执行步数已达到全局上限 "
                f"({global_max})，强制熔断！"
            )
            raise AbortException(
                reason=f"全局大模型思考循环次数已超限 ({global_max}次)",
                display="🚨 系统保护触发：任务过于复杂或陷入多智能体死循环，"
                "已被强行中断以节省资源。",
            )

        return await handler(llm_context)


class PermissionCapability(AbstractCapability):
    """权限校验中间件：在执行前根据确定参数进行动态鉴权"""

    async def wrap_tool_execute(
        self,
        context: RunContext,
        tool_name: str,
        arguments: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> dict[str, Any]:
        tool = context.call.current_tool
        admin_level = _get_tool_meta(tool, "admin_level", 0)
        if admin_level > 0:
            if not await PermissionUtils.check_admin_level(context, admin_level):
                msg = (
                    "系统警告：用户权限不足（需要等级 "
                    f"{admin_level}）。"
                    "请温和地向用户解释权限不足，并拒绝执行。"
                )
                user_id = context.get_user_id()
                logger.warning(
                    f"🛡️ [Capability] 权限拦截: 用户 {user_id} 尝试调用 "
                    f"{getattr(tool, 'name', 'unknown')}"
                )
                raise ToolFatalError(
                    msg, display_content=f"❌ 权限不足: 需要等级 {admin_level}"
                )
        return await handler(arguments)


class BillingCapability(AbstractCapability):
    """经济系统中间件：执行前扣除金币"""

    async def wrap_tool_execute(
        self,
        context: RunContext,
        tool_name: str,
        arguments: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> dict[str, Any]:
        tool = context.call.current_tool
        cost_gold = _get_tool_meta(tool, "cost_gold", 0)
        if cost_gold > 0:
            user_id = context.get_user_id()
            platform = context.get_platform()
            if user_id:
                try:
                    await UserConsole.reduce_gold(
                        user_id,
                        cost_gold,
                        GoldHandle.PLUGIN,
                        f"agent_tool:{getattr(tool, 'name', 'unknown')}",
                        platform,
                    )
                except InsufficientGold:
                    msg = (
                        f"系统警告：用户金币不足（需要 {cost_gold} 金币，但余额不够）。"
                        "请向用户解释金币不足，提醒可通过签到赚取，并拒绝执行。"
                    )
                    logger.warning(
                        f"💰 [Capability] 金币拦截: 用户 {user_id} 尝试调用 "
                        f"{getattr(tool, 'name', 'unknown')}"
                    )
                    raise ToolFatalError(
                        msg, display_content=f"❌ 余额不足: 需要 {cost_gold} 金币"
                    )
        return await handler(arguments)


class ToolRetryAndReflectionCapability(AbstractCapability):
    """
    重试与自愈反思中间件。
    接管原执行器中的重试计数与致命异常熔断。
    将 Python 异常优雅地转化为大模型的反思 Prompt。
    """

    async def wrap_tool_execute(
        self,
        context: RunContext,
        tool_name: str,
        arguments: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        try:
            return await handler(arguments)
        except Exception as e:
            from zhenxun.services.ai.tools.engine.executor import ToolExecutionPolicy
            from zhenxun.services.ai.tools.models import ToolResult

            if isinstance(e, ControlFlowExit):
                raise e

            retries = context.run.tool_retries.get(tool_name, 0)
            retries += 1
            context.run.tool_retries[tool_name] = retries

            from zhenxun.services.ai.tools.core.tool import BaseTool

            tool = cast(BaseTool, context.call.current_tool)
            policy = ToolExecutionPolicy(tool)
            max_retries_limit = policy.max_retries

            if isinstance(e, ToolFatalError | ToolFinishException):
                display_msg = getattr(e, "display_content", f"❌ 系统致命错误: {e}")
                raise AbortException(reason=str(e), display=display_msg)

            if retries > max_retries_limit:
                raise AbortException(
                    reason=f"工具 '{tool_name}' 连续出错达 {retries} 次，超出上限。",
                    display=f"🚨 工具 '{tool_name}' 已达最大重试次数，执行阻断。",
                )

            return ToolResult(output=f"执行发生异常: {e}").as_error()


class ReflexionCapability(AbstractCapability):
    """自愈反思与验证引擎 (Reflexion Engine)。
    统一处理结构化解析失败和语义护栏拦截。"""

    async def wrap_tool_execute(self, context, tool_name, arguments, handler):
        try:
            return await handler(arguments)
        except Exception as error:
            from zhenxun.services.ai.core.engine.structured_parser import (
                DEFAULT_IVR_TEMPLATE,
            )
            from zhenxun.services.ai.tools.models import ToolResult

            if isinstance(error, ToolRetryError | ModelRetry):
                error_msg = getattr(error, "message", str(error))
                feedback_prompt = DEFAULT_IVR_TEMPLATE.format(error_msg=error_msg)
                context.run.add_system_prompt(feedback_prompt)
                return ToolResult(
                    output=f"执行失败：{error_msg}",
                ).as_error()
            raise error.with_traceback(None) from None

    def _extract_error_info(
        self, e: Exception, current_response_text: str
    ) -> tuple[str, str, bool]:
        """
        统一提取异常报错详情与可恢复标记
        返回 (error_msg, raw_response, is_recoverable)
        """
        is_model_retry = isinstance(e, ModelRetry)
        is_llm_error = isinstance(e, LLMException)
        llm_error = cast(LLMException, e) if is_llm_error else None

        if (
            not is_model_retry
            and llm_error
            and not isinstance(
                llm_error, ResponseParseException | UpstreamServerException
            )
        ):
            raise e

        if is_model_retry:
            error_msg = getattr(e, "message", str(e))
            raw_response = current_response_text
        else:
            error_msg = (
                llm_error.details.get("validation_error", str(e))
                if llm_error
                else str(e)
            )
            raw_response = current_response_text or (
                llm_error.details.get("raw_response", "") if llm_error else ""
            )

        is_recoverable = getattr(llm_error, "recoverable", True) if llm_error else True
        return error_msg, raw_response, is_recoverable

    def _generate_feedback_prompt(
        self, e: Exception, error_msg: str, error_template: str | None
    ) -> str:
        """
        根据不同的异常类型生成针对性的自愈反思提示词 (Feedback Prompt)
        """
        if isinstance(e, SchemaParseError):
            if "数据内容未通过规则校验" in error_msg:
                return f"""### ⚠️ [数据内容校验失败]
你输出的 JSON 格式完全正确，但部分字段的内容未能通过业务规则约束。

**失败详情：**
> {error_msg}

**修正要求：** 请仔细阅读上述失败详情，你必须打破先前的部分指令限制以满足上述规则，调整报错字段的值并重新输出。"""  # noqa: E501
            else:
                return f"""### ❌ [格式解析失败]
你输出的结构化数据（JSON）格式损坏或字段类型不匹配，未能通过校验。

**解析错误报告：**
> {error_msg}

**修正要求：** 请仔细检查缺失的必填字段、错误的数据类型或未闭合的括号，严格参考你可用的 Schema 定义，重新输出正确格式的数据。"""  # noqa: E501
        elif isinstance(e, GuardrailViolationError):
            return f"""### 🛡️ [业务护栏违规]
你输出的数据格式完全正确，但在业务逻辑层触发了合规/风控护栏。

**拦截原因报告：**
> {error_msg}

**修正要求：** 请结合上述反馈报告，反思你的决策逻辑或内容生成，在保持数据格式正确的前提下，重新生成符合护栏规范的内容。"""  # noqa: E501
        else:
            if error_template:
                return error_template.format(error_msg=error_msg)
            from zhenxun.services.ai.core.engine.structured_parser import (
                DEFAULT_IVR_TEMPLATE,
            )

            return DEFAULT_IVR_TEMPLATE.format(error_msg=error_msg)

    async def wrap_model_request(
        self,
        context: RunContext,
        llm_context: LLMContext[ChatRequest, ChatResponse],
        handler: WrapModelRequestHandler,
    ) -> ChatResponse:
        output_processor = llm_context.request.extra.get("output_processor")
        guardrails = llm_context.request.extra.get("guardrails", [])

        if not output_processor and not guardrails:
            return await handler(llm_context)

        max_retries = llm_context.request.extra.get("max_retries", 3)
        error_template = (
            output_processor.error_template if output_processor else "{error_msg}"
        )

        ivr_messages = list(llm_context.request.messages)
        last_exception: Exception | None = None

        from zhenxun.services.ai.guardrails import GuardrailPipeline

        pipeline = GuardrailPipeline(guardrails) if guardrails else None

        for attempt in range(max_retries + 1):
            llm_context.request.messages = list(ivr_messages)
            current_response_text: str = ""

            try:
                if pipeline:
                    llm_context.request.messages = await pipeline.run_input_pipeline(
                        llm_context.request.messages, context
                    )

                response = await handler(llm_context)
                current_response_text = response.text

                if response.tool_calls:
                    return response

                if output_processor:
                    final_obj = await output_processor.validate_and_parse(
                        current_response_text, context=context
                    )
                else:
                    final_obj = current_response_text

                if pipeline:
                    resp_out, final_obj_out = await pipeline.run_output_pipeline(
                        response, final_obj, context
                    )
                    response = cast("ChatResponse", resp_out)
                    final_obj = final_obj_out
                    current_response_text = response.text

                response.parsed_obj = final_obj
                return response

            except Exception as e:
                last_exception = e
                try:
                    error_msg, raw_response, is_recoverable = self._extract_error_info(
                        e, current_response_text
                    )
                except Exception as fatal_e:
                    raise fatal_e.with_traceback(None) from None

                if attempt < max_retries:
                    logger.warning(
                        "输出校验未通过 "
                        f"(尝试 {attempt + 1}/{max_retries + 1})。"
                        f"启动反思修复闭环... 失败原因: {error_msg}"
                    )

                    if raw_response:
                        ivr_messages.append(
                            cast(
                                LLMMessage,
                                LLMMessage.assistant_text_response(raw_response),
                            )
                        )

                    feedback_prompt = self._generate_feedback_prompt(
                        e, error_msg, error_template
                    )
                    ivr_messages.append(
                        cast(LLMMessage, LLMMessage.user(feedback_prompt))
                    )
                    continue

                if not is_recoverable:
                    raise last_exception.with_traceback(None) from None

        if last_exception:
            raise last_exception.with_traceback(None) from None
        raise UpstreamServerException(
            "反思循环耗尽，未能生成符合所有校验规则的合法结果。",
        ).with_traceback(None) from None
