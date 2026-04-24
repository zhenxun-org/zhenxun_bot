import asyncio
import time
from typing import Any

from nonebot.adapters import Bot

from zhenxun.services.log import logger

_SEND_APIS = {"send_msg", "send_group_msg", "send_private_msg", "send_like"}
_WORKERS = 3
_MIN_INTERVAL = 0.05
_QUEUE_MAXSIZE = 2000
_SHUTDOWN_DRAIN_TIMEOUT_SECONDS = 3.0
_QUEUE_PRESSURE_LOG_INTERVAL = 10.0
_QUEUE: asyncio.Queue[tuple[Bot, str, dict[str, Any], asyncio.Future[Any]]] = (
    asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
)
_SEND_LOCK = asyncio.Lock()
_LAST_SEND_TS = 0.0
_API_SEMAPHORE = asyncio.Semaphore(3)
_ORIG_CALL_API = Bot.call_api
_PATCHED = False
_WORKER_TASKS: list[asyncio.Task] = []
_QUEUE_TIMEOUT_COUNT = 0
_SEND_LIKE_DROP_COUNT = 0
_LAST_QUEUE_PRESSURE_LOG = 0.0
_STOPPING = False


async def _rate_limit():
    global _LAST_SEND_TS
    async with _SEND_LOCK:
        now = time.monotonic()
        wait = _MIN_INTERVAL - (now - _LAST_SEND_TS)
        if wait > 0:
            await asyncio.sleep(wait)
        _LAST_SEND_TS = time.monotonic()


def _log_queue_pressure(reason: str) -> None:
    global _LAST_QUEUE_PRESSURE_LOG
    now = time.monotonic()
    if now - _LAST_QUEUE_PRESSURE_LOG < _QUEUE_PRESSURE_LOG_INTERVAL:
        return
    _LAST_QUEUE_PRESSURE_LOG = now
    logger.warning(
        f"{reason}; qsize={_QUEUE.qsize()}/{_QUEUE_MAXSIZE} "
        f"timeouts={_QUEUE_TIMEOUT_COUNT} dropped_like={_SEND_LIKE_DROP_COUNT}",
        "SendQueue",
    )


async def _direct_call_api(bot: Bot, api: str, data: dict[str, Any]) -> Any:
    await _rate_limit()
    async with _API_SEMAPHORE:
        return await _ORIG_CALL_API(bot, api, **data)


async def _worker(worker_id: int):
    while True:
        bot, api, data, future = await _QUEUE.get()
        try:
            result = await _direct_call_api(bot, api, data)
            if not future.done():
                future.set_result(result)
        except asyncio.CancelledError:
            if not future.done():
                future.set_exception(RuntimeError("send queue worker cancelled"))
            raise
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
            logger.warning(
                f"send queue failed: {api}",
                "SendQueue",
                target=getattr(bot, "self_id", None),
                e=exc,
            )
        finally:
            _QUEUE.task_done()


async def _queued_call_api(self: Bot, api: str, **data: Any):
    if api not in _SEND_APIS:
        return await _ORIG_CALL_API(self, api, **data)
    if _STOPPING:
        return await _direct_call_api(self, api, data)

    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()
    queue_item = (self, api, data, future)
    try:
        _QUEUE.put_nowait(queue_item)
    except asyncio.QueueFull:
        if api == "send_like":
            global _SEND_LIKE_DROP_COUNT
            _SEND_LIKE_DROP_COUNT += 1
            _log_queue_pressure("send_like dropped because send queue is full")
            return None
        global _QUEUE_TIMEOUT_COUNT
        _QUEUE_TIMEOUT_COUNT += 1
        _log_queue_pressure(f"{api} fallback to direct send because queue is full")
        return await _direct_call_api(self, api, data)
    return await future


def _drain_pending_futures(reason: str) -> int:
    drained = 0
    while True:
        try:
            _, _, _, future = _QUEUE.get_nowait()
        except asyncio.QueueEmpty:
            break
        if not future.done():
            future.set_exception(RuntimeError(reason))
        _QUEUE.task_done()
        drained += 1
    return drained


def patch_send_queue() -> None:
    global _PATCHED
    if _PATCHED:
        return
    Bot.call_api = _queued_call_api  # type: ignore[assignment]
    _PATCHED = True


def unpatch_send_queue() -> None:
    global _PATCHED
    if not _PATCHED:
        return
    Bot.call_api = _ORIG_CALL_API  # type: ignore[assignment]
    _PATCHED = False


async def start_send_queue() -> None:
    global _STOPPING
    patch_send_queue()
    _STOPPING = False
    if _WORKER_TASKS:
        return
    for idx in range(_WORKERS):
        _WORKER_TASKS.append(asyncio.create_task(_worker(idx)))


async def stop_send_queue() -> None:
    global _STOPPING
    _STOPPING = True
    try:
        await asyncio.wait_for(_QUEUE.join(), timeout=_SHUTDOWN_DRAIN_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        drained = _drain_pending_futures("send queue shutdown before drain completed")
        logger.warning(
            f"send queue shutdown timed out, dropped pending futures={drained}, "
            f"qsize={_QUEUE.qsize()}",
            "SendQueue",
        )
    tasks = _WORKER_TASKS.copy()
    _WORKER_TASKS.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    unpatch_send_queue()
    _STOPPING = False
