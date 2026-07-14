from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from typing import Any, TypeVar

from nonebot.utils import is_coroutine_callable
from pydantic import BaseModel, ConfigDict

from zhenxun.services.ai.utils.logger import log_core as logger


class AgentStreamEvent(BaseModel):
    """
    Agent 局部流事件与生命周期事件的绝对统一基类
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)


class LLMStartEvent(AgentStreamEvent):
    """大模型网络请求开始事件"""

    model_name: str
    """请求的大模型名称"""
    messages: list[Any]
    """发往大模型的历史消息列表"""


class LLMEndEvent(AgentStreamEvent):
    """大模型网络请求结束事件"""

    response: Any
    """大模型返回的完整响应 (ChatResponse)"""


class ToolCallStartEvent(AgentStreamEvent):
    """工具调用开始事件"""

    tool_name: str
    """调用的工具名称"""
    arguments: dict[str, Any]
    """工具调用参数"""
    intent: str | None = None
    """从大模型调用参数中剥离出的意图 (_intent)"""


class ToolCallEndEvent(AgentStreamEvent):
    """工具调用结束事件"""

    tool_name: str
    """工具名称"""
    result: Any
    """工具最终返回的结果"""
    is_error: bool
    """工具执行是否失败"""


class ToolStreamChunkEvent(AgentStreamEvent):
    """工具或后台任务流式进度反馈事件"""

    tool_name: str
    """工具名称"""
    content: str
    """当前流式输出的文本片段"""
    metadata: dict[str, Any] | None = None
    """流式的附加元数据 (如进度比例等)"""


class UserCustomEvent(AgentStreamEvent):
    """自定义用户界面交互事件"""

    display: Any
    """用于前端渲染的展示对象 (如 UniMessage, str, ImagePart)"""
    log_content: str | None = None
    """用于后台打印的日志摘要"""


class ControlFlowEvent(AgentStreamEvent):
    """控制流中断与流转事件"""

    action: str
    """控制流动作，如 'handoff', 'abort', 'end_run'"""
    payload: Any = None
    """附加的数据载荷"""


T_Event = TypeVar("T_Event", bound=AgentStreamEvent)
"""泛型变量：用于绑定具体的事件类型，提供完美的 IDE 类型推导"""


class EventBus:
    """
    局部事件总线 (Event Bus)
    EventBus 支持异步迭代与发布-订阅(Pub/Sub)模式
    """

    def __init__(self):
        """
        初始化 EventBus 实例。
        """
        self._queue = asyncio.Queue()
        self._finished = False
        self._subscribers: dict[type[AgentStreamEvent], list[Callable[[Any], Any]]] = (
            defaultdict(list)
        )
        self._background_tasks = set()
        self._async_handlers_queues: dict[Callable, asyncio.Queue] = {}

    def subscribe(
        self, event_type: type[T_Event], handler: Callable[[T_Event], Any]
    ) -> None:
        """
        注册事件监听器，用于订阅特定类型的事件。

        参数：
            event_type: 要订阅的事件类型（需为 AgentStreamEvent 的子类）。
            handler: 事件处理回调函数。当对应事件发布时被触发，参数为事件实例。
        """
        self._subscribers[event_type].append(handler)

        if (
            is_coroutine_callable(handler)
            and handler not in self._async_handlers_queues
        ):
            q = asyncio.Queue(maxsize=1000)
            self._async_handlers_queues[handler] = q

            async def _worker(h=handler, queue=q):
                while True:
                    evt = await queue.get()
                    if evt is None:
                        queue.task_done()
                        break
                    try:
                        await h(evt)
                    except asyncio.CancelledError:
                        queue.task_done()
                        break
                    except Exception as err:
                        logger.error(f"EventBus 订阅者执行异常: {err}")
                    finally:
                        queue.task_done()

            task = asyncio.create_task(_worker())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    async def emit(self, event: AgentStreamEvent) -> None:
        """
        发布事件，触发所有匹配的订阅者，并将事件放入迭代队列中。

        参数：
            event: 要发布的事件实例（需继承自 AgentStreamEvent）。
        """
        handlers = []
        for ev_type, cb_list in self._subscribers.items():
            if isinstance(event, ev_type):
                handlers.extend(cb_list)

        for handler in handlers:
            if is_coroutine_callable(handler):
                q = self._async_handlers_queues.get(handler)
                if q:
                    await q.put(event)
            else:
                try:
                    handler(event)
                except Exception as err:
                    logger.error(f"EventBus 同步订阅者执行异常: {err}")

        if not self._finished:
            await self._queue.put(event)

    async def end(self):
        """
        结束事件总线。等待所有后台任务执行完毕，并向队列中投放结束标记以终止异步迭代。
        """
        self._finished = True
        await self._queue.put(None)

        for q in self._async_handlers_queues.values():
            await q.put(None)

        if self._background_tasks:
            tasks = list(self._background_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)
            self._background_tasks.clear()
            self._async_handlers_queues.clear()

    async def __aiter__(self):
        """
        支持异步迭代，可通过 async for 循环消费事件总线中的事件。

        返回：
            AsyncIterator: 异步事件流生成器。
        """
        while True:
            event = await self._queue.get()
            if event is None:
                break
            yield event
