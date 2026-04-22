import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
import contextlib

from nonebot.log import default_filter, default_format

from zhenxun.services.log import logger_

LogListener = Callable[[str], Awaitable[None]]
DEFAULT_MAX_LOGS = 1000
DEFAULT_MAX_LISTENERS = 16


class LogStorage:
    """
    日志存储
    """

    def __init__(
        self,
        rotation: float = 5 * 60,
        max_logs: int = DEFAULT_MAX_LOGS,
        max_listeners: int = DEFAULT_MAX_LISTENERS,
    ):
        self.count, self.rotation = 0, rotation
        self.max_logs = max_logs
        self.max_listeners = max_listeners
        self.logs: dict[int, str] = {}
        self._order: deque[int] = deque()
        self.listeners: set[LogListener] = set()

    async def add(self, log: str):
        seq = self.count = self.count + 1
        self.logs[seq] = log
        self._order.append(seq)
        self._trim()
        asyncio.get_running_loop().call_later(self.rotation, self.remove, seq)
        listeners = tuple(self.listeners)
        if listeners:
            results = await asyncio.gather(
                *(listener(log) for listener in listeners),
                return_exceptions=True,
            )
            for listener, result in zip(listeners, results, strict=False):
                if isinstance(result, BaseException):
                    self.listeners.discard(listener)
        return seq

    def add_listener(self, listener: LogListener) -> bool:
        if len(self.listeners) >= self.max_listeners:
            return False
        self.listeners.add(listener)
        return True

    def remove_listener(self, listener: LogListener) -> None:
        self.listeners.discard(listener)

    def remove(self, seq: int):
        self.logs.pop(seq, None)
        with contextlib.suppress(ValueError):
            self._order.remove(seq)

    def _trim(self) -> None:
        while self._order and self._order[0] not in self.logs:
            self._order.popleft()
        while len(self.logs) > self.max_logs and self._order:
            self.logs.pop(self._order.popleft(), None)


LOG_STORAGE = LogStorage()

_LOG_SINK_ID: int | None = None


async def ensure_log_sink_started() -> None:
    global _LOG_SINK_ID
    if _LOG_SINK_ID is not None:
        return

    async def log_sink(message: str) -> None:
        await LOG_STORAGE.add(message.rstrip("\n"))

    _LOG_SINK_ID = logger_.add(
        log_sink,
        colorize=True,
        filter=default_filter,
        format=default_format,
    )


def stop_log_sink_if_idle() -> None:
    global _LOG_SINK_ID
    if LOG_STORAGE.listeners or _LOG_SINK_ID is None:
        return
    logger_.remove(_LOG_SINK_ID)
    _LOG_SINK_ID = None
