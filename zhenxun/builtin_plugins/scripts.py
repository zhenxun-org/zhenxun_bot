from zhenxun.models.group_console import GroupConsole
from zhenxun.utils.manager.priority_manager import PriorityLifecycle


@PriorityLifecycle.on_startup(priority=5)
async def _():
    """开启/禁用插件格式修改"""
    _, is_create = await GroupConsole.get_or_create(group_id=133133133)
    """标记"""
    if is_create:
        data_list = []
        for group in await GroupConsole.all():
            if group.block_plugin:
                if modules := group.block_plugin.split(","):
                    block_plugin = "".join(
                        (f"{module}," if module.startswith("<") else f"<{module},")
                        for module in modules
                        if module.strip()
                    )
                    group.block_plugin = block_plugin.replace("<,", "")
            if group.block_task:
                if modules := group.block_task.split(","):
                    block_task = "".join(
                        (f"{module}," if module.startswith("<") else f"<{module},")
                        for module in modules
                        if module.strip()
                    )
                    group.block_task = block_task.replace("<,", "")
            data_list.append(group)
        await GroupConsole.bulk_update(data_list, ["block_plugin", "block_task"], 10)
