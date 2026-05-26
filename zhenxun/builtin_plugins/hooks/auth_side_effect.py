from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

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
    _reserved_limit: Callable[[], Awaitable[None]] | None = None
    _reserved_gold: Callable[[], Awaitable[None]] | None = None

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
        await self.reserve_gold(func)
        await self.commit_gold()

    async def reserve_limit(self, func: Callable[[], Awaitable[None]]) -> None:
        self._reserved_limit = func

    async def commit_limit(
        self,
        func: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if func is not None:
            await self.reserve_limit(func)
        if self._reserved_limit is None:
            return
        action = self._reserved_limit
        self._reserved_limit = None
        await action()

    async def release_limit(self, reason: str | None = None) -> None:
        del reason
        self._reserved_limit = None

    async def reserve_gold(
        self,
        func: Callable[[], Awaitable[None]],
        *,
        amount: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del amount, metadata
        self._reserved_gold = func

    async def commit_gold(self) -> None:
        if self._reserved_gold is None:
            return
        action = self._reserved_gold
        self._reserved_gold = None
        await action()

    async def rollback_gold(self, reason: str | None = None) -> None:
        del reason
        self._reserved_gold = None
