import time
from typing import Literal

from nonebot_plugin_session import EventSession

from zhenxun.models.ban_console import BanConsole
from zhenxun.models.level_user import LevelUser
from zhenxun.services.log import logger
from zhenxun.utils.image_utils import BuildImage, ImageTemplate


async def call_ban(user_id: str):
    """调用ban

    参数:
        user_id: 用户id
    """
    await BanConsole.ban(user_id, None, 9, 60 * 12)
    logger.info("辱骂次数过多，已将用户加入黑名单...", "ban", session=user_id)


class BanManage:
    @classmethod
    async def build_ban_image(
        cls,
        filter_type: Literal["group", "user"] | None,
        user_id: str | None = None,
        group_id: str | None = None,
    ) -> BuildImage | None:
        """构造Ban列表图片

        参数:
            filter_type: 过滤类型
            user_id: 用户id
            group_id: 群组id

        返回:
            BuildImage | None: Ban列表图片
        """
        data_list = None
        query = BanConsole
        if user_id:
            query = query.filter(user_id=user_id)
        elif group_id:
            query = query.filter(group_id=group_id)
        elif filter_type == "user":
            query = query.filter(group_id__isnull=True)
        elif filter_type == "group":
            query = query.filter(user_id__isnull=True)
        data_list = await query.all()
        if not data_list:
            return None
        column_name = [
            "ID",
            "用户ID",
            "群组ID",
            "BAN LEVEL",
            "剩余时长(分钟)",
            "操作员ID",
        ]
        row_data = []
        for data in data_list:
            duration = int((data.ban_time + data.duration - time.time()) / 60)
            if data.duration < 0:
                duration = "∞"
            row_data.append(
                [
                    data.id,
                    data.user_id,
                    data.group_id,
                    data.ban_level,
                    duration,
                    data.operator,
                ]
            )
        return await ImageTemplate.table_page(
            "Ban / UnBan 列表", "在黑屋中狠狠调教!", column_name, row_data
        )

    @classmethod
    async def is_ban(cls, user_id: str, group_id: str | None):
        """判断用户是否被ban

        参数:
            user_id: 用户id

        返回:
            bool: 是否被ban
        """
        return await BanConsole.is_ban(user_id, group_id)

    @classmethod
    async def unban(
        cls,
        user_id: str | None,
        group_id: str | None,
        session: EventSession,
        idx: int | None = None,
        is_superuser: bool = False,
    ) -> tuple[bool, str]:
        """unban目标用户

        参数:
            user_id: 用户id
            group_id: 群组id
            session: Session
            idx: 指定id
            is_superuser: 是否为超级用户操作

        返回:
            tuple[bool, str]: 是否unban成功, 群组/用户id或提示
        """
        user_level = 9999
        if not is_superuser and user_id and session.id1:
            user_level = await LevelUser.get_user_level(session.id1, group_id)
        if idx:
            ban_data = await BanConsole.get_or_none(id=idx)
            if not ban_data:
                return False, "该用户/群组不在黑名单中捏..."
            if ban_data.ban_level > user_level:
                return False, "unBan权限等级不足捏..."
            await ban_data.delete()
            return True, str(ban_data.user_id or ban_data.group_id)
        elif await BanConsole.check_ban_level(user_id, group_id, user_level):
            await BanConsole.unban(user_id, group_id)
            return True, str(group_id)
        return False, "该用户/群组不在黑名单中不足捏..."

    @classmethod
    async def ban(
        cls,
        user_id: str | None,
        group_id: str | None,
        duration: int,
        session: EventSession,
        is_superuser: bool,
    ):
        """ban掉目标用户

        参数:
            user_id: 用户id
            group_id: 群组id
            duration: 时长，秒
            session: Session
            is_superuser: 是否为超级用户操作
        """
        level = 9999
        if not is_superuser and user_id and session.id1:
            level = await LevelUser.get_user_level(session.id1, group_id)
        await BanConsole.ban(user_id, group_id, level, duration, session.id1)
