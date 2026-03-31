from typing import Any

from nonebot.adapters import Bot

from zhenxun import ui
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.task_info import TaskInfo
from zhenxun.ui.models import LayoutData, StatusBadgeCell, TextCell
from zhenxun.utils.enum import PluginType
from zhenxun.utils.exception import GroupInfoNotFound
from zhenxun.utils.platform import PlatformUtils

from .strategy import get_strategy


async def build_plugin() -> bytes:
    """构造插件状态图片"""
    column_name = [
        "ID",
        "模块",
        "名称",
        "全局状态",
        "禁用类型",
        "加载状态",
        "菜单分类",
        "作者",
        "版本",
        "金币花费",
    ]
    plugin_list = await PluginInfo.filter(plugin_type__not=PluginType.HIDDEN).all()
    rows = []
    for plugin in plugin_list:
        status_cell = StatusBadgeCell(
            text="开启" if plugin.status else "关闭",
            status_type="ok" if plugin.status else "error",
        )
        load_cell = StatusBadgeCell(
            text="SUCCESS" if plugin.load_status else "ERROR",
            status_type="ok" if plugin.load_status else "error",
        )
        rows.append(
            [
                plugin.id,
                plugin.module,
                plugin.name,
                status_cell,
                plugin.block_type.value if plugin.block_type else "-",
                load_cell,
                plugin.menu_type or "-",
                plugin.author or "-",
                plugin.version or "-",
                plugin.cost_gold,
            ]
        )

    table = ui.table("Plugin List", "插件状态概览")
    table.set_headers(column_name)
    table.add_rows(rows)
    table.set_column_widths(
        [
            "60px",
            "150px",
            "150px",
            "80px",
            "100px",
            "100px",
            "100px",
            "100px",
            "80px",
            "80px",
        ]
    )
    return await ui.render(table, viewport={"width": 1400, "height": 10})


async def build_task(group_id: str | None) -> bytes:
    """构造被动技能状态图片"""
    task_list = await TaskInfo.all()
    column_name = ["ID", "模块", "名称", "群组状态", "全局状态", "运行时间"]
    group = None
    if group_id:
        group = await GroupConsole.get_group_db(group_id=group_id)
        if not group:
            raise GroupInfoNotFound()
    else:
        column_name.remove("群组状态")
    rows = []
    for task in task_list:
        global_status_cell = StatusBadgeCell(
            text="开启" if task.status else "关闭",
            status_type="ok" if task.status else "error",
        )
        row = [task.id, task.module, task.name]
        if group:
            is_group_open = f"<{task.module}," not in group.block_task
            group_status_cell = StatusBadgeCell(
                text="开启" if is_group_open else "关闭",
                status_type="ok" if is_group_open else "error",
            )
            row.append(group_status_cell)
        row.extend([global_status_cell, task.run_time or "-"])
        rows.append(row)

    table = ui.table("Task List", "被动技能状态概览")
    table.set_headers(column_name)
    table.add_rows(rows)
    if group:
        table.set_column_widths(["60px", "150px", "150px", "100px", "100px", "auto"])
        viewport_width = 1200
    else:
        table.set_column_widths(["60px", "150px", "150px", "100px", "auto"])
        viewport_width = 1000

    return await ui.render(table, viewport={"width": viewport_width, "height": 10})


