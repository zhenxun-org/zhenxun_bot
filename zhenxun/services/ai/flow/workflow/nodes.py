from abc import ABC, abstractmethod
import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
import copy
from typing import cast

from zhenxun.services.ai.core.messages import PromptInput
from zhenxun.services.ai.core.stream_events import AgentStreamEvent
from zhenxun.services.ai.flow.core.base import BaseRunnable
from zhenxun.services.ai.run import RunContext
from zhenxun.services.ai.run.di import DependencyInjector
from zhenxun.services.ai.run.models import AgentRunEnd, RunIntent
from zhenxun.services.ai.utils.logger import log_flow as logger

from .base import BaseNode, StreamCapturer
from .policies import BaseFailurePolicy
from .types import (
    StepInput,
    StepOutput,
    StepType,
)

NodeSource = BaseNode | BaseRunnable | Callable
"""工作流节点来源，可以是图元、可执行引擎或原生函数"""


class Step(BaseNode):
    """
    工作流中的最小执行单元门面
    对外部隐藏了 AgentNode 和 FunctionNode 的具体实现。
    当实例化 Step 时，底层会自动根据 executor 的类型返回专属的节点对象。
    """

    def __new__(cls, *args, **kwargs):
        if cls is Step:
            executor = kwargs.get("executor")
            if executor is None and len(args) > 1:
                executor = args[1]

            from zhenxun.services.ai.flow.core.base import BaseRunnable

            if isinstance(executor, BaseRunnable):
                return object.__new__(RunnableNode)
            elif callable(executor):
                return object.__new__(FunctionNode)
        return object.__new__(cls)

    def __init__(
        self,
        name: str | None = None,
        executor: NodeSource | None = None,
        prompt: PromptInput | None = None,
        failure_policy: BaseFailurePolicy | None = None,
    ):
        """
        初始化工作流单元步骤（门面）。

        参数:
            name: 步骤的名称，为空则自动取执行器的名称，默认 None。
            executor: 该步骤要运行的核心执行器（支持 RunnableNode 或 Callable 依赖注入）。
            prompt: 该步骤的初始输入或提示词定义，默认 None。
            failure_policy: 该节点执行失败时的错误处理策略，默认使用中断策略。
        """  # noqa: E501
        actual_name = name or getattr(
            executor, "name", getattr(executor, "__name__", "unnamed_step")
        )
        super().__init__(
            name=actual_name,
            failure_policy=failure_policy,
        )
        self.executor = executor
        self.prompt = prompt

    @property
    def node_type(self) -> StepType:
        return StepType.STEP

    async def run_stream(
        self, step_input: StepInput, context: RunContext
    ) -> AsyncIterator[AgentStreamEvent | StepOutput]:
        if False:
            yield None
        raise NotImplementedError("这是一个外观门面，实际的执行发生在子类中。")


class RunnableNode(Step):
    """专门处理 Agent/Team/Workflow 等 BaseRunnable 状态机执行的私有节点"""

    async def run_stream(
        self, step_input: StepInput, context: RunContext
    ) -> AsyncIterator[AgentStreamEvent | StepOutput]:
        executor = cast(BaseRunnable, self.executor)

        base_prompt = self.prompt if self.prompt is not None else step_input.input
        node_intent = RunIntent.from_input(base_prompt)

        prev_content = step_input.previous_step_content
        prompt_data = base_prompt

        if prev_content:
            if node_intent.task_obj:
                task_clone = copy.copy(node_intent.task_obj)
                task_clone.description = (
                    f"### 🔙 [上游节点执行输出]\n{prev_content}\n\n"
                    f"### 🎯 [当前需执行的任务]\n{task_clone.description}"
                )
                prompt_data = task_clone
            else:
                prompt_data = (
                    f"[上游节点执行输出]:\n{prev_content}\n\n"
                    f"[当前需执行的任务]:\n{node_intent.text}"
                )

        final_result = None
        sandbox_context = context.clone_for_member(self.name)

        async with executor.run_stream(
            prompt=prompt_data, context=sandbox_context
        ) as stream_result:
            async for event in stream_result.stream_events():
                if isinstance(event, AgentRunEnd):
                    final_result = event.result
                yield event

        context.state.update(sandbox_context.state)
        yield StepOutput(
            content=final_result.output if final_result else "无返回",
            success=True,
        )


