from typing import Any, TypeVar, overload

from pydantic import BaseModel, ValidationError
import ujson as json

from zhenxun.configs.config import Config
from zhenxun.models.group_plugin_setting import GroupPluginSetting
from zhenxun.services.cache import BoundedTTLCache
from zhenxun.services.data_access import DataAccess
from zhenxun.services.log import logger
from zhenxun.utils.pydantic_compat import model_dump, model_validate, parse_as

T = TypeVar("T", bound=BaseModel)


class GroupSettingsService:
    """
    一个用于管理插件分群配置的服务。
    集成了聚合缓存、批量操作和版本迁移功能。
    """

    def __init__(self):
        self.dao = DataAccess(GroupPluginSetting)
        self._cache = BoundedTTLCache[str, dict[str, Any]](
            "GROUP_PLUGIN_SETTINGS_VIEW",
            ttl_seconds=600,
            max_items=10000,
        )

    @staticmethod
    def _build_cache_key(group_id: str, plugin_name: str) -> str:
        return f"{group_id}:{plugin_name}"

    async def _clear_merged_cache(self, group_id: str, plugin_name: str) -> None:
        await self._cache.delete(self._build_cache_key(group_id, plugin_name))

    async def set(
        self, group_id: str, plugin_name: str, settings_model: BaseModel
    ) -> None:
        """
        为一个插件在指定群组中设置完整的配置模型。

        参数:
            group_id: 目标群组ID。
            plugin_name: 插件的模块名。
            settings_model: 包含完整配置的Pydantic模型实例。
        """
        settings_dict = model_dump(settings_model)
        json_value = json.dumps(settings_dict, ensure_ascii=False)

        await self.dao.update_or_create(
            defaults={"settings": json_value},  # type: ignore
            group_id=group_id,
            plugin_name=plugin_name,
        )

        await self.dao.clear_cache(group_id=group_id, plugin_name=plugin_name)
        await self._clear_merged_cache(group_id, plugin_name)

    async def set_key_value(
        self, group_id: str, plugin_name: str, key: str, value: Any
    ) -> None:
        """为一个插件在指定群组中设置单个配置项的值。"""
        setting_entry, _ = await GroupPluginSetting.get_or_create(
            defaults={"settings": {}},
            group_id=group_id,
            plugin_name=plugin_name,
        )

        if not isinstance(setting_entry.settings, dict):
            setting_entry.settings = {}

        setting_entry.settings[key] = value
        await setting_entry.save(update_fields=["settings"])
        await self.dao.clear_cache(group_id=group_id, plugin_name=plugin_name)
        await self._clear_merged_cache(group_id, plugin_name)

    async def reset_key(self, group_id: str, plugin_name: str, key: str) -> bool:
        """重置单个配置项"""
        setting = await self.dao.get_or_none(group_id=group_id, plugin_name=plugin_name)
        if setting and isinstance(setting.settings, dict) and key in setting.settings:
            del setting.settings[key]
            if not setting.settings:
                await setting.delete()
            else:
                await setting.save(update_fields=["settings"])
            await self.dao.clear_cache(group_id=group_id, plugin_name=plugin_name)
            await self._clear_merged_cache(group_id, plugin_name)
            return True
        return False

    async def get(
        self, group_id: str, plugin_name: str, key: str, default: Any = None
    ) -> Any:
        """
        获取一个分群配置项的值，如果群组未单独设置，则回退到全局默认值。

        参数:
            group_id: 目标群组ID。
            plugin_name: 插件的模块名。
            key: 配置项的键。
            default: 如果找不到配置项，返回的默认值。

        返回:
            配置项的值。
        """
        full_settings = await self.get_all_for_plugin(group_id, plugin_name)
        return full_settings.get(key, default)

    async def reset_all_for_plugin(self, group_id: str, plugin_name: str) -> bool:
        """
        重置一个插件在指定群组的配置，使其回退到全局默认值。
        这通过删除数据库中的对应记录来实现。

        参数:
            group_id: 目标群组ID。
            plugin_name: 插件的模块名。

        返回:
            bool: 如果成功删除了一个条目，则返回 True，否则返回 False。
        """
        deleted_count = await self.dao.delete(
            group_id=group_id, plugin_name=plugin_name
        )

        if deleted_count > 0:
            await self.dao.clear_cache(group_id=group_id, plugin_name=plugin_name)
            await self._clear_merged_cache(group_id, plugin_name)
            logger.debug(f"已重置插件 '{plugin_name}' 在群组 '{group_id}' 的配置。")
            return True

        return False

    @overload
    async def get_all_for_plugin(
        self, group_id: str, plugin_name: str, *, parse_model: type[T]
    ) -> T: ...

    @overload
    async def get_all_for_plugin(
        self, group_id: str, plugin_name: str, *, parse_model: None = None
    ) -> dict[str, Any]: ...

    async def get_all_for_plugin(
        self, group_id: str, plugin_name: str, *, parse_model: type[T] | None = None
    ) -> T | dict[str, Any]:
        """
        获取一个插件在指定群组中的完整配置，应用了“继承与覆盖”逻辑。
        它首先获取全局默认配置，然后用数据库中存储的群组特定配置覆盖它。

        参数:
            group_id: 目标群组ID。
            plugin_name: 插件的模块名。
            parse_model: (可选) Pydantic模型，用于解析和验证配置。
        """
        cache_key = self._build_cache_key(group_id, plugin_name)
        cached_settings = await self._cache.get(cache_key)
        if cached_settings is not None:
            logger.debug(f"缓存命中: {cache_key}")
            if parse_model:
                try:
                    return parse_as(parse_model, cached_settings)
                except (ValidationError, TypeError) as e:
                    logger.warning(
                        f"缓存数据 '{cache_key}' 与模型 '{parse_model.__name__}' "
                        f"不匹配: {e}。将从数据库重新加载。"
                    )
            else:
                return cached_settings

        logger.debug(f"缓存未命中: {cache_key}，从数据库加载。")

        global_config_group = Config.get(plugin_name)
        final_settings_dict = {
            key: global_config_group.get(key, build_model=False)
            for key in global_config_group.configs.keys()
        }

        group_setting_entry = await self.dao.get_or_none(
            group_id=group_id, plugin_name=plugin_name
        )
        if group_setting_entry:
            try:
                group_specific_settings = group_setting_entry.settings
                if isinstance(group_specific_settings, dict):
                    final_settings_dict.update(group_specific_settings)
                else:
                    logger.warning(
                        f"群组 {group_id} 插件 '{plugin_name}' 的配置格式不正确"
                        f"（不是字典），已忽略。"
                    )
            except Exception as e:
                logger.warning(
                    f"加载群组 {group_id} 插件 '{plugin_name}' 的特定配置时出错: {e}"
                )

        await self._cache.set(cache_key, final_settings_dict)

        if parse_model:
            try:
                return parse_as(parse_model, final_settings_dict)
            except (ValidationError, TypeError) as e:
                logger.warning(
                    f"插件 '{plugin_name}' 的配置无法解析为 '{parse_model.__name__}'。"
                    f"值: {final_settings_dict}, 错误: {e}。将返回一个默认模型实例。"
                )
                return parse_as(parse_model, {})

        return final_settings_dict

    async def set_bulk(
        self, group_ids: list[str], plugin_name: str, key: str, value: Any
    ) -> tuple[int, int]:
        """
        为多个群组批量设置同一个配置项。

        参数:
            group_ids: 目标群组ID列表。
            plugin_name: 插件模块名。
            key: 配置项的键。
            value: 要设置的值。

        返回:
            一个元组 (updated_count, created_count)。
        """
        if not group_ids:
            return 0, 0

        for group_id in group_ids:
            current_settings = await self.get_all_for_plugin(group_id, plugin_name)
            current_settings[key] = value
            await self.set(
                group_id, plugin_name, model_validate(BaseModel, current_settings)
            )
        return len(group_ids), 0


group_settings_service = GroupSettingsService()
