from nonebot.plugin import PluginMetadata

from zhenxun.configs.utils import PluginExtraData
from zhenxun.utils.enum import PluginType

from . import command  # noqa: F401

__plugin_meta__ = PluginMetadata(
    name="定时任务管理",
    description="查看和管理由 SchedulerManager 控制的定时任务。",
    usage="""
    定时任务 查看 [-all] [-g <群号>] [-p <插件>] [--page <页码>] : 查看定时任务
    定时任务 设置 <插件> <时间> [-g <群号> | --all] [--kwargs <参数>] :
        设置/开启定时任务 (SUPERUSER)
    定时任务 删除 <任务ID> : 通过ID删除任务 (SUPERUSER)
    定时任务 删除 -p <插件> [-g <群号> | --all] : 通过插件+群组删除任务 (SUPERUSER)
    定时任务 删除 -all [-g <群号>] : 删除所有群组的所有任务，-g 指定群 (SUPERUSER)
    定时任务 暂停 <任务ID> | -all [-g <群号>] | -p <插件> [-g <群号> | -all] :
        暂停任务，-all 为所有群组 (SUPERUSER)
    定时任务 恢复 <任务ID> | -all [-g <群号>] | -p <插件> [-g <群号> | -all] :
        恢复任务，-all 为所有群组 (SUPERUSER)
    定时任务 执行 <任务ID> : 立即手动执行一次任务 (SUPERUSER)
    定时任务 更新 <任务ID> [--time <时间>] [--kwargs <参数>] : 更新任务配置 (SUPERUSER)
    定时任务 插件列表 : 查看所有可设置定时任务的插件 (SUPERUSER)

    别名支持:
    - 查看: ls, list
    - 设置: add, 开启
    - 删除: del, rm, remove, 关闭, 取消
    - 暂停: pause
    - 恢复: resume
    - 执行: trigger, run
    - 更新: update, modify, 修改
    - 插件列表: plugins
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1.0",
        plugin_type=PluginType.SUPERUSER,
        is_show=False,
    ).to_dict(),
)
