import nonebot
from nonebot_plugin_uninfo import Uninfo

from zhenxun import ui
from zhenxun.configs.config import BotConfig, Config
from zhenxun.configs.path_config import IMAGE_PATH
from zhenxun.configs.utils import PluginExtraData
from zhenxun.models.bot_console import BotConsole
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.level_user import LevelUser
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.statistics import Statistics
from zhenxun.services import (
    LLMException,
    LLMMessage,
    generate,
)
from zhenxun.services.log import logger
from zhenxun.ui.builders import (
    NotebookBuilder,
    PluginMenuBuilder,
)
from zhenxun.ui.models import PluginMenuCategory
from zhenxun.utils.common_utils import format_usage_for_markdown
from zhenxun.utils.enum import BlockType, PluginType
from zhenxun.utils.platform import PlatformUtils

from ._utils import classify_plugin

random_bk_path = IMAGE_PATH / "background" / "help" / "simple_help"
background = IMAGE_PATH / "background" / "0.png"

driver = nonebot.get_driver()


def _create_plugin_menu_item(
    bot: BotConsole | None,
    plugin: PluginInfo,
    group: GroupConsole | None,
    is_detail: bool,
) -> dict:
    """为插件菜单构造一个插件菜单项数据字典"""
    status = True
    has_superuser_help = False
    nb_plugin = nonebot.get_plugin_by_module_name(plugin.module_path)
    if nb_plugin and nb_plugin.metadata and nb_plugin.metadata.extra:
        extra_data = PluginExtraData(**nb_plugin.metadata.extra)
        if extra_data.superuser_help:
            has_superuser_help = True

    if not plugin.status:
        if plugin.block_type == BlockType.ALL:
            status = False
        elif group and plugin.block_type == BlockType.GROUP:
            status = False
        elif not group and plugin.block_type == BlockType.PRIVATE:
            status = False
    elif group and f"{plugin.module}," in group.block_plugin:
        status = False
    elif bot and f"{plugin.module}," in bot.block_plugins:
        status = False

    commands = []
    if is_detail and nb_plugin and nb_plugin.metadata and nb_plugin.metadata.extra:
        extra_data = PluginExtraData(**nb_plugin.metadata.extra)
        commands = [cmd.command for cmd in extra_data.commands]

    return {
        "id": str(plugin.id),
        "name": plugin.name,
        "status": status,
        "has_superuser_help": has_superuser_help,
        "commands": commands,
    }


async def create_help_img(
    session: Uninfo, group_id: str | None, is_detail: bool
) -> bytes:
    """使用渲染服务生成帮助图片"""
    classified_data = await classify_plugin(
        session, group_id, is_detail, _create_plugin_menu_item
    )

    sorted_categories = dict(
        sorted(classified_data.items(), key=lambda x: len(x[1]), reverse=True)
    )
    categories_for_model = []
    plugin_count = 0
    active_count = 0

    if sorted_categories:
        menu_key = next(iter(sorted_categories.keys()))
        max_data = sorted_categories.pop(menu_key)
        main_category_name = "主要功能" if menu_key in ["normal", "功能"] else menu_key
        categories_for_model.append({"name": main_category_name, "items": max_data})
        plugin_count += len(max_data)
        active_count += sum(1 for item in max_data if item["status"])

    for menu, value in sorted_categories.items():
        category_name = "主要功能" if menu in ["normal", "功能"] else menu
        categories_for_model.append({"name": category_name, "items": value})
        plugin_count += len(value)
        active_count += sum(1 for item in value if item["status"])

    platform = PlatformUtils.get_platform(session)
    bot_id = BotConfig.get_qbot_uid(session.self_id) or session.self_id
    bot_avatar_url = PlatformUtils.get_user_avatar_url(bot_id, platform) or ""

    builder = PluginMenuBuilder(
        bot_name=BotConfig.self_nickname,
        bot_avatar_url=bot_avatar_url,
        is_detail=is_detail,
    )

    for category in categories_for_model:
        builder.add_category(
            PluginMenuCategory(name=category["name"], items=category["items"])
        )

    return await ui.render(builder.build())


async def get_user_allow_help(user_id: str) -> list[PluginType]:
    """获取用户可访问插件类型列表

    参数:
        user_id: 用户id

    返回:
        list[PluginType]: 插件类型列表
    """
    type_list = [PluginType.NORMAL, PluginType.DEPENDANT]
    for level in await LevelUser.filter(user_id=user_id).values_list(
        "user_level", flat=True
    ):
        if level > 0:  # type: ignore
            type_list.extend((PluginType.ADMIN, PluginType.SUPER_AND_ADMIN))
            break
    if user_id in driver.config.superusers:
        type_list.append(PluginType.SUPERUSER)
    return type_list


def min_leading_spaces(str_list: list[str]) -> int:
    min_spaces = 9999

    for s in str_list:
        leading_spaces = len(s) - len(s.lstrip(" "))

        if leading_spaces < min_spaces:
            min_spaces = leading_spaces

    return min_spaces if min_spaces != 9999 else 0


def split_text(text: str):
    split_text = text.split("\n")
    min_spaces = min_leading_spaces(split_text)
    if min_spaces > 0:
        split_text = [s[min_spaces:] for s in split_text]
    return [s.replace(" ", "&nbsp;") for s in split_text]


