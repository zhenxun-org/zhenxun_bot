from collections.abc import Callable
from typing import ClassVar

import nonebot
from nonebot.utils import is_coroutine_callable

from zhenxun.services.log import logger
from zhenxun.utils.enum import PriorityLifecycleType
from zhenxun.utils.exception import HookPriorityException

driver = nonebot.get_driver()


class PriorityLifecycle:
    _data: ClassVar[dict[PriorityLifecycleType, dict[int, list[Callable]]]] = {}

    @classmethod
    def add(cls, hook_type: PriorityLifecycleType, func: Callable, priority: int):
        if hook_type not in cls._data:
            cls._data[hook_type] = {}
        if priority not in cls._data[hook_type]:
            cls._data[hook_type][priority] = []
        cls._data[hook_type][priority].append(func)

    @classmethod
    def on_startup(cls, *, priority: int):
        def wrapper(func):
            cls.add(PriorityLifecycleType.STARTUP, func, priority)
            return func

        return wrapper

    @classmethod
    def on_shutdown(cls, *, priority: int):
        def wrapper(func):
            cls.add(PriorityLifecycleType.SHUTDOWN, func, priority)
            return func

        return wrapper


@driver.on_startup
async def _():
    priority_data = PriorityLifecycle._data.get(PriorityLifecycleType.STARTUP)
    if not priority_data:
        return
    priority_list = sorted(priority_data.keys())
    priority = 0
    try:
        for priority in priority_list:
            for func in priority_data[priority]:
                if is_coroutine_callable(func):
                    await func()
                else:
                    func()
    except HookPriorityException as e:
        logger.error(f"打断优先级 [{priority}] on_startup 方法. {type(e)}: {e}")
