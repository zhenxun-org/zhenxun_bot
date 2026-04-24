from __future__ import annotations

import asyncio
import contextlib
import gc
import inspect
import sys
import time
from typing import Any

from aiocache import SimpleMemoryCache

from zhenxun.services.cache import CacheRoot
from zhenxun.services.cache.bounded_ttl import BoundedTTLCache
from zhenxun.services.cache.cache_containers import CacheDict, CacheList
from zhenxun.services.log import logger
from zhenxun.services.message_load import idle_seconds, is_overloaded

LOG_COMMAND = "MemoryGovernor"

IDLE_CHECK_INTERVAL_SECONDS = 60
IDLE_RECLAIM_SECONDS = 600
RECLAIM_COOLDOWN_SECONDS = 3 * 60 * 60
RECLAIM_TIMEOUT_SECONDS = 10

_task: asyncio.Task | None = None
_reclaim_lock = asyncio.Lock()
_last_reclaim_at = 0.0


def _cooldown_left(now: float | None = None) -> float:
    now = time.monotonic() if now is None else now
    return max(0.0, _last_reclaim_at + RECLAIM_COOLDOWN_SECONDS - now)


async def start_memory_governor() -> None:
    global _task
    if _task is not None and not _task.done():
        return
    if IDLE_CHECK_INTERVAL_SECONDS <= 0 or IDLE_RECLAIM_SECONDS <= 0:
        logger.info("idle memory governor disabled", LOG_COMMAND)
        return
    _task = asyncio.create_task(_idle_reclaim_loop())


async def stop_memory_governor() -> None:
    global _task
    task = _task
    _task = None
    if task is not None:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task


async def _idle_reclaim_loop() -> None:
    while True:
        await asyncio.sleep(IDLE_CHECK_INTERVAL_SECONDS)
        if not await _should_reclaim():
            continue
        if _reclaim_lock.locked():
            continue
        async with _reclaim_lock:
            if not await _should_reclaim():
                continue
            try:
                await asyncio.wait_for(
                    _run_reclaim(),
                    timeout=max(RECLAIM_TIMEOUT_SECONDS, 1),
                )
            except asyncio.TimeoutError:
                logger.warning("idle memory reclaim timed out", LOG_COMMAND)
            except Exception as exc:
                logger.warning("idle memory reclaim failed", LOG_COMMAND, e=exc)


async def _should_reclaim() -> bool:
    if _cooldown_left() > 0:
        return False
    if idle_seconds() < IDLE_RECLAIM_SECONDS:
        return False
    if is_overloaded():
        return False
    if await _has_active_auth_work():
        return False
    return not await _has_active_render_work()


async def _has_active_auth_work() -> bool:
    module = sys.modules.get("zhenxun.builtin_plugins.hooks.auth_checker")
    if module is None:
        return False
    hooks_active = int(getattr(module, "HOOKS_ACTIVE_COUNT", 0) or 0)
    db_active = int(getattr(module, "DB_ACTIVE_COUNT", 0) or 0)
    return hooks_active > 0 or db_active > 0


async def _has_active_render_work() -> bool:
    module = sys.modules.get("zhenxun.services.renderer.engine")
    if module is None:
        return False
    manager = getattr(module, "engine_manager", None)
    engine = getattr(manager, "_instance", None)
    if engine is None:
        return False
    try:
        snapshot = await asyncio.wait_for(engine.get_runtime_snapshot(), timeout=1.0)
    except Exception:
        return True
    if snapshot.get("active_renders", 0):
        return True
    if snapshot.get("htmlrender_active_tasks", 0):
        return True
    active_generation = snapshot.get("active_generation")
    if isinstance(active_generation, dict) and active_generation.get(
        "active_leases", 0
    ):
        return True
    retiring = snapshot.get("retiring_generations", [])
    if isinstance(retiring, list):
        return any(
            isinstance(item, dict) and item.get("active_leases", 0) for item in retiring
        )
    return False


