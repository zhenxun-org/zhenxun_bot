from nonebot.adapters import Bot
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    Match,
    Option,
    Query,
    on_alconna,
    store_true,
)
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.utils import PluginExtraData
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

from ._data_source import UpdateManager

__plugin_meta__ = PluginMetadata(
    name="自动更新",
    description="就算是真寻也会成长的",
    usage="""
    usage：
        检查更新真寻最新版本，包括了自动更新
        资源文件大小一般在130mb左右，除非必须更新一般仅更新代码文件
        指令：
            检查更新 [main|release|resource|webui] ?[-r] ?[-f] ?[-z] ?[-t]
                main: main分支
                release: 最新release
                resource: 资源文件
                webui: webui文件
                -r: 下载资源文件，一般在更新main或release时使用
                -f: 强制更新，一般用于更新main时使用（仅git更新时有效）
                -s: 更新源，为 git 或 ali（默认使用ali）
                -z: 下载zip文件进行更新（仅git有效）
                -t: 更新方式，git或download（默认使用git）
                        git: 使用git pull（推荐）
                        download: 通过commit hash比较文件后下载更新（仅git有效）

            示例:
            检查更新 main
            检查更新 main -r
            检查更新 main -f
            检查更新 release -r
            检查更新 resource
            检查更新 webui
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.SUPERUSER,
    ).to_dict(),
)

_matcher = on_alconna(
    Alconna(
        "检查更新",
        Args["ver_type?", ["main", "release", "resource", "webui"]],
        Option("-r|--resource", action=store_true, help_text="下载资源文件"),
        Option("-f|--force", action=store_true, help_text="强制更新"),
        Option("-s", Args["source?", ["git", "ali"]], help_text="更新源"),
        Option("-z|--zip", action=store_true, help_text="下载zip文件"),
    ),
    priority=1,
    block=True,
    permission=SUPERUSER,
    rule=to_me(),
)


@_matcher.handle()
async def _(
    bot: Bot,
    session: Uninfo,
    ver_type: Match[str],
    resource: Query[bool] = Query("resource", False),
    force: Query[bool] = Query("force", False),
    source: Query[str] = Query("source", "ali"),
    zip: Query[bool] = Query("zip", False),
):
    result = ""
    await MessageUtils.build_message("正在进行检查更新...").send(reply_to=True)
    ver_type_str = ver_type.result
    source_str = source.result
    if ver_type_str in {"main", "release"}:
        if not ver_type.available:
            result += await UpdateManager.check_version()
            logger.info("查看当前版本...", "检查更新", session=session)
            await MessageUtils.build_message(result).finish()
        try:
            result += await UpdateManager.update_zhenxun(
                bot,
                session.user.id,
                ver_type_str,  # type: ignore
                force.result,
                source_str,  # type: ignore
                zip.result,
            )
        except Exception as e:
            logger.error("版本更新失败...", "检查更新", session=session, e=e)
            await MessageUtils.build_message(f"更新版本失败...e: {e}").finish()
    elif ver_type.result == "webui":
        if zip.result:
            source_str = None
        try:
            result += await UpdateManager.update_webui(
                source_str,  # type: ignore
                "test",
                True,
            )
        except Exception as e:
            logger.error("WebUI更新失败...", "检查更新", session=session, e=e)
            result += "\nWebUI更新错误..."
    if resource.result or ver_type.result == "resource":
        try:
            if zip.result:
                source_str = None
            result += await UpdateManager.update_resources(
                source_str,  # type: ignore
                "main",
                force.result,
            )
        except Exception as e:
            logger.error("资源更新下载失败...", "检查更新", session=session, e=e)
            result += "\n资源更新错误..."
    if result:
        await MessageUtils.build_message(result.strip()).finish()
    await MessageUtils.build_message("更新版本失败...").finish()