class FunctionNode(Step):
    """专门处理 Python Callable 依赖注入与执行的私有节点"""

    async def run_stream(
        self, step_input: StepInput, context: RunContext
    ) -> AsyncIterator[AgentStreamEvent | StepOutput]:
        context.run.user_input = str(step_input.input) if step_input.input else ""

        executor = cast(Callable, self.executor)
        res = await DependencyInjector.invoke(
            executor, {"step_input": step_input}, context
        )

        if isinstance(res, StepOutput):
            yield res
        else:
            yield StepOutput(content=res, success=True)


class Steps(BaseNode):
    """串行执行的工作流容器。按照列表顺序依次执行。"""

    def __init__(self, steps: Sequence[NodeSource], name: str = "StepsGroup"):
        """
        初始化串行工作流容器。

        参数:
            steps: 依次串行执行的节点/执行器列表。
            name: 该串行容器 of 名称，默认 "StepsGroup"。
        """
        super().__init__(name=name)
        self.steps = [NodeFactory.build(step) for step in steps]

    @property
    def node_type(self) -> StepType:
        return StepType.STEPS

    async def run_stream(
        self, step_input: StepInput, context: RunContext
    ) -> AsyncIterator[AgentStreamEvent | StepOutput]:
        current_input = StepInput(
            input=step_input.input,
            intent=step_input.intent,
            previous_step_content=step_input.previous_step_content,
            additional_data=step_input.additional_data.copy(),
        )

        all_outputs: list[StepOutput] = []
        for step_obj in self.steps:
            capturer = StreamCapturer(step_obj.aexecute_stream(current_input, context))
            async for event in capturer:
                yield event
            step_out = capturer.output

            if step_out:
                all_outputs.append(step_out)
                current_input.previous_step_content = step_out.content
                if step_out.stop:
                    break

        yield StepOutput(
            content=all_outputs[-1].content if all_outputs else "No steps executed",
            success=all(o.success for o in all_outputs),
            steps=all_outputs,
        )


class BranchingNode(BaseNode, ABC):
    """
    处理基于条件或路由的单分支复合节点基类
    """

    @abstractmethod
    async def select_branch(
        self, step_input: StepInput, context: RunContext
    ) -> tuple[list[BaseNode], str, str]:
        """
        子类实现此方法进行路由决策。
        返回: (目标节点列表, 分支标识名, 未命中有效分支时的兜底提示信息)
        """
        pass

    async def run_stream(
        self, step_input: StepInput, context: RunContext
    ) -> AsyncIterator[AgentStreamEvent | StepOutput]:
        context.run.user_input = str(step_input.input) if step_input.input else ""
        target_steps, branch_name, fallback_msg = await self.select_branch(
            step_input, context
        )

        if not target_steps:
            yield StepOutput(content=fallback_msg, success=True)
            return

        steps_container = Steps(steps=target_steps, name=f"{self.name}_{branch_name}")
        capturer = StreamCapturer(steps_container.aexecute_stream(step_input, context))
        async for event in capturer:
            yield event
        if capturer.output:
            yield capturer.output


class Condition(BranchingNode):
    """根据条件函数的返回结果，决定走向 steps 还是 else_steps"""

    def __init__(
        self,
        evaluator: bool | Callable[..., bool | Awaitable[bool]],
        steps: Sequence[NodeSource],
        else_steps: Sequence[NodeSource] | None = None,
        name: str = "ConditionGroup",
    ):
        """
        初始化条件分支节点。

        参数:
            evaluator: 用于评估条件真假的布尔值、表达式或可调用函数。
            steps: 当 evaluator 求值为真时，将执行的步骤序列。
            else_steps: 当 evaluator 求值为假时，将执行的备用步骤序列，默认 None。
            name: 该条件分支容器的名称，默认 "ConditionGroup"。
        """
        super().__init__(name=name)
        self.evaluator = evaluator
        self.steps = [NodeFactory.build(step) for step in steps]
        self.else_steps = [NodeFactory.build(step) for step in (else_steps or [])]

    @property
    def node_type(self) -> StepType:
        return StepType.CONDITION

    async def select_branch(
        self, step_input: StepInput, context: RunContext
    ) -> tuple[list[BaseNode], str, str]:
        if callable(self.evaluator):
            condition_result = await DependencyInjector.invoke(
                self.evaluator, {"step_input": step_input}, context
            )
        else:
            condition_result = bool(self.evaluator)

        target_steps = self.steps if condition_result else self.else_steps
        branch_name = "if_branch" if condition_result else "else_branch"
        fallback_msg = f"条件求值为 {condition_result}，无对应步骤需执行。"

        return target_steps, branch_name, fallback_msg


