from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class StepType(str, Enum):
    FUNCTION = "Function"
    STEP = "Step"
    STEPS = "Steps"
    LOOP = "Loop"
    PARALLEL = "Parallel"
    CONDITION = "Condition"
    ROUTER = "Router"


class StepInput(BaseModel):
    """传递给每个 Step 的标准输入结构"""

    input: Any = Field(default=None)
    """继承自 Workflow 的初始输入"""

    previous_step_content: Any = Field(default=None)
    """上一个执行步骤产生的直接输出内容"""

    additional_data: dict[str, Any] = Field(default_factory=dict)
    """在生命周期中穿透传递的附加数据"""


class StepOutput(BaseModel):
    """每个 Step 的标准输出结构"""

    step_name: str | None = None
    """步骤的名称"""
    step_id: str | None = None
    """步骤的唯一标识"""
    step_type: StepType | None = None
    """步骤的节点枚举类型"""
    executor_type: str | None = None
    """底层执行器的类型标识"""
    executor_name: str | None = None
    """底层执行器的具体名称"""

    content: Any = None
    """该步骤产生的直接输出内容"""
    success: bool = True
    """标记该步骤是否执行成功"""
    error: str | None = None
    """执行失败时的异常详情"""
    stop: bool = False
    """标记是否触发了终止信号，以阻断后续流程的执行"""

    steps: list["StepOutput"] | None = None
    """嵌套步骤的输出结果集合（如复合节点 Loop、Parallel 的内部产出）"""


class WorkflowRunResult(BaseModel):
    """工作流运行结果（包含断点快照状态）"""

    workflow_id: str
    """工作流实例运行的唯一标识"""
    workflow_name: str
    """工作流的名称"""
    status: str
    """流水线的最终运行状态 (completed, error 等)"""
    original_input: Any
    """最初传入根节点的原始输入"""
    state: dict[str, Any]
    """工作流生命周期中的全局共享上下文状态字典"""
    step_outputs: dict[str, StepOutput]
    """平铺展开的所有经历过的节点步骤输出字典"""
    last_step_content: Any
    """最后一个成功执行的步骤所产出的内容"""
    final_output: StepOutput | None = None
    """工作流根节点最终包装的完整产出对象"""
