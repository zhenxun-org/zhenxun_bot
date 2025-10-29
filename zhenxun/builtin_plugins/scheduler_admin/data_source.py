from typing import Any

from pydantic import BaseModel, ValidationError

from zhenxun.models.level_user import LevelUser
from zhenxun.models.scheduled_job import ScheduledJob
from zhenxun.services import scheduler_manager
from zhenxun.services.log import logger
from zhenxun.services.scheduler.repository import ScheduleRepository
from zhenxun.utils.pydantic_compat import model_dump, model_validate

from . import presenters


class SchedulerAdminService:
    """封装定时任务管理的所有业务逻辑"""

    async def get_schedules_view(
        self,
        user_id: str,
        group_id: str | None,
        is_superuser: bool,
        filters: dict[str, Any],
        page: int,
    ) -> bytes | str:
        """获取任务列表视图"""
        page_size = 30
        schedules, total_items = await scheduler_manager.get_schedules(
            page=page, page_size=page_size, **filters
        )

        if not schedules:
            return "没有找到任何相关的定时任务。"

        permitted_schedules = schedules
        skipped_count = 0
        if not is_superuser:
            permitted_schedules, skipped_count = await self._filter_schedules_for_user(
                schedules, user_id, group_id
            )

        if not permitted_schedules:
            return (
                f"您没有权限查看任何匹配的任务。（因权限不足跳过 {skipped_count} 个）"
            )

        title = self._generate_view_title(filters)

        return await presenters.format_schedule_list_as_image(
            schedules=permitted_schedules,
            title=title,
            current_page=page,
            total_items=total_items,
        )

    async def set_schedule(
        self,
        targets: list[str],
        creator_permission_level: int,
        plugin_name: str,
        trigger_info: tuple[str, dict],
        job_kwargs: dict,
        permission: int,
        bot_id: str,
        job_name: str | None,
        jitter: int | None,
        spread: int | None,
        interval: int | None,
        created_by: str,
    ) -> str:
        """创建或更新一个定时任务"""
        trigger_type, trigger_config = trigger_info
        success_targets = []
        failed_targets = []
        permission_denied_targets = []
        execution_options = {}
        if jitter is not None:
            execution_options["jitter"] = jitter
        if spread is not None:
            execution_options["spread"] = spread
        if interval is not None:
            execution_options["interval"] = interval

        for target_desc in targets:
            target_type, target_id = self._resolve_target_descriptor(target_desc)

            existing_schedule = await ScheduleRepository.filter(
                plugin_name=plugin_name,
                target_type=target_type,
                target_identifier=target_id,
                bot_id=bot_id,
            ).first()

            if (
                existing_schedule
                and creator_permission_level < existing_schedule.required_permission
            ):
                permission_denied_targets.append(
                    (
                        target_desc,
                        f"需要 {existing_schedule.required_permission} 级权限",
                    )
                )
                continue

            if target_type in ["TAG", "ALL_GROUPS"]:
                logger.debug(
                    f"检测到多目标任务 (类型: {target_type})，"
                    f"将所需权限强制提升至超级用户级别。"
                )
                permission = 9

            try:
                schedule = await scheduler_manager.add_schedule(
                    plugin_name=plugin_name,
                    target_type=target_type,
                    target_identifier=target_id,
                    trigger_type=trigger_type,
                    trigger_config=trigger_config,
                    job_kwargs=job_kwargs,
                    bot_id=bot_id,
                    required_permission=permission,
                    name=job_name,
                    created_by=created_by,
                    execution_options=execution_options if execution_options else None,
                )
                if schedule:
                    success_targets.append((target_desc, schedule.id))
                else:
                    failed_targets.append((target_desc, "服务返回失败"))
            except Exception as e:
                failed_targets.append((target_desc, str(e)))

        return self._format_set_result_message(
            targets, success_targets, failed_targets, permission_denied_targets
        )

    async def perform_bulk_operation(
        self,
        operation_name: str,
        user_id: str,
        group_id: str | None,
        is_superuser: bool,
        targeter,
        all_flag: bool,
        global_flag: bool,
    ) -> str:
        """执行批量操作（删除、暂停、恢复）"""
        if not is_superuser:
            permission_denied = False
            if all_flag or global_flag:
                permission_denied = True
            elif targeter._filters.get("target_type") in ["TAG", "ALL_GROUPS"]:
                permission_denied = True

            if permission_denied:
                return "权限不足，只有超级用户才能对所有群组或通过标签进行批量操作。"

        schedules_to_operate = await targeter._get_schedules()
        if not schedules_to_operate:
            return "没有找到符合条件的可操作任务。"

        permitted_schedules, skipped_count = (
            (schedules_to_operate, 0)
            if is_superuser
            else await self._filter_schedules_for_user(
                schedules_to_operate, user_id, group_id
            )
        )

        if not permitted_schedules:
            return (
                f"您没有权限{operation_name}任何匹配的任务。"
                f"（因权限不足跳过 {skipped_count} 个）"
            )

        permitted_ids = [s.id for s in permitted_schedules]
        final_targeter = scheduler_manager.target(id__in=permitted_ids)

        operation_map = {
            "删除": final_targeter.remove,
            "暂停": final_targeter.pause,
            "恢复": final_targeter.resume,
        }
        operation_func = operation_map.get(operation_name)
        if not operation_func:
            return f"未知的批量操作: {operation_name}"

        count, _ = await operation_func()
        msg = f"批量{operation_name}操作完成：\n  - 成功: {count} 个"
        if skipped_count > 0:
            msg += f"\n  - 因权限不足跳过: {skipped_count} 个"
        return msg

    async def trigger_schedule_now(self, schedule: ScheduledJob) -> str:
        """立即触发一个任务"""
        success, message = await scheduler_manager.trigger_now(schedule.id)
        return (
            presenters.format_trigger_success(schedule)
            if success
            else f"❌ 触发失败: {message}"
        )

    async def update_schedule(
        self, schedule: ScheduledJob, trigger_info: tuple | None, kwargs_str: str | None
    ) -> str:
        """更新一个任务的配置"""
        trigger_type = trigger_info[0] if trigger_info else None
        trigger_config = trigger_info[1] if trigger_info else None
        job_kwargs = await self._parse_and_validate_kwargs_for_update(
            schedule.plugin_name, kwargs_str
        )
        success, message = await scheduler_manager.update_schedule(
            schedule.id, trigger_type, trigger_config, job_kwargs
        )
        if success:
            updated_schedule = await scheduler_manager.get_schedule_by_id(schedule.id)
            return (
                presenters.format_update_success(updated_schedule)
                if updated_schedule
                else "✅ 更新成功，但无法获取更新后的任务详情。"
            )
        return f"❌ 更新失败: {message}"

    async def get_schedule_status(self, schedule_id: int) -> str:
        """获取单个任务的状态"""
        status = await scheduler_manager.get_schedule_status(schedule_id)
        if not status:
            return f"未找到ID为 {schedule_id} 的任务。"
        return presenters.format_single_status_message(status)

    async def get_plugins_list(self) -> str:
        """获取可定时执行的插件列表"""
        return await presenters.format_plugins_list()

    async def _filter_schedules_for_user(
        self, schedules: list[ScheduledJob], user_id: str, group_id: str | None
    ) -> tuple[list[ScheduledJob], int]:
        user_level = await LevelUser.get_user_level(user_id, group_id)
        permitted = [s for s in schedules if user_level >= s.required_permission]
        skipped_count = len(schedules) - len(permitted)
        return permitted, skipped_count

    def _generate_view_title(self, filters: dict) -> str:
        title = "定时任务"
        if filters.get("target_type") == "ALL_GROUPS":
            title = "全局定时任务"
        elif "target_identifier" in filters:
            title = f"群 {filters['target_identifier']} 的定时任务"
        if "plugin_name" in filters:
            title += f" [插件: {filters['plugin_name']}]"
        return title

    def _resolve_target_descriptor(self, target_desc: str) -> tuple[str, str]:
        if target_desc == scheduler_manager.ALL_GROUPS:
            return "ALL_GROUPS", scheduler_manager.ALL_GROUPS
        if target_desc.startswith("tag:"):
            return "TAG", target_desc[4:]
        if target_desc.isdigit():
            return "GROUP", target_desc
        return "USER", target_desc

    def _format_set_result_message(
        self, targets: list, success: list, failed: list, permission_denied: list
    ) -> str:
        msg = f"为 {len(targets)} 个目标设置/更新任务完成：\n"
        if success:
            msg += f"- 成功: {len(success)} 个"
            ids_str = ", ".join(str(s[1]) for s in success)
            msg += f"\n  - ID列表: {ids_str}"
        else:
            msg += "- 成功: 0 个"
        if permission_denied:
            msg += f"\n- 因权限不足跳过: {len(permission_denied)} 个"
            for target, reason in permission_denied:
                msg += f"\n  - 目标 {target}: {reason}"
        if failed:
            msg += f"\n- 失败: {len(failed)} 个"
            for target, reason in failed:
                msg += f"\n  - 目标 {target}: {reason}"
        return msg.strip()

    async def _parse_and_validate_kwargs_for_update(
        self, plugin_name: str, kwargs_str: str | None
    ) -> dict:
        if not kwargs_str:
            return {}

        task_meta = scheduler_manager._registered_tasks.get(plugin_name)
        if not task_meta:
            raise ValueError(f"插件 '{plugin_name}' 未注册。")

        params_model = task_meta.get("model")
        if not (
            params_model
            and isinstance(params_model, type)
            and issubclass(params_model, BaseModel)
        ):
            raise ValueError(f"插件 '{plugin_name}' 不支持或配置了无效的参数模型。")

        try:
            raw_kwargs = dict(
                item.strip().split("=", 1) for item in kwargs_str.split(";")
            )
            validated_model = model_validate(params_model, raw_kwargs)
            return model_dump(validated_model)
        except ValidationError as e:
            errors = [f"  - {err['loc'][0]}: {err['msg']}" for err in e.errors()]
            raise ValueError("参数验证失败:\n" + "\n".join(errors))
        except Exception as e:
            raise ValueError(f"参数格式错误: {e}")


scheduler_admin_service = SchedulerAdminService()
