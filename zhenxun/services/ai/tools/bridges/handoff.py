from typing import Any

from pydantic import BaseModel, Field, create_model

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
    ):
        """
        初始化移交工具，为模型赋予转移对话控制权到指定实体的能力。

        参数:
            target_name: 被转移的目标接收者（Agent 或负责人）的唯一标识名称。
            target_description: 目标接收者的职责或专长说明，供模型决策是否移交。
            input_schema: 自定义移交数据结构，指定转移时所需携带的结构化参数。
        """
        super().__init__(
            name=f"transfer_to_{target_name}",
            description=(
                f"将对话控制权移交给 {target_name}。专长/职责：{target_description}"
            ),
        )
        self.target_name = target_name

        actual_schema = None
        if input_schema:
            from zhenxun.services.ai.core.options import BaseOutputDefinition

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
            if counts[self.target_name] > 3:
                return ToolResult(
                    output=(
                        f"❌ 系统拦截：检测到严重的踢皮球现象！\n"
                        f"你所在的团队已经连续 {counts[self.target_name]} 次将任务"
                        f"移交给 {self.target_name}，\n"
                        "但问题仍未解决。请立刻改变策略，"
                        "由你亲自处理或得出最终结论，严禁再次移交！"
                    )
                ).as_error()

        from zhenxun.services.ai.core.messages import HandoffEvent

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
