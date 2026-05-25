import asyncio
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import time
from typing import Any, ClassVar, cast

from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot

from zhenxun.services.log import logger

_SEND_APIS = {"send_msg", "send_group_msg", "send_private_msg", "send_like"}
_OBSERVED_SEND_APIS = {"send_msg", "send_group_msg", "send_private_msg"}
_WORKERS = 3
_MIN_INTERVAL = 0.05
_QUEUE_MAXSIZE = 2000
_SHUTDOWN_DRAIN_TIMEOUT_SECONDS = 3.0
_QUEUE_PRESSURE_LOG_INTERVAL = 10.0
_QUEUE: asyncio.Queue[
    tuple[Bot, str, dict[str, Any], asyncio.Future[Any], str | None]
] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
_SEND_LOCK = asyncio.Lock()
_LAST_SEND_TS = 0.0
_API_SEMAPHORE = asyncio.Semaphore(3)
_ORIG_CALL_API = OneBotV11Adapter._call_api
_PATCHED = False
_WORKER_TASKS: list[asyncio.Task] = []
_QUEUE_TIMEOUT_COUNT = 0
_SEND_LIKE_DROP_COUNT = 0
_LAST_QUEUE_PRESSURE_LOG = 0.0
_STOPPING = False
_CURRENT_SEND_TRACE_ID: ContextVar[str | None] = ContextVar(
    "zhenxun_send_trace_id",
    default=None,
)
_MAX_OBSERVED_RECORDS_PER_TRACE = 12
_MAX_OBSERVED_TEXT_LEN = 900


def _send_platform_scope(adapter: Any) -> str:
    if adapter is None:
        return "unknown"
    if isinstance(adapter, OneBotV11Adapter):
        return "qq_client"
    get_name = getattr(adapter, "get_name", None)
    if callable(get_name):
        try:
            name = str(get_name()).lower()
        except Exception:
            name = ""
    else:
        name = adapter.__class__.__name__.lower()
    if name == "qq" or "qq" in name:
        return "qq_api"
    return name or "unknown"


@dataclass(frozen=True)
class SendObservation:
    trace_id: str
    api: str
    text: str
    raw_message: str
    result: Any
    timestamp: float


class SendObserver:
    _records: ClassVar[dict[str, list[SendObservation]]] = defaultdict(list)

    @classmethod
    @contextmanager
    def activate(cls, trace_id: str):
        trace_key = str(trace_id or "").strip()
        token = _CURRENT_SEND_TRACE_ID.set(trace_key or None)
        try:
            yield
        finally:
            _CURRENT_SEND_TRACE_ID.reset(token)

    @classmethod
    def record(
        cls,
        *,
        trace_id: str | None,
        api: str,
        data: dict[str, Any],
        result: Any,
    ) -> None:
        trace_key = str(trace_id or "").strip()
        if not trace_key or api not in _OBSERVED_SEND_APIS:
            return
        target = cls._records[trace_key]
        if len(target) >= _MAX_OBSERVED_RECORDS_PER_TRACE:
            return
        raw_message = _message_to_text(data.get("message"))
        target.append(
            SendObservation(
                trace_id=trace_key,
                api=api,
                text=_compact_text(raw_message),
                raw_message=raw_message[:_MAX_OBSERVED_TEXT_LEN],
                result=result,
                timestamp=time.time(),
            )
        )

    @classmethod
    def pop(cls, trace_id: str) -> list[SendObservation]:
        return cls._records.pop(str(trace_id or "").strip(), [])


def observe_send_trace(trace_id: str):
    return SendObserver.activate(trace_id)


def pop_send_observations(trace_id: str) -> list[SendObservation]:
    return SendObserver.pop(trace_id)


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


async def _direct_call_api(
    adapter: OneBotV11Adapter,
    bot: Bot,
    api: str,
    data: dict[str, Any],
    trace_id: str | None = None,
) -> Any:
    await _rate_limit()
    async with _API_SEMAPHORE:
        try:
            result = await _ORIG_CALL_API(
                adapter,
                cast(OneBotV11Bot, bot),
                api,
                **data,
            )
        except Exception as exc:
            SendObserver.record(
                trace_id=trace_id,
                api=api,
                data=data,
                result={"ok": False, "error": str(exc)},
            )
            raise
        SendObserver.record(trace_id=trace_id, api=api, data=data, result=result)
        return result


async def _worker(worker_id: int):
    while True:
        bot, api, data, future, trace_id = await _QUEUE.get()
        try:
            result = await _direct_call_api(
                cast(OneBotV11Adapter, bot.adapter),
                bot,
                api,
                data,
                trace_id=trace_id,
            )
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


async def _queued_call_api(
    adapter: OneBotV11Adapter,
    bot: Bot,
    api: str,
    **data: Any,
):
    if _send_platform_scope(adapter) != "qq_client":
        return await _ORIG_CALL_API(adapter, cast(OneBotV11Bot, bot), api, **data)
    if api not in _SEND_APIS:
        return await _ORIG_CALL_API(adapter, cast(OneBotV11Bot, bot), api, **data)
    if _STOPPING:
        return await _direct_call_api(
            adapter,
            bot,
            api,
            data,
            trace_id=_CURRENT_SEND_TRACE_ID.get(),
        )

    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()
    queue_item = (bot, api, data, future, _CURRENT_SEND_TRACE_ID.get())
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
        return await _direct_call_api(
            adapter,
            bot,
            api,
            data,
            trace_id=_CURRENT_SEND_TRACE_ID.get(),
        )
    return await future


def _drain_pending_futures(reason: str) -> int:
    drained = 0
    while True:
        try:
            _, _, _, future, _ = _QUEUE.get_nowait()
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
    OneBotV11Adapter._call_api = _queued_call_api  # type: ignore[assignment]
    _PATCHED = True


def unpatch_send_queue() -> None:
    global _PATCHED
    if not _PATCHED:
        return
    OneBotV11Adapter._call_api = _ORIG_CALL_API  # type: ignore[assignment]
    _PATCHED = False


async def start_send_queue() -> None:
    global _STOPPING
    patch_send_queue()
    _STOPPING = False
    _WORKER_TASKS[:] = [task for task in _WORKER_TASKS if not task.done()]
    if _WORKER_TASKS:
        return
    for idx in range(_WORKERS):
        _WORKER_TASKS.append(asyncio.create_task(_worker(idx)))


def _message_to_text(message: Any) -> str:
    if message is None:
        return ""
    if hasattr(message, "extract_plain_text"):
        try:
            text = str(message.extract_plain_text())
            if text.strip():
                return text
        except Exception:
            pass
    try:
        return str(message)
    except Exception as exc:
        logger.debug(f"send observation stringify failed: {exc}")
        return ""


def _compact_text(text: str) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= _MAX_OBSERVED_TEXT_LEN:
        return normalized
    return normalized[: _MAX_OBSERVED_TEXT_LEN - 1].rstrip() + "…"


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
