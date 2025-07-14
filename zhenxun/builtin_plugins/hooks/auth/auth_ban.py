import asyncio
import time

from nonebot.adapters import Bot
from nonebot.matcher import Matcher
from nonebot_plugin_alconna import At
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.config import Config
from zhenxun.models.ban_console import BanConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.data_access import DataAccess
from zhenxun.services.db_context import DB_TIMEOUT_SECONDS
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.utils import EntityIDs, get_entity_ids

from .config import LOGGER_COMMAND, WARNING_THRESHOLD
from .exception import SkipPluginException
from .utils import freq, send_message

Config.add_plugin_config(
    "hook",
    "BAN_RESULT",
    "才不会给你发消息.",
    help="对被ban用户发送的消息",
)


def calculate_ban_time(ban_record: BanConsole | None) -> int:
    """根据ban记录计算剩余ban时间

    参数:
        ban_record: BanConsole记录

    返回:
        int: ban剩余时长，-1时为永久ban，0表示未被ban
    """
    if not ban_record:
        return 0

    if ban_record.duration == -1:
        return -1

    _time = time.time() - (ban_record.ban_time + ban_record.duration)
    return 0 if _time > 0 else int(abs(_time))


async def is_ban(user_id: str | None, group_id: str | None) -> int:
    """检查用户或群组是否被ban

    参数:
        user_id: 用户ID
        group_id: 群组ID

    返回:
        int: ban的剩余时间，0表示未被ban
    """
    if not user_id and not group_id:
        return 0

    start_time = time.time()
    ban_dao = DataAccess(BanConsole)

    # 分别获取用户在群组中的ban记录和全局ban记录
    group_user = None
    user = None

    try:
        # 并行查询用户和群组的 ban 记录
        tasks = []
        if user_id and group_id:
            tasks.append(ban_dao.safe_get_or_none(user_id=user_id, group_id=group_id))
        if user_id:
            tasks.append(
                ban_dao.safe_get_or_none(user_id=user_id, group_id__isnull=True)
            )

        # 等待所有查询完成，添加超时控制
        if tasks:
            try:
                ban_records = await asyncio.wait_for(
                    asyncio.gather(*tasks), timeout=DB_TIMEOUT_SECONDS
                )
                if len(tasks) == 2:
                    group_user, user = ban_records
                elif user_id and group_id:
                    group_user = ban_records[0]
                else:
                    user = ban_records[0]
            except asyncio.TimeoutError:
                logger.error(
                    f"查询ban记录超时: user_id={user_id}, group_id={group_id}",
                    LOGGER_COMMAND,
                )
                # 超时时返回0，避免阻塞
                return 0

        # 检查记录并计算ban时间
        results = []
        if group_user:
            results.append(group_user)
        if user:
            results.append(user)

        # 如果没有找到记录，返回0
        if not results:
            return 0

        logger.debug(f"查询到的ban记录: {results}", LOGGER_COMMAND)
        # 检查所有记录，找出最严格的ban（时间最长的）
        max_ban_time: int = 0
        for result in results:
            if result.duration > 0 or result.duration == -1:
                # 直接计算ban时间，避免再次查询数据库
                ban_time = calculate_ban_time(result)
                if ban_time == -1 or ban_time > max_ban_time:
                    max_ban_time = ban_time

        return max_ban_time
    finally:
        # 记录执行时间
        elapsed = time.time() - start_time
        if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
            logger.warning(
                f"is_ban 耗时: {elapsed:.3f}s",
                LOGGER_COMMAND,
                session=user_id,
                group_id=group_id,
            )


def check_plugin_type(matcher: Matcher) -> bool:
    """判断插件类型是否是隐藏插件

    参数:
        matcher: Matcher

    返回:
        bool: 是否为隐藏插件
    """
    if plugin := matcher.plugin:
        if metadata := plugin.metadata:
            extra = metadata.extra
            if extra.get("plugin_type") in [PluginType.HIDDEN]:
                return False
    return True


