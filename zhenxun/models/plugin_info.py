from typing import ClassVar

from tortoise import fields

from zhenxun.models.plugin_limit import PluginLimit  # noqa: F401
from zhenxun.services.cache.runtime_cache import PluginInfoMemoryCache
from zhenxun.services.db_context import Model
from zhenxun.utils.enum import BlockType, CacheType, PluginType


class PluginInfo(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    module = fields.CharField(255, description="模块名")
    """模块名"""
    module_path = fields.CharField(255, description="模块路径", unique=True)
    """模块路径"""
    name = fields.CharField(255, description="插件名称")
    """插件名称"""
    status = fields.BooleanField(default=True, description="全局开关状态")
    """全局开关状态"""
    block_type: BlockType | None = fields.CharEnumField(
        BlockType, default=None, null=True, description="禁用类型"
    )
    """禁用类型"""
    load_status = fields.BooleanField(default=True, description="加载状态")
    """加载状态"""
    author = fields.CharField(255, null=True, description="作者")
    """作者"""
    version = fields.CharField(max_length=255, null=True, description="版本")
    """版本"""
    level = fields.IntField(default=5, description="所需群权限")
    """所需群权限"""
    default_status = fields.BooleanField(default=True, description="进群默认开关状态")
    """进群默认开关状态"""
    limit_superuser = fields.BooleanField(default=False, description="是否限制超级用户")
    """是否限制超级用户"""
    menu_type = fields.CharField(max_length=255, default="", description="菜单类型")
    """菜单类型"""
    plugin_type = fields.CharEnumField(PluginType, null=True, description="插件类型")
    """插件类型"""
    cost_gold = fields.IntField(default=0, description="调用插件所需金币")
    """调用插件所需金币"""
    plugin_limit = fields.ReverseRelation["PluginLimit"]
    """插件限制"""
    admin_level = fields.IntField(default=0, null=True, description="调用所需权限等级")
    """调用所需权限等级"""
    ignore_prompt = fields.BooleanField(default=False, description="是否忽略提示")
    """是否忽略阻断提示"""
    is_delete = fields.BooleanField(default=False, description="是否删除")
    """是否删除"""
    parent = fields.CharField(max_length=255, null=True, description="父插件")
    """父插件"""
    is_show = fields.BooleanField(default=True, description="是否显示在帮助中")
    """是否显示在帮助中"""
    ignore_statistics = fields.BooleanField(
        default=False, description="是否不统计调用次数"
    )
    """是否不统计调用次数"""
    impression = fields.FloatField(default=0, description="插件好感度限制")
    """插件好感度限制"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "plugin_info"
        table_description = "插件基本信息"
        indexes: ClassVar = [("module",), ("module_path",)]

    cache_type = CacheType.PLUGINS
    """缓存类型"""
    cache_key_field = "module"
    """缓存键字段"""

    @classmethod
    async def create(cls, *args, **kwargs):
        result = await super().create(*args, **kwargs)
        await PluginInfoMemoryCache.upsert_from_model(result)
        return result

    @classmethod
    async def update_or_create(cls, *args, **kwargs):
        result = await super().update_or_create(*args, **kwargs)
        await PluginInfoMemoryCache.upsert_from_model(result[0])
        return result

    async def save(self, *args, **kwargs):
        await super().save(*args, **kwargs)
        await PluginInfoMemoryCache.upsert_from_model(self)

    async def delete(self, *args, **kwargs):
        module = self.module
        module_path = self.module_path
        await super().delete(*args, **kwargs)
        await PluginInfoMemoryCache.remove(module, module_path)

    @staticmethod
    def _supports_cached_filter(key: str) -> bool:
        if "__" not in key:
            return True
        return key.rsplit("__", 1)[1] in {"in", "not", "not_in"}

    @staticmethod
    def _match_filter_value(current, operator: str, expected) -> bool:
        if operator == "in":
            return current in expected
        if operator == "not":
            return current != expected
        if operator == "not_in":
            return current not in expected
        return current == expected

    @classmethod
    def _can_use_cached_filters(cls, filters: dict) -> bool:
        return all(cls._supports_cached_filter(key) for key in filters)

    @classmethod
    async def _get_cached_plugins(cls) -> list["PluginInfo"]:
        plugins = await PluginInfoMemoryCache.get_all()
        return sorted(
            plugins.values(),
            key=lambda item: (int(getattr(item, "id", 0) or 0), item.module or ""),
        )

    @classmethod
    def _filter_cached_plugins(
        cls, plugins: list["PluginInfo"], filters: dict
    ) -> list["PluginInfo"]:
        result: list["PluginInfo"] = []
        for plugin in plugins:
            matched = True
            for key, expected in filters.items():
                if "__" in key:
                    field, operator = key.rsplit("__", 1)
                else:
                    field, operator = key, ""
                current = getattr(plugin, field, None)
                if not cls._match_filter_value(current, operator, expected):
                    matched = False
                    break
            if matched:
                result.append(plugin)
        return result

    @classmethod
    async def get_plugin(
        cls, load_status: bool | None = True, filter_parent: bool = True, **kwargs
    ) -> "PluginInfo | None":
        """获取插件列表

        参数:
            load_status: 加载状态.
            filter_parent: 过滤父组件

        返回:
            Self | None: 插件
        """
        plugins = await cls.get_plugins(
            load_status=load_status, filter_parent=filter_parent, **kwargs
        )
        return plugins[0] if plugins else None

    @classmethod
    async def get_plugins(
        cls, load_status: bool | None = True, filter_parent: bool = True, **kwargs
    ) -> list["PluginInfo"]:
        """获取插件列表

        参数:
            load_status: 加载状态.
            filter_parent: 过滤父组件

        返回:
            list[Self]: 插件列表
        """
        filters = dict(kwargs)
        if load_status is not None:
            filters.setdefault("load_status", load_status)
        if filter_parent and not any(key.startswith("plugin_type") for key in filters):
            filters["plugin_type__not"] = PluginType.PARENT

        if cls._can_use_cached_filters(filters):
            plugins = await cls._get_cached_plugins()
            return cls._filter_cached_plugins(plugins, filters)

        return await PluginInfo.filter(**filters).all()

    @classmethod
    async def get_plugins_values_list(
        cls,
        *fields: str,
        load_status: bool | None = True,
        filter_parent: bool = True,
        **kwargs,
    ) -> list:
        plugins = await cls.get_plugins(
            load_status=load_status,
            filter_parent=filter_parent,
            **kwargs,
        )
        if len(fields) == 1:
            field = fields[0]
            return [getattr(plugin, field, None) for plugin in plugins]
        return [
            tuple(getattr(plugin, field, None) for field in fields)
            for plugin in plugins
        ]

    @classmethod
    async def _run_script(cls):
        return []
