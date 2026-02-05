from typing import Any

from arclet.alconna.typing import KeyWordVar
import nonebot
from nonebot.adapters import Bot, Event
from nonebot.compat import model_fields
from nonebot.exception import SkippedException
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    Arparma,
    Match,
    MultiVar,
    Option,
    Subcommand,
    on_alconna,
    store_true,
)
from nonebot_plugin_session import EventSession
from pydantic import BaseModel, ValidationError

from zhenxun import ui
from zhenxun.configs.config import Config
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services import group_settings_service, renderer_service
from zhenxun.services.log import logger
from zhenxun.services.tags import tag_manager
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.pydantic_compat import parse_as
from zhenxun.utils.rules import admin_check

__plugin_meta__ = PluginMetadata(
    name="插件配置管理",
    description="一个统一的命令，用于管理所有插件的分群配置",
    usage="""
### ⚙️ 插件配置管理 (pconf)
---
一个统一的命令，用于管理所有插件的分群或全局配置。

#### **📖 命令格式**
`pconf <子命令> [参数] [选项]`

#### **🎯 目标选项 (互斥)**
-   `-g, --group <群号...>`: 指定一个或多个群组ID **(SUPERUSER)**
-   `-t, --tag <标签名>`: 指定一个群组标签 **(SUPERUSER)**
-   `--all`: 对当前Bot所在的所有群组执行操作 **(SUPERUSER)**
-   `--global`: 操作全局配置 (config.yaml) **(SUPERUSER)**
-   **(无)**: 在群聊中操作时，默认目标为当前群。

#### **📋 子命令列表**
*   **`list` (或 `ls`)**: 查看列表
    *   `pconf list`: 查看所有支持分群配置的插件。
    *   `pconf list -p <插件名>`: 查看指定插件的所有分群可配置项。
    *   `pconf list -p <插件名> --all`: 查看所有群组对该插件的配置。
    *   `pconf list -p <插件名> --global`: 查看指定插件的全局可配置项。

*   **`get <配置项>`**: 获取配置值
    *   `pconf get <配置项> -p <插件名>`: 获取当前群的配置值。
    *   `pconf get <配置项> -p <插件名> -g <群号>`: 获取指定群的配置值。

*   **`set <key=value...>`**: 设置一个或多个配置值
    *   `pconf set key1=value1 key2=value2 -p <插件名>`

*   **`reset [配置项]`**: 重置配置为默认值
    *   `pconf reset -p <插件名>`: 重置当前群该插件的所有配置。
    *   `pconf reset <配置项> -p <插件名>`: 重置当前群该插件的指定配置项。
    """,
    extra=PluginExtraData(
        author="HibiKier",
        version="1.0",
        plugin_type=PluginType.SUPERUSER,
        configs=[
            RegisterConfig(
                module="plugin_config_manager",
                key="PCONF_ADMIN_LEVEL",
                value=5,
                help="管理分群配置的基础权限等级",
                default_value=5,
                type=int,
            ),
            RegisterConfig(
                module="plugin_config_manager",
                key="SHOW_DEFAULT_CONFIG_IN_ALL",
                value=False,
                help="在使用 --all 查询时，是否显示配置为默认值的群组",
                default_value=False,
                type=bool,
            ),
        ],
    ).to_dict(),
)


pconf_cmd = on_alconna(
    Alconna(
        "pconf",
        Subcommand(
            "list",
            alias=["ls"],
            help_text="查看插件或配置项列表",
        ),
        Subcommand(
            "get",
            Args["key", str],
            help_text="获取配置值",
        ),
        Subcommand(
            "set",
            Args["settings", MultiVar(KeyWordVar(Any))],
            help_text="设置配置值",
        ),
        Subcommand(
            "reset",
            Args["key?", str],
            help_text="重置配置",
        ),
        Option("-p|--plugin", Args["plugin_name", str], help_text="指定插件名"),
        Option("-g|--group", Args["group_ids", MultiVar(str)], help_text="指定群组ID"),
        Option("-t|--tag", Args["tag_name", str], help_text="指定群组标签"),
        Option("--all", action=store_true, help_text="操作所有群组"),
        Option("--global", action=store_true, help_text="操作全局配置"),
    ),
    rule=admin_check("plugin_config_manager", "PCONF_ADMIN_LEVEL"),
    priority=5,
    block=True,
)


