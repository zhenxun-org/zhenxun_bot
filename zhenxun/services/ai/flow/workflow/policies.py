import copy
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from zhenxun.services.ai.llm.api import generate_structured
from zhenxun.services.ai.utils.logger import log_flow as logger

from .types import StepInput


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


class BaseFailurePolicy:
    """错误处理策略抽象基类"""

    async def handle_failure(
        self, node: Any, exception: BaseException, step_input: StepInput, context: Any
    ) -> PolicyResult:
        """
        处理节点执行失败的策略入口方法。

        参数:
            node: 发生异常的目标工作流节点。
            exception: 捕获到的具体异常实例。
            step_input: 节点执行时的原始输入数据。
            context: 当前工作流运行上下文。

        返回:
            PolicyResult: 包含错误恢复动作、延迟时间以及备用参数等决策信息的策略结果对象。
        """  # noqa: E501
        raise NotImplementedError


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
            logger.warning(f"节点 '{node.name}' 自愈次数达上限，宣告失败。")
            return PolicyResult(action=PolicyAction.ABORT)

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
