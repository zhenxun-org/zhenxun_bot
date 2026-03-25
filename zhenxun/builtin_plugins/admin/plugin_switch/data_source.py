from nonebot.adapters import Bot

from zhenxun.models.group_console import GroupConsole
from zhenxun.services.cache import CacheRoot
from zhenxun.services.cache.runtime_cache import GroupMemoryCache
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.enum import BlockType, CacheType
from zhenxun.utils.platform import PlatformUtils

from .strategy import get_strategy


class PluginManager:
    @staticmethod
    def _modify_block_string(current_str: str, module: str, add: bool) -> str:
        """辅助: 添加或移除禁用模块字符串"""
        items = CommonUtils.convert_module_format(current_str)
        if add:
            if module not in items:
                items.append(module)
        else:
            if module in items:
                items.remove(module)
        return CommonUtils.convert_module_format(items)

    @classmethod
    async def _calculate_affected_groups(
        cls,
        target_groups: set[str],
        status: bool,
        is_whitelist_mode: bool,
        bot: Bot | None,
    ) -> tuple[set[str], set[str]]:
        """提取公用的目标群组计算逻辑（白名单/普通模式交并集）"""
        groups_to_open = set()
        groups_to_close = set()
        clean_targets = {str(gid) for gid in target_groups if gid}

        if is_whitelist_mode and status:
            if bot:
                active_groups, _ = await PlatformUtils.get_group_list(
                    bot, only_group=True
                )
                all_group_set = {str(g.group_id) for g in active_groups if g.group_id}
            else:
                all_group_ids = await GroupConsole.all().values_list(
                    "group_id", flat=True
                )
                all_group_set = {str(gid) for gid in all_group_ids}
            groups_to_open = clean_targets
            groups_to_close = all_group_set - clean_targets
        else:
            if status:
                groups_to_open = clean_targets
            else:
                groups_to_close = clean_targets
        return groups_to_open, groups_to_close

    @classmethod
    async def batch_update_status(
        cls,
        name: str,
        target_groups: set[str],
        status: bool,
        is_task: bool = False,
        is_superuser: bool = False,
        is_whitelist_mode: bool = False,
        bot: Bot | None = None,
        use_su_field: bool = False,
    ) -> str:
        """批量更新状态 (已用策略模式完全重构)"""
        strategy = get_strategy(is_task)
        entity = await strategy.get_entity(name)
        if not entity:
            return f"未找到{strategy.entity_type_name}: {name}"

        module_name = entity.module
        norm_field = strategy.norm_field
        su_field = strategy.su_field

        groups_to_open, groups_to_close = await cls._calculate_affected_groups(
            target_groups, status, is_whitelist_mode, bot
        )

        affected_ids = groups_to_open | groups_to_close
        if not affected_ids:
            return "没有目标群组需要操作。"

        for gid in groups_to_open | groups_to_close:
            platform = bot.adapter.get_name() if bot else "qq"
            await GroupConsole.get_or_create(
                group_id=gid, defaults={"platform": platform}
            )

        groups_obj = await GroupConsole.filter(group_id__in=list(affected_ids)).all()
        update_list = []
        opened_groups: set[str] = set()
        closed_groups: set[str] = set()

        for group in groups_obj:
            gid = str(group.group_id)
            norm_val = getattr(group, norm_field)
            su_val = getattr(group, su_field)
            new_norm_val, new_su_val = norm_val, su_val
            is_changed = False
            change_type = None

            if gid in groups_to_open:
                new_norm_val = cls._modify_block_string(norm_val, module_name, False)
                if is_superuser:
                    new_su_val = cls._modify_block_string(su_val, module_name, False)
                if norm_val != new_norm_val or su_val != new_su_val:
                    is_changed = True
                    change_type = "open"
            elif gid in groups_to_close:
                if is_superuser and use_su_field:
                    new_su_val = cls._modify_block_string(su_val, module_name, True)
                else:
                    new_norm_val = cls._modify_block_string(norm_val, module_name, True)
                if norm_val != new_norm_val or su_val != new_su_val:
                    is_changed = True
                    change_type = "close"

            if is_changed:
                setattr(group, norm_field, new_norm_val)
                setattr(group, su_field, new_su_val)
                update_list.append(group)
                if change_type == "open":
                    opened_groups.add(gid)
                elif change_type == "close":
                    closed_groups.add(gid)

        if update_list:
            await GroupConsole.bulk_update(
                update_list, [norm_field, su_field], batch_size=500
            )
            await CacheRoot.clear(CacheType.GROUPS)
            for group in update_list:
                await GroupMemoryCache.upsert_from_model(group)

        item_str = strategy.entity_type_name
        mode_str = "(白名单模式)" if is_whitelist_mode else ""

        if not update_list:
            if is_whitelist_mode:
                return f"目标群组的 {item_str} {name} 已符合白名单配置，无需重复操作。"
            status_desc = "开启" if status else ("系统禁用" if use_su_field else "关闭")
            return (
                f"目标群组的 {item_str} {name} 均已处于 {status_desc} 状态，"
                "无需重复操作。"
            )

        opened_count, closed_count = len(opened_groups), len(closed_groups)

        if status:
            su_hint = " (已同步解除系统禁用)" if is_superuser else ""
            success_msg = f"已开启 {opened_count} 个群组的 {item_str} {name}{su_hint}"
        else:
            if is_superuser and use_su_field:
                success_msg = f"已系统级禁用 {closed_count} 个群组的 {item_str} {name}"
            else:
                success_msg = f"已在 {closed_count} 个群组中关闭了 {item_str} {name}"

        if is_whitelist_mode:
            msg_parts = []
            if opened_count > 0:
                msg_parts.append(f"已开启 {opened_count} 个群组")
            if closed_count > 0:
                msg_parts.append(f"已关闭 {closed_count} 个群组")
            return f"{'，'.join(msg_parts)} 的 {item_str} {name} {mode_str}。"

        return f"{success_msg}。"

    @classmethod
    async def set_default_status(
        cls, plugin_name: str, status: bool, is_task: bool = False
    ) -> str:
        strategy = get_strategy(is_task)
        entity = await strategy.get_entity(plugin_name)
        if entity:
            await strategy.set_default_status(entity, status)
            status_text = "开启" if status else "关闭"
            return (
                f"成功将 {getattr(entity, 'name', plugin_name)} "
                f"进群默认状态修改为: {status_text}"
            )
        return "没有找到这个功能喔..."

    @classmethod
    async def set_all_plugin_status(
        cls,
        status: bool,
        is_default: bool = False,
        group_id: str | None = None,
        is_task: bool = False,
        is_superuser: bool = False,
        use_su_field: bool = False,
    ) -> str:
        strategy = get_strategy(is_task)
        type_str = strategy.entity_type_name

        if is_default:
            await strategy.set_all_default_status(status)
            return (
                f"成功将所有{type_str}进群默认状态修改为: "
                f"{'开启' if status else '关闭'}"
            )

        if group_id:
            if group := await GroupConsole.get_group_db(group_id=group_id):
                norm_field = strategy.norm_field
                su_field = strategy.su_field
                module_list = await strategy.get_all_modules()
                all_modules_str = CommonUtils.convert_module_format(module_list)
                update_fields = []

                if status:
                    if is_superuser:
                        setattr(group, norm_field, "")
                        setattr(group, su_field, "")
                        update_fields.extend([norm_field, su_field])
                        msg = f"成功将此群组所有{type_str}完全开启 (包括解除系统禁用)"
                    else:
                        setattr(group, norm_field, "")
                        update_fields.append(norm_field)
                        msg = f"成功开启此群组所有{type_str}"
                else:
                    if is_superuser and use_su_field:
                        setattr(group, su_field, all_modules_str)
                        update_fields.append(su_field)
                        msg = f"已由超级用户系统级禁用此群组所有{type_str}"
                    else:
                        setattr(group, norm_field, all_modules_str)
                        update_fields.append(norm_field)
                        msg = f"成功关闭此群组所有{type_str}"

                await group.save(update_fields=update_fields)
                return f"{msg}。"
            return "获取群组失败..."

        await strategy.set_all_global_status(status)
        return f"成功将所有{type_str}全局状态修改为: {'开启' if status else '关闭'}"

    @classmethod
    async def superuser_set_status(
        cls,
        plugin_name: str,
        status: bool,
        block_type: BlockType | None,
        group_id: str | None,
        is_task: bool = False,
    ) -> str:
        strategy = get_strategy(is_task)
        entity = await strategy.get_entity(plugin_name)
        action_cn = "开启" if status else "关闭"

        if entity:
            if group_id:
                is_su_blocked, _ = await strategy.check_block_status(
                    group_id, entity.module
                )
                if status and is_su_blocked:
                    await cls.batch_update_status(
                        plugin_name,
                        {group_id},
                        True,
                        is_task=is_task,
                        is_superuser=True,
                    )
                    return f"已成功{action_cn}群组 {group_id} 的 {plugin_name} 功能!"
                if not status and not is_su_blocked:
                    await cls.batch_update_status(
                        plugin_name,
                        {group_id},
                        False,
                        is_task=is_task,
                        is_superuser=True,
                        use_su_field=True,
                    )
                    return f"已成功{action_cn}群组 {group_id} 的 {plugin_name} 功能!"
                return f"此群组该功能已被超级用户{action_cn}，不要重复操作..."

            await strategy.set_global_status(entity, status, block_type)
            await strategy.refresh_cache()

            if not block_type or block_type == BlockType.ALL:
                return f"已成功将 {entity.name} 全局{action_cn}!"
            if block_type == BlockType.GROUP:
                return f"已成功将 {entity.name} 全局群组{action_cn}!"
            if block_type == BlockType.PRIVATE:
                return f"已成功将 {entity.name} 全局私聊{action_cn}!"

        return "没有找到这个功能喔..."

    @classmethod
    async def batch_set_group_active_status(
        cls,
        target_groups: set[str],
        status: bool,
        is_whitelist_mode: bool = False,
        bot: Bot | None = None,
    ) -> str:
        """批量设置群组激活状态 (休眠/醒来) - 采用与插件相同的目标计算逻辑"""
        groups_to_wake, groups_to_sleep = await cls._calculate_affected_groups(
            target_groups, status, is_whitelist_mode, bot
        )

        affected_ids = groups_to_wake | groups_to_sleep
        if not affected_ids:
            return "没有目标群组需要操作。"

        if groups_to_wake:
            await GroupConsole.filter(group_id__in=list(groups_to_wake)).update(
                status=True
            )
        if groups_to_sleep:
            await GroupConsole.filter(group_id__in=list(groups_to_sleep)).update(
                status=False
            )

        await CacheRoot.clear(CacheType.GROUPS)
        await GroupMemoryCache.refresh()

        action_str = "醒来" if status else "休眠"
        return f"已完成目标群组的 {action_str} 操作。"
