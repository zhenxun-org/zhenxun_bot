from typing import Any, cast, overload
from typing_extensions import Self

from tortoise import fields
from tortoise.backends.base.client import BaseDBAsyncClient

from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.task_info import TaskInfo
from zhenxun.services.db_context import Model
from zhenxun.utils.enum import PluginType


def add_disable_marker(name: str) -> str:
    """添加模块禁用标记符

    Args:
        name: 模块名称

    Returns:
        添加了禁用标记的模块名 (前缀'<'和后缀',')
    """
    return f"<{name},"


@overload
def convert_module_format(data: str) -> list[str]: ...


@overload
def convert_module_format(data: list[str]) -> str: ...


def convert_module_format(data: str | list[str]) -> str | list[str]:
    """
    在 `<aaa,<bbb,<ccc,` 和 `["aaa", "bbb", "ccc"]` (即禁用启用)之间进行相互转换。

    参数:
        data: 要转换的数据

    返回:
        str | list[str]: 根据输入类型返回转换后的数据。
    """
    if isinstance(data, str):
        return [item.strip(",") for item in data.split("<") if item.strip()]
    else:
        return "".join(add_disable_marker(item) for item in data)


class GroupConsole(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    group_id = fields.CharField(255, description="群组id")
    """群聊id"""
    channel_id = fields.CharField(255, null=True, description="频道id")
    """频道id"""
    group_name = fields.TextField(default="", description="群组名称")
    """群聊名称"""
    max_member_count = fields.IntField(default=0, description="最大人数")
    """最大人数"""
    member_count = fields.IntField(default=0, description="当前人数")
    """当前人数"""
    status = fields.BooleanField(default=True, description="群状态")
    """群状态"""
    level = fields.IntField(default=5, description="群权限")
    """群权限"""
    is_super = fields.BooleanField(
        default=False, description="超级用户指定，可以使用全局关闭的功能"
    )
    """超级用户指定群，可以使用全局关闭的功能"""
    group_flag = fields.IntField(default=0, description="群认证标记")
    """群认证标记"""
    block_plugin = fields.TextField(default="", description="禁用插件")
    """禁用插件"""
    superuser_block_plugin = fields.TextField(
        default="", description="超级用户禁用插件"
    )
    """超级用户禁用插件"""
    block_task = fields.TextField(default="", description="禁用被动技能")
    """禁用被动技能"""
    superuser_block_task = fields.TextField(default="", description="超级用户禁用被动")
    """超级用户禁用被动"""
    platform = fields.CharField(255, default="qq", description="所属平台")
    """所属平台"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "group_console"
        table_description = "群组信息表"
        unique_together = ("group_id", "channel_id")

    @classmethod
    async def _get_task_modules(cls, *, default_status: bool) -> list[str]:
        """获取默认禁用的任务模块

        返回:
            list[str]: 任务模块列表
        """
        return cast(
            list[str],
            await TaskInfo.filter(default_status=default_status).values_list(
                "module", flat=True
            ),
        )

    @classmethod
    async def _get_plugin_modules(cls, *, default_status: bool) -> list[str]:
        """获取默认禁用的插件模块

        返回:
            list[str]: 插件模块列表
        """
        return cast(
            list[str],
            await PluginInfo.filter(
                plugin_type__in=[PluginType.NORMAL, PluginType.DEPENDANT],
                default_status=default_status,
            ).values_list("module", flat=True),
        )

    @classmethod
    async def create(
        cls, using_db: BaseDBAsyncClient | None = None, **kwargs: Any
    ) -> Self:
        """覆盖create方法"""
        group = await super().create(using_db=using_db, **kwargs)

        task_modules = await cls._get_task_modules(default_status=False)
        plugin_modules = await cls._get_plugin_modules(default_status=False)

        if task_modules or plugin_modules:
            await cls._update_modules(group, task_modules, plugin_modules, using_db)

        return group

    @classmethod
    async def _update_modules(
        cls,
        group: Self,
        task_modules: list[str],
        plugin_modules: list[str],
        using_db: BaseDBAsyncClient | None = None,
    ) -> None:
        """更新模块设置

        参数:
            group: 群组实例
            task_modules: 任务模块列表
            plugin_modules: 插件模块列表
            using_db: 数据库连接
        """
        update_fields = []

        if task_modules:
            group.block_task = convert_module_format(task_modules)
            update_fields.append("block_task")

        if plugin_modules:
            group.block_plugin = convert_module_format(plugin_modules)
            update_fields.append("block_plugin")

        if update_fields:
            await group.save(using_db=using_db, update_fields=update_fields)

    @classmethod
    async def get_or_create(
        cls,
        defaults: dict | None = None,
        using_db: BaseDBAsyncClient | None = None,
        **kwargs: Any,
    ) -> tuple[Self, bool]:
        """覆盖get_or_create方法"""
        group, is_create = await super().get_or_create(
            defaults=defaults, using_db=using_db, **kwargs
        )
        if not is_create:
            return group, is_create

        task_modules = await cls._get_task_modules(default_status=False)
        plugin_modules = await cls._get_plugin_modules(default_status=False)

        if task_modules or plugin_modules:
            await cls._update_modules(group, task_modules, plugin_modules, using_db)

        return group, is_create

    @classmethod
    async def update_or_create(
        cls,
        defaults: dict | None = None,
        using_db: BaseDBAsyncClient | None = None,
        **kwargs: Any,
    ) -> tuple[Self, bool]:
        """覆盖update_or_create方法"""
        group, is_create = await super().update_or_create(
            defaults=defaults, using_db=using_db, **kwargs
        )
        if not is_create:
            return group, is_create

        task_modules = await cls._get_task_modules(default_status=False)
        plugin_modules = await cls._get_plugin_modules(default_status=False)

        if task_modules or plugin_modules:
            await cls._update_modules(group, task_modules, plugin_modules, using_db)

        return group, is_create

    @classmethod
    async def get_group(
        cls, group_id: str, channel_id: str | None = None
    ) -> Self | None:
        """获取群组

        参数:
            group_id: 群组id
            channel_id: 频道id.

        返回:
            Self: GroupConsole
        """
        if channel_id:
            return await cls.get_or_none(group_id=group_id, channel_id=channel_id)
        return await cls.get_or_none(group_id=group_id, channel_id__isnull=True)

    @classmethod
    async def is_super_group(cls, group_id: str) -> bool:
        """是否超级用户指定群

        参数:
            group_id: 群组id

        返回:
            bool: 是否超级用户指定群
        """
        return group.is_super if (group := await cls.get_group(group_id)) else False

    @classmethod
    async def is_superuser_block_plugin(cls, group_id: str, module: str) -> bool:
        """查看群组是否超级用户禁用功能

        参数:
            group_id: 群组id
            module: 模块名称

        返回:
            bool: 是否禁用被动
        """
        return await cls.exists(
            group_id=group_id,
            superuser_block_plugin__contains=add_disable_marker(module),
        )

    @classmethod
    async def is_block_plugin(cls, group_id: str, module: str) -> bool:
        """查看群组是否禁用插件

        参数:
            group_id: 群组id
            plugin: 插件名称

        返回:
            bool: 是否禁用插件
        """
        module = add_disable_marker(module)
        return await cls.exists(
            group_id=group_id, block_plugin__contains=module
        ) or await cls.exists(
            group_id=group_id, superuser_block_plugin__contains=module
        )

    @classmethod
    async def set_block_plugin(
        cls,
        group_id: str,
        module: str,
        is_superuser: bool = False,
        platform: str | None = None,
    ):
        """禁用群组插件

        参数:
            group_id: 群组id
            task: 任务模块
            is_superuser: 是否为超级用户
            platform: 平台
        """
        group, _ = await cls.get_or_create(
            group_id=group_id, defaults={"platform": platform}
        )
        update_fields = []
        if is_superuser:
            superuser_block_plugin = convert_module_format(group.superuser_block_plugin)
            if module not in superuser_block_plugin:
                superuser_block_plugin.append(module)
                group.superuser_block_plugin = convert_module_format(
                    superuser_block_plugin
                )
                update_fields.append("superuser_block_plugin")
        elif add_disable_marker(module) not in group.block_plugin:
            block_plugin = convert_module_format(group.block_plugin)
            block_plugin.append(module)
            group.block_plugin = convert_module_format(block_plugin)
            update_fields.append("block_plugin")
        if update_fields:
            await group.save(update_fields=update_fields)

    @classmethod
    async def set_unblock_plugin(
        cls,
        group_id: str,
        module: str,
        is_superuser: bool = False,
        platform: str | None = None,
    ):
        """禁用群组插件

        参数:
            group_id: 群组id
            task: 任务模块
            is_superuser: 是否为超级用户
            platform: 平台
        """
        group, _ = await cls.get_or_create(
            group_id=group_id, defaults={"platform": platform}
        )
        update_fields = []
        if is_superuser:
            superuser_block_plugin = convert_module_format(group.superuser_block_plugin)
            if module in superuser_block_plugin:
                superuser_block_plugin.remove(module)
                group.superuser_block_plugin = convert_module_format(
                    superuser_block_plugin
                )
                update_fields.append("superuser_block_plugin")
        elif add_disable_marker(module) in group.block_plugin:
            block_plugin = convert_module_format(group.block_plugin)
            block_plugin.remove(module)
            group.block_plugin = convert_module_format(block_plugin)
            update_fields.append("block_plugin")
        if update_fields:
            await group.save(update_fields=update_fields)

    @classmethod
    async def is_normal_block_plugin(
        cls, group_id: str, module: str, channel_id: str | None = None
    ) -> bool:
        """查看群组是否禁用功能

        参数:
            group_id: 群组id
            module: 模块名称
            channel_id: 频道id

        返回:
            bool: 是否禁用被动
        """
        return await cls.exists(
            group_id=group_id,
            channel_id=channel_id,
            block_plugin__contains=f"<{module},",
        )

    @classmethod
    async def is_superuser_block_task(cls, group_id: str, task: str) -> bool:
        """查看群组是否超级用户禁用被动

        参数:
            group_id: 群组id
            task: 模块名称

        返回:
            bool: 是否禁用被动
        """
        return await cls.exists(
            group_id=group_id,
            superuser_block_task__contains=add_disable_marker(task),
        )

    @classmethod
    async def is_block_task(
        cls, group_id: str, task: str, channel_id: str | None = None
    ) -> bool:
        """查看群组是否禁用被动

        参数:
            group_id: 群组id
            task: 任务模块
            channel_id: 频道id

        返回:
            bool: 是否禁用被动
        """
        task = add_disable_marker(task)
        if not channel_id:
            return await cls.exists(
                group_id=group_id,
                channel_id__isnull=True,
                block_task__contains=task,
            ) or await cls.exists(
                group_id=group_id,
                channel_id__isnull=True,
                superuser_block_task__contains=task,
            )
        return await cls.exists(
            group_id=group_id, channel_id=channel_id, block_task__contains=task
        ) or await cls.exists(
            group_id=group_id,
            channel_id__isnull=True,
            superuser_block_task__contains=task,
        )

    @classmethod
    async def set_block_task(
        cls,
        group_id: str,
        task: str,
        is_superuser: bool = False,
        platform: str | None = None,
    ):
        """禁用群组插件

        参数:
            group_id: 群组id
            task: 任务模块
            is_superuser: 是否为超级用户
            platform: 平台
        """
        group, _ = await cls.get_or_create(
            group_id=group_id, defaults={"platform": platform}
        )
        update_fields = []
        if is_superuser:
            superuser_block_task = convert_module_format(group.superuser_block_task)
            if task not in group.superuser_block_task:
                superuser_block_task.append(task)
                group.superuser_block_task = convert_module_format(superuser_block_task)
                update_fields.append("superuser_block_task")
        elif add_disable_marker(task) not in group.block_task:
            block_task = convert_module_format(group.block_task)
            block_task.append(task)
            group.block_task = convert_module_format(block_task)
            update_fields.append("block_task")
        if update_fields:
            await group.save(update_fields=update_fields)

    @classmethod
    async def set_unblock_task(
        cls,
        group_id: str,
        task: str,
        is_superuser: bool = False,
        platform: str | None = None,
    ):
        """禁用群组插件

        参数:
            group_id: 群组id
            task: 任务模块
            is_superuser: 是否为超级用户
            platform: 平台
        """
        group, _ = await cls.get_or_create(
            group_id=group_id, defaults={"platform": platform}
        )
        update_fields = []
        if is_superuser:
            superuser_block_task = convert_module_format(group.superuser_block_task)
            if task in superuser_block_task:
                superuser_block_task.remove(task)
                group.superuser_block_task = convert_module_format(superuser_block_task)
                update_fields.append("superuser_block_task")
        elif add_disable_marker(task) in group.block_task:
            block_task = convert_module_format(group.block_task)
            block_task.remove(task)
            group.block_task = convert_module_format(block_task)
            update_fields.append("block_task")
        if update_fields:
            await group.save(update_fields=update_fields)

    @classmethod
    def _run_script(cls):
        return [
            "ALTER TABLE group_console ADD superuser_block_plugin"
            " character varying(255) NOT NULL DEFAULT '';",
            "ALTER TABLE group_console ADD superuser_block_task"
            " character varying(255) NOT NULL DEFAULT '';",
        ]