class Router(BranchingNode):
    """根据选择器函数的返回值(名称)，从候选项中挑选步骤执行"""

    def __init__(
        self,
        choices: Sequence[NodeSource],
        selector: str
        | list[str]
        | Callable[..., str | list[str] | Awaitable[str | list[str]]],
        name: str = "RouterGroup",
    ):
        """
        初始化选择路由器节点。

        参数:
            choices: 包含所有候选执行路由分支的步骤序列。
            selector: 用于决定路由流向的匹配值、或者是返回分支名称的动态选择器函数。
            name: 该路由器容器的名称，默认 "RouterGroup"。
        """
        super().__init__(name=name)
        self.choices = [NodeFactory.build(c) for c in choices]
        self.selector = selector
        self._choice_map = {}
        for c in self.choices:
            if c.name:
                self._choice_map[c.name] = c

    @property
    def node_type(self) -> StepType:
        return StepType.ROUTER

    async def select_branch(
        self, step_input: StepInput, context: RunContext
    ) -> tuple[list[BaseNode], str, str]:
        if callable(self.selector):
            selected = await DependencyInjector.invoke(
                self.selector, {"step_input": step_input}, context
            )
        else:
            selected = self.selector

        if not isinstance(selected, list):
            selected = [selected]

        target_steps = []
        for s in selected:
            if isinstance(s, str):
                if s in self._choice_map:
                    target_steps.append(self._choice_map[s])
                else:
                    logger.warning(f"Router '{self.name}' 选择了未知的步骤: '{s}'")
            else:
                target_steps.append(NodeFactory.build(s))

        return target_steps, "routed_steps", "没有命中任何有效路由分支。"


class Loop(BaseNode):
    """循环执行工作流，直至达到最大次数或满足结束条件"""

    def __init__(
        self,
        steps: Sequence[NodeSource],
        max_iterations: int = 3,
        end_condition: bool | Callable[..., bool | Awaitable[bool]] | None = None,
        name: str = "LoopGroup",
    ):
        """
        初始化循环控制器节点。

        参数:
            steps: 每次循环中需要顺序运行的步骤序列。
            max_iterations: 最大允许循环执行的迭代次数上限，默认 3。
            end_condition: 决定是否可以提前终止循环的条件布尔值或可调用判定函数，
                默认 None。
            name: 该循环容器的名称，默认 "LoopGroup"。
        """
        super().__init__(name=name)
        self.steps = [NodeFactory.build(step) for step in steps]
        self.max_iterations = max_iterations
        self.end_condition = end_condition

    @property
    def node_type(self) -> StepType:
        return StepType.LOOP

    async def run_stream(
        self, step_input: StepInput, context: RunContext
    ) -> AsyncIterator[AgentStreamEvent | StepOutput]:
        logger.debug(
            f"  🔁 开始循环: [Loop] `{self.name}` (最大 {self.max_iterations} 次)"
        )

        iteration = 0
        all_results: list[StepOutput] = []
        current_input = StepInput(
            input=step_input.input,
            intent=step_input.intent,
            previous_step_content=step_input.previous_step_content,
            additional_data=step_input.additional_data.copy(),
        )

        while iteration < self.max_iterations:
            logger.debug(f"  ┃  🔄 第 {iteration + 1} 次迭代...")

            steps_container = Steps(
                steps=self.steps, name=f"{self.name}_iter_{iteration + 1}"
            )
            capturer = StreamCapturer(
                steps_container.aexecute_stream(current_input, context)
            )
            async for event in capturer:
                yield event
            iter_output = capturer.output

            should_stop = False
            if iter_output:
                all_results.append(iter_output)
                if self.end_condition:
                    if callable(self.end_condition):
                        should_stop = await DependencyInjector.invoke(
                            self.end_condition,
                            {"iteration_results": iter_output.steps or [iter_output]},
                            context,
                        )
                    else:
                        should_stop = bool(self.end_condition)

                iteration += 1
                if should_stop or iter_output.stop:
                    break
                current_input.previous_step_content = iter_output.content
            else:
                iteration += 1
                break

        yield StepOutput(
            content=all_results[-1].content if all_results else "No iterations run",
            success=all(o.success for o in all_results),
            steps=all_results,
        )

        logger.debug(f"  ✅ 循环结束: [Loop] `{self.name}` (共执行 {iteration} 次)")


