from abc import ABC, abstractmethod
from typing import Any, cast

from zhenxun.models.group_console import GroupConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.task_info import TaskInfo
from zhenxun.services.cache import CacheRoot
from zhenxun.services.cache.runtime_cache import (
    PluginInfoMemoryCache,
    TaskInfoMemoryCache,
)
from zhenxun.utils.enum import BlockType, CacheType, PluginType


class SwitchStrategy(ABC):
    """插件与被动技能切换策略基类"""

    @property
    @abstractmethod
    def entity_type_name(self) -> str:
        pass

    @property
    @abstractmethod
    def norm_field(self) -> str:
        """普通的群组禁用字段名"""
        pass

    @property
    @abstractmethod
    def su_field(self) -> str:
        """超级用户群组禁用字段名"""
        pass

    @abstractmethod
    async def get_entity(self, name: str) -> Any:
        """通过名称获取实体信息"""
        pass

    @abstractmethod
    async def check_block_status(self, group_id: str, module: str) -> tuple[bool, bool]:
        """检查目标群组的禁用状态，返回 (is_su_blocked, is_norm_blocked)"""
        pass

    @abstractmethod
    async def get_all_modules(self) -> list[str]:
        """获取所有模块的名称列表"""
        pass

    @abstractmethod
    async def set_default_status(self, entity: Any, status: bool) -> None:
        """设置单个实体的进群默认状态"""
        pass

    @abstractmethod
    async def set_global_status(
        self, entity: Any, status: bool, block_type: BlockType | None = None
    ) -> None:
        """设置单个实体的全局状态"""
        pass

    @abstractmethod
    async def set_all_default_status(self, status: bool) -> None:
        """设置所有实体的进群默认状态"""
        pass

    @abstractmethod
    async def set_all_global_status(self, status: bool) -> None:
        """设置所有实体的全局状态"""
        pass

    @abstractmethod
    async def refresh_cache(self) -> None:
        """刷新相关的内存缓存"""
        pass


class PluginStrategy(SwitchStrategy):
    @property
    def entity_type_name(self) -> str:
        return "功能"

    @property
    def norm_field(self) -> str:
        return "block_plugin"

    @property
    def su_field(self) -> str:
        return "superuser_block_plugin"

    async def get_entity(self, name: str) -> Any:
        if name.isdigit():
            return await PluginInfo.get_or_none(id=int(name))
        return await PluginInfo.get_or_none(
            name=name, load_status=True, plugin_type__not=PluginType.PARENT
        )

    async def check_block_status(self, group_id: str, module: str) -> tuple[bool, bool]:
        is_su_blocked = await GroupConsole.is_superuser_block_plugin(group_id, module)
        is_norm_blocked = await GroupConsole.is_normal_block_plugin(group_id, module)
        return is_su_blocked, is_norm_blocked

    async def get_all_modules(self) -> list[str]:
        return cast(
            list[str],
            await PluginInfo.filter(plugin_type=PluginType.NORMAL).values_list(
                "module", flat=True
            ),
        )

    async def set_default_status(self, entity: PluginInfo, status: bool) -> None:
        entity.default_status = status
        await entity.save(update_fields=["default_status"])

    async def set_global_status(
        self, entity: PluginInfo, status: bool, block_type: BlockType | None = None
    ) -> None:
        entity.block_type = block_type
        entity.status = not bool(block_type)
        await entity.save(update_fields=["status", "block_type"])

    async def set_all_default_status(self, status: bool) -> None:
        await PluginInfo.filter(plugin_type=PluginType.NORMAL).update(
            default_status=status
        )
        await self.refresh_cache()

    async def set_all_global_status(self, status: bool) -> None:
        await PluginInfo.filter(plugin_type=PluginType.NORMAL).update(
            status=status, block_type=None if status else BlockType.ALL
        )
        await self.refresh_cache()

    async def refresh_cache(self) -> None:
        await CacheRoot.invalidate_cache(CacheType.PLUGINS)
        await PluginInfoMemoryCache.refresh()


class TaskStrategy(SwitchStrategy):
    @property
    def entity_type_name(self) -> str:
        return "被动"

    @property
    def norm_field(self) -> str:
        return "block_task"

    @property
    def su_field(self) -> str:
        return "superuser_block_task"

    async def get_entity(self, name: str) -> Any:
        return await TaskInfo.get_or_none(name=name)

    async def check_block_status(self, group_id: str, module: str) -> tuple[bool, bool]:
        is_su_blocked = await GroupConsole.is_superuser_block_task(group_id, module)
        is_norm_blocked = await GroupConsole.is_block_task(group_id, module)
        return is_su_blocked, is_norm_blocked

    async def get_all_modules(self) -> list[str]:
        return cast(list[str], await TaskInfo.all().values_list("module", flat=True))

    async def set_default_status(self, entity: TaskInfo, status: bool) -> None:
        entity.default_status = status
        await entity.save(update_fields=["default_status"])

    async def set_global_status(
        self, entity: TaskInfo, status: bool, block_type: BlockType | None = None
    ) -> None:
        entity.status = status
        await entity.save(update_fields=["status"])

    async def set_all_default_status(self, status: bool) -> None:
        await TaskInfo.all().update(default_status=status)
        await self.refresh_cache()

    async def set_all_global_status(self, status: bool) -> None:
        await TaskInfo.all().update(status=status)
        await self.refresh_cache()

    async def refresh_cache(self) -> None:
        await TaskInfoMemoryCache.refresh()


def get_strategy(is_task: bool) -> SwitchStrategy:
    """工厂方法：获取对应的处理策略"""
    return TaskStrategy() if is_task else PluginStrategy()
