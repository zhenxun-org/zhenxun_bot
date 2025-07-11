"""
定时调度服务模块

提供一个统一的、持久化的定时任务管理器，供所有插件使用。
"""

from .lifecycle import _load_schedules_from_db
from .service import scheduler_manager

_ = _load_schedules_from_db

__all__ = ["scheduler_manager"]
