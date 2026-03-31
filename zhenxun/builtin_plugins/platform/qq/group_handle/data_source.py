import asyncio
from datetime import datetime
import os
from pathlib import Path
import random

from nonebot.adapters import Bot
from nonebot.exception import ActionFailed
from nonebot_plugin_alconna import At, UniMessage
from nonebot_plugin_uninfo import Uninfo
import ujson as json

from zhenxun.builtin_plugins.platform.qq.exception import ForceAddGroupError
from zhenxun.configs.config import BotConfig, Config
from zhenxun.configs.path_config import DATA_PATH, IMAGE_PATH
from zhenxun.models.fg_request import FgRequest
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.models.level_user import LevelUser
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.log import logger
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.enum import RequestHandleType
from zhenxun.utils.manager.bot_profile_manager import BotProfileManager
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.utils import FreqLimiter

base_config = Config.get("invite_manager")

limit_cd = base_config.get("welcome_msg_cd")

WELCOME_PATH = DATA_PATH / "welcome_message"

DEFAULT_IMAGE_PATH = IMAGE_PATH / "qxz"

_API_SEMAPHORE = asyncio.Semaphore(4)
_API_TIMEOUT = 5.0
_REFRESH_TASKS: set[asyncio.Task] = set()


def _normalize_platform(platform: str | set[str] | None) -> str | None:
    return next(iter(platform), None) if isinstance(platform, set) else platform


async def _safe_get_group_member_info(bot: Bot, group_id: str, user_id: str) -> dict:
    async with _API_SEMAPHORE:
        try:
            return await asyncio.wait_for(
                bot.get_group_member_info(
                    group_id=int(group_id), user_id=int(user_id), no_cache=True
                ),
                timeout=_API_TIMEOUT,
            )
        except (asyncio.TimeoutError, ActionFailed, Exception) as e:
            logger.warning("获取用户信息失败", e=e)
            return {"user_id": user_id, "group_id": group_id, "nickname": ""}


async def _safe_get_group_info(bot: Bot, group_id: str) -> dict | None:
    async with _API_SEMAPHORE:
        try:
            return await asyncio.wait_for(
                bot.get_group_info(group_id=group_id),
                timeout=_API_TIMEOUT,
            )
        except (asyncio.TimeoutError, ActionFailed, Exception) as e:
            logger.warning("获取群信息失败", e=e)
            return None


async def _refresh_member_info_async(
    bot: Bot, group_id: str, user_id: str, platform: str | None
) -> None:
    user_info = await _safe_get_group_member_info(bot, group_id, user_id)
    await GroupInfoUser.update_or_create(
        user_id=str(user_info["user_id"]),
        group_id=str(user_info["group_id"]),
        defaults={
            "user_name": user_info.get("nickname") or "",
            "nickname": user_info.get("card") or user_info.get("nickname") or "",
            "platform": platform,
        },
    )


