import types
from typing import Any, Generic, Union, cast, get_origin

import json_repair
from nonebot.compat import type_validate_json
from pydantic import BaseModel, Field, ValidationError, create_model

from zhenxun.services.ai.core.exceptions import (
    ControlFlowExit,
    ModelRetry,
    SchemaParseError,
)
from zhenxun.services.ai.core.models import ToolDefinition
from zhenxun.services.ai.run.models import OutputDataT
from zhenxun.services.ai.tools.core.tool import BaseTool
from zhenxun.services.ai.tools.models import StructuredSubmissionResult, ToolResult
from zhenxun.services.ai.utils.logger import log_core as logger
from zhenxun.utils.pydantic_compat import model_json_schema, model_validate

DEFAULT_IVR_TEMPLATE = (
    "### ❌ [输出内容或格式验证失败]\n"
    "你的上一次输出未能通过系统的校验与规则检查。请立即启动修正流程：\n\n"
    "**错误反馈报告：**\n"
    "> {error_msg}\n\n"
    "**修正要求：** 请结合反馈报告，"
    "仔细反思你的输出内容或格式，\n"
    "并重新生成正确的数据以满足所有的规则与规范。"
)


class BaseOutputProcessor(Generic[OutputDataT]):
    """
    统一的结构化输出处理器。
    负责管理 Schema 生成、Prompt 约束注入以及最终的
    JSON 反序列化和业务校验。
    """

    def __init__(
        self,
        response_model: type[Any] | None = None,
        error_template: str | None = None,
        raw_schema: dict[str, Any] | None = None,
    ):
        """
        初始化结构化输出处理器。

        参数:
            response_model: 期望的输出目标 Pydantic 模型类或 Union 类型，默认 None。
            error_template: 当 JSON 解析或模型验证失败时，反馈给大模型的 IVR 纠错提示词模板，默认 None。
            raw_schema: 显式传入的原始 JSON Schema 字典，
                如果不为 None 则跳过根据 response_model 生成，默认 None。
        """  # noqa: E501
        self.original_model = response_model
        self.error_template = error_template or DEFAULT_IVR_TEMPLATE
        self.raw_schema = raw_schema
        self.target_model = None
        self.is_union_wrapped = False

        if response_model is not None:
            self.target_model, self.is_union_wrapped = self._create_union_wrapper(
                response_model
            )

    @staticmethod
    def _create_union_wrapper(union_type: Any) -> tuple[type[BaseModel], bool]:
        """[私有方法] 如果是 Union 类型，
        动态构建带 kind 区分字段的模型"""
        origin = get_origin(union_type)
        union_types = [Union]
        if hasattr(types, "UnionType"):
            union_types.append(types.UnionType)

        if origin not in union_types:
            return union_type, False

        UnionWrapper = create_model(
            "UnionResponseWrapper",
            result=(
                union_type,
                Field(..., description="根据你的决策，输出对应的结构化数据"),
            ),
        )
        return UnionWrapper, True

    def get_json_schema(self) -> dict[str, Any]:
        """提取目标模型的 JSON Schema"""
        if self.raw_schema is not None:
            return self.raw_schema
        if self.target_model is None:
            raise ValueError("未提供 response_model 或 raw_schema")
        try:
            return model_json_schema(self.target_model)
        except AttributeError:
            return self.target_model.schema()

    def _parse_and_validate(self, text: str) -> Any:
        """[私有方法] 执行带有容错修复的 JSON 解析与模型验证"""
        if self.raw_schema is not None:
            import json

            try:
                return json.loads(text)
            except Exception:
                try:
                    return json_repair.loads(text, skip_json_loads=True)
                except Exception as repair_error:
                    raise SchemaParseError(f"JSON格式损坏: {repair_error}")
        if self.target_model is None:
            raise SchemaParseError("未提供 response_model 或 raw_schema")
        try:
            return type_validate_json(self.target_model, text)
        except (ValidationError, ValueError) as e:
            try:
                logger.warning(f"标准JSON解析失败，尝试使用json_repair修复: {e}")
                repaired_obj = json_repair.loads(text, skip_json_loads=True)
                return model_validate(self.target_model, repaired_obj)
            except Exception as repair_error:
                logger.debug(
                    "JSON修复或模型校验失败，将交由大模型进行反思自愈: "
                    f"{type(repair_error).__name__}"
                )
                if isinstance(repair_error, ValidationError):
                    error_msgs = []
                    for err in repair_error.errors():
                        loc = ".".join(str(x) for x in err["loc"]) or "root"
                        msg = err.get("msg", "")
                        error_msgs.append(f"字段 `{loc}`: {msg}")
                    clean_error_str = "\n".join(error_msgs)
                    raise SchemaParseError(
                        f"数据内容未通过规则校验:\n{clean_error_str}"
                    )

                raise SchemaParseError(
                    f"JSON格式损坏或字段不匹配，未能通过Schema验证: {repair_error}"
                )
        except Exception as e:
            logger.error(f"解析LLM结构化输出时发生未知错误: {e}", e=e)
            raise SchemaParseError(f"解析LLM的JSON输出时失败: {e}")

    async def validate_and_parse(self, text: str, context: Any = None) -> OutputDataT:
        """执行 JSON 解析与回调验证"""
        try:
            parsed_obj = self._parse_and_validate(text)

            current_obj = parsed_obj

            if getattr(self, "is_union_wrapped", False):
                current_obj = getattr(current_obj, "result")

            final_obj = cast(OutputDataT, current_obj)

            return final_obj
        except Exception as e:
            raise e


class SubmitFinalResultExecutable(BaseTool):
    """
    动态生成的提交最终结果工具。
    用于将大模型的结构化输出拦截并终止 AgentExecutor 的循环。
    """

    def __init__(
        self,
        output_processor: BaseOutputProcessor,
        guardrails: list[Any] | None = None,
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

    async def get_definition(self, context: Any | None = None) -> ToolDefinition | None:
        if getattr(self, "_dynamic_def", None) is not None:
            return self._dynamic_def
        schema = self.output_processor.get_json_schema()
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=schema,
        )

    async def execute(self, context: Any | None = None, **kwargs) -> ToolResult:
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
