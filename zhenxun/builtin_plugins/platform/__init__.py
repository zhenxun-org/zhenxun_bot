from pathlib import Path

import nonebot

from zhenxun.configs.config import BotConfig
from zhenxun.services.log import logger

path = Path(__file__).parent


try:
    from nonebot.adapters.onebot.v11 import Bot

    nonebot.load_plugins(str((path / "qq").resolve()))
except ImportError:
    logger.warning("未安装 onebot-adapter，无法加载QQ平台专用插件...")


if BotConfig.qq_adapter_load:
    try:
        from nonebot.adapters.qq import (  # noqa: F401 # pyright: ignore [reportMissingImports]
            Bot,
        )

        nonebot.load_plugins(str((path / "qq_api").resolve()))
    except ImportError:
        logger.warning("未安装 qq-adapter，无法加载QQ官平台专用插件...")