class GroupManager:
    _flmt = FreqLimiter(limit_cd)

    @classmethod
    async def __handle_add_group(
        cls, bot: Bot, group_id: str, group: GroupConsole | None
    ):
        """允许群组并设置群认证，默认群功能开关

        参数:
            bot: Bot
            group_id: 群组id
            group: GroupConsole
        """
        if group:
            group.group_flag = 1
            await group.save(update_fields=["group_flag"])
        else:
            block_plugin = ""
            if plugin_list := await PluginInfo.filter(default_status=False).all():
                for plugin in plugin_list:
                    block_plugin += f"<{plugin.module},"
            group_info = await _safe_get_group_info(bot, group_id)
            if not group_info:
                logger.warning(
                    "获取群信息失败，跳过群信息写入",
                    "入群检测",
                    group_id=group_id,
                )
                return
            await GroupConsole.update_or_create(
                group_id=group_info["group_id"],
                defaults={
                    "group_name": group_info["group_name"],
                    "max_member_count": group_info["max_member_count"],
                    "member_count": group_info["member_count"],
                    "group_flag": 1,
                    "block_plugin": block_plugin,
                    "platform": "qq",
                },
            )

    @classmethod
    async def __refresh_level(cls, bot: Bot, group_id: str):
        """刷新权限

        参数:
            bot: Bot
            group_id: 群组id
        """
        admin_default_auth = Config.get_config("admin_bot_manage", "ADMIN_DEFAULT_AUTH")
        member_list = await bot.get_group_member_list(group_id=group_id)
        member_id_list = [str(user_info["user_id"]) for user_info in member_list]
        flag2u = await LevelUser.filter(
            user_id__in=member_id_list, group_id=group_id, group_flag=1
        ).values_list("user_id", flat=True)
        # 即刻刷新权限
        for user_info in member_list:
            user_id = str(user_info["user_id"])
            role = user_info["role"]
            if user_id in bot.config.superusers:
                await LevelUser.set_level(user_id, group_id, 9)
                logger.debug(
                    "添加超级用户权限: 9",
                    "入群检测",
                    session=user_id,
                    group_id=user_info["group_id"],
                )
            elif (
                admin_default_auth is not None
                and role in ["owner", "admin"]
                and user_id not in flag2u
            ):
                await LevelUser.set_level(
                    user_id,
                    group_id,
                    admin_default_auth if role == "admin" else admin_default_auth + 1,
                )
                logger.debug(
                    f"添加默认群管理员权限: {admin_default_auth}",
                    "入群检测",
                    session=user_id,
                    group_id=user_info["group_id"],
                )

    @classmethod
    async def add_bot(
        cls, bot: Bot, operator_id: str, group_id: str, group: GroupConsole
    ):
        """拉入bot

        参数:
            bot: Bot
            operator_id: 操作者id
            group_id: 群组id
            group: GroupConsole
        """
        if (
            base_config.get("flag")
            and operator_id not in bot.config.superusers
            and group.group_flag != 1
        ):
            """退出群组"""
            try:
                if result_msg := base_config.get("message"):
                    await bot.send_group_msg(group_id=int(group_id), message=result_msg)
                await bot.set_group_leave(group_id=int(group_id))
                logger.info(
                    "强制拉群或未有群信息，退出群聊成功", "入群检测", group_id=group_id
                )
                await FgRequest.filter(
                    group_id=group_id, handle_type__isnull=True
                ).update(handle_type=RequestHandleType.IGNORE)
            except Exception as e:
                logger.error(
                    "强制拉群或未有群信息，退出群聊失败",
                    "入群检测",
                    group_id=group_id,
                    e=e,
                )
                raise ForceAddGroupError(
                    "强制拉群或未有群信息，退出群聊失败...", group_id
                ) from e
            # await GroupConsole.filter(group_id=group_id).delete()
            raise ForceAddGroupError(
                f"触发强制入群保护，已成功退出群聊 {group_id}...", group_id
            )
        else:
            await cls.__handle_add_group(bot, group_id, group)
            """刷新群管理员权限"""
            await cls.__refresh_level(bot, group_id)
            if BotProfileManager.is_auto_send_profile():
                file_path = await BotProfileManager.build_bot_profile_image(bot.self_id)
                if file_path:
                    await MessageUtils.build_message(
                        [
                            f"嗨，大家好，我是{BotConfig.self_nickname}， "
                            "希望我们可以友好相处（眨眼眨眼）！",
                            file_path,
                        ]
                    ).send()
                    logger.info("加入群组自动发送BOT自我介绍图片", session=group_id)

    @classmethod
    def get_path(cls, session: Uninfo) -> Path | None:
        """根据Session获取存储路径

        参数:
            session: Uninfo:

        返回:
            Path: 存储路径
        """
        if not session.group:
            return None
        platform = PlatformUtils.get_platform(session)
        path = WELCOME_PATH / f"{platform}" / f"{session.group.id}"
        if session.group.parent:
            path = (
                WELCOME_PATH
                / f"{platform}"
                / f"{session.group.parent.id}"
                / f"{session.group.id}"
            )
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def __get_welcome_data(cls, session: Uninfo) -> dict | None:
        """获取存储数据

        参数:
            session: Uninfo

        返回:
            dict | None: 欢迎消息数据
        """
        if not session.group:
            return None
        path = cls.get_path(session)
        if not path:
            return None
        file = path / "text.json"
        if not file.exists():
            return None
        with file.open(encoding="utf8") as f:
            return json.load(f)

    @classmethod
    async def __send_welcome_message(cls, session: Uninfo, user_id: str):
        """发送群欢迎消息

        参数:
            user_id: 用户id
            group_id: 群组id
        """
        if not session.group:
            return
        cls._flmt.start_cd(session.group.id)
        if json_data := cls.__get_welcome_data(session):
            key = random.choice([k for k in json_data.keys() if json_data[k]["status"]])
            welcome_data = json_data[key]
            msg_list = UniMessage().load(welcome_data["message"])
            if welcome_data["at"]:
                msg_list.insert(0, At("user", user_id))
            logger.info("发送群欢迎消息...", "入群检测", session=session)
            if msg_list:
                await MessageUtils.build_message(msg_list).finish()  # type: ignore
        image = DEFAULT_IMAGE_PATH / random.choice(os.listdir(DEFAULT_IMAGE_PATH))
        await MessageUtils.build_message(
            [
                "新人快跑啊！！本群现状↓（快使用自定义群欢迎消息！）",
                image,
            ]
        ).send()

    @classmethod
    async def add_user(cls, session: Uninfo, bot: Bot):
        """拉入用户

        参数:
            session: Uninfo
            bot: Bot
        """
        user_id = session.user.id
        group_id = ""
        if session.group:
            if session.group.parent:
                group_id = session.group.parent.id
            else:
                group_id = session.group.id
        join_time = datetime.now()
        user_name = getattr(session.user, "name", None) or getattr(
            session.user, "nick", None
        )
        platform = PlatformUtils.get_platform(session)
        await GroupInfoUser.update_or_create(
            user_id=str(user_id),
            group_id=str(group_id),
            defaults={
                "user_name": user_name or "",
                "user_join_time": join_time,
                "platform": platform,
            },
        )
        task = asyncio.create_task(
            _refresh_member_info_async(
                bot, str(group_id), str(user_id), _normalize_platform(platform)
            )
        )
        _REFRESH_TASKS.add(task)
        task.add_done_callback(_REFRESH_TASKS.discard)
        logger.info(f"用户{user_id} 所属{group_id} 更新成功")
        if not await CommonUtils.task_is_block(
            session, "group_welcome"
        ) and cls._flmt.check(group_id):
            await cls.__send_welcome_message(session, user_id)

    @classmethod
    async def kick_bot(cls, bot: Bot, group_id: str, operator_id: str):
        """踢出bot

        参数:
            bot: Bot
            group_id: 群组id
            operator_id: 操作员id
        """
        if user := await GroupInfoUser.get_or_none(
            user_id=operator_id, group_id=group_id
        ):
            operator_name = user.user_name
        else:
            operator_name = "None"
        group = await GroupConsole.get_group_db(group_id)
        group_name = group.group_name if group else ""
        if group:
            await group.delete()
        await PlatformUtils.send_superuser(
            bot,
            f"****呜..一份踢出报告****\n"
            f"我被 {operator_name}({operator_id})\n"
            f"踢出了 {group_name}({group_id})\n"
            f"日期：{str(datetime.now()).split('.')[0]}",
        )

    @classmethod
    async def run_user(
        cls,
        bot: Bot,
        user_id: str,
        group_id: str,
        operator_id: str,
        sub_type: str,
    ) -> str | None:
        """踢出用户或用户离开

        参数:
            bot: Bot
            user_id: 用户id
            group_id: 群组id
            operator_id: 操作员id
            sub_type: 类型

        返回:
            str | None: 返回消息
        """
        if user := await GroupInfoUser.get_or_none(user_id=user_id, group_id=group_id):
            user_name = user.user_name
        else:
            user_name = f"{user_id}"
        if user:
            await user.delete()
        logger.info(
            f"名称: {user_name} 退出群聊",
            "group_decrease_handle",
            session=user_id,
            group_id=group_id,
        )
        if sub_type == "kick":
            if operator_id != "0":
                operator_user = await GroupInfoUser.get_or_none(
                    user_id=operator_id, group_id=group_id
                )
                operator_name = (
                    (operator_user.user_name or operator_id)
                    if operator_user
                    else operator_id
                )
            else:
                operator_name = ""
            return f"{user_name} 被 {operator_name} 送走了."
        elif sub_type == "leave":
            return f"{user_name}离开了我们..."
        return None
