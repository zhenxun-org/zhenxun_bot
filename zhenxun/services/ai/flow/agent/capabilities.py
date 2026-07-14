import asyncio
from typing import Any, cast

from zhenxun.services.ai.capabilities import AbstractCapability, WrapRunHandler
from zhenxun.services.ai.capabilities.base import CapabilityOrdering
from zhenxun.services.ai.core.engine.structured_parser import (
    BaseOutputProcessor,
)
from zhenxun.services.ai.core.exceptions import (
    ControlFlowExit,
    ModelRetry,
    SchemaParseError,
    UpstreamServerException,
)
from zhenxun.services.ai.core.messages import TaskLifecycleEvent
from zhenxun.services.ai.core.models import ToolDefinition
from zhenxun.services.ai.core.options import BaseOutputDefinition, ToolOutput
from zhenxun.services.ai.guardrails import (
    BaseGuardrail,
    GuardrailSource,
    parse_guardrails,
)
from zhenxun.services.ai.run import AgentRunResult, AgentTask, RunContext
from zhenxun.services.ai.tools.core.tool import BaseTool
from zhenxun.services.ai.tools.models import StructuredSubmissionResult, ToolResult
from zhenxun.services.ai.utils.logger import log_agent as logger


class SubmitFinalResultExecutable(BaseTool):
    """
    动态生成的提交最终结果工具。
    用于将大模型的结构化输出拦截并终止 AgentExecutor 的循环。
    """

    def __init__(
        self,
        output_processor: BaseOutputProcessor,
        guardrails: list[BaseGuardrail] | None = None,
    ):
        """
        初始化提交最终结果的动态执行工具。

        参数:
            output_processor: 绑定的结构化输出处理器，用于验证提交的最终结果。
            guardrails: 用于在结果输出前进行安全合规拦截的护栏中间件列表，默认 None。
        """
        super().__init__(
            name="submit_final_result",
            description=(
                "当你完成所有必要的调查 and 思考后，"
                "必须且只能调用此工具来提交最终的结构化结果。"
                "提交后任务将立刻结束。"
            ),
        )
        self.output_processor = output_processor
        self.guardrails = guardrails or []

    async def get_definition(
        self, context: RunContext | None = None
    ) -> ToolDefinition | None:
        if getattr(self, "_dynamic_def", None) is not None:
            return self._dynamic_def
        schema = self.output_processor.get_json_schema()
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=schema,
        )

    async def execute(self, context: RunContext | None = None, **kwargs) -> ToolResult:
        parse_target = kwargs
        if isinstance(kwargs, dict):
            if "kwargs" in kwargs and len(kwargs) == 1:
                parse_target = kwargs["kwargs"]
            elif "result" in kwargs and len(kwargs) == 1:
                parse_target = kwargs["result"]

        try:
            json_str = __import__("json").dumps(parse_target, ensure_ascii=False)
            final_obj = await self.output_processor.validate_and_parse(
                json_str, context=context
            )
            from zhenxun.services.ai.guardrails import GuardrailPipeline

            pipeline = GuardrailPipeline(self.guardrails)
            json_str, final_obj = await pipeline.run_output_pipeline(
                json_str, final_obj, context
            )

            return StructuredSubmissionResult(
                output="结构化数据已成功提交", parsed_obj=final_obj
            )
        except ControlFlowExit as e:
            raise e
        except ModelRetry as e:
            raise e
        except Exception as e:
            error_msg = f"系统捕获到解析异常：\n{e}"
            raise SchemaParseError(error_msg)


