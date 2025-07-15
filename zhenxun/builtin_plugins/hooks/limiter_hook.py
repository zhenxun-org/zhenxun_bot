from nonebot.matcher import Matcher
from nonebot.message import run_postprocessor

from zhenxun.utils.limiters import ConcurrencyLimiter


@run_postprocessor
async def _concurrency_release_hook(matcher: Matcher):
    """
    后处理器：在事件处理结束后，释放并发限制的信号量。
    """
    if concurrency_info := matcher.state.get("_concurrency_limiter_info"):
        limiter: ConcurrencyLimiter = concurrency_info["limiter"]
        key = concurrency_info["key"]
        limiter.release(key)
