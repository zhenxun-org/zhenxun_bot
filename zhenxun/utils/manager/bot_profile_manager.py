import asyncio
import os
from pathlib import Path
from typing import ClassVar

import aiofiles
import nonebot
from nonebot.compat import model_dump
from nonebot_plugin_htmlrender import template_to_pic
from pydantic import BaseModel

from zhenxun.configs.config import BotConfig, Config
from zhenxun.configs.path_config import DATA_PATH, TEMPLATE_PATH
from zhenxun.configs.utils.models import PluginExtraData
from zhenxun.models.statistics import Statistics
from zhenxun.models.user_console import UserConsole
from zhenxun.services.log import logger
from zhenxun.utils._build_image import BuildImage
from zhenxun.utils.platform import PlatformUtils

DIR_PATH = DATA_PATH / "bot_profile"

PROFILE_PATH = DIR_PATH / "profile"
PROFILE_PATH.mkdir(parents=True, exist_ok=True)

PROFILE_IMAGE_PATH = DIR_PATH / "image"
PROFILE_IMAGE_PATH.mkdir(parents=True, exist_ok=True)


Config.add_plugin_config(
    "bot_profile",
    "AUTO_SEND_PROFILE",
    True,
    help="在添加好友/群组时是否自动发送BOT自我介绍图片",
    default_value=True,
    type=bool,
)


class Profile(BaseModel):
    bot_id: str
    """BOT ID"""
    introduction: str
    """BOT自我介绍"""
    avatar: Path | None
    """BOT头像"""
    name: str
    """BOT名称"""


class PluginProfile(BaseModel):
    name: str
    """插件名称"""
    introduction: str
    """插件自我介绍"""
    precautions: list[str] | None = None
    """BOT自我介绍时插件的注意事项"""


class BotProfileManager:
    """BOT自我介绍管理器"""

    _bot_data: ClassVar[dict[str, Profile]] = {}

    _plugin_data: ClassVar[dict[str, PluginProfile]] = {}

    @classmethod
    def clear_profile_image(cls, bot_id: str | None = None):
        """清除BOT自我介绍图片"""
        if bot_id:
            file_path = PROFILE_IMAGE_PATH / f"{bot_id}.png"
            if file_path.exists():
                file_path.unlink()
        else:
            for f in os.listdir(PROFILE_IMAGE_PATH):
                _f = PROFILE_IMAGE_PATH / f
                if _f.is_file():
                    _f.unlink()

    @classmethod
    async def _read_profile(cls, bot_id: str):
        """读取BOT自我介绍

        参数:
            bot_id: BOT ID

        异常:
            FileNotFoundError: 文件不存在
        """
        bot_file_path = PROFILE_PATH / f"{bot_id}"
        bot_file_path.mkdir(parents=True, exist_ok=True)
        bot_profile_file = bot_file_path / "profile.txt"
        if not bot_profile_file.exists():
            logger.debug(f"BOT自我介绍文件不存在: {bot_profile_file}, 跳过读取")
            bot_file_path.touch()
            return
        async with aiofiles.open(bot_profile_file, encoding="utf-8") as f:
            introduction = await f.read()
        avatar = bot_file_path / f"{bot_id}.png"
        if not avatar.exists():
            avatar = None
        bot = await PlatformUtils.get_user(nonebot.get_bot(bot_id), bot_id)
        name = bot.name if bot else "未知"
        cls._bot_data[bot_id] = Profile(
            bot_id=bot_id, introduction=introduction, avatar=avatar, name=name
        )

    @classmethod
    async def get_bot_profile(cls, bot_id: str) -> Profile | None:
        if bot_id not in cls._bot_data:
            await cls._read_profile(bot_id)
        return cls._bot_data.get(bot_id)

    @classmethod
    def load_plugin_profile(cls):
        """加载插件自我介绍"""
        for plugin in nonebot.get_loaded_plugins():
            if plugin.module_name in cls._plugin_data:
                continue
            metadata = plugin.metadata
            if not metadata:
                continue
            extra = metadata.extra
            if not extra:
                continue
            extra_data = PluginExtraData(**extra)
            if extra_data.introduction or extra_data.precautions:
                cls._plugin_data[plugin.name] = PluginProfile(
                    name=metadata.name,
                    introduction=extra_data.introduction or "",
                    precautions=extra_data.precautions or [],
                )

    @classmethod
    def get_plugin_profile(cls) -> list[dict]:
        """获取插件自我介绍"""
        if not cls._plugin_data:
            cls.load_plugin_profile()
        return [model_dump(e) for e in cls._plugin_data.values()]

    @classmethod
    def is_auto_send_profile(cls) -> bool:
        """是否自动发送BOT自我介绍图片"""
        return Config.get_config("bot_profile", "AUTO_SEND_PROFILE")

    @classmethod
    async def build_bot_profile_image(
        cls, bot_id: str, tags: list[dict[str, str]] | None = None
    ) -> Path | None:
        """构建BOT自我介绍图片"""
        file_path = PROFILE_IMAGE_PATH / f"{bot_id}.png"
        if file_path.exists():
            return file_path
        profile, service_count, call_count = await asyncio.gather(
            cls.get_bot_profile(bot_id),
            UserConsole.get_new_uid(),
            Statistics.filter(bot_id=bot_id).count(),
        )
        if not profile:
            return None
        if not tags:
            tags = [
                {"text": f"服务人数: {service_count}", "color": "#5e92e0"},
                {"text": f"调用次数: {call_count}", "color": "#31e074"},
            ]
        image_bytes = await template_to_pic(
            template_path=str((TEMPLATE_PATH / "bot_profile").absolute()),
            template_name="main.html",
            templates={
                "avatar": str(profile.avatar.absolute()) if profile.avatar else None,
                "bot_name": profile.name,
                "bot_description": profile.introduction,
                "service_count": service_count,
                "call_count": call_count,
                "plugin_list": cls.get_plugin_profile(),
                "tags": tags,
                "title": f"{BotConfig.self_nickname}简介",
            },
            pages={
                "viewport": {"width": 1077, "height": 1000},
                "base_url": f"file://{TEMPLATE_PATH}",
            },
            wait=2,
        )
        image = BuildImage.open(image_bytes)
        await image.save(file_path)
        return file_path


BotProfileManager.clear_profile_image()
