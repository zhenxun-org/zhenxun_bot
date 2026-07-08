from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import Enum
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field

from zhenxun.services.ai.core.messages import AgentMessage
from zhenxun.services.ai.core.options import BaseOutputDefinition
from zhenxun.services.ai.flow.base import BaseRunnable, BaseRuntimeConfig
from zhenxun.services.ai.run import AgentTask


class TeamRuntimeConfig(BaseRuntimeConfig):
    """Team 专属的运行时配置"""

    leader_enable_hitl: bool = Field(default=False)
    """是否允许团队的隐式 Leader / Router 发起人机求助 (Human-in-the-Loop)"""


class RouteDecision(BaseModel):
    """大模型动态路由决策的数据契约"""

    target_name: str
    """选定的最合适的团队成员名称"""
    reason: str = ""
    """选择该成员的详细理由"""
    context_data: Any = ""
    """传递的上下文载荷"""


class Transition(BaseModel):
    """
    声明式移交契约。
    用于定义 Team 模式下，智能体之间转移控制权的条件和目标。
    """

    target: str
    """目标智能体的名称"""
    description: str = ""
    """自然语言描述的移交条件（提供给大模型 LLMRouter 思考时使用）"""
    input_schema: type[BaseModel] | BaseOutputDefinition | None = None
    """(可选) 强类型的输入约束。如果设置，
    LLMRouter 决定移交时必须且只能生成符合该 Schema 的 JSON 参数，
    并作为 context_data 传递。"""
    trigger_regex: str | None = None
    """(可选) 正则表达式。
    如果用户的输入匹配此正则，将触发极速硬路由，跳过大模型思考。"""
    trigger_func: Callable[..., bool | str | None] | None = None
    """(可选) 自定义校验函数。返回 True 或目标名称时触发硬路由。支持依赖注入。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class TeamAction(BaseModel):
    """多智能体团队协作动作基类"""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class CallAction(TeamAction):
    """
    调度动作：呼叫指定的 Agent 执行任务
    """

    agent: str | BaseRunnable[Any]
    """目标 Agent 的名称（字符串）或动态生成的 Agent 实例"""
    task: str | AgentTask
    """派发给该 Agent 的具体任务或提示词"""
    history: Sequence[AgentMessage] | None = None
    """需要传递给该 Agent 的上下文历史记录（可选）"""
    kwargs: dict[str, Any] | None = None
    """其他透传给 Agent.run_stream 的 kwargs（可选）"""


class ConcurrentCallAction(TeamAction):
    """
    并发调度动作：同时呼叫多个 Agent 执行任务
    """

    actions: list[CallAction]
    """并发执行的呼叫动作列表"""


class FinishAction(TeamAction):
    """
    结束动作：团队协作完成，返回最终结果
    """

    result: Any
    """团队协作的最终产出"""


class TaskNodeStatus(str, Enum):
    """团队自主任务节点状态枚举"""

    pending = "pending"
    """待处理：所有前置依赖已完成，等待分配执行"""
    in_progress = "in_progress"
    """进行中：正在被 Member Agent 执行"""
    completed = "completed"
    """已完成：执行成功"""
    failed = "failed"
    """已失败：执行报错或由于前置依赖失败而自动失败"""
    blocked = "blocked"
    """阻塞中：有前置依赖任务尚未完成"""


class SubTaskRecord(BaseModel):
    """单条子任务（工单）数据契约"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    """子任务的唯一标识 ID"""
    title: str = ""
    """子任务标题"""
    description: str = ""
    """子任务的具体描述"""
    assignee: str | None = None
    """执行该任务的指派成员 Agent 名称"""
    dependencies: list[str] = Field(default_factory=list)
    """该任务依赖的前置任务 ID 列表"""
    status: TaskNodeStatus = TaskNodeStatus.pending
    """任务的当前执行状态"""
    result: str | None = None
    """任务执行结果或输出信息"""
    notes: list[str] = Field(default_factory=list)
    """执行过程中的追加备注或错误记录"""
    metadata: dict[str, Any] = Field(default_factory=dict)
    """附加元数据，供系统底层或第三方插件挂载隐式上下文，对大模型不可见"""


class TaskBoardState(BaseModel):
    """
    Team 自主任务模式下的全局共享黑板状态 (Task Board)。
    提供任务的 CRUD、依赖拓扑计算和格式化渲染功能。
    """

    tasks: list[SubTaskRecord] = Field(default_factory=list)
    """看板中存储的所有子任务记录列表"""
    is_goal_complete: bool = False
    """团队的终极目标是否已宣告完成"""
    final_summary: str | None = None
    """目标完成后的终结总结报告"""

    def create_task(
        self,
        title: str,
        description: str = "",
        assignee: str | None = None,
        dependencies: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SubTaskRecord:
        """创建一个新任务并加入看板。Python 引擎和 Tool 均调用此方法。"""
        clean_deps = [d for d in (dependencies or []) if d.strip()]
        task = SubTaskRecord(
            title=title,
            description=description,
            assignee=assignee,
            dependencies=clean_deps,
            metadata=metadata or {},
        )
        self.tasks.append(task)
        self._update_blocked_statuses()
        return task

    def get_task(self, task_id: str) -> SubTaskRecord | None:
        """根据 ID 或标题获取对应的子任务记录"""
        return next(
            (t for t in self.tasks if t.id == task_id or t.title == task_id), None
        )

    def update_task_status(
        self, task_id: str, status: TaskNodeStatus, result: str | None = None
    ) -> SubTaskRecord | None:
        """更新指定任务的状态以及执行结果"""
        task = self.get_task(task_id)
        if not task:
            return None
        task.status = status
        if result is not None:
            task.result = result
        self._update_blocked_statuses()
        return task

    def _is_blocked(self, task: SubTaskRecord) -> bool:
        """检查该任务是否有尚未完成的前置依赖"""
        if not task.dependencies:
            return False
        for dep_id in task.dependencies:
            dep = self.get_task(dep_id)
            if dep is None:
                return True
            if dep.status != TaskNodeStatus.completed:
                return True
        return False

    def _has_failed_dependency(self, task: SubTaskRecord) -> bool:
        """检查该任务是否有已经失败的前置依赖"""
        if not task.dependencies:
            return False
        for dep_id in task.dependencies:
            dep = self.get_task(dep_id)
            if dep is not None and dep.status == TaskNodeStatus.failed:
                return True
        return False

    def _update_blocked_statuses(self) -> None:
        """重新计算所有未终结任务的阻塞状态 (基于拓扑依赖)"""
        for task in self.tasks:
            if task.status == TaskNodeStatus.blocked:
                if self._has_failed_dependency(task):
                    task.status = TaskNodeStatus.failed
                    task.result = "自动标记失败: 前置依赖任务已失败。"
                elif not self._is_blocked(task):
                    task.status = TaskNodeStatus.pending
            elif task.status == TaskNodeStatus.pending:
                if self._has_failed_dependency(task):
                    task.status = TaskNodeStatus.failed
                    task.result = "自动标记失败: 前置依赖任务已失败。"
                elif self._is_blocked(task):
                    task.status = TaskNodeStatus.blocked

    def get_available_tasks(
        self, for_assignee: str | None = None
    ) -> list[SubTaskRecord]:
        """获取所有当前无依赖阻塞、可立即执行的 Pending 任务"""
        available = []
        for task in self.tasks:
            if task.status != TaskNodeStatus.pending:
                continue
            if self._is_blocked(task):
                continue
            if for_assignee and task.assignee and task.assignee != for_assignee:
                continue
            available.append(task)
        return available

    def all_terminal(self) -> bool:
        """检查是否所有的任务均已进入终结状态（已完成或已失败）"""
        if not self.tasks:
            return False
        return all(
            t.status in (TaskNodeStatus.completed, TaskNodeStatus.failed)
            for t in self.tasks
        )

    def render_board_to_string(self) -> str:
        """渲染供 LLM 阅读的 Markdown 看板战报"""
        if not self.tasks:
            return "目前尚未创建任何任务。"

        counts: dict[str, int] = {}
        for t in self.tasks:
            counts[t.status.value] = counts.get(t.status.value, 0) + 1

        parts = [f"{v} {k}" for k, v in counts.items()]
        header = (
            f"### 📋 任务状态总览 (共计 {len(self.tasks)} 个任务: {', '.join(parts)}):"
        )

        lines = [header]
        for t in self.tasks:
            status_str = t.status.value.upper()
            assignee_str = f" (指派给: {t.assignee})" if t.assignee else " (尚未指派)"
            lines.append(f"  [{t.id}] {t.title} - {status_str}{assignee_str}")
            if t.dependencies:
                lines.append(f"      依赖于: {t.dependencies}")
            if t.result:
                result_preview = (
                    t.result[:1000] + "..." if len(t.result) > 1000 else t.result
                )
                lines.append(f"      结果: {result_preview}")
            if t.notes:
                for note in t.notes[-3:]:
                    lines.append(f"      附注: {note}")

        if self.is_goal_complete and self.final_summary:
            lines.append(f"\n✅ 终极目标已标记完成: {self.final_summary}")

        return "<current_task_state>\n" + "\n".join(lines) + "\n</current_task_state>"
