import asyncio
from datetime import datetime
import random

from nonebot import on_message, on_request
from nonebot.adapters.onebot.v11 import (
    ActionFailed,
    FriendRequestEvent,
    GroupRequestEvent,
)
from nonebot.adapters.onebot.v11 import Bot as v11Bot
from nonebot.adapters.onebot.v12 import Bot as v12Bot
from nonebot.plugin import PluginMetadata
from nonebot_plugin_session import EventSession

from zhenxun.configs.config import BotConfig, Config
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.models.ban_console import BanConsole
from zhenxun.models.event_log import EventLog
from zhenxun.models.fg_request import FgRequest
from zhenxun.models.friend_user import FriendUser
from zhenxun.models.group_console import GroupConsole
from zhenxun.services.cache import CacheRoot
from zhenxun.services.log import logger
from zhenxun.utils.enum import EventLogType, PluginType, RequestHandleType, RequestType
from zhenxun.utils.platform import PlatformUtils

base_config = Config.get("invite_manager")

__plugin_meta__ = PluginMetadata(
    name="记录请求",
    description="记录 好友/群组 请求",
    usage="",
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.HIDDEN,
        configs=[
            RegisterConfig(
                module="invite_manager",
                key="AUTO_ADD_FRIEND",
                value=False,
                help="是否自动同意好友添加",
                type=bool,
                default_value=False,
            ),
            RegisterConfig(
                module="invite_manager",
                key="AUTO_ADD_GROUP",
                value=False,
                help="是否自动同意邀请入群",
                type=bool,
                default_value=False,
            ),
            RegisterConfig(
                module="invite_manager",
                key="TIP_MESSAGE_LIMIT",
                value=360,
                help="重复申请与退群提醒过滤时间（分钟）",
                type=int,
                default_value=360,
            ),
        ],
    ).to_dict(),
)


# TODO: 其他平台请求

friend_req = on_request(priority=5, block=True)
group_req = on_request(priority=5, block=True)
_t = on_message(priority=999, block=False, rule=lambda: False)


cache = CacheRoot.cache_dict("REQUEST_CACHE", 60, str)
_API_TIMEOUT = 5.0


async def _safe_get_group_info(bot, group_id: str):
    try:
        return await asyncio.wait_for(
            bot.get_group_info(group_id=group_id),
            timeout=_API_TIMEOUT,
        )
    except (asyncio.TimeoutError, ActionFailed, Exception) as e:
        logger.warning("获取群信息失败", "群邀请", e=e)
        return None


def _format_ban_target(ban_data: BanConsole) -> str:
    user_id = ban_data.user_id or ""
    group_id = ban_data.group_id or ""
    if user_id and group_id:
        return f"用户 {user_id} 在群组 {group_id}"
    if user_id:
        return f"用户 {user_id}"
    return f"群组 {group_id}"


async def _build_permanent_ban_tip(
    *targets: tuple[str | None, str | None],
) -> str:
    ban_list: list[BanConsole] = []
    seen: set[tuple[str, str]] = set()
    for user_id, group_id in targets:
        ban_data = await BanConsole.get_ban(user_id=user_id, group_id=group_id)
        if not ban_data or ban_data.duration != -1:
            continue
        key = (ban_data.user_id or "", ban_data.group_id or "")
        if key in seen:
            continue
        seen.add(key)
        ban_list.append(ban_data)
    if not ban_list:
        return ""
    lines = ["", "永久黑名单提示："]
    for ban_data in ban_list:
        lines.extend(
            [
                f"- {_format_ban_target(ban_data)} 在黑名单中",
                f"  操作员ID：{ban_data.operator}",
                f"  封禁原因：{ban_data.ban_reason or '无'}",
            ]
        )
    return "\n".join(lines)


