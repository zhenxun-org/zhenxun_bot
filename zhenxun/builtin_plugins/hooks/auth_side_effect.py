from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nonebot_plugin_uninfo import Uninfo

from zhenxun.services.log import logger

from .auth.config import LOGGER_COMMAND
from .auth.utils import send_message


@dataclass(slots=True)
class SideEffectCommit:
    """权限链副作用提交器。

    第一阶段只封装既有调用点，不改变扣金币、限流提交、权限提示发送时机。
    """

    session: Uninfo
    module: str

    async def send_permission_tip(
        self,
        message: list | str,
        check_tag: str | None = None,
        *,
        background: bool = False,
        timeout: float | None = None,
    ) -> None:
        try:
            tip_coro = send_message(
                self.session,
                message,
                check_tag,
                background=background,
            )
            if timeout and not background:
                await asyncio.wait_for(tip_coro, timeout=timeout)
            else:
                await tip_coro
        except asyncio.TimeoutError:
            logger.error("发送权限提示超时", LOGGER_COMMAND, session=self.session)

    async def reduce_gold(
        self,
        func: Callable[[], Awaitable[None]],
    ) -> None:
        await func()

    async def commit_limit(self, func: Callable[[], Awaitable[None]]) -> None:
        await func()
