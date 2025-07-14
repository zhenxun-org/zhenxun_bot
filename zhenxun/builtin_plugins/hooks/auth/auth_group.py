import asyncio
import time

from nonebot_plugin_alconna import UniMsg

from zhenxun.models.group_console import GroupConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.data_access import DataAccess
from zhenxun.services.db_context import DB_TIMEOUT_SECONDS
from zhenxun.services.log import logger
from zhenxun.utils.utils import EntityIDs

from .config import LOGGER_COMMAND, WARNING_THRESHOLD, SwitchEnum
from .exception import SkipPluginException


async def auth_group(plugin: PluginInfo, entity: EntityIDs, message: UniMsg):
    """群黑名单检测 群总开关检测

    参数:
        plugin: PluginInfo
        entity: EntityIDs
        message: UniMsg
    """
    start_time = time.time()

    if not entity.group_id:
        return

    try:
        text = message.extract_plain_text()

        # 从数据库或缓存中获取群组信息
        group_dao = DataAccess(GroupConsole)

        try:
            group: GroupConsole | None = await asyncio.wait_for(
                group_dao.safe_get_or_none(
                    group_id=entity.group_id, channel_id__isnull=True
                ),
                timeout=DB_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.error("查询群组信息超时", LOGGER_COMMAND, session=entity.user_id)
            # 超时时不阻塞，继续执行
            return

        if not group:
            raise SkipPluginException("群组信息不存在...")
        if group.level < 0:
            raise SkipPluginException("群组黑名单, 目标群组群权限权限-1...")
        if text.strip() != SwitchEnum.ENABLE and not group.status:
            raise SkipPluginException("群组休眠状态...")
        if plugin.level > group.level:
            raise SkipPluginException(
                f"{plugin.name}({plugin.module}) 群等级限制，"
                f"该功能需要的群等级: {plugin.level}..."
            )
    finally:
        # 记录执行时间
        elapsed = time.time() - start_time
        if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
            logger.warning(
                f"auth_group 耗时: {elapsed:.3f}s, plugin={plugin.module}",
                LOGGER_COMMAND,
                session=entity.user_id,
                group_id=entity.group_id,
            )
