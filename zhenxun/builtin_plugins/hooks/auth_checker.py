import asyncio
import time

from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot_plugin_alconna import UniMsg
from nonebot_plugin_uninfo import Uninfo
from tortoise.exceptions import IntegrityError

from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.user_console import UserConsole
from zhenxun.services.data_access import DataAccess
from zhenxun.services.log import logger
from zhenxun.utils.enum import GoldHandle, PluginType
from zhenxun.utils.exception import InsufficientGold
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.utils import get_entity_ids

from .auth.auth_admin import auth_admin
from .auth.auth_ban import auth_ban
from .auth.auth_bot import auth_bot
from .auth.auth_cost import auth_cost
from .auth.auth_group import auth_group
from .auth.auth_limit import LimitManager, auth_limit
from .auth.auth_plugin import auth_plugin
from .auth.bot_filter import bot_filter
from .auth.config import LOGGER_COMMAND, WARNING_THRESHOLD
from .auth.exception import (
    IsSuperuserException,
    PermissionExemption,
    SkipPluginException,
)

# 超时设置（秒）
TIMEOUT_SECONDS = 5.0
# 熔断计数器
CIRCUIT_BREAKERS = {
    "auth_ban": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
    "auth_bot": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
    "auth_group": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
    "auth_admin": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
    "auth_plugin": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
    "auth_limit": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
}
# 熔断重置时间（秒）
CIRCUIT_RESET_TIME = 300  # 5分钟


