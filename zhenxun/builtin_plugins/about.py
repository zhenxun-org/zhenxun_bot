from pathlib import Path

import aiofiles
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
from nonebot_plugin_alconna import Alconna, Arparma, on_alconna
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.path_config import DATA_PATH
from zhenxun.configs.utils import PluginExtraData
from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils

__plugin_meta__ = PluginMetadata(
    name="关于",
    description="想要更加了解真寻吗",
    usage="""
    指令：
        关于
    """.strip(),
    extra=PluginExtraData(author="HibiKier", version="0.1", menu_type="其他").to_dict(),
)


_matcher = on_alconna(Alconna("关于"), priority=5, block=True, rule=to_me())


QQ_INFO = """
『绪山真寻Bot』
版本：{version}
简介：基于Nonebot2开发，支持多平台，是一个非常可爱的Bot呀，希望与大家要好好相处
""".strip()

INFO = """
『绪山真寻Bot』
版本：{version}
简介：基于Nonebot2开发，支持多平台，是一个非常可爱的Bot呀，希望与大家要好好相处
项目地址：https://github.com/zhenxun-org/zhenxun_bot
文档地址：https://zhenxun-org.github.io/zhenxun_bot/
""".strip()


@_matcher.handle()
async def _(session: Uninfo, arparma: Arparma):
    ver_file = Path() / "__version__"
    version = None
    if ver_file.exists():
        async with aiofiles.open(ver_file, encoding="utf8") as f:
            if text := await f.read():
                version = text.split(":")[-1].strip()
    if PlatformUtils.is_qbot(session):
        result: list[str | Path] = [QQ_INFO.format(version=version)]
        path = DATA_PATH / "about.png"
        if path.exists():
            result.append(path)
        await MessageUtils.build_message(result).send()  # type: ignore
    else:
        await MessageUtils.build_message(INFO.format(version=version)).send()
        logger.info("查看关于", arparma.header_result, session=session)
