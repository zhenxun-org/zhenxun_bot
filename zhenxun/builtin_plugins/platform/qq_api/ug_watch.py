from nonebot import on_message
from nonebot_plugin_uninfo import Uninfo

from zhenxun.models.friend_user import FriendUser
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.services.log import logger
from zhenxun.utils.platform import PlatformUtils


def rule(session: Uninfo) -> bool:
    return PlatformUtils.is_qbot(session)


_matcher = on_message(priority=999, block=False, rule=rule)


@_matcher.handle()
async def _(session: Uninfo):
    platform = PlatformUtils.get_platform(session)
    if session.group:
        if not await GroupConsole.exists(group_id=session.group.id):
            await GroupConsole.create(group_id=session.group.id)
            logger.info("添加当前群组ID信息", session=session)
        await GroupInfoUser.update_or_create(
            user_id=session.user.id,
            group_id=session.group.id,
            platform=PlatformUtils.get_platform(session),
        )
    elif not await FriendUser.exists(user_id=session.user.id, platform=platform):
        await FriendUser.create(
            user_id=session.user.id, platform=PlatformUtils.get_platform(session)
        )
        logger.info("添加当前好友用户信息", "", session=session)