@friend_req.handle()
async def _(bot: v12Bot | v11Bot, event: FriendRequestEvent, session: EventSession):
    logger.debug("收录好友请求...", "好友请求", target=event.user_id)
    user = await bot.get_stranger_info(user_id=event.user_id)
    nickname = user["nickname"]
    # sex = user["sex"]
    # age = str(user["age"])
    comment = event.comment
    if base_config.get("AUTO_ADD_FRIEND"):
        logger.debug(
            "已开启好友请求自动同意，成功通过该请求",
            "好友请求",
            target=event.user_id,
        )
        await asyncio.sleep(random.randint(1, 10))
        await bot.set_friend_add_request(flag=event.flag, approve=True)
        await FriendUser.create(
            user_id=str(user["user_id"]), user_name=user["nickname"]
        )
    else:
        # 旧请求全部设置为过期
        await FgRequest.filter(
            request_type=RequestType.FRIEND,
            user_id=str(event.user_id),
            handle_type__isnull=True,
        ).update(handle_type=RequestHandleType.EXPIRE)
        f = await FgRequest.create(
            request_type=RequestType.FRIEND,
            platform=session.platform,
            bot_id=bot.self_id,
            flag=event.flag,
            user_id=event.user_id,
            nickname=nickname,
            comment=comment,
        )
        cache_key = str(event.user_id)
        if not cache.get(cache_key):
            cache.set(cache_key, "1")
            ban_tip = await _build_permanent_ban_tip((str(event.user_id), None))
            results = await PlatformUtils.send_superuser(
                bot,
                f"*****一份好友申请*****\n"
                f"ID: {f.id}\n"
                f"昵称：{nickname}({event.user_id})\n"
                f"自动同意：{'√' if base_config.get('AUTO_ADD_FRIEND') else '×'}\n"
                f"日期：{datetime.now().replace(microsecond=0)}\n"
                f"备注：{event.comment}"
                f"{ban_tip}",
            )
            if message_ids := [
                str(r[1].msg_ids[0]["message_id"])
                for r in results
                if r[1] and r[1].msg_ids
            ]:
                f.message_ids = ",".join(message_ids)
                await f.save(update_fields=["message_ids"])
        else:
            tip_limit = base_config.get("TIP_MESSAGE_LIMIT") or 360
            logger.debug(
                f"好友请求{tip_limit}分钟内重复, 已忽略",
                "好友请求",
                target=cache_key,
            )


