from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
import time
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
SideEffectKind = str
SideEffectState = str


@dataclass(slots=True)
class SideEffectReservation:
    kind: SideEffectKind
    reservation: ReservationLike
    amount: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    state: SideEffectState = "reserved"
    reserved_at: float = field(default_factory=time.monotonic)
    committed_at: float | None = None
    released_at: float | None = None
    reason: str | None = None

    @property
    def should_auto_unblock(self) -> bool:
        return bool(getattr(self.reservation, "should_auto_unblock", False))


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
    _reservations: dict[SideEffectKind, SideEffectReservation] = field(
        default_factory=dict
    )
    committed: bool = False

    @property
    def limit_should_auto_unblock(self) -> bool:
        record = self._reservations.get("limit")
        return bool(record and record.should_auto_unblock)

    @property
    def has_pending(self) -> bool:
        return any(record.state == "reserved" for record in self._reservations.values())

    @property
    def pending_kinds(self) -> tuple[str, ...]:
        return tuple(
            kind
            for kind, record in self._reservations.items()
            if record.state == "reserved"
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "committed": self.committed,
            "pending": list(self.pending_kinds),
            "reservations": {
                kind: {
                    "state": record.state,
                    "amount": record.amount,
                    "metadata": record.metadata,
                    "reason": record.reason,
                }
                for kind, record in self._reservations.items()
            },
        }

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

    async def reserve(
        self,
        kind: SideEffectKind,
        reservation: ReservationLike,
        *,
        amount: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self.release(kind, f"replace_{kind}_reservation")
        self._reservations[kind] = SideEffectReservation(
            kind=kind,
            reservation=reservation,
            amount=amount,
            metadata=metadata or {},
        )

    async def commit(self, kind: SideEffectKind) -> None:
        record = self._reservations.get(kind)
        if record is None or record.state != "reserved":
            return
        try:
            await _commit_reservation(record.reservation)
        except Exception:
            record.reason = "commit_failed"
            raise
        record.state = "committed"
        record.committed_at = time.monotonic()

    async def release(
        self,
        kind: SideEffectKind,
        reason: str | None = None,
    ) -> None:
        record = self._reservations.get(kind)
        if record is None or record.state != "reserved":
            return
        try:
            await _release_reservation(record.reservation)
        finally:
            record.state = "released"
            record.released_at = time.monotonic()
            record.reason = reason

    async def reserve_limit(self, reservation: ReservationLike) -> None:
        await self.reserve("limit", reservation)

    async def commit_limit(
        self,
        reservation: ReservationLike | None = None,
    ) -> None:
        if reservation is not None:
            await self.reserve_limit(reservation)
        await self.commit("limit")

    async def release_limit(self, reason: str | None = None) -> None:
        await self.release("limit", reason)

    async def reserve_gold(
        self,
        reservation: ReservationLike,
        *,
        amount: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self.reserve(
            "gold",
            reservation,
            amount=amount,
            metadata=metadata,
        )

    async def commit_gold(self) -> None:
        await self.commit("gold")

    async def rollback_gold(self, reason: str | None = None) -> None:
        await self.release("gold", reason)

    async def rollback_all(self, reason: str | None = None) -> None:
        for kind in list(self._reservations):
            await self.release(kind, reason)

    async def commit_all(self, *, order: Sequence[str] = ("gold", "limit")) -> None:
        for name in order:
            await self.commit(name)
        self.committed = True
