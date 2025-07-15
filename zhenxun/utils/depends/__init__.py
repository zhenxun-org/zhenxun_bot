from typing import Any, Literal

from nonebot.adapters import Bot, Event
from nonebot.internal.params import Depends
from nonebot.matcher import Matcher
from nonebot.params import Command
from nonebot.permission import SUPERUSER
from nonebot_plugin_session import EventSession
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.config import Config
from zhenxun.utils.limiters import ConcurrencyLimiter, FreqLimiter, RateLimiter
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.time_utils import TimeUtils

_coolers: dict[str, FreqLimiter] = {}
_rate_limiters: dict[str, RateLimiter] = {}
_concurrency_limiters: dict[str, ConcurrencyLimiter] = {}


def _create_limiter_dependency(
    limiter_class: type,
    limiter_storage: dict,
    limiter_init_args: dict[str, Any],
    scope: Literal["user", "group", "global"],
    prompt: str,
    **kwargs,
):
    """
    一个高阶函数，用于创建不同类型的限制器依赖。

    参数:
        limiter_class: 限制器类 (FreqLimiter, RateLimiter, etc.).
        limiter_storage: 用于存储限制器实例的字典.
        limiter_init_args: 限制器类的初始化参数.
        scope: 限制作用域.
        prompt: 触发限制时的提示信息.
        **kwargs: 传递给特定限制器逻辑的额外参数.
    """

    async def dependency(
        matcher: Matcher, session: EventSession, bot: Bot, event: Event
    ) -> bool:
        if await SUPERUSER(bot, event):
            return True

        handler_id = (
            f"{matcher.plugin_name}:{matcher.handlers[0].call.__code__.co_firstlineno}"
        )

        key: str | None = None
        if scope == "user":
            key = session.id1
        elif scope == "group":
            key = session.id3 or session.id2 or session.id1
        elif scope == "global":
            key = f"global_{handler_id}"

        if not key:
            return True

        if handler_id not in limiter_storage:
            limiter_storage[handler_id] = limiter_class(**limiter_init_args)
        limiter = limiter_storage[handler_id]

        if isinstance(limiter, ConcurrencyLimiter):
            await limiter.acquire(key)
            matcher.state["_concurrency_limiter_info"] = {
                "limiter": limiter,
                "key": key,
            }
            return True
        else:
            if limiter.check(key):
                if isinstance(limiter, FreqLimiter):
                    limiter.start_cd(
                        key, kwargs.get("duration_sec", limiter.default_cd)
                    )
                return True
            else:
                left_time = limiter.left_time(key)
                format_kwargs = {
                    "cd_str": TimeUtils.format_duration(left_time),
                    **(kwargs.get("prompt_format_kwargs", {})),
                }
                message = prompt.format(**format_kwargs)
                await matcher.finish(message)

    return Depends(dependency)


def Cooldown(
    duration: str,
    *,
    scope: Literal["user", "group", "global"] = "user",
    prompt: str = "操作过于频繁，请等待 {cd_str}",
) -> bool:
    """声明式冷却检查依赖，限制用户操作频率

    参数:
        duration: 冷却时间字符串 (e.g., "30s", "10m", "1h")
        scope: 冷却作用域
        prompt: 自定义的冷却提示消息，可使用 {cd_str} 占位符

    返回:
        bool: 是否允许执行
    """
    try:
        parsed_seconds = TimeUtils.parse_time_string(duration)
    except ValueError as e:
        raise ValueError(f"Cooldown装饰器中的duration格式错误: {e}")

    return _create_limiter_dependency(
        limiter_class=FreqLimiter,
        limiter_storage=_coolers,
        limiter_init_args={"default_cd_seconds": parsed_seconds},
        scope=scope,
        prompt=prompt,
        duration_sec=parsed_seconds,
    )


def RateLimit(
    count: int,
    duration: str,
    *,
    scope: Literal["user", "group", "global"] = "user",
    prompt: str = "太快了，在 {duration_str} 内只能触发{limit}次，请等待 {cd_str}",
) -> bool:
    """声明式速率限制依赖，在指定时间窗口内限制操作次数

    参数:
        count: 在时间窗口内允许的最大调用次数
        duration: 时间窗口字符串 (e.g., "1m", "1h")
        scope: 限制作用域
        prompt: 自定义的提示消息，可使用 {cd_str}, {duration_str}, {limit} 占位符

    返回:
        bool: 是否允许执行
    """
    try:
        parsed_seconds = TimeUtils.parse_time_string(duration)
    except ValueError as e:
        raise ValueError(f"RateLimit装饰器中的duration格式错误: {e}")

    return _create_limiter_dependency(
        limiter_class=RateLimiter,
        limiter_storage=_rate_limiters,
        limiter_init_args={"max_calls": count, "time_window": parsed_seconds},
        scope=scope,
        prompt=prompt,
        prompt_format_kwargs={"duration_str": duration, "limit": count},
    )


def ConcurrencyLimit(
    count: int,
    *,
    scope: Literal["user", "group", "global"] = "global",
    prompt: str | None = "当前功能繁忙，请稍后再试...",
) -> bool:
    """声明式并发数限制依赖，控制某个功能同时执行的实例数量

    参数:
        count: 最大并发数
        scope: 限制作用域
        prompt: 提示消息（暂未使用，主要用于未来扩展超时功能）

    返回:
        bool: 是否允许执行
    """
    return _create_limiter_dependency(
        limiter_class=ConcurrencyLimiter,
        limiter_storage=_concurrency_limiters,
        limiter_init_args={"max_concurrent": count},
        scope=scope,
        prompt=prompt or "",
    )


def CheckUg(check_user: bool = True, check_group: bool = True):
    """检测群组id和用户id是否存在

    参数:
        check_user: 检查用户id.
        check_group: 检查群组id.
    """

    async def dependency(session: EventSession):
        if check_user:
            user_id = session.id1
            if not user_id:
                await MessageUtils.build_message("用户id为空").finish()
        if check_group:
            group_id = session.id3 or session.id2
            if not group_id:
                await MessageUtils.build_message("群组id为空").finish()

    return Depends(dependency)


def OneCommand():
    """
    获取单个命令Command
    """

    async def dependency(
        cmd: tuple[str, ...] = Command(),
    ):
        return cmd[0] if cmd else None

    return Depends(dependency)


def UserName():
    """
    用户名称
    """

    async def dependency(user_info: Uninfo):
        return user_info.user.nick or user_info.user.name or ""

    return Depends(dependency)


def GetConfig(
    module: str | None = None,
    config: str = "",
    default_value: Any = None,
    prompt: str | None = None,
):
    """获取配置项

    参数:
        module: 模块名，为空时默认使用当前插件模块名
        config: 配置项名称
        default_value: 默认值
        prompt: 为空时提示
    """

    async def dependency(matcher: Matcher):
        module_ = module or matcher.plugin_name
        if module_:
            value = Config.get_config(module_, config, default_value)
            if value is None and prompt:
                await matcher.finish(prompt)
            return value

    return Depends(dependency)


def CheckConfig(
    module: str | None = None,
    config: str | list[str] = "",
    prompt: str | None = None,
):
    """检测配置项在配置文件中是否填写

    参数:
        module: 模块名，为空时默认使用当前插件模块名
        config: 需要检查的配置项名称
        prompt: 为空时提示
    """

    async def dependency(matcher: Matcher):
        module_ = module or matcher.plugin_name
        if module_:
            config_list = [config] if isinstance(config, str) else config
            for c in config_list:
                if Config.get_config(module_, c) is None:
                    await matcher.finish(prompt or f"配置项 {c} 未填写！")

    return Depends(dependency)
