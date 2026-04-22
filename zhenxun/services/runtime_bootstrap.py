import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextlib
import os

import anyio.to_thread
from nonebot.drivers import Driver

from zhenxun.services.memory_governor import (
    start_memory_governor,
    stop_memory_governor,
)
from zhenxun.services.uninfo_patch import apply_uninfo_onebot11_patch
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

DEFAULT_EXECUTOR_MIN_WORKERS = 16
DEFAULT_EXECUTOR_MAX_WORKERS = 64
DEFAULT_ANYIO_MIN_TOKENS = 32
DEFAULT_ANYIO_MAX_TOKENS = 128

_thread_executor: ThreadPoolExecutor | None = None
_runtime_hooks_registered = False
_alconna_patch_applied = False


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def _get_executor_workers() -> int:
    cpu = os.cpu_count() or 4
    return _clamp(cpu * 4, DEFAULT_EXECUTOR_MIN_WORKERS, DEFAULT_EXECUTOR_MAX_WORKERS)


def _get_anyio_tokens(executor_workers: int) -> int:
    return _clamp(
        executor_workers * 2, DEFAULT_ANYIO_MIN_TOKENS, DEFAULT_ANYIO_MAX_TOKENS
    )


def _apply_alconna_conflict_patch() -> None:
    global _alconna_patch_applied
    if _alconna_patch_applied:
        return
    with contextlib.suppress(Exception):
        from arclet.alconna import formatter as alconna_formatter

        text_formatter = getattr(alconna_formatter, "TextFormatter", None)
        if text_formatter is None:
            return
        original_remove = getattr(text_formatter, "remove", None)
        if getattr(original_remove, "__zhenxun_safe_remove__", False):
            _alconna_patch_applied = True
            return

        def _safe_remove(self, base):
            # Tolerate duplicate command cleanup when formatter hash is absent.
            self.data.pop(base._hash, None)

        setattr(_safe_remove, "__zhenxun_safe_remove__", True)
        setattr(text_formatter, "remove", _safe_remove)
        _alconna_patch_applied = True


def register_runtime_bootstrap(driver: Driver) -> None:
    _apply_alconna_conflict_patch()
    apply_uninfo_onebot11_patch()
    global _runtime_hooks_registered
    if _runtime_hooks_registered:
        return
    _runtime_hooks_registered = True

    @driver.on_startup
    async def _setup_runtime_concurrency() -> None:
        global _thread_executor
        workers = _get_executor_workers()
        loop = asyncio.get_running_loop()
        if _thread_executor is None:
            _thread_executor = ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="zhenxun-worker"
            )
        loop.set_default_executor(_thread_executor)
        with contextlib.suppress(Exception):
            limiter = anyio.to_thread.current_default_thread_limiter()
            limiter.total_tokens = _get_anyio_tokens(workers)
        await start_memory_governor()

    @PriorityLifecycle.on_shutdown(priority=50)
    async def _shutdown_runtime_concurrency() -> None:
        global _thread_executor
        await stop_memory_governor()
        executor = _thread_executor
        _thread_executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
