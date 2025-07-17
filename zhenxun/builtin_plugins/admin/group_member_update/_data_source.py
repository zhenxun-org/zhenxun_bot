from datetime import datetime
import re

import nonebot
from nonebot.adapters import Bot
from nonebot_plugin_uninfo import Member, SceneType, get_interface

from zhenxun.configs.config import Config
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.models.level_user import LevelUser
from zhenxun.services.log import logger
from zhenxun.utils.platform import PlatformUtils


class MemberUpdateManage:
    @classmethod
    async def __handle_user(
        cls,
        member: Member,
        db_user: list[GroupInfoUser],
        group_id: str,
        data_list: tuple[list, list, list],
        platform: str | None,
    ):
        """单个成员操作

        参数:
            member: Member
            db_user: db成员数据
            group_id: 群组id
            data_list: 数据列表
            platform: 平台
        """
        driver = nonebot.get_driver()
        default_auth = Config.get_config("admin_bot_manage", "ADMIN_DEFAULT_AUTH")
        nickname = re.sub(
            r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", "", member.nick or member.user.name or ""
        )
        role = member.role
        db_user_uid = [u.user_id for u in db_user]
        uid2name = {u.user_id: u.user_name for u in db_user}
        if member.id in driver.config.superusers:
            await LevelUser.set_level(member.id, group_id, 9)
        elif role and default_auth:
            if role.id != "MEMBER" and not await LevelUser.is_group_flag(
                member.id, group_id
            ):
                if role.id == "OWNER":
                    await LevelUser.set_level(member.id, group_id, default_auth + 1)
                elif role.id == "ADMINISTRATOR":
                    await LevelUser.set_level(member.id, group_id, default_auth)
        if cnt := db_user_uid.count(member.id):
            users = [u for u in db_user if u.user_id == member.id]
            if cnt > 1:
                for u in users[1:]:
                    data_list[2].append(u.id)
            if nickname != uid2name.get(member.id):
                user = users[0]
                user.user_name = nickname
                data_list[1].append(user)
        else:
            data_list[0].append(
                GroupInfoUser(
                    user_id=member.id,
                    group_id=group_id,
                    user_name=nickname,
                    user_join_time=member.joined_at or datetime.now(),
                    platform=platform,
                )
            )

    @classmethod
    async def update_group_member(cls, bot: Bot, group_id: str) -> str:
        """更新群组成员信息

        参数:
            bot: Bot
            group_id: 群组id

        返回:
            str: 返回消息
        """
        if not group_id:
            logger.warning(f"bot: {bot.self_id}，group_id为空，无法更新群成员信息...")
            return "群组id为空..."
        if interface := get_interface(bot):
            scenes = await interface.get_scenes()
            platform = PlatformUtils.get_platform(bot)
            group_list = [s for s in scenes if s.is_group and s.id == group_id]
            if not group_list:
                logger.warning(
                    f"bot: {bot.self_id}，group_id: {group_id}，群组不存在，"
                    "无法更新群成员信息..."
                )
                return "更新群组失败，群组不存在..."
            members = await interface.get_members(SceneType.GROUP, group_list[0].id)
            db_user = await GroupInfoUser.filter(group_id=group_id).all()
            db_user_uid = [u.user_id for u in db_user]
            data_list = ([], [], [])
            exist_member_list = []
            for member in members:
                logger.debug(f"即将更新群组成员: {member}", "更新群组成员信息")
                await cls.__handle_user(member, db_user, group_id, data_list, platform)
                exist_member_list.append(member.id)
            if data_list[0]:
                try:
                    await GroupInfoUser.bulk_create(data_list[0], 30)
                    logger.debug(
                        f"创建用户数据 {len(data_list[0])} 条",
                        "更新群组成员信息",
                        target=group_id,
                    )
                except Exception as e:
                    logger.error(
                        f"批量创建用户数据失败: {e}，开始进行逐个存储",
                        "更新群组成员信息",
                    )
                    for u in data_list[0]:
                        try:
                            await u.save()
                        except Exception as e:
                            logger.error(
                                f"创建用户 {u.user_name}({u.user_id}) 数据失败: {e}",
                                "更新群组成员信息",
                            )
            if data_list[1]:
                await GroupInfoUser.bulk_update(data_list[1], ["user_name"], 30)
                logger.debug(
                    f"更新户数据 {len(data_list[1])} 条",
                    "更新群组成员信息",
                    target=group_id,
                )
            if data_list[2]:
                await GroupInfoUser.filter(id__in=data_list[2]).delete()
                logger.debug(f"删除重复数据 Ids: {data_list[2]}", "更新群组成员信息")

            if delete_member_list := [
                uid for uid in db_user_uid if uid not in exist_member_list
            ]:
                await GroupInfoUser.filter(
                    user_id__in=delete_member_list, group_id=group_id
                ).delete()
                logger.info(
                    f"删除已退群用户 {len(delete_member_list)} 条",
                    "更新群组成员信息",
                    group_id=group_id,
                    platform="qq",
                )
        return "群组成员信息更新完成!"