class OutputValidationCapability(AbstractCapability):
    """输出拦截与校验能力组件 (支持纯文本及结构化护栏)"""

    def get_ordering(self) -> CapabilityOrdering | None:
        from zhenxun.services.ai.capabilities.builtin import (
            ReflexionCapability,
        )

        return CapabilityOrdering(wraps=[ReflexionCapability])

    def __init__(
        self,
        output_type: type[Any] | BaseOutputDefinition | None = None,
        guardrails: list[GuardrailSource] | None = None,
        raw_schema: dict[str, Any] | None = None,
    ):
        self.output_type = output_type
        self.raw_schema = raw_schema

        self.guardrails = parse_guardrails(guardrails)
        self.processor = None
        self.submit_tool = None

        if self.output_type is not None:
            if isinstance(self.output_type, BaseOutputDefinition):
                out_type = self.output_type.type_
                tool_name_override = (
                    self.output_type.name
                    if isinstance(self.output_type, ToolOutput)
                    else None
                )
            else:
                out_type = cast(type[Any], self.output_type)
                tool_name_override = None

            self.processor = BaseOutputProcessor(
                response_model=out_type,
            )
            self.submit_tool = SubmitFinalResultExecutable(
                self.processor, self.guardrails
            )
            if tool_name_override:
                self.submit_tool.name = tool_name_override
        elif self.raw_schema is not None:
            self.processor = BaseOutputProcessor(
                response_model=None,
                raw_schema=self.raw_schema,
            )
            self.submit_tool = SubmitFinalResultExecutable(
                self.processor, self.guardrails
            )

    async def get_system_prompts(self, context: RunContext) -> list[str]:
        """动态注入结构化要求提示词"""
        if self.submit_tool:
            return [
                "### ⚠️ [核心任务：结构化输出要求]\n"
                "当前任务处于严格的 **结构化输出模式**。\n"
                "当你完成所有调查和思考后，必须且只能调用 "
                f"`{self.submit_tool.name}` 工具来提交最终结果，"
                "禁止用纯文本直接作答。\n"
                "（📌 提示：最终需要返回的数据结构要求，"
                "请严格查阅并遵循 "
                f"`{self.submit_tool.name}` 工具的参数 Schema 定义，"
                "将其视为唯一的数据约束）"
            ]
        return []

    async def get_tools(self, context: RunContext) -> list[BaseTool]:
        """动态挂载提交最终结果的工具"""
        if self.submit_tool:
            return [self.submit_tool]
        return []

    async def wrap_model_request(self, context, llm_context, handler):
        """将 Processor 和 Guardrails 传给底层的 IvrCapability"""
        llm_context.request.extra["output_processor"] = self.processor
        llm_context.request.extra["guardrails"] = self.guardrails
        return await handler(llm_context)

    async def wrap_run(
        self, context: RunContext, handler: WrapRunHandler
    ) -> AgentRunResult[Any]:
        """运行结束后，校验是否成功提取了结构化数据"""
        result = await handler()
        if self.output_type is not None or self.raw_schema is not None:
            if result.structured_data is not None:
                result.output = result.structured_data
            else:
                tool_name = self.submit_tool.name if self.submit_tool else "unknown"
                logger.error(f"Agent 未能调用 {tool_name} 提交结构化数据。")
                raise UpstreamServerException(
                    "模型未能输出符合要求的结构化数据。",
                )
        return result


class TaskTrackingCapability(AbstractCapability):
    """数据契约任务状态追踪与事件遥测组件"""

    def __init__(self, task: AgentTask, agent_name: str):
        self.task = task
        self.agent_name = agent_name

    async def wrap_run(
        self, context: RunContext, handler: WrapRunHandler
    ) -> AgentRunResult[Any]:
        """任务生命周期追踪"""

        task_name = self.task.name or self.task.id[:8]
        logger.debug(f"📋 **开始任务**: `{task_name}` (由 {self.agent_name} 执行)")
        context.run.add_event(TaskLifecycleEvent(task_name=task_name, action="start"))
        try:
            result = await handler()
            logger.debug(f"✅ **任务完成**: `{task_name}`")
            context.run.add_event(
                TaskLifecycleEvent(task_name=task_name, action="complete")
            )
            return result
        except asyncio.CancelledError as e:
            logger.warning(f"⚠️ **任务被强制取消**: `{task_name}`")
            context.run.add_event(
                TaskLifecycleEvent(
                    task_name=task_name,
                    action="fail",
                    error_msg="任务执行被中止或取消",
                )
            )
            raise e
        except BaseException as error:
            event_error = (
                error if isinstance(error, Exception) else Exception(str(error))
            )
            logger.error(f"❌ **任务失败**: `{task_name}` - {event_error}")
            context.run.add_event(
                TaskLifecycleEvent(
                    task_name=task_name,
                    action="fail",
                    error_msg=str(event_error),
                )
            )
            raise error
