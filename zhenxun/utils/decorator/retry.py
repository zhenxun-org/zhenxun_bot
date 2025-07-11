from collections.abc import Callable
from functools import partial, wraps
from typing import Any, Literal

from anyio import EndOfStream
from httpx import (
    ConnectError,
    HTTPStatusError,
    RemoteProtocolError,
    StreamError,
    TimeoutException,
)
from nonebot.utils import is_coroutine_callable
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
    wait_fixed,
)

from zhenxun.services.log import logger

LOG_COMMAND = "RetryDecorator"
_SENTINEL = object()


def _log_before_sleep(log_name: str | None, retry_state: RetryCallState):
    """
    tenacity 重试前的日志记录回调函数。
    """
    func_name = retry_state.fn.__name__ if retry_state.fn else "unknown_function"
    log_context = f"函数 '{func_name}'"
    if log_name:
        log_context = f"操作 '{log_name}' ({log_context})"

    reason = ""
    if retry_state.outcome:
        if exc := retry_state.outcome.exception():
            reason = f"触发异常: {exc.__class__.__name__}({exc})"
        else:
            reason = f"不满足结果条件: result={retry_state.outcome.result()}"

    wait_time = (
        getattr(retry_state.next_action, "sleep", 0) if retry_state.next_action else 0
    )
    logger.warning(
        f"{log_context} 第 {retry_state.attempt_number} 次重试... "
        f"等待 {wait_time:.2f} 秒. {reason}",
        LOG_COMMAND,
    )


class Retry:
    @staticmethod
    def simple(
        stop_max_attempt: int = 3,
        wait_fixed_seconds: int = 2,
        exception: tuple[type[Exception], ...] = (),
        *,
        log_name: str | None = None,
        on_failure: Callable[[Exception], Any] | None = None,
        return_on_failure: Any = _SENTINEL,
    ):
        """
        一个简单的、用于通用网络请求的重试装饰器预设。

        参数:
            stop_max_attempt: 最大重试次数。
            wait_fixed_seconds: 固定等待策略的等待秒数。
            exception: 额外需要重试的异常类型元组。
            log_name: 用于日志记录的操作名称。
            on_failure: (可选) 所有重试失败后的回调。
            return_on_failure: (可选) 所有重试失败后的返回值。
        """
        return Retry.api(
            stop_max_attempt=stop_max_attempt,
            wait_fixed_seconds=wait_fixed_seconds,
            exception=exception,
            strategy="fixed",
            log_name=log_name,
            on_failure=on_failure,
            return_on_failure=return_on_failure,
        )

    @staticmethod
    def download(
        stop_max_attempt: int = 3,
        exception: tuple[type[Exception], ...] = (),
        *,
        wait_exp_multiplier: int = 2,
        wait_exp_max: int = 15,
        log_name: str | None = None,
        on_failure: Callable[[Exception], Any] | None = None,
        return_on_failure: Any = _SENTINEL,
    ):
        """
        一个适用于文件下载的重试装饰器预设，使用指数退避策略。

        参数:
            stop_max_attempt: 最大重试次数。
            exception: 额外需要重试的异常类型元组。
            wait_exp_multiplier: 指数退避的乘数。
            wait_exp_max: 指数退避的最大等待时间。
            log_name: 用于日志记录的操作名称。
            on_failure: (可选) 所有重试失败后的回调。
            return_on_failure: (可选) 所有重试失败后的返回值。
        """
        return Retry.api(
            stop_max_attempt=stop_max_attempt,
            exception=exception,
            strategy="exponential",
            wait_exp_multiplier=wait_exp_multiplier,
            wait_exp_max=wait_exp_max,
            log_name=log_name,
            on_failure=on_failure,
            return_on_failure=return_on_failure,
        )

    @staticmethod
    def api(
        stop_max_attempt: int = 3,
        wait_fixed_seconds: int = 1,
        exception: tuple[type[Exception], ...] = (),
        *,
        strategy: Literal["fixed", "exponential"] = "fixed",
        retry_on_result: Callable[[Any], bool] | None = None,
        wait_exp_multiplier: int = 1,
        wait_exp_max: int = 10,
        log_name: str | None = None,
        on_failure: Callable[[Exception], Any] | None = None,
        return_on_failure: Any = _SENTINEL,
    ):
        """
        通用、可配置的API调用重试装饰器。

        参数:
            stop_max_attempt: 最大重试次数。
            wait_fixed_seconds: 固定等待策略的等待秒数。
            exception: 额外需要重试的异常类型元组。
            strategy: 重试等待策略, 'fixed' (固定) 或 'exponential' (指数退避)。
            retry_on_result: 一个回调函数，接收函数返回值。如果返回 True，则触发重试。
                             例如 `lambda r: r.status_code != 200`
            wait_exp_multiplier: 指数退避的乘数。
            wait_exp_max: 指数退避的最大等待时间。
            log_name: 用于日志记录的操作名称，方便区分不同的重试场景。
            on_failure: (可选) 当所有重试都失败后，在抛出异常或返回默认值之前，
                        会调用此函数，并将最终的异常实例作为参数传入。
            return_on_failure: (可选) 如果设置了此参数，当所有重试失败后，
                              将不再抛出异常，而是返回此参数指定的值。
        """
        base_exceptions = (
            TimeoutException,
            ConnectError,
            HTTPStatusError,
            StreamError,
            RemoteProtocolError,
            EndOfStream,
            *exception,
        )

        def decorator(func: Callable) -> Callable:
            if strategy == "exponential":
                wait_strategy = wait_exponential(
                    multiplier=wait_exp_multiplier, max=wait_exp_max
                )
            else:
                wait_strategy = wait_fixed(wait_fixed_seconds)

            retry_conditions = retry_if_exception_type(base_exceptions)
            if retry_on_result:
                retry_conditions |= retry_if_result(retry_on_result)

            log_callback = partial(_log_before_sleep, log_name)

            tenacity_retry_decorator = retry(
                stop=stop_after_attempt(stop_max_attempt),
                wait=wait_strategy,
                retry=retry_conditions,
                before_sleep=log_callback,
                reraise=True,
            )

            decorated_func = tenacity_retry_decorator(func)

            if return_on_failure is _SENTINEL:
                return decorated_func

            if is_coroutine_callable(func):

                @wraps(func)
                async def async_wrapper(*args, **kwargs):
                    try:
                        return await decorated_func(*args, **kwargs)
                    except Exception as e:
                        if on_failure:
                            if is_coroutine_callable(on_failure):
                                await on_failure(e)
                            else:
                                on_failure(e)
                        return return_on_failure

                return async_wrapper
            else:

                @wraps(func)
                def sync_wrapper(*args, **kwargs):
                    try:
                        return decorated_func(*args, **kwargs)
                    except Exception as e:
                        if on_failure:
                            if is_coroutine_callable(on_failure):
                                logger.error(
                                    f"不能在同步函数 '{func.__name__}' 中调用异步的 "
                                    f"on_failure 回调。",
                                    LOG_COMMAND,
                                )
                            else:
                                on_failure(e)
                        return return_on_failure

                return sync_wrapper

        return decorator