# 超时装饰器
async def with_timeout(coro, timeout=TIMEOUT_SECONDS, name=None):
    """带超时控制的协程执行

    参数:
        coro: 要执行的协程
        timeout: 超时时间（秒）
        name: 操作名称，用于日志记录

    返回:
        协程的返回值，或者在超时时抛出 TimeoutError
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        if name:
            logger.error(f"{name} 操作超时 (>{timeout}s)", LOGGER_COMMAND)
            # 更新熔断计数器
            if name in CIRCUIT_BREAKERS:
                CIRCUIT_BREAKERS[name]["failures"] += 1
                if (
                    CIRCUIT_BREAKERS[name]["failures"]
                    >= CIRCUIT_BREAKERS[name]["threshold"]
                    and not CIRCUIT_BREAKERS[name]["active"]
                ):
                    CIRCUIT_BREAKERS[name]["active"] = True
                    CIRCUIT_BREAKERS[name]["reset_time"] = (
                        time.time() + CIRCUIT_RESET_TIME
                    )
                    logger.warning(
                        f"{name} 熔断器已激活，将在 {CIRCUIT_RESET_TIME} 秒后重置",
                        LOGGER_COMMAND,
                    )
        raise


# 检查熔断状态
def check_circuit_breaker(name):
    """检查熔断器状态

    参数:
        name: 操作名称

    返回:
        bool: 是否已熔断
    """
    if name not in CIRCUIT_BREAKERS:
        return False

    # 检查是否需要重置熔断器
    if (
        CIRCUIT_BREAKERS[name]["active"]
        and time.time() > CIRCUIT_BREAKERS[name]["reset_time"]
    ):
        CIRCUIT_BREAKERS[name]["active"] = False
        CIRCUIT_BREAKERS[name]["failures"] = 0
        logger.info(f"{name} 熔断器已重置", LOGGER_COMMAND)

    return CIRCUIT_BREAKERS[name]["active"]


async def get_plugin_and_user(
    module: str, user_id: str
) -> tuple[PluginInfo, UserConsole]:
    """获取用户数据和插件信息

    参数:
        module: 模块名
        user_id: 用户id

    异常:
        PermissionExemption: 插件数据不存在
        PermissionExemption: 插件类型为HIDDEN
        PermissionExemption: 重复创建用户
        PermissionExemption: 用户数据不存在

    返回:
        tuple[PluginInfo, UserConsole]: 插件信息，用户信息
    """
    user_dao = DataAccess(UserConsole)
    plugin_dao = DataAccess(PluginInfo)

    # 并行查询插件和用户数据
    plugin_task = plugin_dao.safe_get_or_none(module=module)
    user_task = user_dao.get_by_func_or_none(
        UserConsole.get_user, False, user_id=user_id
    )

    try:
        plugin, user = await with_timeout(
            asyncio.gather(plugin_task, user_task), name="get_plugin_and_user"
        )
    except asyncio.TimeoutError:
        # 如果并行查询超时，尝试串行查询
        logger.warning("并行查询超时，尝试串行查询", LOGGER_COMMAND)
        plugin = await with_timeout(
            plugin_dao.safe_get_or_none(module=module), name="get_plugin"
        )
        user = await with_timeout(
            user_dao.safe_get_or_none(user_id=user_id), name="get_user"
        )

    if not plugin:
        raise PermissionExemption(f"插件:{module} 数据不存在，已跳过权限检查...")
    if plugin.plugin_type == PluginType.HIDDEN:
        raise PermissionExemption(
            f"插件: {plugin.name}:{plugin.module} 为HIDDEN，已跳过权限检查..."
        )
    user = None
    try:
        user = await user_dao.get_by_func_or_none(
            UserConsole.get_user, False, user_id=user_id
        )
    except IntegrityError as e:
        raise PermissionExemption("重复创建用户，已跳过该次权限检查...") from e
    if not user:
        raise PermissionExemption("用户数据不存在，已跳过权限检查...")
    return plugin, user


async def get_plugin_cost(
    bot: Bot, user: UserConsole, plugin: PluginInfo, session: Uninfo
) -> int:
    """获取插件费用

    参数:
        bot: Bot
        user: 用户数据
        plugin: 插件数据
        session: Uninfo

    异常:
        IsSuperuserException: 超级用户
        IsSuperuserException: 超级用户

    返回:
        int: 调用插件金币费用
    """
    cost_gold = await with_timeout(auth_cost(user, plugin, session), name="auth_cost")
    if session.user.id in bot.config.superusers:
        if plugin.plugin_type == PluginType.SUPERUSER:
            raise IsSuperuserException()
        if not plugin.limit_superuser:
            raise IsSuperuserException()
    return cost_gold


async def reduce_gold(user_id: str, module: str, cost_gold: int, session: Uninfo):
    """扣除用户金币

    参数:
        user_id: 用户id
        module: 插件模块名称
        cost_gold: 消耗金币
        session: Uninfo
    """
    user_dao = DataAccess(UserConsole)
    try:
        await with_timeout(
            UserConsole.reduce_gold(
                user_id,
                cost_gold,
                GoldHandle.PLUGIN,
                module,
                PlatformUtils.get_platform(session),
            ),
            name="reduce_gold",
        )
    except InsufficientGold:
        if u := await UserConsole.get_user(user_id):
            u.gold = 0
            await u.save(update_fields=["gold"])
    except asyncio.TimeoutError:
        logger.error(
            f"扣除金币超时，用户: {user_id}, 金币: {cost_gold}",
            LOGGER_COMMAND,
            session=session,
        )

    # 清除缓存，使下次查询时从数据库获取最新数据
    await user_dao.clear_cache(user_id=user_id)
    logger.debug(f"调用功能花费金币: {cost_gold}", LOGGER_COMMAND, session=session)


# 辅助函数，用于记录每个 hook 的执行时间
async def time_hook(coro, name, time_dict):
    start = time.time()
    try:
        # 检查熔断状态
        if check_circuit_breaker(name):
            logger.info(f"{name} 熔断器激活中，跳过执行", LOGGER_COMMAND)
            time_dict[name] = "熔断跳过"
            return

        # 添加超时控制
        return await with_timeout(coro, name=name)
    except asyncio.TimeoutError:
        time_dict[name] = f"超时 (>{TIMEOUT_SECONDS}s)"
    finally:
        if name not in time_dict:
            time_dict[name] = f"{time.time() - start:.3f}s"


async def auth(
    matcher: Matcher,
    event: Event,
    bot: Bot,
    session: Uninfo,
    message: UniMsg,
):
    """权限检查

    参数:
        matcher: matcher
        event: Event
        bot: bot
        session: Uninfo
        message: UniMsg
    """
    start_time = time.time()
    cost_gold = 0
    ignore_flag = False
    entity = get_entity_ids(session)
    module = matcher.plugin_name or ""

    # 用于记录各个 hook 的执行时间
    hook_times = {}
    hooks_time = 0  # 初始化 hooks_time 变量

    try:
        if not module:
            raise PermissionExemption("Matcher插件名称不存在...")

        # 获取插件和用户数据
        plugin_user_start = time.time()
        try:
            plugin, user = await with_timeout(
                get_plugin_and_user(module, entity.user_id), name="get_plugin_and_user"
            )
            hook_times["get_plugin_user"] = f"{time.time() - plugin_user_start:.3f}s"
        except asyncio.TimeoutError:
            logger.error(
                f"获取插件和用户数据超时，模块: {module}",
                LOGGER_COMMAND,
                session=session,
            )
            raise PermissionExemption("获取插件和用户数据超时，请稍后再试...")

        # 获取插件费用
        cost_start = time.time()
        try:
            cost_gold = await with_timeout(
                get_plugin_cost(bot, user, plugin, session), name="get_plugin_cost"
            )
            hook_times["cost_gold"] = f"{time.time() - cost_start:.3f}s"
        except asyncio.TimeoutError:
            logger.error(
                f"获取插件费用超时，模块: {module}", LOGGER_COMMAND, session=session
            )
            # 继续执行，不阻止权限检查

        # 执行 bot_filter
        bot_filter(session)

        # 并行执行所有 hook 检查，并记录执行时间
        hooks_start = time.time()

        # 创建所有 hook 任务
        hook_tasks = [
            time_hook(auth_ban(matcher, bot, session), "auth_ban", hook_times),
            time_hook(auth_bot(plugin, bot.self_id), "auth_bot", hook_times),
            time_hook(auth_group(plugin, entity, message), "auth_group", hook_times),
            time_hook(auth_admin(plugin, session), "auth_admin", hook_times),
            time_hook(auth_plugin(plugin, session, event), "auth_plugin", hook_times),
            time_hook(auth_limit(plugin, session), "auth_limit", hook_times),
        ]

        # 使用 gather 并行执行所有 hook，但添加总体超时控制
        try:
            await with_timeout(
                asyncio.gather(*hook_tasks),
                timeout=TIMEOUT_SECONDS * 2,  # 给总体执行更多时间
                name="auth_hooks_gather",
            )
        except asyncio.TimeoutError:
            logger.error(
                f"权限检查 hooks 总体执行超时，模块: {module}",
                LOGGER_COMMAND,
                session=session,
            )
            # 不抛出异常，允许继续执行

        hooks_time = time.time() - hooks_start

    except SkipPluginException as e:
        LimitManager.unblock(module, entity.user_id, entity.group_id, entity.channel_id)
        logger.info(str(e), LOGGER_COMMAND, session=session)
        ignore_flag = True
    except IsSuperuserException:
        logger.debug("超级用户跳过权限检测...", LOGGER_COMMAND, session=session)
    except PermissionExemption as e:
        logger.info(str(e), LOGGER_COMMAND, session=session)

    # 扣除金币
    if not ignore_flag and cost_gold > 0:
        gold_start = time.time()
        try:
            await with_timeout(
                reduce_gold(entity.user_id, module, cost_gold, session),
                name="reduce_gold",
            )
            hook_times["reduce_gold"] = f"{time.time() - gold_start:.3f}s"
        except asyncio.TimeoutError:
            logger.error(
                f"扣除金币超时，模块: {module}", LOGGER_COMMAND, session=session
            )

    # 记录总执行时间
    total_time = time.time() - start_time
    if total_time > WARNING_THRESHOLD:  # 如果总时间超过500ms，记录详细信息
        logger.warning(
            f"权限检查耗时过长: {total_time:.3f}s, 模块: {module}, "
            f"hooks时间: {hooks_time:.3f}s, "
            f"详情: {hook_times}",
            LOGGER_COMMAND,
            session=session,
        )

    if ignore_flag:
        raise IgnoredException("权限检测 ignore")