async def get_plugin_help(
    user_id: str, name: str, is_superuser: bool, variant: str | None = None
) -> str | bytes:
    """获取功能的帮助信息

    参数:
        user_id: 用户id
        name: 插件名称或id
        is_superuser: 是否为超级用户
        variant: 使用的皮肤/变体名称
    """
    type_list = await get_user_allow_help(user_id)
    if name.isdigit():
        plugin = await PluginInfo.get_or_none(id=int(name), plugin_type__in=type_list)
    else:
        plugin = await PluginInfo.get_or_none(
            name__iexact=name, load_status=True, plugin_type__in=type_list
        )

    if plugin:
        _plugin = nonebot.get_plugin_by_module_name(plugin.module_path)
        if _plugin and _plugin.metadata:
            extra_data = PluginExtraData(**_plugin.metadata.extra)

            call_count = await Statistics.filter(plugin_name=plugin.module).count()
            usage = _plugin.metadata.usage
            if is_superuser:
                if not extra_data.superuser_help:
                    return "该功能没有超级用户帮助信息"
                usage = extra_data.superuser_help

            metadata_items = [
                {"label": "作者", "value": extra_data.author or "未知"},
                {"label": "版本", "value": extra_data.version or "未知"},
                {"label": "调用次数", "value": call_count},
            ]

            processed_description = format_usage_for_markdown(
                _plugin.metadata.description.strip()
            )
            processed_usage = format_usage_for_markdown(usage.strip())

            sections = [
                {"title": "简介", "content": [processed_description]},
                {"title": "使用方法", "content": [processed_usage]},
            ]

            page_data = {
                "title": _plugin.metadata.name,
                "metadata": metadata_items,
                "sections": sections,
            }

            component = ui.template("pages/builtin/help", data=page_data)
            if variant:
                component.variant = variant
            return await ui.render(component, use_cache=True, device_scale_factor=2)
        return "糟糕! 该功能没有帮助喔..."
    return "没有查找到这个功能噢..."


async def get_llm_help(question: str, user_id: str) -> str | bytes:
    """
    使用LLM来回答用户的自然语言求助。

    参数:
        question: 用户的问题。
        user_id: 提问用户的ID。

    返回:
        str | bytes: LLM生成的回答或错误提示。
    """

    try:
        allowed_types = await get_user_allow_help(user_id)

        plugins = await PluginInfo.filter(
            is_show=True, plugin_type__in=allowed_types
        ).all()

        knowledge_base_parts = []
        for p in plugins:
            meta = nonebot.get_plugin_by_module_name(p.module_path)
            if not meta or not meta.metadata:
                continue
            usage = meta.metadata.usage.strip() or "无"
            desc = meta.metadata.description.strip() or "无"
            part = f"功能名称: {p.name}\n功能描述: {desc}\n用法示例:\n{usage}"
            knowledge_base_parts.append(part)

        if not knowledge_base_parts:
            return "抱歉，根据您的权限，当前没有可供查询的功能信息。"

        knowledge_base = "\n\n---\n\n".join(knowledge_base_parts)

        user_role = "普通用户"
        if PluginType.SUPERUSER in allowed_types:
            user_role = "超级管理员"
        elif PluginType.ADMIN in allowed_types:
            user_role = "管理员"

        base_system_prompt = (
            f"你是一个精通机器人功能的AI助手。当前向你提问的用户是一位「{user_role}」。\n"
            "你的任务是根据下面提供的功能列表和详细说明，来回答用户关于如何使用机器人的问题。\n"
            "请仔细阅读每个功能的描述和用法，然后用简洁、清晰的语言告诉用户应该使用哪个或哪些命令来解决他们的问题。\n"
            "如果找不到完全匹配的功能，可以推荐最相关的一个或几个。直接给出操作指令和简要解释即可。"
        )

        if (
            Config.get_config("help", "LLM_HELPER_STYLE")
            and Config.get_config("help", "LLM_HELPER_STYLE").strip()
        ):
            style = Config.get_config("help", "LLM_HELPER_STYLE")
            style_instruction = f"请务必使用「{style}」的风格和口吻来回答。"
            system_prompt = f"{base_system_prompt}\n{style_instruction}"
        else:
            system_prompt = base_system_prompt

        full_instruction = (
            f"{system_prompt}\n\n=== 功能列表和说明 ===\n{knowledge_base}"
        )

        messages = [
            LLMMessage.system(full_instruction),
            LLMMessage.user(question),
        ]
        response = await generate(
            messages=messages,
            model=Config.get_config("help", "DEFAULT_LLM_MODEL"),
        )

        reply_text = response.text if response else "抱歉，我暂时无法回答这个问题。"
        threshold = Config.get_config("help", "LLM_HELPER_REPLY_AS_IMAGE_THRESHOLD", 50)

        if len(reply_text) > threshold:
            builder = NotebookBuilder()
            builder.text(reply_text)
            return await ui.render(builder.build())

        return reply_text

    except LLMException as e:
        logger.error(f"LLM智能帮助出错: {e}", "帮助", e=e)
        return "抱歉，智能帮助功能当前不可用，请稍后再试或联系管理员。"
    except Exception as e:
        logger.error(f"构建LLM帮助时发生未知错误: {e}", "帮助", e=e)
        return "抱歉，智能帮助功能遇到了一点小问题，正在紧急处理中！"
