import asyncio
from collections import defaultdict, deque
import time
from typing import Any


class FreqLimiter:
    """
    命令冷却，检测用户是否处于冷却状态
    """

    def __init__(self, default_cd_seconds: int):
        self.next_time: dict[Any, float] = defaultdict(float)
        self.default_cd = default_cd_seconds

    def check(self, key: Any) -> bool:
        return time.time() >= self.next_time[key]

    def start_cd(self, key: Any, cd_time: int = 0):
        self.next_time[key] = time.time() + (
            cd_time if cd_time > 0 else self.default_cd
        )

    def left_time(self, key: Any) -> float:
        return max(0.0, self.next_time[key] - time.time())


class CountLimiter:
    """
    每日调用命令次数限制
    """

    tz = None

    def __init__(self, max_num: int):
        self.today = -1
        self.count: dict[Any, int] = defaultdict(int)
        self.max = max_num

    def check(self, key: Any) -> bool:
        import datetime

        day = datetime.datetime.now().day
        if day != self.today:
            self.today = day
            self.count.clear()
        return self.count[key] < self.max

    def get_num(self, key: Any) -> int:
        return self.count[key]

    def increase(self, key: Any, num: int = 1):
        self.count[key] += num

    def reset(self, key: Any):
        self.count[key] = 0


class UserBlockLimiter:
    """
    检测用户是否正在调用命令 (简单阻塞锁)
    """

    def __init__(self):
        self.flag_data: dict[Any, bool] = defaultdict(bool)
        self.time: dict[Any, float] = defaultdict(float)

    def set_true(self, key: Any):
        self.time[key] = time.time()
        self.flag_data[key] = True

    def set_false(self, key: Any):
        self.flag_data[key] = False

    def check(self, key: Any) -> bool:
        if self.flag_data[key] and time.time() - self.time[key] > 30:
            self.set_false(key)
        return not self.flag_data[key]


class RateLimiter:
    """
    一个简单的基于时间窗口的速率限制器。
    """

    def __init__(self, max_calls: int, time_window: int):
        self.requests: dict[Any, deque[float]] = defaultdict(deque)
        self.max_calls = max_calls
        self.time_window = time_window

    def check(self, key: Any) -> bool:
        """检查是否超出速率限制。如果未超出，则记录本次调用。"""
        now = time.time()

        while self.requests[key] and self.requests[key][0] <= now - self.time_window:
            self.requests[key].popleft()

        if len(self.requests[key]) < self.max_calls:
            self.requests[key].append(now)
            return True
        return False

    def left_time(self, key: Any) -> float:
        """计算距离下次可调用还需等待的时间"""
        if self.requests[key]:
            return max(0.0, self.requests[key][0] + self.time_window - time.time())
        return 0.0


class ConcurrencyLimiter:
    """
    一个基于 asyncio.Semaphore 的并发限制器。
    """

    def __init__(self, max_concurrent: int):
        self._semaphores: dict[Any, asyncio.Semaphore] = {}
        self.max_concurrent = max_concurrent
        self._active_tasks: dict[Any, int] = defaultdict(int)

    def _get_semaphore(self, key: Any) -> asyncio.Semaphore:
        if key not in self._semaphores:
            self._semaphores[key] = asyncio.Semaphore(self.max_concurrent)
        return self._semaphores[key]

    async def acquire(self, key: Any):
        """获取一个信号量，如果达到并发上限则会阻塞等待。"""
        semaphore = self._get_semaphore(key)
        await semaphore.acquire()
        self._active_tasks[key] += 1

    def release(self, key: Any):
        """释放一个信号量。"""
        if key in self._semaphores:
            if self._active_tasks[key] > 0:
                self._semaphores[key].release()
                self._active_tasks[key] -= 1
            else:
                import logging

                logging.warning(f"尝试释放键 '{key}' 的信号量时，计数已经为零。")