async def get_plugin_config_model(plugin_name: str) -> type[BaseModel] | None:
    """通过插件名查找其注册的分群配置模型"""
    for p in nonebot.get_loaded_plugins():
        if p.name == plugin_name and p.metadata and p.metadata.extra:
            extra = PluginExtraData(**p.metadata.extra)
            if extra.group_config_model:
                return extra.group_config_model
    return None


def truncate_text(text: str, max_len: int) -> str:
    """截断文本，过长时添加省略号"""
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


async def GetTargets(
    bot: Bot, event: Event, session: EventSession, arp: Arparma
) -> list[str]:
    """
    依赖注入，根据 -g, -t, --all 或当前会话解析目标群组ID列表，并进行权限检查。
    """
    is_superuser = await SUPERUSER(bot, event)

    if group_ids_match := arp.query[list[str]]("group.group_ids"):
        if not is_superuser:
            logger.warning(f"非超级用户 {session.id1} 尝试使用 -g 参数。")
            raise SkippedException("权限不足")
        return group_ids_match

    if tag_name_match := arp.query[str]("tag.tag_name"):
        if not is_superuser:
            logger.warning(f"非超级用户 {session.id1} 尝试使用 -t 参数。")
            raise SkippedException("权限不足")

        resolved_groups = await tag_manager.resolve_tag_to_group_ids(
            tag_name_match, bot=bot
        )
        if not resolved_groups:
            await pconf_cmd.finish(f"标签 '{tag_name_match}' 没有匹配到任何群组。")
        return resolved_groups

    if arp.find("all"):
        if not is_superuser:
            logger.warning(f"非超级用户 {session.id1} 尝试使用 --all 参数。")
            raise SkippedException("权限不足")
        from zhenxun.utils.platform import PlatformUtils

        all_groups, _ = await PlatformUtils.get_group_list(bot)
        return [g.group_id for g in all_groups]

    if gid := session.id3 or session.id2:
        return [gid]

    if not is_superuser:
        logger.warning(f"管理员 {session.id1} 尝试在私聊中操作分群配置。")
        raise SkippedException("权限不足")

    await pconf_cmd.finish(
        "超级用户在私聊中操作时，必须使用 -g <群号>、-t <标签名> 或 --all 指定目标群组"
    )