async def _run_reclaim() -> None:
    global _last_reclaim_at
    start = time.monotonic()
    before_rss = _get_total_rss()
    cleared: dict[str, Any] = {}
    cache_stats_before = {
        "cache_dict": CacheDict.stats_all(),
        "cache_list": CacheList.stats_all(),
        "bounded_ttl": await BoundedTTLCache.stats_all(),
    }

    cleared["statistics"] = await _flush_statistics_buffer()
    cleared["user_gold_logs"] = await _flush_user_gold_log_buffer()
    cleared["bounded_ttl_clear"] = await BoundedTTLCache.clear_all()
    cleared["cache_dict_clear"] = CacheDict.clear_all()
    cleared["cache_list_clear"] = CacheList.clear_all()
    cleared["runtime_negative"] = _clear_runtime_negative_caches()
    cleared["auth_local"] = _clear_auth_local_caches()
    cleared["avatar_l1"] = _clear_avatar_memory_cache()
    cleared["renderer_runtime"] = await _clear_renderer_runtime_caches()
    cleared["message_manager"] = _clear_message_manager_cache()
    cleared["aiocache_memory"] = await _clear_simple_memory_backend()

    collected = gc.collect(2)
    malloc_trimmed = _malloc_trim()
    after_rss = _get_total_rss()
    _last_reclaim_at = time.monotonic()

    logger.info(
        "idle memory reclaim completed: "
        f"cost={time.monotonic() - start:.3f}s "
        f"rss_before={_format_bytes(before_rss)} "
        f"rss_after={_format_bytes(after_rss)} "
        f"gc={collected} malloc_trim={malloc_trimmed} "
        f"cleared={cleared} cache_stats_before={cache_stats_before}",
        LOG_COMMAND,
    )


async def _flush_statistics_buffer() -> int:
    module = sys.modules.get("zhenxun.builtin_plugins.statistics.statistics_hook")
    if module is None:
        return 0
    flush = getattr(module, "_flush_statistics_buffer", None)
    if flush is None:
        return 0
    result = await flush("内存回收")
    return int(result or 0)


async def _flush_user_gold_log_buffer() -> int:
    module = sys.modules.get("zhenxun.services.buffered_writers")
    if module is None:
        return 0
    flush = getattr(module, "flush_user_gold_log_buffer", None)
    if flush is None:
        return 0
    result = await flush("内存回收")
    return int(result or 0)


async def _clear_simple_memory_backend() -> bool:
    backend = getattr(CacheRoot, "_cache_backend", None)
    if not isinstance(backend, SimpleMemoryCache):
        return False
    await backend.clear()
    return True


def _clear_runtime_negative_caches() -> dict[str, int]:
    module = sys.modules.get("zhenxun.services.cache.runtime_cache")
    if module is None:
        return {}
    result: dict[str, int] = {}
    for name in (
        "BotMemoryCache",
        "GroupMemoryCache",
        "LevelUserMemoryCache",
        "TaskInfoMemoryCache",
        "PluginLimitMemoryCache",
        "BanMemoryCache",
    ):
        cache_cls = getattr(module, name, None)
        negative = getattr(cache_cls, "_negative", None)
        if isinstance(negative, dict) and negative:
            result[name] = len(negative)
            negative.clear()
    return result


def _clear_auth_local_caches() -> dict[str, int]:
    module = sys.modules.get("zhenxun.builtin_plugins.hooks.auth_checker")
    if module is None:
        return {}
    result: dict[str, int] = {}
    for name in (
        "_MATCHER_COMMAND_TYPE_CACHE",
        "_MATCHER_COMMAND_LITERAL_CACHE",
        "_MATCHER_ALCONNA_SHORTCUT_CACHE",
    ):
        cache = getattr(module, name, None)
        if isinstance(cache, dict) and cache:
            result[name] = len(cache)
            cache.clear()
    return result


def _clear_avatar_memory_cache() -> int:
    module = sys.modules.get("zhenxun.services.avatar_service")
    if module is None:
        return 0
    service = getattr(module, "avatar_service", None)
    clear = getattr(service, "clear_memory_cache", None)
    if not callable(clear):
        return 0
    result = clear()
    return result if isinstance(result, int) and result > 0 else 0


async def _clear_renderer_runtime_caches() -> dict[str, int]:
    module = sys.modules.get("zhenxun.services.renderer.service")
    if module is None:
        return {}
    service = getattr(module, "renderer_service", None)
    clear = getattr(service, "clear_runtime_caches", None)
    if not callable(clear):
        return {}
    result = clear()
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        return {}
    return {
        str(key): int(value)
        for key, value in result.items()
        if isinstance(value, int) and value > 0
    }


def _clear_message_manager_cache() -> int:
    module = sys.modules.get("zhenxun.utils.manager.message_manager")
    if module is None:
        return 0
    manager_cls = getattr(module, "MessageManager", None)
    clear = getattr(manager_cls, "clear_all", None)
    if not callable(clear):
        return 0
    result = clear()
    return result if isinstance(result, int) and result > 0 else 0


def _get_total_rss() -> int | None:
    try:
        import psutil

        process = psutil.Process()
        total = process.memory_info().rss
        for child in process.children(recursive=True):
            with contextlib.suppress(Exception):
                total += child.memory_info().rss
        return int(total)
    except Exception:
        return None


def _malloc_trim() -> bool:
    if sys.platform.startswith(("win", "darwin")):
        return False
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6")
        return bool(libc.malloc_trim(0))
    except Exception:
        return False


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    return f"{value / 1024 / 1024:.2f}MiB"
