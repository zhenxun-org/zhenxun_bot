from typing import Any

from pydantic import BaseModel, Field, create_model

from zhenxun.services.ai.core.messages import HandoffEvent
from zhenxun.services.ai.core.options import BaseOutputDefinition
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.tools.core.tool import BaseTool
from zhenxun.services.ai.tools.models import HandoffResult, ToolResult


class HandoffTool(BaseTool):
    """
    触发控制权移交 (Handoff) 信号的底层工具。
    当大模型调用此工具时，当前 Agent 执行流将被立即熔断并向外抛出 ToolHandoff 信号。
    """

    def __init__(
        self,
        target_name: str,
        target_description: str,
        input_schema: type[BaseModel] | Any | None = None,
        max_handoffs: int = 3,
    ):
        """
        初始化移交工具，为模型赋予转移对话控制权到指定实体的能力。

        参数:
            target_name: 被转移的目标接收者（Agent 或负责人）的唯一标识名称。
            target_description: 目标接收者的职责或专长说明，供模型决策是否移交。
            input_schema: 自定义移交数据结构，指定转移时所需携带的结构化参数。
            max_handoffs: 允许在同一个会话中向同一个实体发起移交的最大次数，防止无限踢皮球，默认 3。
        """  # noqa: E501
        super().__init__(
            name=f"transfer_to_{target_name}",
            description=(
                f"将对话控制权移交给 {target_name}。专长/职责：{target_description}"
            ),
        )
        self.target_name = target_name
        self.max_handoffs = max_handoffs

        actual_schema = None
        if input_schema:
            if isinstance(input_schema, BaseOutputDefinition):
                actual_schema = input_schema.type_
            else:
                actual_schema = input_schema

        if actual_schema:
            fields: dict[str, Any] = {
                "reason": (str, Field(..., description="移交的原因或简要状态说明")),
            }
            schema_fields = getattr(
                actual_schema, "model_fields", getattr(actual_schema, "__fields__", {})
            )
            for k, v in schema_fields.items():
                fields[k] = (v.annotation, v)
            self.args_schema = create_model(
                f"DynamicHandoffArgs_{target_name}", **fields
            )
        else:

            class DefaultHandoffArgs(BaseModel):
                reason: str = Field(..., description="移交的原因或简要状态说明")
                context_data: Any = Field(
                    default="",
                    description="你需要传递给下一个负责人的所有核心数据",
                )

            self.args_schema = DefaultHandoffArgs

    async def execute(
        self, context: RunContext | None = None, **kwargs: Any
    ) -> ToolResult:
        reason = kwargs.pop("reason", "")

        if "context_data" in kwargs and len(kwargs) == 1:
            context_data = kwargs["context_data"]
        else:
            context_data = kwargs

        if context:
            counts = context.session.shared_state.setdefault("__handoff_counts__", {})
            counts[self.target_name] = counts.get(self.target_name, 0) + 1
            if counts[self.target_name] > self.max_handoffs:
                return ToolResult(
                    output=(
                        f"❌ 系统拦截：移交次数已达上限。\n"
                        f"你所在的团队已经向 {self.target_name} 尝试移交了 "
                        f"{counts[self.target_name]} 次，"
                        f"超出了最大允许次数 ({self.max_handoffs})。\n"
                        "为防止任务陷入停滞，请立即改变策略，"
                        "由你亲自处理当前任务或得出最终结论，严禁再次移交！"
                    )
                ).as_error()

        if context:
            context.run.add_event(
                HandoffEvent(
                    target=self.target_name,
                    reason=reason,
                    context_data=context_data,
                )
            )

        return HandoffResult(
            target=self.target_name, reason=reason, context_data=context_data
        )
