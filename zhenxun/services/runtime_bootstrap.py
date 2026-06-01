import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextlib
import os
import signal

import anyio.to_thread

from zhenxun.services.log import logger
from zhenxun.services.memory_governor import (
    start_memory_governor,
    stop_memory_governor,
)
from zhenxun.services.send_queue import start_send_queue, stop_send_queue
from zhenxun.services.uninfo_patch import apply_uninfo_onebot11_patch
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

DEFAULT_EXECUTOR_MIN_WORKERS = 16
DEFAULT_EXECUTOR_MAX_WORKERS = 64
DEFAULT_ANYIO_MIN_TOKENS = 32
DEFAULT_ANYIO_MAX_TOKENS = 128

_thread_executor: ThreadPoolExecutor | None = None
_launcher_watchdog_task: asyncio.Task[None] | None = None
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


async def _launcher_watchdog_loop(launcher_pid: int) -> None:
    try:
        import psutil
    except Exception:
        return
    current_pid = os.getpid()
    while True:
        await asyncio.sleep(2)
        if psutil.pid_exists(launcher_pid):
            continue
        logger.warning(
            f"检测到 launcher 进程 {launcher_pid} 已退出，worker 将主动结束...",
            "RuntimeBootstrap",
        )
        with contextlib.suppress(Exception):
            os.kill(current_pid, signal.SIGTERM)
        return


def _start_launcher_watchdog() -> None:
    global _launcher_watchdog_task
    if _launcher_watchdog_task is not None and not _launcher_watchdog_task.done():
        return
    launcher_pid_text = os.getenv("ZHENXUN_LAUNCHER_PID", "").strip()
    if not launcher_pid_text:
        return
    with contextlib.suppress(ValueError):
        launcher_pid = int(launcher_pid_text)
        if launcher_pid > 0:
            _launcher_watchdog_task = asyncio.create_task(
                _launcher_watchdog_loop(launcher_pid)
            )


async def _stop_launcher_watchdog() -> None:
    global _launcher_watchdog_task
    task = _launcher_watchdog_task
    _launcher_watchdog_task = None
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def register_runtime_bootstrap(_driver) -> None:
    _apply_alconna_conflict_patch()
    apply_uninfo_onebot11_patch()
    global _runtime_hooks_registered
    if _runtime_hooks_registered:
        return
    _runtime_hooks_registered = True

    @PriorityLifecycle.on_startup(priority=-100)
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
        _start_launcher_watchdog()
        await start_send_queue()
        await start_memory_governor()

    @PriorityLifecycle.on_shutdown(priority=50)
    async def _shutdown_runtime_concurrency() -> None:
        global _thread_executor
        await _stop_launcher_watchdog()
        await stop_send_queue()
        await stop_memory_governor()
        executor = _thread_executor
        _thread_executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
