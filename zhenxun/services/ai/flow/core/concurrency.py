import asyncio
from contextlib import asynccontextmanager

from zhenxun.services.ai.core.exceptions import (
    ConcurrencyRejectException,
    InterventionHandledException,
)
from zhenxun.services.ai.core.models import CancellationToken
from zhenxun.services.ai.run.models import RunIntent
from zhenxun.services.ai.run.session import LockContext, session_manager
from zhenxun.services.ai.utils.logger import log_flow as logger

from .models import ConcurrencyPolicy, InterventionPolicy


@asynccontextmanager
async def apply_concurrency_policy(
    session_id: str,
    lock_id: str,
    policy: ConcurrencyPolicy,
    cancel_token: CancellationToken,
    intervention_policy: InterventionPolicy | None = None,
    intent: RunIntent | None = None,
):
    """
    异步上下文管理器：对大模型执行流应用特定的并发及消息干预调度策略。
    负责请求互斥锁竞争、任务中断/拒绝处理，以及运行时用户实时指令的插队控制。

    参数：
        session_id: 当前会话的唯一标识，用于在会话管理器中隔离上下文。
        lock_id: 当前锁域标识，决定了哪些 Agent 或任务使用同一套互斥锁竞争机制。
        policy: 当发生并发锁占用时执行的策略（允许、拒绝、中断、排队）。
        cancel_token: 运行时用于监听取消请求的取消令牌实例。
        intervention_policy: 用户消息干预策略（转向、追加）。
        message: 并发竞争发生时新入站的用户请求消息或 AgentTask 载荷。

    返回：
        AsyncGenerator: 返回异步生成器，供 async with 消费，包裹大模型的整个执行环节。
    """

    current_task = asyncio.current_task()
    task_tuple = (cancel_token, current_task)
    if session_id not in session_manager.live_tasks:
        session_manager.live_tasks[session_id] = []
    session_manager.live_tasks[session_id].append(task_tuple)

    try:
        exec_lock = session_manager.get_exec_lock(lock_id)
        lock_ctx = session_manager.lock_contexts.setdefault(lock_id, LockContext())

        if exec_lock.locked():
            if intervention_policy in (
                InterventionPolicy.STEER,
                InterventionPolicy.FOLLOW_UP,
            ):
                session = await session_manager.get_or_create(session_id)

                if intervention_policy == InterventionPolicy.STEER:
                    session.steer_queue.enqueue(intent.text if intent else "")
                    raise InterventionHandledException(
                        "Steer successful",
                        display_content="💬 已将您的补充信息传递给正在思考的 AI...",
                    )
                elif intervention_policy == InterventionPolicy.FOLLOW_UP:
                    session.follow_up_queue.enqueue(intent.text if intent else "")
                    raise InterventionHandledException(
                        "Follow-up successful",
                        display_content="📝 已记录，AI 处理完当前任务后即刻执行...",
                    )

        if policy == ConcurrencyPolicy.ALLOW:
            yield
            return

        if policy == ConcurrencyPolicy.REJECT:
            if exec_lock.locked():
                raise ConcurrencyRejectException(
                    f"并发域 {lock_id} 正忙，新请求被拒绝。"
                )

        elif policy == ConcurrencyPolicy.INTERRUPT:
            if exec_lock.locked():
                if lock_ctx.cancel_token:
                    lock_ctx.cancel_token.cancel()
                if lock_ctx.active_task and not lock_ctx.active_task.done():
                    lock_ctx.active_task.cancel()

        elif policy == ConcurrencyPolicy.QUEUE:
            if exec_lock.locked():
                logger.info(
                    f"⏳ [并发控制] 锁域 {lock_id} 被占用，"
                    "新请求已进入后台等待队列 (QUEUE)..."
                )

        async with exec_lock:
            session = await session_manager.get_or_create(session_id)
            session.active_task = asyncio.current_task()
            session.cancel_token = cancel_token

            lock_ctx.active_task = asyncio.current_task()
            lock_ctx.cancel_token = cancel_token
            try:
                yield
            finally:
                session.active_task = None
                session.cancel_token = None
                lock_ctx.active_task = None
                lock_ctx.cancel_token = None
    finally:
        if session_id in session_manager.live_tasks:
            if task_tuple in session_manager.live_tasks[session_id]:
                session_manager.live_tasks[session_id].remove(task_tuple)
            if not session_manager.live_tasks[session_id]:
                del session_manager.live_tasks[session_id]
