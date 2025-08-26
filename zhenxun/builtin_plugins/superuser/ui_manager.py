from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
from nonebot_plugin_alconna import (
    Alconna,
    AlconnaMatch,
    Args,
    Arparma,
    Match,
    Subcommand,
    on_alconna,
)

from zhenxun.configs.config import Config
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services import renderer_service
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

__plugin_meta__ = PluginMetadata(
    name="UI管理",
    description="管理UI、主题和渲染服务的相关配置",
    usage="""
    指令：
        ui reload / 重载主题: 重新加载当前主题的配置和资源。
        ui theme / 主题列表: 显示所有可用的主题，并高亮显示当前主题。
        ui theme [主题名称] / 切换主题 [主题名称]: 将UI主题切换为指定主题。
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.SUPERUSER,
        configs=[
            RegisterConfig(
                module="UI",
                key="THEME",
                value="default",
                help="设置渲染服务使用的全局主题名称(对应 resources/themes/下的目录名)",
                default_value="default",
                type=str,
            ),
            RegisterConfig(
                module="UI",
                key="CACHE",
                value=True,
                help="是否为渲染服务生成的图片启用文件缓存",
                default_value=True,
                type=bool,
            ),
            RegisterConfig(
                module="UI",
                key="DEBUG_MODE",
                value=False,
                help="是否在日志中输出渲染组件的完整HTML源码，用于调试",
                default_value=False,
                type=bool,
            ),
        ],
    ).to_dict(),
)


ui_matcher = on_alconna(
    Alconna(
        "ui",
        Subcommand("reload", help_text="重载当前主题"),
        Subcommand("theme", Args["theme_name?", str], help_text="查看或切换主题"),
    ),
    aliases={"主题管理"},
    rule=to_me(),
    permission=SUPERUSER,
    priority=1,
    block=True,
)

ui_matcher.shortcut("重载主题", command="ui reload")
ui_matcher.shortcut("主题列表", command="ui theme")
ui_matcher.shortcut("切换主题", command="ui theme", arguments=["{%0}"])


@ui_matcher.assign("reload")
async def handle_reload(arparma: Arparma):
    theme_name = await renderer_service.reload_theme()
    logger.info(
        f"UI主题已重载为: {theme_name}", "UI管理器", session=arparma.header_result
    )
    await MessageUtils.build_message(f"UI主题已成功重载为 '{theme_name}'！").send(
        reply_to=True
    )


@ui_matcher.assign("theme")
async def handle_theme(
    arparma: Arparma, theme_name_match: Match[str] = AlconnaMatch("theme_name")
):
    if theme_name_match.available:
        new_theme_name = theme_name_match.result
        try:
            await renderer_service.switch_theme(new_theme_name)
            logger.info(
                f"UI主题已切换为: {new_theme_name}",
                "UI管理器",
                session=arparma.header_result,
            )
            await MessageUtils.build_message(
                f"🎨 主题已成功切换为 '{new_theme_name}'！"
            ).send(reply_to=True)
        except FileNotFoundError as e:
            logger.warning(
                f"尝试切换到不存在的主题: {new_theme_name}",
                "UI管理器",
                session=arparma.header_result,
            )
            await MessageUtils.build_message(str(e)).send(reply_to=True)
        except Exception as e:
            logger.error(
                f"切换主题时发生错误: {e}",
                "UI管理器",
                session=arparma.header_result,
                e=e,
            )
            await MessageUtils.build_message(f"切换主题失败: {e}").send(reply_to=True)
    else:
        try:
            available_themes = renderer_service.list_available_themes()
            current_theme = Config.get_config("UI", "THEME", "default")

            theme_list_str = "\n".join(
                f"  - {theme}{'  <- 当前' if theme == current_theme else ''}"
                for theme in sorted(available_themes)
            )
            response = f"🎨 可用主题列表:\n{theme_list_str}"
            await MessageUtils.build_message(response).send(reply_to=True)
        except Exception as e:
            logger.error(
                f"获取主题列表时发生错误: {e}",
                "UI管理器",
                session=arparma.header_result,
                e=e,
            )
            await MessageUtils.build_message("获取主题列表失败。").send(reply_to=True)
