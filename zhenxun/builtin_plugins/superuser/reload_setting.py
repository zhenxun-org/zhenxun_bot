import contextlib

from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
from nonebot_plugin_alconna import Alconna, Arparma, on_alconna
from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_session import EventSession

from zhenxun.configs.config import Config
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services.llm.config.providers import get_llm_config
from zhenxun.services.llm.manager import clear_model_cache
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from zhenxun.utils.message import MessageUtils

AUTO_RELOAD_JOB_ID = "zhenxun.reload_setting.auto_reload"

__plugin_meta__ = PluginMetadata(
    name="重载配置",
    description="重新加载config.yaml",
    usage="""
    重载配置
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.SUPERUSER,
        configs=[
            RegisterConfig(
                key="AUTO_RELOAD",
                value=False,
                help="自动重载配置文件",
                default_value=False,
                type=bool,
            ),
            RegisterConfig(
                key="AUTO_RELOAD_TIME",
                value=180,
                help="自动重载配置文件时长",
                default_value=180,
                type=int,
            ),
        ],
    ).to_dict(),
)

_matcher = on_alconna(
    Alconna(
        "重载配置",
    ),
    rule=to_me(),
    permission=SUPERUSER,
    priority=1,
    block=True,
)


def _get_auto_reload_interval() -> int:
    value = Config.get_config("reload_setting", "AUTO_RELOAD_TIME", 180)
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        logger.warning(
            f"AUTO_RELOAD_TIME 配置无效: {value!r}，已使用默认值 180 秒",
            "重载配置",
        )
        return 180
    if seconds <= 0:
        logger.warning(
            f"AUTO_RELOAD_TIME 配置小于等于 0: {seconds}，已使用默认值 180 秒",
            "重载配置",
        )
        return 180
    return seconds


def _reschedule_auto_reload_job() -> None:
    seconds = _get_auto_reload_interval()
    if scheduler.get_job(AUTO_RELOAD_JOB_ID):
        scheduler.reschedule_job(
            AUTO_RELOAD_JOB_ID,
            trigger="interval",
            seconds=seconds,
        )
    else:
        scheduler.add_job(
            _auto_reload_config,
            "interval",
            seconds=seconds,
            id=AUTO_RELOAD_JOB_ID,
            replace_existing=True,
        )
    logger.debug(f"自动重载配置任务间隔已设置为 {seconds} 秒", "重载配置")


async def _reload_plugin_limit_config() -> None:
    from zhenxun.builtin_plugins.hooks.auth.auth_limit import LimitManager
    from zhenxun.builtin_plugins.init.manager import manager

    manager.init()
    await manager.load_to_db()
    await LimitManager.update_limits()


async def _reload_runtime_config() -> None:
    Config.reload()
    get_llm_config.cache_clear()
    clear_model_cache()
    await _reload_plugin_limit_config()
    with contextlib.suppress(Exception):
        _reschedule_auto_reload_job()


@PriorityLifecycle.on_startup(priority=1)
def _init_auto_reload_job() -> None:
    _reschedule_auto_reload_job()


@_matcher.handle()
async def _(session: EventSession, arparma: Arparma):
    await _reload_runtime_config()
    logger.debug("自动重载配置文件", arparma.header_result, session=session)
    await MessageUtils.build_message("重载完成!").send(reply_to=True)


async def _auto_reload_config() -> None:
    if Config.get_config("reload_setting", "AUTO_RELOAD"):
        await _reload_runtime_config()
        logger.debug("已自动重载配置文件...")
