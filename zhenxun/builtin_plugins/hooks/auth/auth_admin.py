import time

from nonebot_plugin_alconna import At
from nonebot_plugin_uninfo import Uninfo

from zhenxun.models.level_user import LevelUser
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.cache.runtime_cache import LevelUserMemoryCache, LevelUserSnapshot
from zhenxun.services.log import logger
from zhenxun.utils.utils import EntityIDs, get_entity_ids

from .config import LOGGER_COMMAND, WARNING_THRESHOLD
from .context import PermissionContext
from .exception import SkipPluginException


async def auth_admin(
    plugin: PluginInfo,
    session: Uninfo,
    cached_levels: tuple[
        LevelUser | LevelUserSnapshot | None, LevelUser | LevelUserSnapshot | None
    ]
    | None = None,
    *,
    context: PermissionContext | None = None,
    entity: EntityIDs | None = None,
):
    """管理员命令 个人权限

    参数:
        plugin: PluginInfo
        session: Uninfo
    """
    start_time = time.time()

    if not plugin.admin_level:
        return

    try:
        if context is not None:
            entity = context.entity
            if cached_levels is None:
                cached_levels = context.admin_levels
        if entity is None:
            entity = get_entity_ids(session)

        global_user: LevelUser | LevelUserSnapshot | None = None
        group_users: LevelUser | LevelUserSnapshot | None = None

        if cached_levels is not None:
            global_user, group_users = cached_levels
        else:
            global_user, group_users = await LevelUserMemoryCache.get_levels(
                entity.user_id, entity.group_id
            )

        user_level = global_user.user_level if global_user else 0
        if entity.group_id and group_users:
            user_level = max(user_level, group_users.user_level)

            if user_level < plugin.admin_level:
                raise SkipPluginException(
                    f"{plugin.name}({plugin.module}) 管理员权限不足...",
                    tip_message=[
                        At(flag="user", target=entity.user_id),
                        f"你的权限不足喔，该功能需要的权限等级: {plugin.admin_level}",
                    ],
                    tip_check_tag=entity.user_id,
                    tip_background=True,
                )
        elif global_user:
            if global_user.user_level < plugin.admin_level:
                raise SkipPluginException(
                    f"{plugin.name}({plugin.module}) 管理员权限不足...",
                    tip_message=(
                        f"你的权限不足喔，该功能需要的权限等级: "
                        f"{plugin.admin_level}"
                    ),
                    tip_background=True,
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