@pconf_cmd.assign("list")
async def handle_list(arp: Arparma, bot: Bot, event: Event):
    """处理 list 子命令"""
    plugin_name_str = None
    is_superuser = await SUPERUSER(bot, event)
    if arp.find("plugin"):
        plugin_name_str = arp.query[str]("plugin.plugin_name")

    if plugin_name_str:
        is_global = arp.find("global")
        is_all_groups = arp.find("all")

        if is_all_groups and not is_global:
            if not is_superuser:
                await MessageUtils.build_message(
                    "只有超级用户才能查看所有群的配置。"
                ).finish()

            model = await get_plugin_config_model(plugin_name_str)
            model_fields_list = model_fields(model) if model else []
            if not model_fields_list:
                await MessageUtils.build_message(
                    f"插件 '{plugin_name_str}' 不支持分群配置。"
                ).finish()

            all_groups, _ = await PlatformUtils.get_group_list(bot)
            if not all_groups:
                await MessageUtils.build_message("机器人未加入任何群组。").finish()

            model_fields_dict = {field.name: field for field in model_fields_list}
            config_keys = list(model_fields_dict.keys())
            headers = ["群号", "群名称", *config_keys]
            rows = []

            for group in all_groups:
                settings_dict = await group_settings_service.get_all_for_plugin(
                    group.group_id, plugin_name_str
                )
                row_data = [group.group_id, truncate_text(group.group_name, 10)]
                for key in config_keys:
                    value = settings_dict.get(key)
                    default_value = model_fields_dict[key].field_info.default

                    if value == default_value:
                        value_str = "默认"
                    else:
                        value_str = str(value) if value is not None else "N/A"

                    row_data.append(truncate_text(value_str, 20))

                show_default = Config.get_config(
                    "plugin_config_manager", "SHOW_DEFAULT_CONFIG_IN_ALL", False
                )
                if not show_default:
                    is_all_default = all(val == "默认" for val in row_data[2:])
                    if is_all_default:
                        continue

                rows.append(row_data)

            table = ui.table(
                title=f"插件 '{plugin_name_str}' 全群配置",
                tip=f"共查询 {len(rows)} 个群组",
            )
            table.set_headers(headers).add_rows(rows)

            viewport_width = 300 + len(config_keys) * 280
            img = await renderer_service.render(
                table, viewport={"width": viewport_width, "height": 10}
            )
            await MessageUtils.build_message(img).finish()

        if is_global:
            if not is_superuser:
                await MessageUtils.build_message(
                    "只有超级用户才能查看全局配置。"
                ).finish()
            config_group = Config.get(plugin_name_str)
            if not config_group or not config_group.configs:
                await MessageUtils.build_message(
                    f"插件 '{plugin_name_str}' 没有可配置的全局项。"
                ).finish()

            table = ui.table(
                title=f"插件 '{plugin_name_str}' 全局可配置项",
                tip=(
                    f"位于 config.yaml, 使用 pconf set <key>=<value> "
                    f"-p {plugin_name_str} --global 进行设置"
                ),
            )
            table.set_headers(["配置项", "当前值", "类型", "描述"])

            for key, config_model in config_group.configs.items():
                type_name = getattr(
                    config_model.type, "__name__", str(config_model.type)
                )
                table.add_row(
                    [
                        key,
                        truncate_text(str(config_model.value), 20),
                        type_name,
                        truncate_text(config_model.help or "无", 20),
                    ]
                )

            img = await renderer_service.render(table)
            await MessageUtils.build_message(img).finish()
        else:
            model = await get_plugin_config_model(plugin_name_str)
            model_fields_list = model_fields(model) if model else []
            if not model_fields_list:
                await MessageUtils.build_message(
                    f"插件 '{plugin_name_str}' 不支持分群配置。"
                ).finish()

            table = ui.table(
                title=f"插件 '{plugin_name_str}' 可配置项",
                tip=f"使用 pconf set <key>=<value> -p {plugin_name_str} 进行设置",
            )
            table.set_headers(["配置项", "类型", "描述", "默认值"])

            for field in model_fields_list:
                type_name = getattr(field.annotation, "__name__", str(field.annotation))
                description = field.field_info.description or "无"
                default_value = (
                    str(field.get_default())
                    if field.field_info.default is not None
                    else "无"
                )
                table.add_row([field.name, type_name, description, default_value])

            img = await renderer_service.render(table)
            await MessageUtils.build_message(img).finish()

    else:
        configurable_plugins = []
        for p in nonebot.get_loaded_plugins():
            if p.metadata and p.metadata.extra:
                extra = PluginExtraData(**p.metadata.extra)
                if extra.group_config_model:
                    configurable_plugins.append(p.name)

        if not configurable_plugins:
            await MessageUtils.build_message("当前没有插件支持分群配置。").finish()

        await MessageUtils.build_message(
            "支持分群配置的插件列表:\n"
            + "\n".join(f"- {name}" for name in configurable_plugins)
        ).finish()


