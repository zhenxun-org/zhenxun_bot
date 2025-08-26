from nonebot.adapters import Bot
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
from nonebot_plugin_alconna import (
    Alconna,
    AlconnaQuery,
    Args,
    Match,
    Option,
    Query,
    on_alconna,
    store_true,
)
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.config import Config
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

from ._data_source import create_help_img, get_llm_help, get_plugin_help

__plugin_meta__ = PluginMetadata(
    name="帮助",
    description="帮助",
    usage="",
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.DEPENDANT,
        is_show=False,
        configs=[
            RegisterConfig(
                key="ENABLE_LLM_HELPER",
                value=False,
                help="是否开启LLM智能帮助功能",
                default_value=False,
                type=bool,
            ),
            RegisterConfig(
                key="DEFAULT_LLM_MODEL",
                value="Gemini/gemini-2.5-flash-lite-preview-06-17",
                help="智能帮助功能使用的默认LLM模型",
                default_value="Gemini/gemini-2.5-flash-lite-preview-06-17",
                type=str,
            ),
            RegisterConfig(
                key="LLM_HELPER_STYLE",
                value="绪山真寻",
                help="设置智能帮助功能的回复口吻或风格",
                default_value="绪山真寻",
                type=str,
            ),
            RegisterConfig(
                key="LLM_HELPER_REPLY_AS_IMAGE_THRESHOLD",
                value=100,
                help="AI帮助回复超过多少字时转为图片发送",
                default_value=100,
                type=int,
            ),
            RegisterConfig(
                key="HELP_STYLE",
                value="default",
                help="帮助页面的显示样式 (可选值: 'default', 'simple')",
                default_value="default",
                type=str,
            ),
        ],
    ).to_dict(),
)


_matcher = on_alconna(
    Alconna(
        "功能",
        Args["name?", str],
        Option("-s|--superuser", action=store_true, help_text="超级用户帮助"),
        Option("-d|--detail", action=store_true, help_text="详细帮助"),
    ),
    aliases={"help", "帮助", "菜单"},
    rule=to_me(),
    priority=1,
    block=True,
)


_matcher.shortcut(
    r"详细帮助",
    command="功能",
    arguments=["--detail"],
    prefix=True,
)


@_matcher.handle()
async def _(
    bot: Bot,
    name: Match[str],
    session: Uninfo,
    is_superuser: Query[bool] = AlconnaQuery("superuser.value", False),
    is_detail: Query[bool] = AlconnaQuery("detail.value", False),
):
    _is_superuser = is_superuser.result if is_superuser.available else False

    if _is_superuser and session.user.id not in bot.config.superusers:
        await MessageUtils.build_message("权限不足，无法查看超级用户帮助").finish(
            reply_to=True
        )

    if name.available:
        help_style = Config.get_config("help", "HELP_STYLE")
        variant = help_style if help_style != "default" else None

        traditional_help_result = await get_plugin_help(
            session.user.id, name.result, _is_superuser, variant=variant
        )

        is_plugin_found = not (
            isinstance(traditional_help_result, str)
            and "没有查找到这个功能噢..." in traditional_help_result
        )
        if is_plugin_found:
            await MessageUtils.build_message(traditional_help_result).send(
                reply_to=True
            )
            logger.info(f"查看帮助详情: {name.result}", "帮助", session=session)
        elif Config.get_config("help", "ENABLE_LLM_HELPER"):
            logger.info(f"智能帮助处理问题: {name.result}", "帮助", session=session)
            llm_answer = await get_llm_help(name.result, session.user.id)
            await MessageUtils.build_message(llm_answer).send(reply_to=True)
        else:
            await MessageUtils.build_message(traditional_help_result).send(
                reply_to=True
            )
            logger.info(
                f"查看帮助详情失败，未找到: {name.result}", "帮助", session=session
            )
    elif session.group and (gid := session.group.id):
        image_bytes = await create_help_img(session, gid, is_detail.result)
        await MessageUtils.build_message(image_bytes).finish()
    else:
        image_bytes = await create_help_img(session, None, is_detail.result)
        await MessageUtils.build_message(image_bytes).finish()
