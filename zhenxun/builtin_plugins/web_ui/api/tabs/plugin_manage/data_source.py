import re

import cattrs
from fastapi import Query
from tortoise.exceptions import DoesNotExist

from zhenxun.configs.config import Config
from zhenxun.configs.utils import ConfigGroup
from zhenxun.models.plugin_info import PluginInfo as DbPluginInfo
from zhenxun.utils.enum import BlockType, PluginType

from .model import (
    BatchUpdatePlugins,
    PluginConfig,
    PluginDetail,
    PluginInfo,
    UpdatePlugin,
)


class ApiDataSource:
    @classmethod
    async def get_plugin_list(
        cls, plugin_type: list[PluginType] = Query(None), menu_type: str | None = None
    ) -> list[PluginInfo]:
        """获取插件列表

        参数:
            plugin_type: 插件类型.
            menu_type: 菜单类型.

        返回:
            list[PluginInfo]: 插件数据列表
        """
        plugin_list: list[PluginInfo] = []
        query = DbPluginInfo
        if plugin_type:
            query = query.filter(plugin_type__in=plugin_type, load_status=True)
        if menu_type:
            query = query.filter(menu_type=menu_type, load_status=True)
        plugins = await query.all()
        for plugin in plugins:
            plugin_info = PluginInfo(
                module=plugin.module,
                plugin_name=plugin.name,
                default_status=plugin.default_status,
                limit_superuser=plugin.limit_superuser,
                cost_gold=plugin.cost_gold,
                menu_type=plugin.menu_type,
                version=plugin.version or "0",
                level=plugin.level,
                status=plugin.status,
                author=plugin.author,
                block_type=plugin.block_type,
            )
            plugin_list.append(plugin_info)
        return plugin_list

    @classmethod
    async def update_plugin(cls, param: UpdatePlugin) -> DbPluginInfo:
        """更新插件数据

        参数:
            param: UpdatePlugin

        返回:
            DbPluginInfo | None: 插件数据
        """
        db_plugin = await DbPluginInfo.get_plugin(module=param.module)
        if not db_plugin:
            raise ValueError("插件不存在")
        db_plugin.default_status = param.default_status
        db_plugin.limit_superuser = param.limit_superuser
        db_plugin.cost_gold = param.cost_gold
        db_plugin.level = param.level
        db_plugin.menu_type = param.menu_type
        db_plugin.block_type = param.block_type
        db_plugin.status = param.block_type != BlockType.ALL
        await db_plugin.save()
        if param.configs and (configs := Config.get(param.module)):
            for key in param.configs:
                if c := configs.configs.get(key):
                    value = param.configs[key]
                    if c.type and value is not None:
                        value = cattrs.structure(value, c.type)
                    Config.set_config(param.module, key, value)
            Config.save(save_simple_data=True)
        return db_plugin

    @classmethod
    async def batch_update_plugins(cls, params: BatchUpdatePlugins) -> dict:
        """批量更新插件数据

        参数:
            params: BatchUpdatePlugins

        返回:
            dict: 更新结果, 例如 {'success': True, 'updated_count': 5, 'errors': []}
        """
        plugins_to_update_other_fields = []
        other_update_fields = set()
        updated_count = 0
        errors = []

        for item in params.updates:
            try:
                db_plugin = await DbPluginInfo.get(module=item.module)
                plugin_changed_other = False
                plugin_changed_block = False

                if db_plugin.block_type != item.block_type:
                    db_plugin.block_type = item.block_type
                    db_plugin.status = item.block_type != BlockType.ALL
                    plugin_changed_block = True

                if item.menu_type is not None and db_plugin.menu_type != item.menu_type:
                    db_plugin.menu_type = item.menu_type
                    other_update_fields.add("menu_type")
                    plugin_changed_other = True

                if (
                    item.default_status is not None
                    and db_plugin.default_status != item.default_status
                ):
                    db_plugin.default_status = item.default_status
                    other_update_fields.add("default_status")
                    plugin_changed_other = True

                if plugin_changed_block:
                    try:
                        await db_plugin.save(update_fields=["block_type", "status"])
                        updated_count += 1
                    except Exception as e_save:
                        errors.append(
                            {
                                "module": item.module,
                                "error": f"Save block_type failed: {e_save!s}",
                            }
                        )
                        plugin_changed_other = False

                if plugin_changed_other:
                    plugins_to_update_other_fields.append(db_plugin)

            except DoesNotExist:
                errors.append({"module": item.module, "error": "Plugin not found"})
            except Exception as e:
                errors.append({"module": item.module, "error": str(e)})

        bulk_updated_count = 0
        if plugins_to_update_other_fields and other_update_fields:
            try:
                await DbPluginInfo.bulk_update(
                    plugins_to_update_other_fields, list(other_update_fields)
                )
                bulk_updated_count = len(plugins_to_update_other_fields)
            except Exception as e_bulk:
                errors.append(
                    {
                        "module": "batch_update_other",
                        "error": f"Bulk update failed: {e_bulk!s}",
                    }
                )

        return {
            "success": len(errors) == 0,
            "updated_count": updated_count + bulk_updated_count,
            "errors": errors,
        }

    @classmethod
    def __build_plugin_config(
        cls, module: str, cfg: str, config: ConfigGroup
    ) -> PluginConfig:
        """获取插件配置项

        参数:
            module: 模块名
            cfg: cfg
            config: ConfigGroup

        返回:
            lPluginConfig: 配置数据
        """
        type_str = ""
        type_inner = None
        if r := re.search(r"<class '(.*)'>", str(config.configs[cfg].type)):
            type_str = r[1]
        elif r := re.search(r"typing\.(.*)\[(.*)\]", str(config.configs[cfg].type)):
            type_str = r[1]
            if type_str:
                type_str = type_str.lower()
            type_inner = r[2]
            if type_inner:
                type_inner = [x.strip() for x in type_inner.split(",")]
        return PluginConfig(
            module=module,
            key=cfg,
            value=config.configs[cfg].value,
            help=config.configs[cfg].help,
            default_value=config.configs[cfg].default_value,
            type=type_str,
            type_inner=type_inner,  # type: ignore
        )

    @classmethod
    async def rename_menu_type(cls, old_name: str, new_name: str) -> dict:
        """重命名菜单类型，并更新所有相关插件

        参数:
            old_name: 旧菜单类型名称
            new_name: 新菜单类型名称

        返回:
            dict: 更新结果, 例如 {'success': True, 'updated_count': 3}
        """
        if not old_name or not new_name:
            raise ValueError("旧名称和新名称都不能为空")
        if old_name == new_name:
            return {
                "success": True,
                "updated_count": 0,
                "info": "新旧名称相同，无需更新",
            }

        # 检查新名称是否已存在（理论上前端会校验，后端再保险一次）
        exists = await DbPluginInfo.filter(menu_type=new_name).exists()
        if exists:
            raise ValueError(f"新的菜单类型名称 '{new_name}' 已被其他插件使用")

        try:
            # 使用 filter().update() 进行批量更新
            updated_count = await DbPluginInfo.filter(menu_type=old_name).update(
                menu_type=new_name
            )
            return {"success": True, "updated_count": updated_count}
        except Exception as e:
            # 可以添加更详细的日志记录
            raise RuntimeError(f"数据库更新菜单类型失败: {e!s}")

    @classmethod
    async def get_plugin_detail(cls, module: str) -> PluginDetail:
        """获取插件详情

        参数:
            module: 模块名

        异常:
            ValueError: 插件不存在

        返回:
            PluginDetail: 插件详情数据
        """
        db_plugin = await DbPluginInfo.get_plugin(module=module)
        if not db_plugin:
            raise ValueError("插件不存在")
        config_list = []
        if config := Config.get(module):
            config_list.extend(
                cls.__build_plugin_config(module, cfg, config) for cfg in config.configs
            )
        return PluginDetail(
            module=module,
            plugin_name=db_plugin.name,
            default_status=db_plugin.default_status,
            limit_superuser=db_plugin.limit_superuser,
            cost_gold=db_plugin.cost_gold,
            menu_type=db_plugin.menu_type,
            version=db_plugin.version or "0",
            level=db_plugin.level,
            status=db_plugin.status,
            author=db_plugin.author,
            config_list=config_list,
            block_type=db_plugin.block_type,
        )