@pconf_cmd.assign("get")
async def handle_get(
    arp: Arparma,
    key: Match[str],
    bot: Bot,
    event: Event,
    session: EventSession,
):
    if not arp.find("plugin"):
        await pconf_cmd.finish("必须使用 -p <插件名> 指定要操作的插件。")
    plugin_name_str = arp.query[str]("plugin.plugin_name")
    if not plugin_name_str:
        await pconf_cmd.finish("插件名不能为空。")
    is_superuser = await SUPERUSER(bot, event)

    if arp.find("global"):
        if not is_superuser:
            await MessageUtils.build_message("只有超级用户才能获取全局配置。").finish()
        value = Config.get_config(plugin_name_str, key.result)
        await MessageUtils.build_message(
            f"全局配置项 '{key.result}' 的值为: {value}"
        ).finish()
    else:
        target_group_ids = await GetTargets(bot, event, session, arp)
        target_group_id = target_group_ids[0]
        value = await group_settings_service.get(
            target_group_id, plugin_name_str, key.result
        )
        await MessageUtils.build_message(
            f"群组 {target_group_id} 的配置项 '{key.result}' 的值为: {value}"
        ).finish()


@pconf_cmd.assign("set")
async def handle_set(
    arp: Arparma,
    settings: Match[dict],
    bot: Bot,
    event: Event,
    session: EventSession,
):
    if not arp.find("plugin"):
        await pconf_cmd.finish("必须使用 -p <插件名> 指定要操作的插件。")
    plugin_name_str = arp.query[str]("plugin.plugin_name")
    if not plugin_name_str:
        await pconf_cmd.finish("插件名不能为空。")
    is_superuser = await SUPERUSER(bot, event)

    is_global = arp.find("global")

    if is_global:
        if not is_superuser:
            await MessageUtils.build_message("只有超级用户才能设置全局配置。").finish()
        config_group = Config.get(plugin_name_str)
        if not config_group or not config_group.configs:
            await MessageUtils.build_message(
                f"插件 '{plugin_name_str}' 没有可配置的全局项。"
            ).finish()

        changes_made = False
        success_messages = []
        for key, value_str in settings.result.items():
            config_model = config_group.configs.get(key.upper())
            if not config_model:
                await MessageUtils.build_message(
                    f"❌ 全局配置项 '{key}' 不存在。"
                ).send()
                continue

            target_type = config_model.type
            if target_type is None:
                if config_model.default_value is not None:
                    target_type = type(config_model.default_value)
                elif config_model.value is not None:
                    target_type = type(config_model.value)

            converted_value: Any = value_str
            if target_type and value_str is not None:
                try:
                    converted_value = parse_as(target_type, value_str)
                except (ValidationError, TypeError, ValueError) as e:
                    type_name = getattr(target_type, "__name__", str(target_type))
                    await MessageUtils.build_message(
                        f"❌ 配置项 '{key}' 的值 '{value_str}' "
                        f"无法转换为期望的类型 '{type_name}': {e}"
                    ).send()
                    continue

            Config.set_config(plugin_name_str, key.upper(), converted_value)
            success_messages.append(f"  - 配置项 '{key}' 已设置为: `{converted_value}`")
            changes_made = True

        if changes_made:
            Config.save(save_simple_data=True)
            response_msg = (
                f"✅ 插件 '{plugin_name_str}' 的全局配置已更新:\n"
                + "\n".join(success_messages)
            )
            await MessageUtils.build_message(response_msg).finish()
    else:
        model = await get_plugin_config_model(plugin_name_str)
        if not model:
            await MessageUtils.build_message(
                f"插件 '{plugin_name_str}' 不支持分群配置。"
            ).finish()

        target_group_ids = await GetTargets(bot, event, session, arp)
        model_fields_map = {field.name: field for field in model_fields(model)}

        success_groups = []
        failed_groups = []
        update_details = []

        for group_id in target_group_ids:
            for key, value_str in settings.result.items():
                field = model_fields_map.get(key)
                if not field:
                    await MessageUtils.build_message(
                        f"配置项 '{key}' 在插件 '{plugin_name_str}' 中不存在。"
                    ).finish()

                try:
                    validated_value = (
                        parse_as(field.annotation, value_str)
                        if field.annotation is not None
                        else value_str
                    )
                    await group_settings_service.set_key_value(
                        group_id, plugin_name_str, key, validated_value
                    )
                    if group_id not in success_groups:
                        success_groups.append(group_id)

                    if (key, validated_value) not in update_details:
                        update_details.append((key, validated_value))
                except (ValidationError, TypeError, ValueError) as e:
                    failed_groups.append(
                        (group_id, f"配置项 '{key}' 值 '{value_str}' 类型错误: {e}")
                    )
                except Exception as e:
                    failed_groups.append((group_id, f"内部错误: {e}"))

        if len(target_group_ids) == 1:
            group_id = target_group_ids[0]
            if group_id in success_groups and group_id not in [
                g[0] for g in failed_groups
            ]:
                settings_summary = [
                    f"  - '{k}' 已设置为: `{v}`" for k, v in update_details
                ]
                msg = (
                    f"✅ 群组 {group_id} 插件 '{plugin_name_str}' 配置更新成功:\n"
                    + "\n".join(settings_summary)
                )
            else:
                errors = [f[1] for f in failed_groups if f[0] == group_id]
                msg = (
                    f"❌ 群组 {group_id} 插件 '{plugin_name_str}' 配置更新失败:\n"
                    + "\n".join(errors)
                )
        else:
            settings_count = len(settings.result)
            msg = (
                f"✅ 批量为 {len(success_groups)} 个群组设置了 "
                f"{settings_count} 个配置项。"
            )
            if failed_groups:
                failed_count = len({g[0] for g in failed_groups})
                msg += f"\n❌ 其中 {failed_count} 个群组部分或全部设置失败。"

        await MessageUtils.build_message(msg).finish()


