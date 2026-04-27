from typing import TYPE_CHECKING, Any, ClassVar, cast, overload
from typing_extensions import Self

from tortoise import fields
from tortoise.backends.base.client import BaseDBAsyncClient

from zhenxun.configs.config import BotConfig
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.task_info import TaskInfo
from zhenxun.services.cache import CacheRoot
from zhenxun.services.cache.runtime_cache import GroupMemoryCache
from zhenxun.services.data_access import DataAccess
from zhenxun.services.db_context import Model
from zhenxun.utils.enum import CacheType, DbLockType, PluginType

if TYPE_CHECKING:
    from zhenxun.services.cache.runtime_cache import GroupSnapshot


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
        indexes = [  # noqa: RUF012
            ("group_id",)
        ]

    cache_type = CacheType.GROUPS
    """缓存类型"""
    cache_key_field = ("group_id", "channel_id")
    """缓存键字段"""
    enable_lock: ClassVar[list[DbLockType]] = [DbLockType.CREATE, DbLockType.UPSERT]
    """开启锁"""

    @classmethod
    async def _get_task_modules(cls, *, default_status: bool) -> list[str]:
        """获取默认禁用的任务模块

        返回:
            list[str]: 任务模块列表
        """
        return cast(
            list[str],
            await TaskInfo.get_modules(
                default_status=default_status,
                load_status=None,
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
            await PluginInfo.get_plugins_values_list(
                "module",
                load_status=None,
                filter_parent=False,
                plugin_type__in=[PluginType.NORMAL, PluginType.DEPENDANT],
                default_status=default_status,
            ),
        )

    @classmethod
    async def _update_cache(cls, instance):
        """更新缓存

        参数:
            instance: 需要更新缓存的实例
        """
        if cache_type := cls.get_cache_type():
            key = cls.get_cache_key(instance)
            if key is not None:
                await CacheRoot.invalidate_cache(cache_type, key)

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

        # 更新缓存
        await cls._update_cache(group)
        await GroupMemoryCache.upsert_from_model(group)

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

        # 更新缓存
        if is_create:
            await cls._update_cache(group)
            await GroupMemoryCache.upsert_from_model(group)

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

        # 更新缓存
        await cls._update_cache(group)
        await GroupMemoryCache.upsert_from_model(group)

        return group, is_create

    async def save(self, *args, **kwargs):
        await super().save(*args, **kwargs)
        await GroupMemoryCache.upsert_from_model(self)

    async def delete(self, *args, **kwargs):
        group_id = self.group_id
        channel_id = self.channel_id
        await super().delete(*args, **kwargs)
        await GroupMemoryCache.remove(group_id, channel_id)

    @classmethod
    async def get_group(
        cls,
        group_id: str,
        channel_id: str | None = None,
        clean_duplicates: bool = True,
    ) -> "GroupSnapshot | None":
        return GroupMemoryCache.get_if_ready(group_id, channel_id)

    @classmethod
    async def get_group_db(
        cls,
        group_id: str,
        channel_id: str | None = None,
        clean_duplicates: bool = True,
    ) -> Self | None:
        """获取群组（数据库）"""
        dao = DataAccess(cls)
        if channel_id:
            return await dao.safe_get_or_none(
                group_id=group_id,
                channel_id=channel_id,
                clean_duplicates=clean_duplicates,
            )
        return await dao.safe_get_or_none(
            group_id=group_id,
            channel_id__isnull=True,
            clean_duplicates=clean_duplicates,
        )

    @classmethod
    async def is_super_group(cls, group_id: str) -> bool:
        group = GroupMemoryCache.get_if_ready(group_id, None)
        return bool(group and group.is_super)

    @classmethod
    async def is_superuser_block_plugin(cls, group_id: str, module: str) -> bool:
        if group := GroupMemoryCache.get_if_ready(group_id, None):
            return bool(
                group.superuser_block_plugin_set
                and module in group.superuser_block_plugin_set
            )
        else:
            return False

    @classmethod
    async def is_block_plugin(cls, group_id: str, module: str) -> bool:
        if group := GroupMemoryCache.get_if_ready(group_id, None):
            return (
                True
                if group.block_plugin_set and module in group.block_plugin_set
                else bool(
                    group.superuser_block_plugin_set
                    and module in group.superuser_block_plugin_set
                )
            )
        else:
            return False

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

        # 更新缓存
        await cls._update_cache(group)

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

        # 更新缓存
        await cls._update_cache(group)

    @classmethod
    async def is_normal_block_plugin(
        cls, group_id: str, module: str, channel_id: str | None = None
    ) -> bool:
        if group := GroupMemoryCache.get_if_ready(group_id, channel_id):
            return bool(group.block_plugin_set and module in group.block_plugin_set)
        else:
            return False

    @classmethod
    async def is_superuser_block_task(cls, group_id: str, task: str) -> bool:
        if group := GroupMemoryCache.get_if_ready(group_id, None):
            return bool(
                group.superuser_block_task_set
                and task in group.superuser_block_task_set
            )
        else:
            return False

    @classmethod
    async def is_block_task(
        cls, group_id: str, task: str, channel_id: str | None = None
    ) -> bool:
        if not channel_id:
            group = GroupMemoryCache.get_if_ready(group_id, None)
            if not group:
                return False
            if group.block_task_set and task in group.block_task_set:
                return True
            return bool(
                group.superuser_block_task_set
                and task in group.superuser_block_task_set
            )
        group = GroupMemoryCache.get_if_ready(group_id, channel_id)
        if group and group.block_task_set and task in group.block_task_set:
            return True
        super_group = GroupMemoryCache.get_if_ready(group_id, None)
        return bool(
            super_group
            and super_group.superuser_block_task_set
            and task in super_group.superuser_block_task_set
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

        # 更新缓存
        await cls._update_cache(group)

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

        # 更新缓存
        await cls._update_cache(group)

    @classmethod
    def _run_script(cls):
        db_type = (BotConfig.get_sql_type() or "").lower()

        scripts = [
            "ALTER TABLE group_console ADD superuser_block_plugin"
            " Text NOT NULL DEFAULT '';",
            "ALTER TABLE group_console ADD superuser_block_task"
            " Text NOT NULL DEFAULT '';",
            "CREATE INDEX idx_group_console_group_id ON group_console(group_id);",
            (
                "CREATE INDEX idx_group_console_group_null_channel ON "
                "group_console(group_id) WHERE channel_id IS NULL;"
            ),
        ]

        if "postgres" in db_type:
            scripts.extend(
                [
                    ("ALTER TABLE group_console ALTER COLUMN block_plugin TYPE TEXT;"),
                    (
                        "ALTER TABLE group_console ALTER COLUMN "
                        "superuser_block_plugin TYPE TEXT;"
                    ),
                    ("ALTER TABLE group_console ALTER COLUMN block_task TYPE TEXT;"),
                    (
                        "ALTER TABLE group_console ALTER COLUMN "
                        "superuser_block_task TYPE TEXT;"
                    ),
                ]
            )
        elif "mysql" in db_type:
            scripts.extend(
                [
                    ("ALTER TABLE group_console MODIFY COLUMN block_plugin TEXT;"),
                    (
                        "ALTER TABLE group_console MODIFY COLUMN "
                        "superuser_block_plugin TEXT;"
                    ),
                    ("ALTER TABLE group_console MODIFY COLUMN block_task TEXT;"),
                    (
                        "ALTER TABLE group_console MODIFY COLUMN "
                        "superuser_block_task TEXT;"
                    ),
                ]
            )

        return scripts
