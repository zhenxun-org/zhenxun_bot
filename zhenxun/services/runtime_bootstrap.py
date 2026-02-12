import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextlib
import os

import anyio.to_thread
from nonebot.drivers import Driver

DEFAULT_EXECUTOR_MIN_WORKERS = 16
DEFAULT_EXECUTOR_MAX_WORKERS = 64
DEFAULT_ANYIO_MIN_TOKENS = 32
DEFAULT_ANYIO_MAX_TOKENS = 128

_thread_executor: ThreadPoolExecutor | None = None
_runtime_hooks_registered = False


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def _get_executor_workers() -> int:
    cpu = os.cpu_count() or 4
    return _clamp(cpu * 4, DEFAULT_EXECUTOR_MIN_WORKERS, DEFAULT_EXECUTOR_MAX_WORKERS)


def _get_anyio_tokens(executor_workers: int) -> int:
    return _clamp(
        executor_workers * 2, DEFAULT_ANYIO_MIN_TOKENS, DEFAULT_ANYIO_MAX_TOKENS
    )


def register_runtime_bootstrap(driver: Driver) -> None:
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

    @driver.on_shutdown
    async def _shutdown_runtime_concurrency() -> None:
        global _thread_executor
        executor = _thread_executor
        _thread_executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