class Parallel(BaseNode):
    """并发执行的工作流容器。无序地并发执行内部所有步骤，并最终聚合成一个输出。"""

    def __init__(self, *args: NodeSource | str, name: str | None = None):
        """
        初始化并发工作流容器。

        参数:
            *args: 并发执行的任务节点/执行器，支持混入字符串覆盖作为 Parallel 的名字。
            name: 该并发容器的名称，默认 "ParallelGroup"。
        """
        super().__init__(name=name or "ParallelGroup")
        self.steps = []
        for arg in args:
            if isinstance(arg, str):
                self.name = arg
            else:
                self.steps.append(NodeFactory.build(arg))

    @property
    def node_type(self) -> StepType:
        return StepType.PARALLEL

    async def run_stream(
        self, step_input: StepInput, context: RunContext
    ) -> AsyncIterator[AgentStreamEvent | StepOutput]:
        logger.debug(f"  🔀 [并发] `{self.name}` 开启了 {len(self.steps)} 个并发任务")

        queue = asyncio.Queue()
        bg_tasks = []

        async def worker(idx: int, s_obj: BaseNode, c_ctx: RunContext):
            try:
                async for evt in s_obj.aexecute_stream(step_input, c_ctx):
                    await queue.put(("event", evt))
            except asyncio.CancelledError:
                pass
            except Exception as e:
                await queue.put(("error", e, getattr(s_obj, "name", f"step_{idx}")))
            finally:
                await queue.put(
                    ("done", idx, c_ctx.state, getattr(c_ctx, "upstream_results", {}))
                )

        for i, step_obj in enumerate(self.steps):
            child_context = context.clone_for_execution()
            task = asyncio.create_task(worker(i, step_obj, child_context))
            bg_tasks.append(task)

        completed = 0
        all_outputs: list[StepOutput] = []
        aggregated_content_parts = [f"## 并发执行结果汇总 [{self.name}]\n"]
        has_any_failure = False
        early_stopped = False

        while completed < len(self.steps):
            msg_type, *data = await queue.get()
            if msg_type == "event":
                if isinstance(data[0], StepOutput):
                    out = cast(StepOutput, data[0])
                    all_outputs.append(out)
                    if not out.success:
                        has_any_failure = True
                    status_icon = "✅ 成功" if out.success else "❌ 失败"
                    aggregated_content_parts.append(
                        f"### {status_icon}: {out.step_name}\n{out.content}"
                    )
                    if out.stop and not early_stopped:
                        early_stopped = True
                        logger.info(
                            f"并行分支 '{out.step_name}' 请求终止，"
                            "正在取消其他并发任务..."
                        )
                        for t in bg_tasks:
                            if not t.done():
                                t.cancel()
                else:
                    yield data[0]
            elif msg_type == "error":
                err, s_name = data
                logger.error(f"并发步骤 '{s_name}' 执行崩溃: {err}")
                out = StepOutput(
                    step_name=s_name,
                    step_type=StepType.STEP,
                    content=f"执行崩溃: {err}",
                    success=False,
                    error=str(err),
                )
                all_outputs.append(out)
                has_any_failure = True
                aggregated_content_parts.append(f"### ❌ 失败: {s_name}\n{err}")
            elif msg_type == "done":
                _, child_state, child_upstream_results = data
                context.state.update(child_state)
                context.upstream_results.update(child_upstream_results)
                completed += 1

        yield StepOutput(
            content="\n\n".join(aggregated_content_parts),
            success=not has_any_failure,
            steps=all_outputs,
            stop=any(getattr(o, "stop", False) for o in all_outputs),
        )

        logger.debug(f"  ✅ [并发] `{self.name}` 执行完毕")


class NodeFactory:
    """统一节点装配工厂"""

    @classmethod
    def _create_step(
        cls,
        executor: NodeSource,
        name: str | None = None,
        failure_policy: BaseFailurePolicy | None = None,
    ) -> BaseNode:
        """底层物理实例化分发"""
        kwargs = {
            "name": name,
            "executor": executor,
            "failure_policy": failure_policy,
        }
        if isinstance(executor, BaseRunnable):
            return RunnableNode(**kwargs)
        elif callable(executor):
            return FunctionNode(**kwargs)
        raise ValueError(f"执行器类型 {type(executor)} 无法转换为叶子节点(Step)。")

    @staticmethod
    def build(item: NodeSource, name: str | None = None) -> BaseNode:
        if isinstance(item, BaseNode):
            if name and item.name in (
                "unnamed_step",
                "StepsGroup",
                "ParallelGroup",
                "ConditionGroup",
                "RouterGroup",
                "LoopGroup",
            ):
                item.name = name
            return item

        if isinstance(item, BaseRunnable) or callable(item):
            return NodeFactory._create_step(executor=item, name=name)

        raise ValueError(
            f"无法将类型 {type(item)} 装配为工作流节点。"
            "支持的类型：BaseRunnable, Callable 或 BaseNode。"
        )