@group_req.handle()
async def _(bot: v12Bot | v11Bot, event: GroupRequestEvent, session: EventSession):
    # sourcery skip: low-code-quality
    if event.sub_type != "invite":
        return
    if str(event.user_id) in bot.config.superusers or base_config.get("AUTO_ADD_GROUP"):
        try:
            logger.debug(
                "超级用户自动同意加入群聊或开启自动同意入群",
                "群聊请求",
                session=event.user_id,
                target=event.group_id,
            )
            group, _ = await GroupConsole.get_or_create_root_group(
                group_id=str(event.group_id),
                defaults={
                    "group_name": "",
                    "max_member_count": 0,
                    "member_count": 0,
                    "group_flag": 1,
                },
                update_defaults=True,
            )
            await bot.set_group_add_request(
                flag=event.flag, sub_type="invite", approve=True
            )
            group_info = await _safe_get_group_info(bot, str(event.group_id))
            if isinstance(bot, v11Bot) and group_info:
                max_member_count = group_info.get("max_member_count", 0)
                member_count = group_info.get("member_count", 0)
            else:
                max_member_count = 0
                member_count = 0
            group.max_member_count = max_member_count
            group.member_count = member_count
            group.group_name = group_info.get("group_name", "") if group_info else ""
            await group.save(
                update_fields=["group_name", "max_member_count", "member_count"]
            )
        except ActionFailed as e:
            logger.error(
                "超超级用户自动同意加入群聊或开启自动同意入群，加入群组发生错误",
                "群聊请求",
                session=event.user_id,
                target=event.group_id,
                e=e,
            )
        if str(event.user_id) not in bot.config.superusers and base_config.get(
            "AUTO_ADD_GROUP"
        ):
            # 非超级用户邀请自动加入群组
            nickname = await FriendUser.get_user_name(str(event.user_id))
            f = await FgRequest.create(
                request_type=RequestType.GROUP,
                platform=session.platform,
                bot_id=bot.self_id,
                flag=event.flag,
                user_id=str(event.user_id),
                nickname=nickname,
                group_id=str(event.group_id),
                handle_type=RequestHandleType.APPROVE,
            )
            ban_tip = await _build_permanent_ban_tip(
                (str(event.user_id), None),
                (str(event.user_id), str(event.group_id)),
                (None, str(event.group_id)),
            )
            results = await PlatformUtils.send_superuser(
                bot,
                f"*****一份入群申请*****\n"
                f"ID：{f.id}\n"
                f"申请人：{nickname}({event.user_id})\n群聊："
                f"{event.group_id}\n邀请日期：{datetime.now().replace(microsecond=0)}\n"
                "注: 该请求已自动同意"
                f"{ban_tip}",
            )
            await asyncio.sleep(random.randint(1, 5))
            await bot.send_private_msg(
                user_id=event.user_id,
                message=f"管理员已开启自动同意群组邀请，请不要让{BotConfig.self_nickname}受委屈哦（狠狠监控）"
                "\n在群组中 群组管理员与群主 允许使用管理员帮助"
                "（包括ban与功能开关等）\n请在群组中发送 '管理员帮助'",
            )
    elif not cache.get(f"{event.group_id}"):
        cache.set(f"{event.group_id}", "1")
        logger.debug(
            f"收录 用户[{event.user_id}] 群聊[{event.group_id}] 群聊请求",
            "群聊请求",
            target=event.group_id,
        )
        nickname = await FriendUser.get_user_name(str(event.user_id))
        await bot.send_private_msg(
            user_id=event.user_id,
            message=f"想要邀请我偷偷入群嘛~已经提醒{BotConfig.self_nickname}的管理员大人了\n"
            "请确保已经群主或群管理沟通过！\n"
            "等待管理员处理吧！",
        )
        # 旧请求全部设置为过期
        await FgRequest.filter(
            request_type=RequestType.GROUP,
            user_id=str(event.user_id),
            group_id=str(event.group_id),
            handle_type__isnull=True,
        ).update(handle_type=RequestHandleType.EXPIRE)
        f = await FgRequest.create(
            request_type=RequestType.GROUP,
            platform=session.platform,
            bot_id=bot.self_id,
            flag=event.flag,
            user_id=str(event.user_id),
            nickname=nickname,
            group_id=str(event.group_id),
        )
        kick_count = await EventLog.filter(
            group_id=str(event.group_id), event_type=EventLogType.KICK_BOT
        ).count()
        kick_message = (
            f"\n该群累计踢出{BotConfig.self_nickname} <{kick_count}>次"
            if kick_count
            else ""
        )
        ban_tip = await _build_permanent_ban_tip(
            (str(event.user_id), None),
            (str(event.user_id), str(event.group_id)),
            (None, str(event.group_id)),
        )
        results = await PlatformUtils.send_superuser(
            bot,
            f"*****一份入群申请*****\n"
            f"ID：{f.id}\n"
            f"申请人：{nickname}({event.user_id})\n群聊："
            f"{event.group_id}\n邀请日期：{datetime.now().replace(microsecond=0)}"
            f"{kick_message}"
            f"{ban_tip}",
        )
        if message_ids := [
            str(r[1].msg_ids[0]["message_id"]) for r in results if r[1] and r[1].msg_ids
        ]:
            f.message_ids = ",".join(message_ids)
            await f.save(update_fields=["message_ids"])
    else:
        logger.debug(
            "群聊请求五分钟内重复, 已忽略",
            "群聊请求",
            target=f"{event.user_id}:{event.group_id}",
        )