async def render_global_status(name: str, is_task: bool, bot: Bot) -> bytes:
    """渲染全局状态报表，含差异化过滤和双栏展示"""
    strategy = get_strategy(is_task)
    info = await strategy.get_entity(name)
    if not info:
        raise ValueError(f"未找到{strategy.entity_type_name}: {name}")

    module = info.module
    default_status = info.status

    online_groups, _ = await PlatformUtils.get_group_list(bot)
    valid_keys = {(str(g.group_id), g.channel_id) for g in online_groups}

    all_db_groups = await GroupConsole.all()
    target_groups = [
        g for g in all_db_groups if (str(g.group_id), g.channel_id) in valid_keys
    ]

    total_count = len(target_groups)
    status_data = []
    for group in target_groups:
        gid = str(group.group_id)
        is_su_blocked, is_norm_blocked = await strategy.check_block_status(gid, module)
        is_open = bool(default_status) and not is_su_blocked and not is_norm_blocked

        if not default_status:
            status_text, badge_color = "全局关闭", "error"
        elif is_su_blocked:
            status_text, badge_color = "系统禁用", "error"
        elif is_norm_blocked:
            status_text, badge_color = "群内关闭", "warning"
        else:
            status_text, badge_color = "开启", "success"

        status_data.append(
            {
                "id": str(group.group_id),
                "name": group.group_name,
                "status": is_open,
                "status_text": status_text,
                "badge_color": badge_color,
            }
        )

    open_list = [item for item in status_data if item["status"]]
    close_list = [item for item in status_data if not item["status"]]
    open_count = len(open_list)
    open_rate = open_count / total_count if total_count > 0 else 0

    global_alert = None
    if not default_status:
        global_alert = ui.alert(
            "全局已禁用",
            f"{strategy.entity_type_name} [{name}] 当前处于全局关闭状态。",
            type="error",
        )

    display_list = []
    list_title = "群组状态详情"
    if total_count > 0 and default_status:
        if open_rate > 0.9:
            display_list, list_title = (
                close_list,
                f"异常状态列表 (其余 {open_count} 个群均正常开启)",
            )
        elif open_rate < 0.1:
            display_list, list_title = (
                open_list,
                f"异常状态列表 (其余 {len(close_list)} 个群均已禁用)",
            )
        else:
            display_list = sorted(status_data, key=lambda x: not x["status"])

    return await build_dashboard_report(
        page_title=f"{strategy.entity_type_name}状态报告: {name}",
        total_count=total_count,
        active_count=open_count,
        inactive_count=len(close_list),
        active_rate=open_rate,
        active_label="已开启",
        active_color="var(--color-accent-green)",
        inactive_label="已关闭",
        inactive_color="var(--color-accent-red)",
        progress_label=f"功能 [{name}] 全局覆盖率",
        summary_tip=(
            f"总群数: {total_count} | 🟢 开启: {open_count} | "
            f"🔴 关闭: {len(close_list)}"
        ),
        display_list=display_list,
        list_title=list_title,
        global_alert=global_alert,
        perfect_state_alert=ui.alert(
            "状态完美", f"所有 {total_count} 个群组状态一致。", type="success"
        )
        if not global_alert
        else None,
    )


async def render_group_active_status(bot: Bot) -> bytes:
    """渲染群组醒来/休眠状态报表"""
    online_groups, _ = await PlatformUtils.get_group_list(bot)
    valid_keys = {(str(g.group_id), g.channel_id) for g in online_groups}
    all_db_groups = await GroupConsole.all()
    target_groups = [
        g for g in all_db_groups if (str(g.group_id), g.channel_id) in valid_keys
    ]

    total_count = len(target_groups)
    status_data = [
        {
            "id": str(group.group_id),
            "name": group.group_name,
            "status": group.status,
            "status_text": "工作中" if group.status else "休息中",
            "badge_color": "success" if group.status else "info",
        }
        for group in target_groups
    ]

    wake_list = [item for item in status_data if item["status"]]
    sleep_list = [item for item in status_data if not item["status"]]
    wake_rate = len(wake_list) / total_count if total_count > 0 else 0

    display_list, list_title = status_data, "群组状态详情"
    if wake_rate > 0.9:
        display_list, list_title = (
            sleep_list,
            f"休息中的群组 (其余 {len(wake_list)} 个群正常工作中)",
        )
    elif wake_rate < 0.1:
        display_list, list_title = (
            wake_list,
            f"工作中/已醒来的群组 (其余 {len(sleep_list)} 个群休息中)",
        )

    return await build_dashboard_report(
        page_title="真寻工作状态统计",
        total_count=total_count,
        active_count=len(wake_list),
        inactive_count=len(sleep_list),
        active_rate=wake_rate,
        active_label="当前工作中",
        active_color="var(--color-accent-green)",
        inactive_label="当前休息中",
        inactive_color="var(--color-text-muted)",
        progress_label="全服群组活跃覆盖率",
        display_list=display_list,
        list_title=list_title,
        no_record_alert=ui.alert("无记录", "当前没有已加入的群组记录。", type="info"),
        perfect_state_alert=ui.alert(
            "状态统一",
            (
                f"所有 {total_count} 个群组当前均处于 "
                f"{'工作中' if wake_rate > 0.5 else '休息中'} 状态。"
            ),
            type="success",
        ),
    )


