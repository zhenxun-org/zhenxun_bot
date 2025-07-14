import asyncio
import time

from nonebot_plugin_alconna import At
from nonebot_plugin_uninfo import Uninfo

from zhenxun.models.level_user import LevelUser
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.data_access import DataAccess
from zhenxun.services.db_context import DB_TIMEOUT_SECONDS
from zhenxun.services.log import logger
from zhenxun.utils.utils import get_entity_ids

from .config import LOGGER_COMMAND, WARNING_THRESHOLD
from .exception import SkipPluginException
from .utils import send_message


async def auth_admin(plugin: PluginInfo, session: Uninfo):
    """管理员命令 个人权限

    参数:
        plugin: PluginInfo
        session: Uninfo
    """
    start_time = time.time()

    if not plugin.admin_level:
        return

    try:
        entity = get_entity_ids(session)
        level_dao = DataAccess(LevelUser)

        # 并行查询用户权限数据
        global_user: LevelUser | None = None
        group_users: LevelUser | None = None

        # 查询全局权限
        global_user_task = level_dao.safe_get_or_none(
            user_id=session.user.id, group_id__isnull=True
        )

        # 如果在群组中，查询群组权限
        group_users_task = None
        if entity.group_id:
            group_users_task = level_dao.safe_get_or_none(
                user_id=session.user.id, group_id=entity.group_id
            )

        # 等待查询完成，添加超时控制
        try:
            results = await asyncio.wait_for(
                asyncio.gather(global_user_task, group_users_task or asyncio.sleep(0)),
                timeout=DB_TIMEOUT_SECONDS,
            )
            global_user = results[0]
            group_users = results[1] if group_users_task else None
        except asyncio.TimeoutError:
            logger.error(f"查询用户权限超时: user_id={session.user.id}", LOGGER_COMMAND)
            # 超时时不阻塞，继续执行
            return

        user_level = global_user.user_level if global_user else 0
        if entity.group_id and group_users:
            user_level = max(user_level, group_users.user_level)

            if user_level < plugin.admin_level:
                await send_message(
                    session,
                    [
                        At(flag="user", target=session.user.id),
                        f"你的权限不足喔，该功能需要的权限等级: {plugin.admin_level}",
                    ],
                    entity.user_id,
                )

                raise SkipPluginException(
                    f"{plugin.name}({plugin.module}) 管理员权限不足..."
                )
        elif global_user:
            if global_user.user_level < plugin.admin_level:
                await send_message(
                    session,
                    f"你的权限不足喔，该功能需要的权限等级: {plugin.admin_level}",
                )

                raise SkipPluginException(
                    f"{plugin.name}({plugin.module}) 管理员权限不足..."
                )
    finally:
        # 记录执行时间
        elapsed = time.time() - start_time
        if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
            logger.warning(
                f"auth_admin 耗时: {elapsed:.3f}s, plugin={plugin.module}",
                LOGGER_COMMAND,
                session=session,
            )
