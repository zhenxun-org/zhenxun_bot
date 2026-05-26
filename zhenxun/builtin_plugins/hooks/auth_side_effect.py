from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from nonebot_plugin_uninfo import Uninfo

from zhenxun.services.log import logger
from zhenxun.utils.utils import EntityIDs

from .auth.config import LOGGER_COMMAND
from .auth.utils import send_message

AsyncAction = Callable[[], Awaitable[None]]


class SyncReservation(Protocol):
    def commit(self) -> None: ...

    def release(self) -> None: ...


class AsyncReservation(Protocol):
    async def commit(self) -> None: ...

    async def release(self) -> None: ...


ReservationLike = AsyncAction | SyncReservation | AsyncReservation


async def _maybe_await(value: Any) -> None:
    if hasattr(value, "__await__"):
        await value


async def _commit_reservation(reservation: ReservationLike) -> None:
    commit = getattr(reservation, "commit", None)
    if callable(commit):
        await _maybe_await(commit())
        return
    if callable(reservation):
        await reservation()


async def _release_reservation(reservation: ReservationLike) -> None:
    release = getattr(reservation, "release", None)
    if callable(release):
        await _maybe_await(release())


@dataclass(slots=True)
class SideEffectCommit:
    """权限链副作用提交器。

    第一阶段只封装既有调用点，不改变扣金币、限流提交、权限提示发送时机。
    """

    session: Uninfo
    module: str
    owner_matcher_id: int | None = None
    limit_entity: EntityIDs | None = None
    _reserved_limit: ReservationLike | None = None
    _reserved_gold: ReservationLike | None = None
    committed: bool = False

    @property
    def limit_should_auto_unblock(self) -> bool:
        return bool(getattr(self._reserved_limit, "should_auto_unblock", False))

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
        func: ReservationLike,
    ) -> None:
        await self.reserve_gold(func)
        await self.commit_gold()

    async def reserve_limit(self, reservation: ReservationLike) -> None:
        await self.release_limit("replace_limit_reservation")
        self._reserved_limit = reservation

    async def commit_limit(
        self,
        reservation: ReservationLike | None = None,
    ) -> None:
        if reservation is not None:
            await self.reserve_limit(reservation)
        if self._reserved_limit is None:
            return
        action = self._reserved_limit
        self._reserved_limit = None
        await _commit_reservation(action)

    async def release_limit(self, reason: str | None = None) -> None:
        del reason
        reservation = self._reserved_limit
        self._reserved_limit = None
        if reservation is not None:
            await _release_reservation(reservation)

    async def reserve_gold(
        self,
        reservation: ReservationLike,
        *,
        amount: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del amount, metadata
        await self.rollback_gold("replace_gold_reservation")
        self._reserved_gold = reservation

    async def commit_gold(self) -> None:
        if self._reserved_gold is None:
            return
        action = self._reserved_gold
        self._reserved_gold = None
        await _commit_reservation(action)

    async def rollback_gold(self, reason: str | None = None) -> None:
        del reason
        reservation = self._reserved_gold
        self._reserved_gold = None
        if reservation is not None:
            await _release_reservation(reservation)

    async def rollback_all(self, reason: str | None = None) -> None:
        await self.release_limit(reason)
        await self.rollback_gold(reason)

    async def commit_all(self, *, order: Sequence[str] = ("gold", "limit")) -> None:
        for name in order:
            if name == "limit":
                await self.commit_limit()
            elif name == "gold":
                await self.commit_gold()
        self.committed = True