async def build_dashboard_report(
    page_title: str,
    total_count: int,
    active_count: int,
    inactive_count: int,
    active_rate: float,
    active_label: str,
    active_color: str,
    inactive_label: str,
    inactive_color: str,
    progress_label: str,
    display_list: list[dict],
    list_title: str,
    summary_tip: str = "",
    global_alert: Any = None,
    no_record_alert: Any = None,
    perfect_state_alert: Any = None,
) -> bytes:
    """通用的 Dashboard 报表构建器，用于替代原先冗余的 UI 代码"""

    kpi_row = LayoutData.row(gap="12px", align_items="stretch")

    def _build_kpi_card(title: str, value: str, val_color: str):
        header = LayoutData.row(justify_content="space-between", width="100%")
        header.add_item(
            ui.text(title, font_size="13px", color="var(--color-text-muted)")
        )
        if title != "总群数" and title != "管理群总数":
            rate_str = (
                f"{active_rate:.1%}"
                if "已开启" in title or "当前工作" in title
                else f"{(1 - active_rate):.1%}"
            )
            header.add_item(
                ui.text(rate_str, font_size="13px", bold=True, color=val_color)
            )

        content = ui.vstack(
            [
                header.build()
                if "已开启" in title or "已关闭" in title
                else ui.text(title, font_size="13px", color="var(--color-text-muted)"),
                ui.text(value, font_size="24px", bold=True, color=val_color),
            ],
            gap="2px",
            align_items="start" if "总" in title else "stretch",
            padding="0",
        )

        return ui.card(content).with_inline_style({"--card-padding": "12px 16px"})

    kpi_row.add_item(
        _build_kpi_card(
            "总群数" if "功能" in progress_label else "管理群总数",
            str(total_count),
            "var(--color-text-dark)",
        ),
        metadata={"flex": True},
    )
    kpi_row.add_item(
        _build_kpi_card(active_label, str(active_count), active_color),
        metadata={"flex": True},
    )
    kpi_row.add_item(
        _build_kpi_card(inactive_label, str(inactive_count), inactive_color),
        metadata={"flex": True},
    )

    progress_scheme = "primary" if "功能" in progress_label else "success"
    progress_section = ui.vstack(
        [
            ui.text(progress_label, font_size="14px", color="var(--color-text-muted)"),
            ui.progress_bar(
                progress=active_rate * 100,
                label=f"{active_count}/{total_count}",
                color_scheme=progress_scheme,
            ),
        ],
        gap="8px",
    )

    content_area = None
    if not display_list:
        if total_count == 0 and no_record_alert:
            content_area = no_record_alert
        elif global_alert and "功能" in progress_label:
            content_area = global_alert
        elif perfect_state_alert:
            content_area = perfect_state_alert
    elif len(display_list) <= 15:
        rows = []
        for item in display_list:
            status_cell = StatusBadgeCell(
                text=item["status_text"], status_type=item["badge_color"]
            )
            rows.append(
                [
                    TextCell(content=str(item["id"])),
                    TextCell(content=str(item["name"])),
                    status_cell,
                ]
            )
        content_area = (
            ui.table(list_title, None)
            .set_headers(["群号", "群名", "状态"])
            .set_column_widths(["160px", "auto", "100px"])
            .add_rows(rows)
        )
    else:
        grid = LayoutData.grid(columns=3, gap="15px")
        MAX_SHOW = 60
        for item in display_list[:MAX_SHOW]:
            card_content = ui.vstack(
                [
                    ui.text(str(item["name"]), bold=True, font_size="15px"),
                    LayoutData.row(justify_content="space-between", width="100%")
                    .add_item(ui.text(str(item["id"]), font_size="12px", color="#999"))
                    .add_item(
                        ui.badge(item["status_text"], color_scheme=item["badge_color"])
                    ),
                ],
                gap="8px",
                align_items="start",
            )
            grid.add_item(ui.card(card_content))

        container = LayoutData.column(gap="10px")
        container.add_item(grid.build())
        if len(display_list) > MAX_SHOW:
            container.add_item(
                ui.text(
                    f"... 还有 {len(display_list) - MAX_SHOW} 个群组未显示 ...",
                    align="center",
                    color="#ccc",
                )
            )
        content_area = container.build()

    main_layout = LayoutData.column(padding="40px", gap="30px")
    main_layout.add_item(
        ui.text(
            page_title,
            font_size="32px",
            bold=True,
            align="center",
            color="var(--color-primary)",
        )
    )

    stats_items = []
    if global_alert and "功能" in progress_label:
        stats_items.append(global_alert)

    stats_items.extend(
        [
            kpi_row.build(),
            ui.divider(margin="15px 0"),
            progress_section,
        ]
    )

    if summary_tip:
        stats_items.append(
            ui.text(
                summary_tip,
                font_size="13px",
                color="var(--color-text-muted)",
                align="center",
            )
        )

    main_layout.add_item(ui.card(ui.vstack(stats_items)))
    if content_area:
        main_layout.add_item(content_area)

    return await ui.render(main_layout.build(), viewport={"width": 900, "height": 10})