@pconf_cmd.assign("reset")
async def handle_reset(
    arp: Arparma,
    key: Match[str],
    bot: Bot,
    event: Event,
    session: EventSession,
):
    if not arp.find("plugin"):
        await pconf_cmd.finish("必须使用 -p <插件名> 指定要操作的插件。")
    plugin_name_str = arp.query[str]("plugin.plugin_name")
    if not plugin_name_str:
        await pconf_cmd.finish("插件名不能为空。")
    is_superuser = await SUPERUSER(bot, event)

    if arp.find("global"):
        if not is_superuser:
            await MessageUtils.build_message("只有超级用户才能重置全局配置。").finish()
        await MessageUtils.build_message("全局配置重置功能暂未实现。").finish()
    else:
        target_group_ids = await GetTargets(bot, event, session, arp)
        key_str = key.result if key.available else None

        success_groups = []
        failed_groups = []

        for group_id in target_group_ids:
            try:
                if key_str:
                    await group_settings_service.reset_key(
                        group_id, plugin_name_str, key_str
                    )
                else:
                    await group_settings_service.reset_all_for_plugin(
                        group_id, plugin_name_str
                    )
                success_groups.append(group_id)
            except Exception as e:
                failed_groups.append((group_id, str(e)))

        action = f"配置项 '{key_str}'" if key_str else "所有配置"

        if len(target_group_ids) == 1:
            if success_groups:
                msg = (
                    f"✅ 群组 {target_group_ids[0]} 中插件 '{plugin_name_str}' "
                    f"的 {action} 已成功重置。"
                )
            else:
                msg = (
                    f"❌ 群组 {target_group_ids[0]} 中插件 '{plugin_name_str}' "
                    f"的 {action} 重置失败: {failed_groups[0][1]}"
                )
        else:
            msg = (
                f"✅ 批量操作完成: 成功为 {len(success_groups)} 个群组重置了 {action}。"
            )
            if failed_groups:
                failed_count = len({g[0] for g in failed_groups})
                msg += f"\n❌ 其中 {failed_count} 个群组操作失败。"
        await MessageUtils.build_message(msg).finish()
