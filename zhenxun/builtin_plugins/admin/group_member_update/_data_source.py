from datetime import datetime
import re

import nonebot
from nonebot.adapters import Bot
from nonebot_plugin_uninfo import Member, Scene, SceneType, get_interface

from zhenxun.configs.config import Config
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.models.level_user import LevelUser
from zhenxun.services.hot_query_cache import (
    invalidate_group_members,
    invalidate_member_names,
)
from zhenxun.services.log import logger
from zhenxun.utils.platform import PlatformUtils


class MemberUpdateManage:
    @classmethod
    async def __handle_user(
        cls,
        member: Member,
        db_user_map: dict[str, list[GroupInfoUser]],
        group_id: str,
        data_list: tuple[list[GroupInfoUser], list[GroupInfoUser], list[int]],
        platform: str | None,
        *,
        default_auth: int | None,
        superusers: set[str],
    ):
        """单个成员操作

        参数:
            member: Member
            db_user: db成员数据
            group_id: 群组id
            data_list: 数据列表
            platform: 平台
        """
        nickname = re.sub(
            r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", "", member.nick or member.user.name or ""
        )
        role = member.role
        member_id = str(member.id)
        if member_id in superusers:
            await LevelUser.set_level(member_id, group_id, 9)
        elif role and default_auth:
            if role.id != "MEMBER" and not await LevelUser.is_group_flag(
                member_id, group_id
            ):
                if role.id == "OWNER":
                    await LevelUser.set_level(member_id, group_id, default_auth + 1)
                elif role.id == "ADMINISTRATOR":
                    await LevelUser.set_level(member_id, group_id, default_auth)
        if users := db_user_map.get(member_id):
            if len(users) > 1:
                data_list[2].extend(u.id for u in users[1:])
            if nickname != users[0].user_name:
                user = users[0]
                user.user_name = nickname
                data_list[1].append(user)
        else:
            data_list[0].append(
                GroupInfoUser(
                    user_id=member_id,
                    group_id=group_id,
                    user_name=nickname,
                    user_join_time=member.joined_at or datetime.now(),
                    platform=platform,
                )
            )

    @classmethod
    async def update_group_member(
        cls,
        bot: Bot,
        group_id: str,
        *,
        scene_map: dict[str, Scene] | None = None,
        platform: str | None = None,
    ) -> str:
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
            if scene_map is None:
                scenes = await interface.get_scenes(SceneType.GROUP)
                scene_map = {scene.id: scene for scene in scenes if scene.is_group}
            if platform is None:
                platform = PlatformUtils.get_platform(bot)
            group_scene = scene_map.get(group_id) if scene_map else None
            if not group_scene:
                logger.warning(
                    f"bot: {bot.self_id}，group_id: {group_id}，群组不存在，"
                    "无法更新群成员信息..."
                )
                return "更新群组失败，群组不存在..."
            members = await interface.get_members(SceneType.GROUP, group_scene.id)

            try:
                group_console, _ = await GroupConsole.get_or_create(
                    group_id=group_id, defaults={"platform": platform}
                )
                group_console.member_count = len(members)
                group_console.group_name = group_scene.name or ""
                await group_console.save(update_fields=["member_count", "group_name"])
                logger.debug(
                    f"已更新群组 {group_id} 的成员总数为 {len(members)}",
                    "更新群组成员信息",
                )
            except Exception as e:
                logger.error(
                    f"更新群组 {group_id} 的 GroupConsole 信息失败",
                    "更新群组成员信息",
                    e=e,
                )

            db_user = await GroupInfoUser.filter(group_id=group_id).all()
            db_user_map: dict[str, list[GroupInfoUser]] = {}
            for user in db_user:
                db_user_map.setdefault(user.user_id, []).append(user)
            db_user_ids = set(db_user_map)
            data_list: tuple[list[GroupInfoUser], list[GroupInfoUser], list[int]] = (
                [],
                [],
                [],
            )
            exist_member_ids: set[str] = set()
            driver = nonebot.get_driver()
            superusers = set(driver.config.superusers)
            default_auth = Config.get_config("admin_bot_manage", "ADMIN_DEFAULT_AUTH")
            for member in members:
                member_id = str(member.id)
                await cls.__handle_user(
                    member,
                    db_user_map,
                    group_id,
                    data_list,
                    platform,
                    default_auth=default_auth,
                    superusers=superusers,
                )
                exist_member_ids.add(member_id)
            if data_list[0]:
                try:
                    await GroupInfoUser.bulk_create(
                        data_list[0], 30, ignore_conflicts=True
                    )
                    logger.debug(
                        f"创建用户数据 {len(data_list[0])} 条",
                        "更新群组成员信息",
                        target=group_id,
                    )
                except Exception as e:
                    logger.error("批量创建用户数据失败", "更新群组成员信息", e=e)
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

            if delete_member_ids := db_user_ids - exist_member_ids:
                await GroupInfoUser.filter(
                    user_id__in=list(delete_member_ids), group_id=group_id
                ).delete()
                logger.info(
                    f"删除已退群用户 {len(delete_member_ids)} 条",
                    "更新群组成员信息",
                    group_id=group_id,
                    platform="qq",
                )
            changed_user_ids = (
                {user.user_id for user in data_list[0]}
                | {user.user_id for user in data_list[1]}
                | delete_member_ids
            )
            if data_list[0] or data_list[1] or data_list[2] or delete_member_ids:
                await invalidate_group_members(group_id, changed_user_ids)
                await invalidate_member_names(changed_user_ids)
        return "群组成员信息更新完成!"
