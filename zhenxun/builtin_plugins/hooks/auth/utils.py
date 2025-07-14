import contextlib

from nonebot.adapters import Event
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.config import Config
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.utils import FreqLimiter

from .config import LOGGER_COMMAND

base_config = Config.get("hook")


def is_poke(event: Event) -> bool:
    """判断是否为poke类型

    参数:
        event: Event

    返回:
        bool: 是否为poke类型
    """
    with contextlib.suppress(ImportError):
        from nonebot.adapters.onebot.v11 import PokeNotifyEvent

        return isinstance(event, PokeNotifyEvent)
    return False


async def send_message(
    session: Uninfo, message: list | str, check_tag: str | None = None
):
    """发送消息

    参数:
        session: Uninfo
        message: 消息
        check_tag: cd flag
    """
    try:
        if not check_tag:
            await MessageUtils.build_message(message).send(reply_to=True)
        elif freq._flmt.check(check_tag):
            freq._flmt.start_cd(check_tag)
            await MessageUtils.build_message(message).send(reply_to=True)
    except Exception as e:
        logger.error(
            "发送消息失败",
            LOGGER_COMMAND,
            session=session,
            e=e,
        )


class FreqUtils:
    def __init__(self):
        check_notice_info_cd = Config.get_config("hook", "CHECK_NOTICE_INFO_CD")
        if check_notice_info_cd is None or check_notice_info_cd < 0:
            raise ValueError("模块: [hook], 配置项: [CHECK_NOTICE_INFO_CD] 为空或小于0")
        self._flmt = FreqLimiter(check_notice_info_cd)
        self._flmt_g = FreqLimiter(check_notice_info_cd)
        self._flmt_s = FreqLimiter(check_notice_info_cd)
        self._flmt_c = FreqLimiter(check_notice_info_cd)

    def is_send_limit_message(
        self, plugin: PluginInfo, sid: str, is_poke: bool
    ) -> bool:
        """是否发送提示消息

        参数:
            plugin: PluginInfo
            sid: 检测键
            is_poke: 是否是戳一戳

        返回:
            bool: 是否发送提示消息
        """
        if is_poke:
            return False
        if not base_config.get("IS_SEND_TIP_MESSAGE"):
            return False
        if plugin.plugin_type == PluginType.DEPENDANT:
            return False
        return plugin.module != "ai" if self._flmt_s.check(sid) else False


freq = FreqUtils()
