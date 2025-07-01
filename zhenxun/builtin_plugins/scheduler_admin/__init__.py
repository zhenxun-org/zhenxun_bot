from nonebot.plugin import PluginMetadata

from zhenxun.configs.utils import PluginExtraData
from zhenxun.utils.enum import PluginType

from . import command  # noqa: F401

__plugin_meta__ = PluginMetadata(
    name="定时任务管理",
    description="查看和管理由 SchedulerManager 控制的定时任务。",
    usage="""
📋 定时任务管理 - 支持群聊和私聊操作

🔍 查看任务:
  定时任务 查看 [-all] [-g <群号>] [-p <插件>] [--page <页码>]
  • 群聊中: 查看本群任务
  • 私聊中: 必须使用 -g <群号> 或 -all 选项 (SUPERUSER)

📊 任务状态:
  定时任务 状态 <任务ID>  或  任务状态 <任务ID>
  • 查看单个任务的详细信息和状态

⚙️ 任务管理 (SUPERUSER):
  定时任务 设置 <插件> [时间选项] [-g <群号> | -g all] [--kwargs <参数>]
  定时任务 删除 <任务ID> | -p <插件> [-g <群号>] | -all
  定时任务 暂停 <任务ID> | -p <插件> [-g <群号>] | -all
  定时任务 恢复 <任务ID> | -p <插件> [-g <群号>] | -all
  定时任务 执行 <任务ID>
  定时任务 更新 <任务ID> [时间选项] [--kwargs <参数>]

📝 时间选项 (三选一):
  --cron "<分> <时> <日> <月> <周>"     # 例: --cron "0 8 * * *"
  --interval <时间间隔>               # 例: --interval 30m, 2h, 10s
  --date "<YYYY-MM-DD HH:MM:SS>"     # 例: --date "2024-01-01 08:00:00"
  --daily "<HH:MM>"                  # 例: --daily "08:30"

📚 其他功能:
  定时任务 插件列表  # 查看所有可设置定时任务的插件 (SUPERUSER)

🏷️ 别名支持:
  查看: ls, list  |  设置: add, 开启  |  删除: del, rm, remove, 关闭, 取消
  暂停: pause  |  恢复: resume  |  执行: trigger, run  |  状态: status, info
  更新: update, modify, 修改  |  插件列表: plugins
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1.2",
        plugin_type=PluginType.SUPERUSER,
        is_show=False,
    ).to_dict(),
)
