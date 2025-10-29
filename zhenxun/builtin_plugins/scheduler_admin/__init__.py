from nonebot.plugin import PluginMetadata

from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.utils.enum import PluginType

from . import commands, handlers

__all__ = ["commands", "handlers"]

__plugin_meta__ = PluginMetadata(
    name="定时任务管理",
    description="查看和管理由 SchedulerManager 控制的定时任务。",
    usage="""### 📋 定时任务管理
---
#### 🔍 **查看任务**
-   **命令**: `定时任务 查看 [选项]` (别名: `ls`, `list`)
-   **选项**:
    -   `--all`: 查看所有群组的任务 **(SUPERUSER)**。
    -   `-g <群号>`: 查看指定群组的任务 **(SUPERUSER)**。
    -   `-p <插件名>`: 按插件名筛选。
    -   `--page <页码>`: 指定页码。
-   **说明**:
    -   在群聊中不带选项使用，默认查看本群任务。
    -   在私聊中必须使用 `-g <群号>` 或 `--all`。

#### 📊 **任务状态**
-   **命令**: `定时任务 状态 <任务ID>` (别名: `status`, `info`, `任务状态`)
-   **说明**: 查看单个任务的详细信息和状态。

#### ⚙️ **任务管理 (SUPERUSER)**
-   **设置**: `定时任务 设置 <插件>` (别名: `add`, `开启`)
    -   **选项**:
        -   `<时间选项>`: 详见下文。
        -   `-g <群号|all>`: 指定目标群组。
        -   `--kwargs "<参数>"`: 设置任务参数 (例: `"key=value"`)。
-   **删除**: `定时任务 删除 <ID>` (别名: `del`, `rm`, `remove`, `关闭`, `取消`)
-   **暂停**: `定时任务 暂停 <ID>` (别名: `pause`)
-   **恢复**: `定时任务 恢复 <ID>` (别名: `resume`)
-   **执行**: `定时任务 执行 <ID>` (别名: `trigger`, `run`)
-   **更新**: `定时任务 更新 <ID>` (别名: `update`, `modify`, `修改`)
    -   **选项**:
        -   `<时间选项>`: 详见下文。
        -   `--kwargs "<参数>"`: 更新任务参数。
    -   **批量操作**: `删除/暂停/恢复` 命令支持通过 `-p <插件名>` 或 `--all`
    (当前群) 进行批量操作。

#### 📝 **时间选项 (设置/更新时三选一)**
-   `--cron "<分> <时> <日> <月> <周>"` (例: `--cron "0 8 * * *"`)
-   `--interval <时间间隔>` (例: `--interval 30m`, `2h`, `10s`)
-   `--date "<YYYY-MM-DD HH:MM:SS>"` (例: `--date "2024-01-01 08:00:00"`)
-   `--daily "<HH:MM>"` (例: `--daily "08:30"`)

#### 📚 **其他功能**
-   **命令**: `定时任务 插件列表` (别名: `plugins`)
-   **说明**: 查看所有可设置定时任务的插件 **(SUPERUSER)**。
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1.2",
        plugin_type=PluginType.SUPERUSER,
        configs=[
            RegisterConfig(
                module="SchedulerManager",
                key="ALL_GROUPS_CONCURRENCY_LIMIT",
                value=5,
                help="“所有群组”类型定时任务的并发执行数量限制",
                type=int,
            ),
            RegisterConfig(
                module="SchedulerManager",
                key="JOB_MAX_RETRIES",
                value=2,
                help="定时任务执行失败时的最大重试次数",
                type=int,
            ),
            RegisterConfig(
                module="SchedulerManager",
                key="JOB_RETRY_DELAY",
                value=10,
                help="定时任务执行重试的间隔时间（秒）",
                type=int,
            ),
            RegisterConfig(
                module="SchedulerManager",
                key="SCHEDULER_TIMEZONE",
                value="Asia/Shanghai",
                help="定时任务使用的时区，默认为 Asia/Shanghai",
                type=str,
            ),
            RegisterConfig(
                module="SchedulerManager",
                key="SCHEDULE_ADMIN_LEVEL",
                value=5,
                help="设置'定时任务'系列命令的基础使用权限等级",
                default_value=5,
                type=int,
            ),
            RegisterConfig(
                module="SchedulerManager",
                key="DEFAULT_JITTER_SECONDS",
                value=60,
                help="为多目标定时任务（如 --all, -t）设置的默认触发抖动秒数，避免所有任务同时启动。",  # noqa: E501
                default_value=60,
                type=int,
            ),
            RegisterConfig(
                module="SchedulerManager",
                key="DEFAULT_SPREAD_SECONDS",
                value=300,
                help="为多目标定时任务设置的默认执行分散秒数，将任务执行分散在一个时间窗口内。",
                default_value=300,
                type=int,
            ),
            RegisterConfig(
                module="SchedulerManager",
                key="DEFAULT_INTERVAL_SECONDS",
                value=0,
                help="为多目标定时任务设置的默认串行执行间隔秒数(大于0时生效)，用于控制任务间的固定时间间隔。",
                default_value=0,
                type=int,
            ),
        ],
    ).to_dict(),
)
