import nonebot
from nonebot import get_loaded_plugins
from nonebot.drivers import Driver
from nonebot.plugin import Plugin
from nonebot.utils import is_coroutine_callable
from nonebot_plugin_apscheduler import scheduler

from zhenxun.configs.utils import PluginExtraData, Task
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.task_info import TaskInfo
from zhenxun.services.log import logger
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

driver: Driver = nonebot.get_driver()


async def _handle_setting(
    plugin: Plugin,
    task_info_list: list[tuple[bool, TaskInfo]],
    task_list: list[Task],
):
    """处理插件设置

    参数:
        plugin: Plugin
        task_info_list: 被动技能db数据列表
        task_list: 被动技能列表
    """
    metadata = plugin.metadata
    if not metadata:
        return
    extra = metadata.extra
    extra_data = PluginExtraData(**extra)
    if extra_data.tasks:
        task_info_list.extend(
            (
                task.create_status,
                TaskInfo(
                    module=task.module,
                    name=task.name,
                    status=task.status,
                    default_status=task.default_status,
                ),
            )
            for task in extra_data.tasks
        )
        task_list.extend(extra_data.tasks)


async def update_to_group(create_list: list[tuple[bool, TaskInfo]]):
    """根据创建时状态对群组进行被动技能更新

    参数:
        create_list: 被动技能创建列表
    """
    if blocks := [t[1].module for t in create_list if not t[0]]:
        if group_list := await GroupConsole.all():
            for group in group_list:
                block_tasks = list(
                    set(CommonUtils.convert_module_format(group.block_task) + blocks)
                )
                group.block_task = CommonUtils.convert_module_format(block_tasks)
            await GroupConsole.bulk_update(group_list, ["block_task"], 10)


async def to_db(
    load_task: list[str],
    create_list: list[tuple[bool, TaskInfo]],
    update_list: list[TaskInfo],
):
    """将被动技能保存至数据库

    参数:
        load_task: 已加载的被动技能模块
        create_list: 被动技能创建列表
        update_list: 被动技能更新列表
    """
    if create_list:
        _create_list = [t[1] for t in create_list]
        await TaskInfo.bulk_create(_create_list, 10)
        await update_to_group(create_list)
    if update_list:
        await TaskInfo.bulk_update(
            update_list,
            ["run_time", "name"],
            10,
        )
    if load_task:
        await TaskInfo.filter(module__in=load_task).update(load_status=True)
        await TaskInfo.filter(module__not_in=load_task).update(load_status=False)


async def get_run_task(task: Task, *args, **kwargs):
    is_run = False
    if task.check:
        if is_coroutine_callable(task.check):
            if await task.check(*task.check_args):
                is_run = True
        elif task.check(*task.check_args):
            is_run = True
    else:
        bot = task.check_args[0]
        group_id = task.check_args[1]
        if not await CommonUtils.task_is_block(bot, task.module, group_id):
            is_run = True
    if is_run and task.run_func:
        if is_coroutine_callable(task.run_func):
            await task.run_func(*args, **kwargs)
        else:
            task.run_func(*args, **kwargs)


async def create_schedule(task: Task):
    scheduler_model = task.scheduler
    if not scheduler_model or not task.run_func:
        return
    try:
        scheduler.add_job(
            get_run_task,
            scheduler_model.trigger,
            run_date=scheduler_model.run_date,
            hour=scheduler_model.hour,
            minute=scheduler_model.minute,
            second=scheduler_model.second,
            id=scheduler_model.id,
            max_instances=scheduler_model.max_instances,
            args=scheduler_model.args,
            kwargs=scheduler_model.kwargs,
        )
        logger.debug(f"成功动态创建定时任务: {task.name}({task.module})")
    except Exception as e:
        logger.error(f"动态创建定时任务 {task.name}({task.module}) 失败", e=e)


@PriorityLifecycle.on_startup(priority=5)
async def _():
    """
    初始化插件数据配置
    """
    task_list: list[Task] = []
    task_info_list: list[tuple[bool, TaskInfo]] = []
    for plugin in get_loaded_plugins():
        await _handle_setting(plugin, task_info_list, task_list)
    if not task_info_list:
        await TaskInfo.all().update(load_status=False)
        return
    module_dict = {t[1]: t[0] for t in await TaskInfo.all().values_list("id", "module")}
    load_task = []
    create_list = []
    update_list = []
    for status, task in task_info_list:
        if task.module not in module_dict:
            create_list.append((status, task))
        else:
            task.id = module_dict[task.module]
            update_list.append(task)
        load_task.append(task.module)
    await to_db(load_task, create_list, update_list)
    # db_task = await TaskInfo.filter(load_status=True, status=True).values_list(
    #     "module", flat=True
    # )
    # task_list = [t for t in task_list if t.module in db_task]
    # for task in task_list:
    #     create_schedule(task)
