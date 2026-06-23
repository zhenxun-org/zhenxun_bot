from __future__ import annotations

import asyncio
import contextlib
import time

from tortoise import Tortoise
from tortoise.connection import connections

from zhenxun.services.log import logger
from zhenxun.services.low_priority_writer import low_priority_writer_active_count
from zhenxun.services.message_load import (
    signal_db_unhealthy,
)
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

from .config import LOG_COMMAND

_CHECK_INTERVAL_SECONDS = 15.0
_CHECK_TIMEOUT_SECONDS = 2.0
_FAIL_THRESHOLD = 3
_UNHEALTHY_SECONDS = 60.0
_RECONNECT_COOLDOWN_SECONDS = 60.0
_RECONNECT_WAIT_IDLE_SECONDS = 3.0

_WATCHDOG_TASK: asyncio.Task[None] | None = None
_RECONNECT_LOCK = asyncio.Lock()
_LAST_RECONNECT_AT = 0.0


def _is_sqlite_connection() -> bool:
    with contextlib.suppress(Exception):
        connection = Tortoise.get_connection("default")
        capabilities = getattr(connection, "capabilities", None)
        dialect = str(getattr(capabilities, "dialect", "") or "").lower()
        return dialect.startswith("sqlite")
    return False


async def _select_one() -> None:
    connection = Tortoise.get_connection("default")
    await connection.execute_query("SELECT 1")


async def _try_reconnect(reason: str) -> None:
    global _LAST_RECONNECT_AT
    now = time.monotonic()
    if now - _LAST_RECONNECT_AT < _RECONNECT_COOLDOWN_SECONDS:
        return
    if low_priority_writer_active_count() > 0:
        return
    async with _RECONNECT_LOCK:
        now = time.monotonic()
        if now - _LAST_RECONNECT_AT < _RECONNECT_COOLDOWN_SECONDS:
            return
        await asyncio.sleep(_RECONNECT_WAIT_IDLE_SECONDS)
        if low_priority_writer_active_count() > 0:
            return
        try:
            await connections.close_all(discard=True)
            # ConnectionHandler lazily recreates default connection from db_config.
            Tortoise.get_connection("default")
            _LAST_RECONNECT_AT = time.monotonic()
            logger.warning(
                f"SQLite watchdog rebuilt default connection: {reason}",
                LOG_COMMAND,
            )
        except Exception as exc:
            _LAST_RECONNECT_AT = time.monotonic()
            signal_db_unhealthy(
                _UNHEALTHY_SECONDS,
                reason=f"watchdog reconnect:{reason}",
            )
            logger.warning("SQLite watchdog reconnect failed", LOG_COMMAND, e=exc)


async def _watchdog_loop() -> None:
    failures = 0
    while True:
        await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
        if not _is_sqlite_connection():
            failures = 0
            continue
        try:
            await asyncio.wait_for(_select_one(), timeout=_CHECK_TIMEOUT_SECONDS)
            failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures += 1
            reason = f"sqlite watchdog SELECT 1 failed x{failures}: {exc}"
            signal_db_unhealthy(_UNHEALTHY_SECONDS, reason=reason)
            logger.warning(reason, LOG_COMMAND)
            if failures >= _FAIL_THRESHOLD:
                await _try_reconnect(reason)


def start_db_watchdog() -> None:
    global _WATCHDOG_TASK
    if _WATCHDOG_TASK is not None and not _WATCHDOG_TASK.done():
        return
    _WATCHDOG_TASK = asyncio.create_task(_watchdog_loop())


async def stop_db_watchdog() -> None:
    global _WATCHDOG_TASK
    task = _WATCHDOG_TASK
    _WATCHDOG_TASK = None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(BaseException):
        await task


@PriorityLifecycle.on_startup(priority=8)
async def _start_db_watchdog() -> None:
    start_db_watchdog()


@PriorityLifecycle.on_shutdown(priority=10)
async def _stop_db_watchdog() -> None:
    await stop_db_watchdog()
