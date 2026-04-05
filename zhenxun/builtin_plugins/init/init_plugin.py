import asyncio

import nonebot
from nonebot import get_loaded_plugins
from nonebot.drivers import Driver
from nonebot.plugin import Plugin, PluginMetadata
from ruamel.yaml import YAML

from zhenxun.configs.utils import PluginExtraData, PluginSetting
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.plugin_limit import PluginLimit
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

from .manager import manager

_yaml = YAML(pure=True)
_yaml.allow_unicode = True
_yaml.indent = 2

driver: Driver = nonebot.get_driver()


async def _handle_setting(
    plugin: Plugin,
    plugin_list: list[PluginInfo],
    limit_list: list[PluginLimit],
):
    """处理插件设置

    参数:
        plugin: Plugin
        plugin_list: 插件列表
        limit_list: 插件限制列表
    """
    metadata = plugin.metadata
    if not metadata:
        if not plugin.sub_plugins:
            return
        """父插件"""
        metadata = PluginMetadata(name=plugin.name, description="", usage="")
    extra = metadata.extra
    extra_data = PluginExtraData(**extra)
    logger.debug(f"{metadata.name}:{plugin.name} -> {extra}", "初始化插件数据")
    setting = extra_data.setting or PluginSetting()
    if metadata.type == "library":
        extra_data.plugin_type = PluginType.HIDDEN
    if extra_data.plugin_type == PluginType.HIDDEN:
        extra_data.menu_type = ""
    if plugin.sub_plugins:
        extra_data.plugin_type = PluginType.PARENT
    plugin_list.append(
        PluginInfo(
            module=plugin.name,
            module_path=plugin.module_name,
            name=metadata.name,
            author=extra_data.author,
            version=extra_data.version,
            level=setting.level,
            default_status=setting.default_status,
            limit_superuser=setting.limit_superuser,
            menu_type=extra_data.menu_type,
            cost_gold=setting.cost_gold,
            plugin_type=extra_data.plugin_type,
            admin_level=extra_data.admin_level,
            is_show=extra_data.is_show,
            ignore_prompt=extra_data.ignore_prompt,
            parent=(plugin.parent_plugin.module_name if plugin.parent_plugin else None),
            impression=setting.impression,
            ignore_statistics=extra_data.ignore_statistics,
        )
    )
    if extra_data.limits:
        limit_list.extend(
            PluginLimit(
                module=plugin.name,
                module_path=plugin.module_name,
                limit_type=limit._type,
                watch_type=limit.watch_type,
                status=limit.status,
                check_type=limit.check_type,
                result=limit.result,
                cd=getattr(limit, "cd", None),
                max_count=getattr(limit, "max_count", None),
            )
            for limit in extra_data.limits
        )


@PriorityLifecycle.on_startup(priority=5)
async def _():
    """
    初始化插件数据配置
    """
    plugin_list: list[PluginInfo] = []
    limit_list: list[PluginLimit] = []
    module2id = {}
    load_plugin = []
    if module_list := await PluginInfo.all().values("id", "module_path"):
        module2id = {m["module_path"]: m["id"] for m in module_list}
    for plugin in get_loaded_plugins():
        load_plugin.append(plugin.module_name)
        await _handle_setting(plugin, plugin_list, limit_list)
    create_list = []
    update_list = []
    update_task_list = []
    for plugin in plugin_list:
        if plugin.module_path not in module2id:
            create_list.append(plugin)
        else:
            plugin.id = module2id[plugin.module_path]
            update_task_list.append(
                plugin.save(
                    update_fields=[
                        "name",
                        "author",
                        "version",
                        "admin_level",
                        "plugin_type",
                        "is_show",
                        "ignore_prompt",
                        "ignore_statistics",
                    ]
                )
            )
            update_list.append(plugin)
    if create_list:
        await PluginInfo.bulk_create(create_list, 10)
    if update_task_list:
        await asyncio.gather(*update_task_list)
    # if update_list:
    #     # TODO: 批量更新无法更新plugin_type: tortoise.exceptions.OperationalError:
    #           column "superuser" does not exist
    #     pass
    # await PluginInfo.bulk_update(
    #     update_list,
    #     ["name", "author", "version", "admin_level", "plugin_type"],
    #     10,
    # )
    # for limit in limit_list:
    #     limit_create = []
    #     plugins = []
    #     if module_path_list := [limit.module_path for limit in limit_list]:
    #         plugins = await PluginInfo.get_plugins(module_path__in=module_path_list)
    #     if plugins:
    #         for limit in limit_list:
    #             if lmt := [p for p in plugins if p.module_path == limit.module_path]:
    #                 plugin = lmt[0]
    #                 """不在数据库中"""
    #                 limit_type_list = [
    #                     _limit.limit_type
    #                     for _limit in await plugin.plugin_limit.all()  # type: ignore
    #                 ]
    #                 if limit.limit_type not in limit_type_list:
    #                     limit.plugin = plugin
    #                     limit_create.append(limit)
    #     if limit_create:
    #         await PluginLimit.bulk_create(limit_create, 10)
    await PluginInfo.filter(module_path__in=load_plugin).update(load_status=True)
    await PluginInfo.filter(module_path__not_in=load_plugin).delete()
    manager.init()
    if limit_list:
        for limit in limit_list:
            if not manager.exists(limit.module, limit.limit_type):
                """不存在，添加"""
                manager.add(limit.module, limit)
    manager.save_file()
    await manager.load_to_db()
