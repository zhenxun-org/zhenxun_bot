from typing_extensions import Self

from tortoise import fields

from zhenxun.models.plugin_limit import PluginLimit  # noqa: F401
from zhenxun.services.db_context import Model
from zhenxun.utils.enum import BlockType, PluginType


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
    impression = fields.FloatField(default=0, description="插件好感度限制")
    """插件好感度限制"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "plugin_info"
        table_description = "插件基本信息"

    @classmethod
    async def get_plugin(
        cls, load_status: bool = True, filter_parent: bool = True, **kwargs
    ) -> Self | None:
        """获取插件列表

        参数:
            load_status: 加载状态.
            filter_parent: 过滤父组件

        返回:
            Self | None: 插件
        """
        if filter_parent:
            return await cls.get_or_none(
                load_status=load_status, plugin_type__not=PluginType.PARENT, **kwargs
            )
        return await cls.get_or_none(load_status=load_status, **kwargs)

    @classmethod
    async def get_plugins(
        cls, load_status: bool = True, filter_parent: bool = True, **kwargs
    ) -> list[Self]:
        """获取插件列表

        参数:
            load_status: 加载状态.
            filter_parent: 过滤父组件

        返回:
            list[Self]: 插件列表
        """
        if filter_parent:
            return await cls.filter(
                load_status=load_status, plugin_type__not=PluginType.PARENT, **kwargs
            ).all()
        return await cls.filter(load_status=load_status, **kwargs).all()

    @classmethod
    async def _run_script(cls):
        return [
            "ALTER TABLE plugin_info ADD COLUMN parent character varying(255);",
            "ALTER TABLE plugin_info ADD COLUMN is_show boolean DEFAULT true;",
            "ALTER TABLE plugin_info ADD COLUMN ignore_prompt boolean DEFAULT false;",
            "ALTER TABLE plugin_info ADD COLUMN impression float DEFAULT 0;",
        ]