def format_time(time_val: float) -> str:
    """格式化时间

    参数:
        time_val: ban时长

    返回:
        str: 格式化时间文本
    """
    if time_val == -1:
        return "∞"
    time_val = abs(int(time_val))
    if time_val < 60:
        time_str = f"{time_val!s} 秒"
    else:
        minute = int(time_val / 60)
        if minute > 60:
            hours = minute // 60
            minute %= 60
            time_str = f"{hours} 小时 {minute}分钟"
        else:
            time_str = f"{minute} 分钟"
    return time_str


async def group_handle(group_id: str) -> None:
    """群组ban检查

    参数:
        group_id: 群组id

    异常:
        SkipPluginException: 群组处于黑名单
    """
    start_time = time.time()
    try:
        if await is_ban(None, group_id):
            raise SkipPluginException("群组处于黑名单中...")
    finally:
        # 记录执行时间
        elapsed = time.time() - start_time
        if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
            logger.warning(
                f"group_handle 耗时: {elapsed:.3f}s",
                LOGGER_COMMAND,
                group_id=group_id,
            )


async def user_handle(module: str, entity: EntityIDs, session: Uninfo) -> None:
    """用户ban检查

    参数:
        module: 插件模块名
        entity: 实体ID信息
        session: Uninfo

    异常:
        SkipPluginException: 用户处于黑名单
    """
    start_time = time.time()
    try:
        ban_result = Config.get_config("hook", "BAN_RESULT")
        time_val = await is_ban(entity.user_id, entity.group_id)
        if not time_val:
            return
        time_str = format_time(time_val)
        plugin_dao = DataAccess(PluginInfo)
        try:
            db_plugin = await asyncio.wait_for(
                plugin_dao.safe_get_or_none(module=module), timeout=DB_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            logger.error(f"查询插件信息超时: {module}", LOGGER_COMMAND)
            # 超时时不阻塞，继续执行
            raise SkipPluginException("用户处于黑名单中...")

        if (
            db_plugin
            and not db_plugin.ignore_prompt
            and time_val != -1
            and ban_result
            and freq.is_send_limit_message(db_plugin, entity.user_id, False)
        ):
            try:
                await asyncio.wait_for(
                    send_message(
                        session,
                        [
                            At(flag="user", target=entity.user_id),
                            f"{ban_result}\n在..在 {time_str} 后才会理你喔",
                        ],
                        entity.user_id,
                    ),
                    timeout=DB_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error(f"发送消息超时: {entity.user_id}", LOGGER_COMMAND)
        raise SkipPluginException("用户处于黑名单中...")
    finally:
        # 记录执行时间
        elapsed = time.time() - start_time
        if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
            logger.warning(
                f"user_handle 耗时: {elapsed:.3f}s",
                LOGGER_COMMAND,
                session=session,
            )


async def auth_ban(matcher: Matcher, bot: Bot, session: Uninfo) -> None:
    """权限检查 - ban 检查

    参数:
        matcher: Matcher
        bot: Bot
        session: Uninfo
    """
    start_time = time.time()
    try:
        if not check_plugin_type(matcher):
            return
        if not matcher.plugin_name:
            return
        entity = get_entity_ids(session)
        if entity.user_id in bot.config.superusers:
            return
        if entity.group_id:
            try:
                await asyncio.wait_for(
                    group_handle(entity.group_id), timeout=DB_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.error(f"群组ban检查超时: {entity.group_id}", LOGGER_COMMAND)
                # 超时时不阻塞，继续执行

        if entity.user_id:
            try:
                await asyncio.wait_for(
                    user_handle(matcher.plugin_name, entity, session),
                    timeout=DB_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error(f"用户ban检查超时: {entity.user_id}", LOGGER_COMMAND)
                # 超时时不阻塞，继续执行
    finally:
        # 记录总执行时间
        elapsed = time.time() - start_time
        if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
            logger.warning(
                f"auth_ban 总耗时: {elapsed:.3f}s, plugin={matcher.plugin_name}",
                LOGGER_COMMAND,
                session=session,
            )
