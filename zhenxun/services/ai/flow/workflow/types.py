from abc import ABC, abstractmethod
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
    is_paused: bool = False
    """标记该步骤是否因等待外力交互 (HITL) 而处于挂起状态"""
    pause_reason: str | None = None
    """导致步骤挂起的原因描述"""

    steps: list["StepOutput"] | None = None
    """嵌套步骤的输出结果集合（如复合节点 Loop、Parallel 的内部产出）"""


class WorkflowRunResult(BaseModel):
    """工作流运行结果（包含断点快照状态）"""

    workflow_id: str
    """工作流实例运行的唯一标识"""
    workflow_name: str
    """工作流的名称"""
    status: str
    """流水线的最终运行状态 (completed, paused, error 等)"""
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
    paused_step_name: str | None = None
    """若流水线处于挂起态，记录是哪个步骤引发了挂起"""


class PolicyAction(str, Enum):
    RETRY = "retry"
    CONTINUE = "continue"
    ABORT = "abort"
    FALLBACK = "fallback"


class PolicyResult(BaseModel):
    """错误策略执行结果"""

    action: PolicyAction
    """采取的具体恢复策略动作"""
    delay: float = 0.0
    """执行延迟或重试前需要等待的缓冲秒数"""
    new_input: StepInput | None = None
    """用于动态纠错自愈时替换传入的新参数结构"""
    fallback_node: Any | None = None
    """策略裁定降级时所指定的备用工作流节点"""
    healer_agent_name: str | None = None
    """执行了高级自愈的大模型或修复者名称"""


class BaseFailurePolicy(ABC):
    """错误处理策略抽象基类"""

    @abstractmethod
    async def handle_failure(
        self, node: Any, exception: BaseException, step_input: StepInput, context: Any
    ) -> PolicyResult:
        pass


class AbortPolicy(BaseFailurePolicy):
    """直接中断策略"""

    async def handle_failure(
        self, node: Any, exception: BaseException, step_input: StepInput, context: Any
    ) -> PolicyResult:
        return PolicyResult(action=PolicyAction.ABORT)


class SkipPolicy(BaseFailurePolicy):
    """跳过并继续策略"""

    async def handle_failure(
        self, node: Any, exception: BaseException, step_input: StepInput, context: Any
    ) -> PolicyResult:
        return PolicyResult(action=PolicyAction.CONTINUE)


class RetryPolicy(BaseFailurePolicy):
    """退避重试策略"""

    def __init__(self, max_retries: int = 3, delay: float = 1.0):
        """
        初始化退避重试策略。

        参数:
            max_retries: 最大允许重试的次数限制，默认 3。
            delay: 每次重试前需要等待和睡眠的秒数，默认 1.0。
        """
        self.max_retries = max_retries
        self.delay = delay

    async def handle_failure(
        self, node: Any, exception: BaseException, step_input: StepInput, context: Any
    ) -> PolicyResult:
        counts = context.state.setdefault("__retry_counts__", {})
        key = f"{node.name}_{id(self)}"
        counts[key] = counts.get(key, 0) + 1

        if counts[key] <= self.max_retries:
            return PolicyResult(action=PolicyAction.RETRY, delay=self.delay)
        return PolicyResult(action=PolicyAction.ABORT)


class FallbackPolicy(BaseFailurePolicy):
    """降级路由策略"""

    def __init__(self, fallback_node: Any):
        """
        初始化降级路由策略。

        参数:
            fallback_node: 当主节点发生致命故障时，直接转入执行的备用降级节点。
        """
        self.fallback_node = fallback_node

    async def handle_failure(
        self, node: Any, exception: BaseException, step_input: StepInput, context: Any
    ) -> PolicyResult:
        return PolicyResult(
            action=PolicyAction.FALLBACK, fallback_node=self.fallback_node
        )


class SelfHealingPolicy(BaseFailurePolicy):
    """大模型高级自愈策略"""

    def __init__(self, healer_model: str, max_retries: int = 2):
        """
        初始化大模型高级自愈策略。

        参数:
            healer_model: 用于分析错误原因并智能修复入参的大模型名称。
            max_retries: 最大尝试自愈修复的次数，默认 2。
        """
        self.healer_model = healer_model
        self.max_retries = max_retries

    async def handle_failure(
        self, node: Any, exception: BaseException, step_input: StepInput, context: Any
    ) -> PolicyResult:
        counts = context.state.setdefault("__heal_counts__", {})
        key = f"{node.name}_{id(self)}"
        counts[key] = counts.get(key, 0) + 1

        if counts[key] > self.max_retries:
            from zhenxun.services.log import logger

            logger.warning(f"节点 '{node.name}' 自愈次数达上限，宣告失败。")
            return PolicyResult(action=PolicyAction.ABORT)

        import copy

        from zhenxun.services.ai.llm.api import generate_structured
        from zhenxun.services.log import logger

        class HealedInput(BaseModel):
            """自愈后输入结构"""

            fixed_input: str = Field(
                description="""修复后的输入参数 必须是完全合法的数据结构"""
            )

        prompt = f"""# Self-Healing Task

请修复节点 `{node.name}` 的参数错误。

## Original Input
{step_input.input}

## Exception
{exception}

## Requirements
- 分析错误原因
- 将输入修复为可被程序正确解析的格式
- 只输出修复后的结果，不要输出额外解释
"""

        try:
            logger.info(f"🩹 触发 AI 自愈分析 (节点: {node.name})...")
            res = await generate_structured(
                prompt, response_model=HealedInput, model=self.healer_model
            )

            new_input = copy.copy(step_input)
            new_input.input = res.fixed_input

            return PolicyResult(
                action=PolicyAction.RETRY,
                new_input=new_input,
                healer_agent_name=self.healer_model,
            )

        except Exception as e:
            logger.error(f"自愈过程发生大模型调用异常: {e}")
            return PolicyResult(action=PolicyAction.ABORT)
