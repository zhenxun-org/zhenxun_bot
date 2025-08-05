"""
LLM 轻量级工具执行器

提供驱动 LLM 与本地函数工具之间交互的核心循环。
"""

import asyncio
from enum import Enum
import json
from typing import Any

from pydantic import BaseModel, Field

from zhenxun.services.log import logger
from zhenxun.utils.decorator.retry import Retry
from zhenxun.utils.pydantic_compat import model_dump

from .service import LLMModel
from .types import (
    LLMErrorCode,
    LLMException,
    LLMMessage,
    ToolExecutable,
    ToolResult,
)


class ExecutionConfig(BaseModel):
    """
    轻量级执行器的配置。
    """

    max_cycles: int = Field(default=5, description="工具调用循环的最大次数。")


class ToolErrorType(str, Enum):
    """结构化工具错误的类型枚举。"""

    TOOL_NOT_FOUND = "ToolNotFound"
    INVALID_ARGUMENTS = "InvalidArguments"
    EXECUTION_ERROR = "ExecutionError"
    USER_CANCELLATION = "UserCancellation"


class ToolErrorResult(BaseModel):
    """一个结构化的工具执行错误模型，用于返回给 LLM。"""

    error_type: ToolErrorType = Field(..., description="错误的类型。")
    message: str = Field(..., description="对错误的详细描述。")
    is_retryable: bool = Field(False, description="指示这个错误是否可能通过重试解决。")

    def model_dump(self, **kwargs):
        return model_dump(self, **kwargs)


def _is_exception_retryable(e: Exception) -> bool:
    """判断一个异常是否应该触发重试。"""
    if isinstance(e, LLMException):
        retryable_codes = {
            LLMErrorCode.API_REQUEST_FAILED,
            LLMErrorCode.API_TIMEOUT,
            LLMErrorCode.API_RATE_LIMITED,
        }
        return e.code in retryable_codes
    return True


class LLMToolExecutor:
    """
    一个通用的执行器，负责驱动 LLM 与工具之间的多轮交互。
    """

    def __init__(self, model: LLMModel):
        self.model = model

    async def run(
        self,
        messages: list[LLMMessage],
        tools: dict[str, ToolExecutable],
        config: ExecutionConfig | None = None,
    ) -> list[LLMMessage]:
        """
        执行完整的思考-行动循环。
        """
        effective_config = config or ExecutionConfig()
        execution_history = list(messages)

        for i in range(effective_config.max_cycles):
            response = await self.model.generate_response(
                execution_history, tools=tools
            )

            assistant_message = LLMMessage(
                role="assistant",
                content=response.text,
                tool_calls=response.tool_calls,
            )
            execution_history.append(assistant_message)

            if not response.tool_calls:
                logger.info("✅ LLMToolExecutor：模型未请求工具调用，执行结束。")
                return execution_history

            logger.info(
                f"🛠️ LLMToolExecutor：模型请求并行调用 {len(response.tool_calls)} 个工具"
            )
            tool_results = await self._execute_tools_parallel_safely(
                response.tool_calls,
                tools,
            )
            execution_history.extend(tool_results)

        raise LLMException(
            f"超过最大工具调用循环次数 ({effective_config.max_cycles})。",
            code=LLMErrorCode.GENERATION_FAILED,
        )

    async def _execute_single_tool_safely(
        self, tool_call: Any, available_tools: dict[str, ToolExecutable]
    ) -> tuple[Any, ToolResult]:
        """安全地执行单个工具调用。"""
        tool_name = tool_call.function.name
        arguments = {}

        try:
            if tool_call.function.arguments:
                arguments = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError as e:
            error_result = ToolErrorResult(
                error_type=ToolErrorType.INVALID_ARGUMENTS,
                message=f"参数解析失败: {e}",
                is_retryable=False,
            )
            return tool_call, ToolResult(output=model_dump(error_result))

        try:
            executable = available_tools.get(tool_name)
            if not executable:
                raise LLMException(
                    f"Tool '{tool_name}' not found.",
                    code=LLMErrorCode.CONFIGURATION_ERROR,
                )

            @Retry.simple(
                stop_max_attempt=2, wait_fixed_seconds=1, return_on_failure=None
            )
            async def execute_with_retry():
                return await executable.execute(**arguments)

            execution_result = await execute_with_retry()
            if execution_result is None:
                raise LLMException("工具执行在多次重试后仍然失败。")

            return tool_call, execution_result
        except Exception as e:
            error_type = ToolErrorType.EXECUTION_ERROR
            is_retryable = _is_exception_retryable(e)
            if (
                isinstance(e, LLMException)
                and e.code == LLMErrorCode.CONFIGURATION_ERROR
            ):
                error_type = ToolErrorType.TOOL_NOT_FOUND
                is_retryable = False

            error_result = ToolErrorResult(
                error_type=error_type, message=str(e), is_retryable=is_retryable
            )
            return tool_call, ToolResult(output=model_dump(error_result))

    async def _execute_tools_parallel_safely(
        self,
        tool_calls: list[Any],
        available_tools: dict[str, ToolExecutable],
    ) -> list[LLMMessage]:
        """并行执行所有工具调用，并对每个调用的错误进行隔离。"""
        if not tool_calls:
            return []

        tasks = [
            self._execute_single_tool_safely(call, available_tools)
            for call in tool_calls
        ]
        results = await asyncio.gather(*tasks)

        tool_messages = [
            LLMMessage.tool_response(
                tool_call_id=original_call.id,
                function_name=original_call.function.name,
                result=result.output,
            )
            for original_call, result in results
        ]
        return tool_messages
